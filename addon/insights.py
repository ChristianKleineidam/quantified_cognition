"""Historical, paired daily associations. No collection access or third-party runtime."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from .metrics import FORMATION_METRICS, RECALL_METRICS
from .phase_comparisons import (
    CLOSURE_TOLERANCE_MINUTES,
    CONTRASTS,
    MAX_VIF,
    MIN_DONOR_DAYS,
    MIN_INDEPENDENT_MINUTES,
    SHIFT_MINUTES,
    PhaseAssociation,
    build_phase_cohorts,
    compare_phases,
)
from .private_storage import LockedHealthData
from .sleep_sessions import NAP_STAGES, study_features
from .statistical_math import calendar_windows, draw_blocks
from .workouts import study_workouts

VERSION = 7
METRICS = RECALL_METRICS + FORMATION_METRICS
SLEEP_STAGES = ("light_sleep", "deep_sleep", "rem_sleep")
SLEEP_SHARE_FACTORS = tuple(f"{stage}_share" for stage in SLEEP_STAGES)
MAIN_FACTORS = (
    "total_sleep",
    *SLEEP_STAGES,
    *SLEEP_SHARE_FACTORS,
    "awake",
    "restless",
    "steps",
)
NAP_FACTORS = ("nap_recorded", "nap_duration", *("nap_" + stage for stage in NAP_STAGES), "nap_gap")
WORKOUT_FACTORS = (
    "workout_recorded",
    "workout_duration",
    "workout_easy",
    "workout_moderate",
    "workout_hard",
    "workout_gap",
)
FACTORS = MAIN_FACTORS + NAP_FACTORS + WORKOUT_FACTORS
FACTOR_LABELS = (
    "Total sleep",
    "Light sleep (hours)",
    "Deep sleep (hours)",
    "REM sleep (hours)",
    "Light sleep (%)",
    "Deep sleep (%)",
    "REM sleep (%)",
    "Awake",
    "Restless",
    "Daily steps",
    "Recorded nap",
    "Nap duration",
    "Light sleep in naps",
    "Deep sleep in naps",
    "REM sleep in naps",
    "Nap proximity",
    "Recorded workout",
    "Workout duration",
    "Easy exercise",
    "Moderate exercise",
    "Hard exercise",
    "Workout proximity",
)
PROVIDERS = ("oura", "google_health")


@dataclass(frozen=True)
class InsightSettings:
    adjust_month: bool = True
    minimum_days: int = 60
    minimum_span_days: int = 90
    minimum_blocks: int = 6
    block_days: int = 14
    bootstrap_replicates: int = 999
    minimum_month_days: int = 4
    formation_age_days: int = 30
    formation_coverage: float = 80.0
    minimum_nap_group_days: int = 10
    minimum_workout_group_days: int = 10

    def __post_init__(self):
        if (
            type(self.adjust_month) is not bool
            or self.minimum_days < 3
            or self.minimum_span_days < 1
            or self.minimum_blocks < 2
            or self.block_days < 2
            or self.bootstrap_replicates < 199
            or self.minimum_month_days < 3
            or self.formation_age_days < 30
            or self.minimum_nap_group_days < 2
            or self.minimum_workout_group_days < 2
            or not 0 < self.formation_coverage <= 100
        ):
            raise ValueError("Invalid personal insight settings.")


DEFAULT_INSIGHT_SETTINGS = InsightSettings()


class InputsChanged(Exception):
    """Saved source data changed while a worker was estimating uncertainty."""


def _inputs_token(source):
    from .health import health_database_path, health_revision_at
    from .storage import database_path

    path = database_path(source)
    revision = ""
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "select value from metadata where key='report_revision'"
            ).fetchone()
            revision = row[0] if row else ""
    return revision, health_revision_at(health_database_path(source))


def delete_cached_insights(source, provider=None):
    """Provider deletion also removes locally derived health associations."""
    from .storage import profile_job_lock, results_directory

    with profile_job_lock(source, "insights"):
        (results_directory(source) / "personal-insights.json").unlink(missing_ok=True)
    from .insight_visits import delete_visit_history

    delete_visit_history(source, provider)


@dataclass(frozen=True)
class Point:
    day: date
    health_day: date | None
    x: float
    y: float
    reviews: int


@dataclass
class Series:
    factor: str
    metric: str
    timing: str
    points: list[Point] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.factor}/{self.metric}/{self.timing}"


@dataclass(frozen=True)
class Association:
    days: int
    span_days: int
    blocks: int
    reviews: int
    first_day: str
    last_day: str
    correlation: float | None
    low: float | None
    high: float | None
    raw_correlation: float | None
    status: str
    reason: str
    nap_days: int | None = None
    no_recorded_nap_days: int | None = None
    workout_days: int | None = None
    no_recorded_workout_days: int | None = None


@dataclass
class Insights:
    provider: str
    settings: InsightSettings
    series: dict[str, Series]
    associations: dict[str, Association]
    generated_at: str
    memory_generated_at: str = ""
    health_days: int = 0
    reused: bool = False
    memory_metadata: dict = field(default_factory=dict)
    phase_cohorts: dict = field(default_factory=dict)
    phase_associations: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    visit_snapshot: dict = field(default_factory=dict)
    visit_changes: dict = field(default_factory=dict)
    visit_state: dict = field(default_factory=dict)
    main_sleep: SleepStageSummary | None = None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _stage_shares(total, stages):
    if not _finite(total) or total <= 0 or any(not _finite(s) or s < 0 for s in stages):
        return None
    staged = sum(stages)
    if staged <= 0 or abs(staged - total) > CLOSURE_TOLERANCE_MINUTES * 60:
        return None
    return tuple(100 * stage / staged for stage in stages)


@dataclass(frozen=True)
class SleepStageSummary:
    nights: int
    average_total_seconds: float
    average_stage_seconds: tuple[float, ...]
    average_stage_percent: tuple[float, ...]


def summarize_main_sleep(health, provider, *, start=None, end=None):
    """Use the same complete, unambiguous main sleep sessions for every stage."""
    nights = []
    for session in health.sessions:
        if session.provider != provider or session.kind != "main" or session.status != "available":
            continue
        wake_day = date.fromisoformat(session.wake_day or session.day)
        if (start and wake_day < start) or (end and wake_day > end):
            continue
        total = session.values.get("total_sleep")
        stages = tuple(session.values.get(stage) for stage in SLEEP_STAGES)
        shares = _stage_shares(total, stages)
        if shares is not None:
            nights.append((total, *stages, *shares))
    if not nights:
        return None
    averages = tuple(map(mean, zip(*nights, strict=True)))
    stage_end = 1 + len(SLEEP_STAGES)
    return SleepStageSummary(len(nights), averages[0], averages[1:stage_end], averages[stage_end:])


def build_series(
    report, health, provider, *, as_of, settings=DEFAULT_INSIGHT_SETTINGS, start=None, end=None
):
    """Source Anki date is the observational unit; never pool provider records."""
    if provider not in PROVIDERS:
        raise ValueError("Select Oura or Google Health.")
    if start and end and end < start:
        raise ValueError("The end date precedes the start date.")
    result = {}
    session_features = study_features(report, health, provider)
    workout_features = study_workouts(report, health) if provider == "oura" else {}
    for factor in FACTORS:
        if factor in WORKOUT_FACTORS and provider != "oura":
            continue
        for metric in METRICS:
            formation = metric in FORMATION_METRICS
            if factor == "steps":
                timings = ("study_day",) if formation else ("previous_day",)
            else:
                timings = ("before", "after") if formation else ("before",)
            for timing in timings:
                series = Series(factor, metric, timing)
                omitted = Counter()
                for day in report.days if report is not None else ():
                    if (start and day.day < start) or (end and day.day > end):
                        continue
                    reason = None
                    score = getattr(day, metric)
                    if day.day >= as_of:
                        reason = "unfinished/current day"
                    elif score is None or not _finite(score.score_pp):
                        reason = "no evaluated score"
                    elif score.count < report.settings.minimum_reviews:
                        reason = "too few evaluated reviews"
                    elif formation:
                        if (as_of - day.day).days < settings.formation_age_days:
                            reason = "formation still recent"
                        elif day.formation.coverage_percent < settings.formation_coverage:
                            reason = "formation follow-ups incomplete"
                        elif score.outcome_days < report.settings.minimum_formation_outcome_days:
                            reason = "too few formation outcome days"
                        elif score.uncertainty_low_pp is None:
                            reason = "follow-ups concentrated on too few outcome days"
                    if reason:
                        omitted[reason] += 1
                        continue
                    offset = 1 if timing == "after" else -1 if timing == "previous_day" else 0
                    health_day = day.day + timedelta(days=offset)
                    units = 1
                    if factor == "steps":
                        record = health.days.get(health_day)
                        value = record.values.get(f"{provider}_steps") if record else None
                        status = "available"
                    elif factor in WORKOUT_FACTORS:
                        values = workout_features[day.day]
                        key = f"oura_workout_{timing}_"
                        suffix = {
                            "workout_recorded": "recorded",
                            "workout_duration": "duration_seconds",
                            "workout_easy": "easy_seconds",
                            "workout_moderate": "moderate_seconds",
                            "workout_hard": "hard_seconds",
                            "workout_gap": "gap_seconds",
                        }[factor]
                        value, status = values[key + suffix], values[key + "status"]
                        units = (
                            1
                            if factor == "workout_recorded"
                            else 3600
                            if factor == "workout_gap"
                            else 60
                        )
                    else:
                        values = session_features[day.day]
                        if factor in NAP_FACTORS:
                            suffix = {
                                "nap_recorded": "recorded",
                                "nap_duration": "duration_seconds",
                                "nap_gap": "gap_seconds",
                                **{"nap_" + stage: stage + "_seconds" for stage in NAP_STAGES},
                            }[factor]
                            key = f"{provider}_nap_{timing}_"
                            value = values[key + suffix]
                            units = (
                                60
                                if factor != "nap_recorded" and factor != "nap_gap"
                                else 3600
                                if factor == "nap_gap"
                                else 1
                            )
                            status = values[key + "status"]
                        else:
                            key = f"{provider}_main_{timing}_"
                            status = values[key + "status"]
                            if factor in SLEEP_SHARE_FACTORS:
                                stages = tuple(
                                    values[key + stage + "_seconds"] for stage in SLEEP_STAGES
                                )
                                shares = _stage_shares(values[key + "total_sleep_seconds"], stages)
                                value = (
                                    shares[SLEEP_SHARE_FACTORS.index(factor)] if shares else None
                                )
                            else:
                                value = values[key + factor + "_seconds"]
                                units = 3600
                    if status not in ("available", "nap duration unavailable"):
                        omitted[status] += 1
                        continue
                    if not _finite(value) or value < 0:
                        omitted[
                            "no recorded workout in window"
                            if factor == "workout_gap" and values.get(key + "recorded") == 0
                            else "workout intensity unavailable"
                            if factor in ("workout_easy", "workout_moderate", "workout_hard")
                            else "no recorded nap in window"
                            if factor in NAP_FACTORS
                            and factor != "nap_recorded"
                            and values.get(key + "recorded") == 0
                            else "nap stage unavailable"
                            if factor in {"nap_" + stage for stage in NAP_STAGES}
                            else "complete sleep staging unavailable"
                            if factor in SLEEP_SHARE_FACTORS
                            else "missing health measurement"
                        ] += 1
                        continue
                    series.points.append(
                        Point(
                            day.day,
                            health_day if factor == "steps" else None,
                            float(value) / units,
                            float(score.score_pp),
                            score.count,
                        )
                    )
                series.points.sort(key=lambda p: p.day)
                if settings.adjust_month:
                    months = Counter((p.day.year, p.day.month) for p in series.points)
                    kept = [
                        p
                        for p in series.points
                        if months[p.day.year, p.day.month] >= settings.minimum_month_days
                    ]
                    omitted["too few matched days in calendar month"] += len(series.points) - len(
                        kept
                    )
                    series.points = kept
                series.omitted = dict(omitted)
                result[series.key] = series
    return result


def plotted_points(series, adjust_month):
    """Chart uses the same centering as the reported coefficient."""
    if not adjust_month:
        return [(p.x, p.y) for p in series.points]
    groups = defaultdict(list)
    for p in series.points:
        groups[p.day.year, p.day.month].append(p)
    means = {
        key: (sum(p.x for p in rows) / len(rows), sum(p.y for p in rows) / len(rows))
        for key, rows in groups.items()
    }
    return [
        (p.x - means[p.day.year, p.day.month][0], p.y - means[p.day.year, p.day.month][1])
        for p in series.points
    ]


def _coefficient(groups, adjust_month):
    # Moments are standardized before accumulation, avoiding cancellation from
    # e.g. daily step baselines or large numbers of bootstrap duplicates.
    if not groups:
        return None
    if not adjust_month:
        groups = [tuple(sum(row[i] for row in groups) for i in range(6))]
    xx = yy = xy = 0.0
    for n, x, y, x2, y2, cross in groups:
        if n:
            xx += x2 - x * x / n
            yy += y2 - y * y / n
            xy += cross - x * y / n
    if xx <= 1e-10 or yy <= 1e-10:
        return None
    return max(-1.0, min(1.0, xy / math.sqrt(xx * yy)))


def _moments(points):
    n = len(points)
    if n < 3:
        return None
    mx, my = sum(p.x for p in points) / n, sum(p.y for p in points) / n
    sx = math.sqrt(sum((p.x - mx) ** 2 for p in points) / n)
    sy = math.sqrt(sum((p.y - my) ** 2 for p in points) / n)
    if sx <= 1e-10 or sy <= 1e-10:
        return None
    return [(p.day, (p.x - mx) / sx, (p.y - my) / sy) for p in points]


def _group_moments(rows):
    grouped = {}
    for day, x, y in rows:
        key = (day.year, day.month)
        value = grouped.setdefault(key, [0.0] * 6)
        for i, item in enumerate((1, x, y, x * x, y * y, x * y)):
            value[i] += item
    return grouped


def _quantile(values, q):
    pos = (len(values) - 1) * q
    lower = int(pos)
    upper = min(len(values) - 1, lower + 1)
    return values[lower] + (values[upper] - values[lower]) * (pos - lower)


def correlate(series, settings=DEFAULT_INSIGHT_SETTINGS, *, check_cancelled=lambda: None):
    points, n = series.points, len(series.points)
    span = (points[-1].day - points[0].day).days + 1 if n else 0
    blocks = len({(p.day - points[0].day).days // settings.block_days for p in points}) if n else 0
    base = dict(
        days=n,
        span_days=span,
        blocks=blocks,
        reviews=sum(p.reviews for p in points),
        first_day=points[0].day.isoformat() if n else "",
        last_day=points[-1].day.isoformat() if n else "",
        nap_days=sum(p.x == 1 for p in points) if series.factor == "nap_recorded" else None,
        no_recorded_nap_days=sum(p.x == 0 for p in points)
        if series.factor == "nap_recorded"
        else None,
        workout_days=sum(p.x == 1 for p in points) if series.factor == "workout_recorded" else None,
        no_recorded_workout_days=sum(p.x == 0 for p in points)
        if series.factor == "workout_recorded"
        else None,
    )
    rows = _moments(points)
    grouped = _group_moments(rows) if rows else {}
    raw = _coefficient(list(grouped.values()), False)
    coefficient = _coefficient(list(grouped.values()), settings.adjust_month)

    def insufficient(reason):
        return Association(
            **base,
            correlation=coefficient,
            low=None,
            high=None,
            raw_correlation=raw,
            status="insufficient data",
            reason=reason,
        )

    if n < settings.minimum_days:
        return insufficient(f"Needs {settings.minimum_days} matched eligible days; has {n}.")
    if span < settings.minimum_span_days or blocks < settings.minimum_blocks:
        return insufficient(
            f"Needs at least {settings.minimum_span_days} calendar days and "
            f"{settings.minimum_blocks} occupied {settings.block_days}-day blocks."
        )
    if (
        series.factor == "nap_recorded"
        and min(base["nap_days"], base["no_recorded_nap_days"]) < settings.minimum_nap_group_days
    ):
        return insufficient(
            f"Needs at least {settings.minimum_nap_group_days} recorded-nap days "
            "and as many days with no recorded nap."
        )
    if coefficient is None:
        return insufficient("Not enough variation in the measurement or score after adjustment.")
    if (
        series.factor == "workout_recorded"
        and min(base["workout_days"], base["no_recorded_workout_days"])
        < settings.minimum_workout_group_days
    ):
        return insufficient(
            f"Needs at least {settings.minimum_workout_group_days} recorded-workout days "
            "and as many days with no recorded workout."
        )
    first = points[0].day
    # Calendar slots preserve gaps. Consecutive observed records separated by
    # weeks/months must never become fake adjacent days during resampling.
    calendar = [[] for _ in range(span)]
    for row in rows:
        calendar[(row[0] - first).days].append(row)
    length = min(settings.block_days, span)
    draws, tail = divmod(span, length)

    def windows(width):
        result = []
        for start, window in enumerate(calendar_windows(calendar, width)):
            if start % 500 == 0:
                check_cancelled()
            result.append(
                tuple((key, tuple(value)) for key, value in _group_moments(window).items())
            )
        return result

    full, partial = windows(length), windows(tail) if tail else []
    seed = hashlib.sha256(
        json.dumps(
            [(p.day.isoformat(), p.x, p.y) for p in points], separators=(",", ":"), allow_nan=False
        ).encode()
    ).digest()
    rng = random.Random(seed)
    estimates = []
    for iteration in range(settings.bootstrap_replicates):
        if iteration % 20 == 0:
            check_cancelled()
        sampled = {}
        for block in draw_blocks(full, partial, draws, rng):
            for key, values in block:
                current = sampled.setdefault(key, [0.0] * 6)
                for i in range(6):
                    current[i] += values[i]
        value = _coefficient(list(sampled.values()), settings.adjust_month)
        if value is not None:
            estimates.append(value)
    if len(estimates) < math.ceil(settings.bootstrap_replicates * 0.95):
        return insufficient("Too many resampled histories had no usable variation.")
    estimates.sort()
    return Association(
        **base,
        correlation=coefficient,
        low=_quantile(estimates, 0.025),
        high=_quantile(estimates, 0.975),
        raw_correlation=raw,
        status="exploratory estimate",
        reason="",
    )


def _fingerprint(series, settings, provider, phase_cohorts=None):
    digest = hashlib.sha256(
        json.dumps([VERSION, provider, asdict(settings)], sort_keys=True).encode()
    )
    for key, value in sorted(series.items()):
        digest.update(key.encode())
        digest.update(
            json.dumps(
                [(p.day.isoformat(), p.x, p.y, p.reviews) for p in value.points],
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
    for key, cohort in sorted((phase_cohorts or {}).items()):
        digest.update(key.encode())
        digest.update(
            json.dumps(
                [
                    (p.day.isoformat(), p.total, p.deep, p.rem, p.score, p.reviews)
                    for p in cohort.points
                ],
                allow_nan=False,
            ).encode()
        )
    return digest.hexdigest()


def history_coverage(report, health, provider, *, as_of, start=None, end=None):
    selected = (
        [
            day
            for day in report.days
            if day.day < as_of
            and (start is None or day.day >= start)
            and (end is None or day.day <= end)
        ]
        if report
        else []
    )
    health_dates = [
        day
        for day, row in health.days.items()
        if row.values.get(f"{provider}_day_basis")
        and (start is None or day >= start)
        and (end is None or day <= end)
    ]
    return dict(
        study_days=len(selected),
        health_days=len(health_dates),
        first_study_day=min(day.day for day in selected).isoformat() if selected else "",
        last_study_day=max(day.day for day in selected).isoformat() if selected else "",
        first_health_day=min(health_dates).isoformat() if health_dates else "",
        last_health_day=max(health_dates).isoformat() if health_dates else "",
        pending_formation_days=sum(day.formation.pending_count > 0 for day in selected),
        recent_formation_days=sum(
            (as_of - day.day).days < 30 and day.formation.cohort_count > 0 for day in selected
        ),
    )


def _atomic_json(path, value):
    from .private_storage import write_json

    write_json(path, value)


def load_insights(
    source,
    provider,
    *,
    settings=DEFAULT_INSIGHT_SETTINGS,
    as_of=None,
    start=None,
    end=None,
    period_days=None,
    check_cancelled=lambda: None,
):
    """Capture saved inputs briefly, release the publisher lock, then bootstrap."""
    from .health import load_health
    from .metrics import anki_day
    from .storage import database_path, load_report, profile_job_lock, results_directory

    check_cancelled()
    with profile_job_lock(source):
        report, health = load_report(database_path(source)), load_health(source)
        token = _inputs_token(source)
    if provider is None:
        provider = max(
            PROVIDERS,
            key=lambda name: sum(
                bool(day.values.get(f"{name}_day_basis")) for day in health.days.values()
            ),
        )
    if as_of is None:
        as_of = anki_day(time.time(), report.rollover_hour) if report else date.today()
    series = build_series(
        report,
        health,
        provider,
        as_of=as_of,
        settings=settings,
        start=start,
        end=end,
    )
    phase_cohorts = build_phase_cohorts(series, settings)
    phase_keys = {
        f"{cohort.scope}/{contrast}/{cohort.metric}/{cohort.timing}"
        for cohort in phase_cohorts.values()
        for contrast in CONTRASTS
    }
    key = _fingerprint(series, settings, provider, phase_cohorts)
    path = results_directory(source) / "personal-insights.json"
    with profile_job_lock(source, "insights"):
        check_cancelled()
        try:
            from .private_storage import read_json

            cache = read_json(path)
            if (
                not isinstance(cache, dict)
                or cache.get("version") != VERSION
                or not isinstance(cache.get("entries"), dict)
            ):
                cache = {"version": VERSION, "entries": {}}
        except LockedHealthData:
            raise
        except (OSError, ValueError):
            cache = {"version": VERSION, "entries": {}}
        saved = cache["entries"].get(key)
        if not isinstance(saved, dict):
            saved = None
        reused = saved is not None
        if saved:
            try:
                if not isinstance(saved.get("associations"), dict) or not isinstance(
                    saved.get("generated_at"), str
                ):
                    raise ValueError()
                associations = {k: Association(**v) for k, v in saved["associations"].items()}
                if set(associations) != set(series):
                    raise ValueError()
                for item in associations.values():
                    if item.status not in ("insufficient data", "exploratory estimate"):
                        raise ValueError()
                    if any(
                        value is not None and (not _finite(value) or not -1 <= value <= 1)
                        for value in (item.correlation, item.raw_correlation, item.low, item.high)
                    ):
                        raise ValueError()
                    if item.status == "exploratory estimate" and (
                        item.low is None or item.high is None
                    ):
                        raise ValueError()
                    if item.low is not None and item.high is not None and item.low > item.high:
                        raise ValueError()
                if not isinstance(saved.get("phase_associations"), dict):
                    raise ValueError()
                phase_associations = {
                    k: PhaseAssociation(**v) for k, v in saved["phase_associations"].items()
                }
                if set(phase_associations) != phase_keys:
                    raise ValueError()
                for item in phase_associations.values():
                    if item.status not in ("insufficient data", "exploratory estimate"):
                        raise ValueError()
                    if any(
                        value is not None and not _finite(value)
                        for value in (item.effect_pp, item.low_pp, item.high_pp, item.maximum_vif)
                    ):
                        raise ValueError()
                    if item.status == "exploratory estimate" and (
                        item.effect_pp is None or item.low_pp is None or item.high_pp is None
                    ):
                        raise ValueError()
                    if (
                        item.low_pp is not None
                        and item.high_pp is not None
                        and item.low_pp > item.high_pp
                    ):
                        raise ValueError()
            except (KeyError, TypeError, ValueError):
                saved, reused = None, False
        if not saved:
            associations = {
                k: correlate(value, settings, check_cancelled=check_cancelled)
                for k, value in series.items()
            }
            phase_associations = {}
            for cohort in phase_cohorts.values():
                check_cancelled()
                for contrast, value in compare_phases(
                    cohort, settings, check_cancelled=check_cancelled
                ).items():
                    phase_associations[
                        f"{cohort.scope}/{contrast}/{cohort.metric}/{cohort.timing}"
                    ] = value
            saved = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "associations": {k: asdict(v) for k, v in associations.items()},
                "phase_associations": {k: asdict(v) for k, v in phase_associations.items()},
            }
            cache["entries"][key] = saved
            cache["entries"] = dict(list(cache["entries"].items())[-8:])
            check_cancelled()
            if _inputs_token(source) != token:
                raise InputsChanged("Saved history changed; updating personal insights.")
            _atomic_json(path, cache)
        if _inputs_token(source) != token:
            raise InputsChanged("Saved history changed; updating personal insights.")
    result = Insights(
        provider,
        settings,
        series,
        associations,
        saved["generated_at"],
        report.generated_at if report else "",
        sum(bool(row.values.get(f"{provider}_day_basis")) for row in health.days.values()),
        reused,
        {
            "minimum_reviews": report.settings.minimum_reviews,
            "minimum_formation_outcome_days": report.settings.minimum_formation_outcome_days,
            "parameters_hash": report.parameters_hash,
            "anki_version": report.anki_version,
            "rollover_hour": report.rollover_hour,
            "timezone": report.timezone_name,
        }
        if report
        else {},
        phase_cohorts,
        phase_associations,
        history_coverage(report, health, provider, as_of=as_of, start=start, end=end),
        main_sleep=summarize_main_sleep(health, provider, start=start, end=end),
    )
    from .insight_visits import make_snapshot

    result.visit_snapshot = make_snapshot(
        result,
        report,
        health,
        method_version=VERSION,
        as_of=as_of,
        start=start,
        end=end,
        period_days=period_days,
    )
    return result


def export_insights(insights, path, *, before_replace=lambda: None):
    """Separate personal summary export; the research package stays daily-only."""
    path, temporary = Path(path), None
    from .insight_visits import EXPORT_FIELDS as VISIT_EXPORT_FIELDS
    from .insight_visits import export_changes

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            fields = [
                "provider",
                "factor",
                "metric",
                "timing",
                *Association.__dataclass_fields__,
                "month_adjusted",
                "block_days",
                "bootstrap_replicates",
                "formation_min_age_days",
                "formation_min_coverage_percent",
                "method_version",
                "generated_at_utc",
                "memory_generated_at_utc",
                "minimum_days",
                "minimum_span_days",
                "minimum_blocks",
                "minimum_month_days",
                "minimum_nap_group_days",
                "minimum_reviews",
                "minimum_formation_outcome_days",
                "parameters_hash",
                "anki_version",
                "rollover_hour",
                "timezone",
                "omitted_days_by_reason",
                "analysis_kind",
                "phase_scope",
                "phase_donor",
                "phase_receiver",
                "effect_pp",
                "low_pp",
                "high_pp",
                "shift_minutes",
                "donor_supported_days",
                "maximum_vif",
                "phase_model",
                "phase_closure_tolerance_minutes",
                "phase_maximum_vif",
                "phase_minimum_independent_minutes",
                "phase_minimum_donor_days",
                *VISIT_EXPORT_FIELDS,
            ]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for key, result in insights.associations.items():
                series = insights.series[key]
                writer.writerow(
                    dict(
                        provider=insights.provider,
                        factor=series.factor,
                        metric=series.metric,
                        timing=series.timing,
                        **asdict(result),
                        month_adjusted=insights.settings.adjust_month,
                        block_days=insights.settings.block_days,
                        bootstrap_replicates=insights.settings.bootstrap_replicates,
                        formation_min_age_days=insights.settings.formation_age_days,
                        formation_min_coverage_percent=insights.settings.formation_coverage,
                        method_version=VERSION,
                        generated_at_utc=insights.generated_at,
                        memory_generated_at_utc=insights.memory_generated_at,
                        minimum_days=insights.settings.minimum_days,
                        minimum_span_days=insights.settings.minimum_span_days,
                        minimum_blocks=insights.settings.minimum_blocks,
                        minimum_month_days=insights.settings.minimum_month_days,
                        minimum_nap_group_days=insights.settings.minimum_nap_group_days,
                        omitted_days_by_reason=json.dumps(series.omitted, sort_keys=True),
                        **insights.memory_metadata,
                        analysis_kind="correlation",
                        **export_changes(insights.visit_changes, "association/" + key),
                    )
                )
            for key, result in insights.phase_associations.items():
                scope, contrast, metric, timing = key.split("/")
                cohort = insights.phase_cohorts[f"{scope}/{metric}/{timing}"]
                donor, receiver = CONTRASTS[contrast]
                writer.writerow(
                    dict(
                        provider=insights.provider,
                        factor=contrast,
                        metric=metric,
                        timing=timing,
                        **asdict(result),
                        analysis_kind="phase_substitution",
                        phase_scope=scope,
                        phase_donor=donor,
                        phase_receiver=receiver,
                        shift_minutes=SHIFT_MINUTES,
                        phase_model="linear_isotemporal_ols_total_other_phase",
                        month_adjusted=insights.settings.adjust_month,
                        block_days=insights.settings.block_days,
                        bootstrap_replicates=insights.settings.bootstrap_replicates,
                        formation_min_age_days=insights.settings.formation_age_days,
                        formation_min_coverage_percent=insights.settings.formation_coverage,
                        method_version=VERSION,
                        generated_at_utc=insights.generated_at,
                        memory_generated_at_utc=insights.memory_generated_at,
                        minimum_days=insights.settings.minimum_days,
                        minimum_span_days=insights.settings.minimum_span_days,
                        minimum_blocks=insights.settings.minimum_blocks,
                        minimum_month_days=insights.settings.minimum_month_days,
                        phase_closure_tolerance_minutes=CLOSURE_TOLERANCE_MINUTES,
                        phase_maximum_vif=MAX_VIF,
                        phase_minimum_independent_minutes=MIN_INDEPENDENT_MINUTES,
                        phase_minimum_donor_days=MIN_DONOR_DAYS,
                        omitted_days_by_reason=json.dumps(cohort.omitted, sort_keys=True),
                        **insights.memory_metadata,
                        **export_changes(insights.visit_changes, "phase/" + key),
                    )
                )
            handle.flush()
            os.fsync(handle.fileno())
        before_replace()
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return len(insights.associations) + len(insights.phase_associations)
