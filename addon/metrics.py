"""Daily metrics, independent of Anki and Qt."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from math import isfinite, sqrt

from .statistical_math import t_critical_95, wilson_range


@dataclass(frozen=True)
class Settings:
    minimum_reviews: int = 30
    minimum_formation_outcome_days: int = 3

    @classmethod
    def from_config(cls, config: dict | None) -> Settings:
        config = config or {}
        values = {}
        for key, default, upper in (
            ("minimum_reviews", 30, 100_000),
            ("minimum_formation_outcome_days", 3, 365),
        ):
            value = config.get(key, default)
            values[key] = value if type(value) is int and 1 <= value <= upper else default
        return cls(**values)


DEFAULT_SETTINGS = Settings()
SHORT_TERM_MAX_DAYS = 30
SHORT_TERM_MAX_MILLISECONDS = SHORT_TERM_MAX_DAYS * 86_400 * 1000


def anki_day(timestamp: float, rollover_hour: int, timezone: tzinfo | None = None) -> date:
    """Use local wall-clock rollover, including daylight-saving transitions.

    With timezone=None, astimezone() resolves the OS's offset at this timestamp.
    Subtracting an hour count from the timestamp would misassign DST transition days.
    """
    if not 0 <= rollover_hour <= 23:
        raise ValueError("Invalid Anki day rollover hour")
    local = (
        datetime.fromtimestamp(timestamp, timezone).astimezone()
        if timezone is None
        else (datetime.fromtimestamp(timestamp, timezone))
    )
    return local.date() - timedelta(days=int(local.hour < rollover_hour))


@dataclass(frozen=True)
class Attempt:
    review_id: int
    card_id: int
    day: date
    rating: int
    prediction: float | None
    has_post_state: bool
    # Incremented at resets or unmodelled preview/cram exposures.
    episode: int = 0
    formation_blocked: bool = False

    @property
    def success(self) -> int:
        return int(self.rating > 1)


@dataclass(frozen=True)
class Sample:
    probability: float
    success: int
    outcome_day: date

    def __post_init__(self) -> None:
        if not isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise ValueError("A prediction must be a finite probability")
        if self.success not in (0, 1):
            raise ValueError("Recall outcomes must be binary")

    @property
    def residual(self) -> float:
        return self.success - self.probability


@dataclass(frozen=True)
class Metric:
    count: int
    cohort_count: int
    pending_count: int
    excluded_count: int
    outcome_days: int
    predicted_percent: float | None
    recalled_percent: float | None
    score_pp: float | None
    uncertainty_low_pp: float | None
    uncertainty_high_pp: float | None
    status: str

    @property
    def coverage_percent(self) -> float:
        return 100 * self.count / self.cohort_count if self.cohort_count else 0.0

    @property
    def completeness(self) -> str:
        """Follow-up availability, independent of the observed comparison/uncertainty.

        Use the overall formation cohort for this, including when displaying a
        delay band: pending cards do not yet have a known short/long delay.
        """
        if not self.cohort_count:
            return "no data"
        if self.pending_count:
            return "provisional"
        if not self.count:
            return "no evaluable follow-ups"
        return "no pending follow-ups"


def summarize(
    samples: list[Sample],
    *,
    cohort_count: int,
    pending_count: int = 0,
    excluded_count: int = 0,
    formation: bool = False,
    settings: Settings = DEFAULT_SETTINGS,
) -> Metric:
    n = len(samples)
    if n + pending_count + excluded_count != cohort_count:
        raise ValueError("Every cohort member must be evaluated, pending, or excluded")
    if not n:
        return Metric(
            0,
            cohort_count,
            pending_count,
            excluded_count,
            0,
            None,
            None,
            None,
            None,
            None,
            "insufficient data",
        )
    mean = sum(s.residual for s in samples) / n
    predicted = sum(s.probability for s in samples) / n
    observed = sum(s.success for s in samples) / n
    # A model-based variance floor prevents zero uncertainty for all-pass cohorts.
    variance = sum(s.probability * (1 - s.probability) for s in samples) / n**2
    if n > 1:
        variance = max(variance, sum((s.residual - mean) ** 2 for s in samples) / (n * (n - 1)))
    by_day: dict[date, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_day[sample.outcome_day].append(sample)
    groups = len(by_day)
    effective_days = n**2 / sum(len(group) ** 2 for group in by_day.values())
    if formation and groups > 1:
        # Cluster by the day on which recall was tested: outcomes on a shared day
        # may have a shared retrieval effect. Use the larger uncertainty estimate.
        clustered_variance = (
            groups
            / (groups - 1)
            * sum(sum(s.residual - mean for s in group) ** 2 for group in by_day.values())
            / n**2
        )
        variance = max(variance, clustered_variance)
        # Delete-one-outcome-day jackknife (CV3). High-leverage days need more
        # correction than the equal-size G/(G-1) factor can provide.
        jackknife_variance = (
            (groups - 1)
            / groups
            * sum(
                ((sum(s.residual for s in group) - len(group) * mean) / (n - len(group))) ** 2
                for group in by_day.values()
            )
        )
        variance = max(variance, jackknife_variance)
    supported = not formation or (
        groups > 1 and effective_days + 1e-12 >= max(2, settings.minimum_formation_outcome_days)
    )
    if supported:
        if formation:
            critical = t_critical_95(max(1, int(effective_days + 1e-12) - 1))
            # A symmetric Wald range can collapse toward the boundary at high
            # recall. Invert a design-adjusted Wilson score range instead.
            effective_n = (
                min(n, observed * (1 - observed) / variance)
                if 0 < observed < 1 and variance > 0
                else effective_days
            )
            low_probability, high_probability = wilson_range(observed, effective_n, critical)
            low, high = (
                min(mean, low_probability - predicted),
                max(mean, high_probability - predicted),
            )
        else:
            half_width = 1.96 * sqrt(variance)
            low, high = max(-1.0, mean - half_width), min(1.0, mean + half_width)
    else:
        low = high = None
    if n < settings.minimum_reviews or (formation and not supported):
        status = "insufficient data"
    elif low > 0:
        status = "better"
    elif high < 0:
        status = "worse"
    else:
        status = "on par"
    return Metric(
        n,
        cohort_count,
        pending_count,
        excluded_count,
        groups,
        100 * predicted,
        100 * observed,
        100 * mean,
        100 * low if low is not None else None,
        100 * high if high is not None else None,
        status,
    )


@dataclass(frozen=True)
class DailyResult:
    day: date
    recall: Metric
    formation: Metric
    # None denotes a legacy saved report whose split has not been calculated.
    formation_short_term: Metric | None = None
    formation_long_term: Metric | None = None


@dataclass
class _DayBucket:
    recall_samples: list[Sample] = field(default_factory=list)
    formation_samples: list[Sample] = field(default_factory=list)
    short_term_samples: list[Sample] = field(default_factory=list)
    long_term_samples: list[Sample] = field(default_factory=list)
    cohort_count: int = 0
    recall_excluded: int = 0
    formation_pending: int = 0
    formation_excluded: int = 0


def daily_results(
    attempts: list[Attempt], settings: Settings = DEFAULT_SETTINGS
) -> list[DailyResult]:
    """First attempt tests retrieval; last attempt starts the next-day formation pair."""
    by_card: dict[int, list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        by_card[attempt.card_id].append(attempt)
    buckets: dict[date, _DayBucket] = defaultdict(_DayBucket)
    for card_attempts in by_card.values():
        by_day: dict[date, list[Attempt]] = defaultdict(list)
        for attempt in sorted(card_attempts, key=lambda a: a.review_id):
            by_day[attempt.day].append(attempt)
        days = sorted(by_day)
        for index, day in enumerate(days):
            first, last = by_day[day][0], by_day[day][-1]
            bucket = buckets[day]
            bucket.cohort_count += 1
            if first.prediction is None:
                bucket.recall_excluded += 1
            else:
                bucket.recall_samples.append(Sample(first.prediction, first.success, day))
            if not last.has_post_state or last.formation_blocked:
                bucket.formation_excluded += 1
            elif index + 1 == len(days):
                bucket.formation_pending += 1
            else:
                following = by_day[days[index + 1]][0]
                if following.episode != last.episode or following.prediction is None:
                    bucket.formation_excluded += 1
                else:
                    sample = Sample(following.prediction, following.success, following.day)
                    bucket.formation_samples.append(sample)
                    # Revlog IDs are review timestamps in milliseconds. Use the
                    # actual source-to-test delay, not the scheduled interval or
                    # the age of the source day when this report is generated.
                    elapsed = following.review_id - last.review_id
                    group = (
                        bucket.short_term_samples
                        if elapsed <= SHORT_TERM_MAX_MILLISECONDS
                        else bucket.long_term_samples
                    )
                    group.append(sample)
    return [
        DailyResult(
            day,
            summarize(
                bucket.recall_samples,
                cohort_count=bucket.cohort_count,
                excluded_count=bucket.recall_excluded,
                settings=settings,
            ),
            summarize(
                bucket.formation_samples,
                cohort_count=bucket.cohort_count,
                pending_count=bucket.formation_pending,
                excluded_count=bucket.formation_excluded,
                formation=True,
                settings=settings,
            ),
            summarize(
                bucket.short_term_samples,
                cohort_count=len(bucket.short_term_samples),
                formation=True,
                settings=settings,
            ),
            summarize(
                bucket.long_term_samples,
                cohort_count=len(bucket.long_term_samples),
                formation=True,
                settings=settings,
            ),
        )
        for day, bucket in sorted(buckets.items())
    ]
