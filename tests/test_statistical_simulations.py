"""Seeded Monte Carlo regression gates, with sampling-error allowances.

Calibration assertions concern the declared synthetic data-generating models,
not a guarantee of real-world coverage or absence of confounding.
"""

import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from addon.insights import InsightSettings
from addon.metrics import Attempt, Sample, daily_results, summarize
from addon.phase_comparisons import compare_phases
from addon.statistical_math import calendar_windows, draw_blocks, t_critical_95
from tools.statistical_simulations import formation_experiment, phase_experiment, phase_history


@pytest.mark.parametrize(
    "sizes,kwargs",
    [
        ([100] * 3, {}),
        ([60] * 5, {}),
        ([25] * 12, {}),
        ([10] * 40, {}),
        ([25] * 12, {"probability": 0.9}),
        ([25] * 12, {"vary_predictions": True}),
        ([100, 80, 60, 40, 20], {}),
        ([25] * 12, {"effect": 0.06}),
        ([25] * 12, {"effect": -0.06}),
    ],
)
def test_formation_interval_calibration(sizes, kwargs):
    result = formation_experiment(sizes, trials=600, seed=83000, **kwargs)
    assert result["usable"] == 600
    # A 95% Monte Carlo upper bound below 0.94 demonstrates meaningful
    # undercoverage. Also prevent a vacuous full-support interval from passing.
    assert result["monte_carlo_95"][1] >= 0.94, result
    assert result["coverage"] >= 0.91, result
    assert abs(result["bias"]) < 3.0, result  # pp, including strong shared-day noise.
    assert result["mean_width"] < (80 if len(sizes) == 3 else 65), result


@pytest.mark.parametrize("sizes", [[240] + [10] * 6, [150, 80, 40, 20, 10]])
def test_many_raw_days_do_not_hide_dominant_outcome_day(sizes):
    result = formation_experiment(sizes, trials=200, seed=95000)
    assert result["usable"] == 0
    source = date(2020, 1, 1)
    metric = summarize(
        [
            Sample(0.8, 1, source + timedelta(days=i))
            for i, size in enumerate(sizes)
            for _ in range(size)
        ],
        cohort_count=sum(sizes),
        formation=True,
    )
    assert metric.score_pp == pytest.approx(20)
    assert metric.outcome_days == len(sizes)
    assert metric.status == "insufficient data"
    assert metric.uncertainty_low_pp is metric.uncertainty_high_pp is None


@pytest.mark.parametrize("success", (0, 1))
def test_boundary_formation_outcomes_have_nonzero_asymmetric_ranges(success):
    samples = [Sample(0.8, success, date(2020, 1, 1) + timedelta(days=i % 12)) for i in range(120)]
    metric = summarize(samples, cohort_count=120, formation=True)
    assert metric.uncertainty_low_pp < metric.uncertainty_high_pp
    assert -80 - 1e-10 <= metric.uncertainty_low_pp <= metric.score_pp
    assert metric.score_pp <= metric.uncertainty_high_pp <= 20 + 1e-10


def test_current_outcome_variance_is_not_mistaken_for_pending_outcomes():
    rng = random.Random(139)
    source = date(2020, 1, 1)
    base = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
    attempts = []
    for card in range(1000):
        attempts.append(Attempt(base + card, card, source, 3, None, True))
        success = rng.random() < 0.7
        # Outcome-dependent availability intentionally biases observed follow-ups.
        # Their interval is conditional on observed outcomes, not the full cohort.
        if rng.random() < (0.9 if success else 0.2):
            delay = 1 + card % 30
            attempts.append(
                Attempt(
                    base + delay * 86400000 + card,
                    card,
                    source + timedelta(days=delay),
                    3 if success else 1,
                    0.7,
                    True,
                )
            )
    metric = daily_results(attempts)[0].formation
    assert metric.score_pp > 15  # Known selection effect; not a learning improvement.
    assert metric.pending_count > 200 and metric.completeness == "provisional"
    assert metric.count + metric.pending_count + metric.excluded_count == metric.cohort_count


