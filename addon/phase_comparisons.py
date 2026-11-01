"""Matched, fixed-total linear sleep-phase substitutions; no runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from .statistical_math import bootstrap_range, calendar_windows, draw_blocks

SHIFT_MINUTES = 10
CLOSURE_TOLERANCE_MINUTES = 3
MAX_VIF = 100
MIN_INDEPENDENT_MINUTES = 1
MIN_DONOR_DAYS = 10
CONTRASTS = {
    "deep_for_light": ("light", "deep"),
    "rem_for_light": ("light", "REM"),
    "deep_for_rem": ("REM", "deep"),
}
LABELS = ("Light → deep", "Light → REM", "REM → deep")
_PAIRS = tuple((j, k) for j in range(4) for k in range(j, 4))


@dataclass(frozen=True)
class PhasePoint:
    day: date
    total: float  # Minutes; the three stages are closed to reported total.
    light: float
    deep: float
    rem: float
    score: float
    reviews: int


@dataclass
class PhaseCohort:
    scope: str
    metric: str
    timing: str
    points: list[PhasePoint] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.scope}/{self.metric}/{self.timing}"


@dataclass(frozen=True)
class PhaseAssociation:
    days: int
    span_days: int
    blocks: int
    reviews: int
    first_day: str
    last_day: str
    effect_pp: float | None
    low_pp: float | None
    high_pp: float | None
    status: str
    reason: str
    donor_supported_days: int
    maximum_vif: float | None = None


def build_phase_cohorts(series, settings):
    """Intersect eligible dates *before* fitting any phase contrast."""
    result = {}
    for scope in ("main", "nap"):
        factors = (
            ("total_sleep", "light_sleep", "deep_sleep", "rem_sleep")
            if scope == "main"
            else ("nap_duration", "nap_light_sleep", "nap_deep_sleep", "nap_rem_sleep")
        )
        for key, total_series in series.items():
            if total_series.factor != factors[0]:
                continue
            suffix = key.split("/", 1)[1]
            stages = [{p.day: p.x for p in series[f"{f}/{suffix}"].points} for f in factors[1:]]
            cohort = PhaseCohort(scope, total_series.metric, total_series.timing)
            omitted = Counter(total_series.omitted)
            units = 60 if scope == "main" else 1  # Raw main series uses hours; naps minutes.
            for point in total_series.points:
                if any(point.day not in stage for stage in stages):
                    omitted["one or more sleep phases unavailable"] += 1
                    continue
                total = point.x * units
                light, deep, rem = (stage[point.day] * units for stage in stages)
                staged = light + deep + rem
                if total <= 0 or staged <= 0 or abs(staged - total) > CLOSURE_TOLERANCE_MINUTES:
                    omitted["phase durations do not match total sleep"] += 1
                    continue
                # Accommodate only small summary-rounding discrepancies. Do not
                # infer a missing stage or treat awake/restless time as a phase.
                scale = total / staged
                cohort.points.append(
                    PhasePoint(
                        point.day,
                        total,
                        light * scale,
                        deep * scale,
                        rem * scale,
                        point.y,
                        point.reviews,
                    )
                )
            if settings.adjust_month:
                months = Counter((p.day.year, p.day.month) for p in cohort.points)
                kept = [
                    p
                    for p in cohort.points
                    if months[p.day.year, p.day.month] >= settings.minimum_month_days
                ]
                omitted["too few common days in calendar month"] += len(cohort.points) - len(kept)
                cohort.points = kept
            cohort.omitted = dict(omitted)
            result[cohort.key] = cohort
    return result


def _moments(rows):
    groups = {}
    for day, values in rows:
        current = groups.setdefault((day.year, day.month), [0.0] * 15)
        current[0] += 1
        for j, value in enumerate(values):
            current[1 + j] += value
        for index, (j, k) in enumerate(_PAIRS, 5):
            current[index] += values[j] * values[k]
    return groups


def _inverse(matrix):
    """Cholesky inversion of a small positive-definite covariance matrix."""
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for j in range(n):
        for k in range(j + 1):
            value = matrix[j][k] - sum(lower[j][i] * lower[k][i] for i in range(k))
            if j == k:
                if value <= 1e-10 * max(1, matrix[j][j]):
                    return None
                lower[j][k] = math.sqrt(value)
            else:
                lower[j][k] = value / lower[k][k]
    inverse = [[0.0] * n for _ in range(n)]
    for column in range(n):
        forward = [0.0] * n
        for j in range(n):
            forward[j] = (
                int(j == column) - sum(lower[j][k] * forward[k] for k in range(j))
            ) / lower[j][j]
        for j in reversed(range(n)):
            inverse[j][column] = (
                forward[j] - sum(lower[k][j] * inverse[k][column] for k in range(j + 1, n))
            ) / lower[j][j]
    return inverse


def _fit(groups, scales, adjust_month):
    if not groups:
        return None
    if not adjust_month:
        groups = [tuple(math.fsum(row[j] for row in groups) for j in range(15))]
    covariance = [[0.0] * 4 for _ in range(4)]
    n = sum(row[0] for row in groups)
    if n - len(groups) < 10:
        return None
    for row in groups:
        for index, (j, k) in enumerate(_PAIRS, 5):
            value = row[index] - row[1 + j] * row[1 + k] / row[0]
            covariance[j][k] += value
            if j != k:
                covariance[k][j] += value
    if covariance[3][3] <= 1e-10 * n:
        return None
    # Constant total sleep is already held fixed, including when constant
    # within each month. Its redundant column must not invalidate the model.
    active = ([0] if covariance[0][0] > 1e-8 * n else []) + [1, 2]
    matrix = [[covariance[j][k] for k in active] for j in active]
    inverse = _inverse(matrix)
    if inverse is None:
        return None
    vifs = [inverse[j][j] * matrix[j][j] for j in range(len(active))]
    if max(vifs) > MAX_VIF:
        return None
    for column in (1, 2):
        j = active.index(column)
        independent_sd = scales[column] / math.sqrt(max(1e-20, inverse[j][j] * n))
        if independent_sd < MIN_INDEPENDENT_MINUTES:
            return None
    beta = {}
    for j, column in enumerate(active):
        beta[column] = (
            sum(inverse[j][k] * covariance[other][3] for k, other in enumerate(active))
            / scales[column]
        )
    effects = (
        beta[1] * SHIFT_MINUTES,
        beta[2] * SHIFT_MINUTES,
        (beta[1] - beta[2]) * SHIFT_MINUTES,
    )
    if not all(math.isfinite(value) for value in effects):
        return None
    return effects, max(vifs), n - len(groups) - len(active)


def compare_phases(cohort, settings, *, check_cancelled=lambda: None):
    """One shared OLS fit and paired calendar-block bootstrap for all contrasts."""
    points = cohort.points
    n = len(points)
    span = (points[-1].day - points[0].day).days + 1 if n else 0
    block_counts = (
        Counter((p.day - points[0].day).days // settings.block_days for p in points) if n else {}
    )
    blocks = len(block_counts)
    base = dict(
        days=n,
        span_days=span,
        blocks=blocks,
        reviews=sum(p.reviews for p in points),
        first_day=points[0].day.isoformat() if n else "",
        last_day=points[-1].day.isoformat() if n else "",
    )
    effective_blocks = n**2 / sum(count**2 for count in block_counts.values()) if n else 0
    supported = [
        sum(getattr(p, donor.lower()) >= SHIFT_MINUTES for p in points)
        for donor, _receiver in CONTRASTS.values()
    ]

    def results(reason, fit=None, estimates=None):
        out = {}
        for index, contrast in enumerate(CONTRASTS):
            why = reason
            if not why and supported[index] < MIN_DONOR_DAYS:
                why = f"Needs ten days with at least {SHIFT_MINUTES} minutes in the replaced phase."
            values = sorted(estimates[index]) if estimates and not why else None
            low, high = (
                bootstrap_range(
                    values,
                    fit[0][index],
                    max(1, int(effective_blocks + 1e-12) - 1),
                    variance_scale=(n - 1) / fit[2] * effective_blocks / (effective_blocks - 1),
                )
                if values
                else (None, None)
            )
            out[contrast] = PhaseAssociation(
                **base,
                effect_pp=fit[0][index] if fit else None,
                low_pp=low,
                high_pp=high,
                status="insufficient data" if why else "exploratory estimate",
                reason=why,
                donor_supported_days=supported[index],
                maximum_vif=fit[1] if fit else None,
            )
        return out

    if n < settings.minimum_days:
        return results(f"Needs {settings.minimum_days} common eligible days; has {n}.")
    if span < settings.minimum_span_days or blocks < settings.minimum_blocks:
        return results(
            f"Needs {settings.minimum_span_days} calendar days and "
            f"{settings.minimum_blocks} occupied {settings.block_days}-day blocks."
        )
    if effective_blocks + 1e-12 < settings.minimum_blocks:
        return results(
            f"Needs {settings.minimum_blocks} effectively represented "
            f"{settings.block_days}-day blocks; observations are too concentrated."
        )
    # Standardize predictors once for stable accumulated moments. Every draw
    # refits its own intercept/month means and total/phase coefficients.
    predictors = [(p.total, p.deep, p.rem) for p in points]
    means = [math.fsum(row[j] for row in predictors) / n for j in range(3)]
    scales = [
        math.sqrt(math.fsum((row[j] - means[j]) ** 2 for row in predictors) / n) or 1
        for j in range(3)
    ]
    rows = [
        (p.day, tuple((x[j] - means[j]) / scales[j] for j in range(3)) + (p.score,))
        for p, x in zip(points, predictors, strict=True)
    ]
    fitted = _fit(list(_moments(rows).values()), scales, settings.adjust_month)
    if fitted is None:
        return results(
            "Not enough independent variation in phases or memory scores after accounting "
            "for total sleep and the other phase; the model is unstable."
        )
    calendar = [[] for _ in range(span)]
    for row in rows:
        calendar[(row[0] - points[0].day).days].append(row)
    length = min(settings.block_days, span)
    draws, tail = divmod(span, length)

    def windows(width):
        out = []
        for start, window in enumerate(calendar_windows(calendar, width)):
            if start % 500 == 0:
                check_cancelled()
            out.append(tuple((key, tuple(value)) for key, value in _moments(window).items()))
        return out

    full, partial = windows(length), windows(tail) if tail else []
    seed = hashlib.sha256(
        json.dumps(
            [(p.day.isoformat(), p.total, p.deep, p.rem, p.score) for p in points], allow_nan=False
        ).encode()
    ).digest()
    rng = random.Random(seed)
    estimates = [[], [], []]
    for iteration in range(settings.bootstrap_replicates):
        if iteration % 20 == 0:
            check_cancelled()
        sampled = defaultdict(lambda: [0.0] * 15)
        for block in draw_blocks(full, partial, draws, rng):
            for key, value in block:
                current = sampled[key]
                for j in range(15):
                    current[j] += value[j]
        fit = _fit(list(sampled.values()), scales, settings.adjust_month)
        if fit:
            for index, value in enumerate(fit[0]):
                estimates[index].append(value)
    if len(estimates[0]) < math.ceil(settings.bootstrap_replicates * 0.95):
        return results("Too many resampled histories had an unstable phase model.", fitted)
    return results("", fitted, estimates)
