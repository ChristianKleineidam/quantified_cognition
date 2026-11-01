"""Small numerical helpers, with no third-party runtime dependencies."""

from functools import lru_cache
from math import fsum, sqrt

# Student t 0.975 quantiles for integer df 1..30 (NIST table; extra precision
# avoids accumulated rounding). Above 30 the Cornish-Fisher expansion is stable.
_T975 = (
    12.706205,
    4.302653,
    3.182446,
    2.776445,
    2.570582,
    2.446912,
    2.364624,
    2.306004,
    2.262157,
    2.228139,
    2.200985,
    2.178813,
    2.160369,
    2.144787,
    2.131450,
    2.119905,
    2.109816,
    2.100922,
    2.093024,
    2.085963,
    2.079614,
    2.073873,
    2.068658,
    2.063899,
    2.059539,
    2.055529,
    2.051831,
    2.048407,
    2.045230,
    2.042272,
)


@lru_cache(maxsize=256)
def t_critical_95(df: int) -> float:
    if df < 1:
        raise ValueError("Positive degrees of freedom required")
    if df <= len(_T975):
        return _T975[df - 1]
    z = 1.959963984540054
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * df**4)
    )


def wilson_range(proportion, effective_n, critical):
    """Modified Wilson score range for a design-adjusted effective sample size."""
    a = critical**2 / effective_n
    center = (proportion + a / 2) / (1 + a)
    half = (
        critical
        * sqrt(proportion * (1 - proportion) / effective_n + a / (4 * effective_n))
        / (1 + a)
    )
    return max(0.0, center - half), min(1.0, center + half)


def bootstrap_range(values, center, df, *, variance_scale=1.0):
    """Percentile envelope with a finite-block bootstrap-SE t floor."""

    def quantile(q):
        position = (len(values) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    mean = fsum(values) / len(values)
    se = sqrt(variance_scale * fsum((x - mean) ** 2 for x in values) / (len(values) - 1))
    half = t_critical_95(df) * se
    return min(quantile(0.025), center - half), max(quantile(0.975), center + half)


def calendar_windows(calendar, width):
    """Include every eligible start for this width, retaining empty day slots."""
    for start in range(len(calendar) - width + 1):
        yield (row for slot in calendar[start : start + width] for row in slot)


def draw_blocks(full, partial, draws, rng):
    for _ in range(draws):
        yield full[rng.randrange(len(full))]
    if partial:
        yield partial[rng.randrange(len(partial))]