def test_overall_short_and_long_formation_recover_known_effects_with_missing_followups():
    covered = [0, 0, 0]
    trials = 240
    for trial in range(trials):
        rng = random.Random(115000 + trial)
        source = date(2020, 1, 1)
        base = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
        attempts = []
        card = 0
        counts = []
        for start, days, per_day, effect in ((1, 12, 10, 0.05), (35, 20, 5, -0.03)):
            evaluated = 0
            for group in range(days):
                q = rng.betavariate((0.7 + effect) * 8, (0.3 - effect) * 8)
                for _ in range(per_day):
                    card += 1
                    attempts.append(Attempt(base + card, card, source, 3, None, True))
                    if rng.random() < 0.15:  # Independent censoring, never a failure.
                        continue
                    evaluated += 1
                    delay = start + group
                    attempts.append(
                        Attempt(
                            base + delay * 86400000 + card,
                            card,
                            source + timedelta(days=delay),
                            3 if rng.random() < q else 1,
                            0.7,
                            True,
                        )
                    )
            counts.append(evaluated)
        result = daily_results(attempts)[0]
        expected = [(counts[0] * 5 - counts[1] * 3) / sum(counts), 5, -3]
        for index, metric in enumerate(
            (result.formation, result.formation_short_term, result.formation_long_term)
        ):
            assert metric.uncertainty_low_pp is not None
            covered[index] += (
                metric.uncertainty_low_pp <= expected[index] <= metric.uncertainty_high_pp
            )
        assert result.formation.count == sum(counts)
        assert result.formation.pending_count == card - sum(counts)
    assert min(covered) / trials >= 0.9, covered


@pytest.mark.parametrize(
    "scenario",
    [
        {},
        {"effect": False},
        {"rho": 0.7},
        {"rho": 0.7, "effect": False},
        {"missing": True, "trend": True, "rho": 0.5},
        {"nap": True, "missing": True},
    ],
)
def test_phase_interval_calibration_and_total_duration_control(scenario):
    results = phase_experiment(trials=180, seed=129000, replicates=199, **scenario)
    for result in results.values():
        assert result["usable"] == 180
        assert result["monte_carlo_95"][1] >= 0.94, result
        assert result["coverage"] >= 0.9, result
        assert abs(result["bias"]) < 0.16, result  # pp per 10 minutes.
        assert 0.2 < result["mean_width"] < 4, result


def test_unidentifiable_phase_data_never_receive_a_confident_range():
    from dataclasses import replace

    cohort, _ = phase_history(53)
    cohort.points = [
        replace(p, deep=p.total * 0.2, rem=p.total * 0.3, light=p.total * 0.5)
        for p in cohort.points
    ]
    for result in compare_phases(cohort, InsightSettings(bootstrap_replicates=199)).values():
        assert result.status == "insufficient data" and result.low_pp is None


def test_t_critical_values_against_published_reference_points():
    assert t_critical_95(1) == pytest.approx(12.706, abs=0.001)
    assert t_critical_95(2) == pytest.approx(4.303, abs=0.001)
    assert t_critical_95(30) == pytest.approx(2.042, abs=0.001)
    assert t_critical_95(100) == pytest.approx(1.984, abs=0.001)
    assert math.isclose(t_critical_95(1000000), 1.959964, abs_tol=0.00001)


def test_partial_blocks_can_select_the_final_calendar_day_without_closing_gaps():
    calendar = [["first"], [], [], ["last"]]
    full = [list(window) for window in calendar_windows(calendar, 3)]
    partial = [list(window) for window in calendar_windows(calendar, 1)]
    assert full == [["first"], ["last"]]
    assert partial == [["first"], [], [], ["last"]]

    class LastAvailable:
        def randrange(self, count):
            return count - 1

    # The partial pool has more eligible starts than the full pool. Sampling
    # using the full pool's length would lose the final observation here.
    assert list(draw_blocks(full, partial, 1, LastAvailable())) == [["last"], ["last"]]
