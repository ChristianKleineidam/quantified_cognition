"""Seeded calibration experiments with known truth; all data are synthetic.

This is a developer tool, never a background task in Anki. It imports the actual
estimators and keeps data generation independent of their implementation.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from addon.insights import InsightSettings  # noqa: E402
from addon.metrics import Sample, summarize  # noqa: E402
from addon.phase_comparisons import PhaseCohort, PhasePoint, compare_phases  # noqa: E402

START = date(2020, 1, 1)


def wilson(successes, total, z=1.96):
    if not total:
        return [None, None]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - radius, center + radius]


def summary(estimates, intervals, truth, trials):
    usable = len(intervals)
    covered = sum(low <= truth <= high for low, high in intervals)
    return dict(
        trials=trials,
        usable=usable,
        covered=covered,
        coverage=covered / usable if usable else None,
        monte_carlo_95=wilson(covered, usable),
        bias=statistics.mean(estimates) - truth if estimates else None,
        mean_width=statistics.mean(high - low for low, high in intervals) if usable else None,
        false_direction=sum(low > 0 or high < 0 for low, high in intervals) / usable
        if usable and truth == 0
        else None,
    )


def formation_samples(
    seed, sizes, effect=0, *, concentration=4, probability=0.7, vary_predictions=False
):
    rng = random.Random(seed)
    samples = []
    for group, size in enumerate(sizes):
        predicted = (0.5, 0.7, 0.9)[group % 3] if vary_predictions else probability
        mean = predicted + effect
        # Beta-binomial model: outcomes on a review day share a latent retrieval
        # condition. E[q] is known, and conditional successes are independent.
        q = rng.betavariate(mean * concentration, (1 - mean) * concentration)
        samples.extend(
            Sample(predicted, int(rng.random() < q), START + timedelta(days=group))
            for _ in range(size)
        )
    return samples


def formation_experiment(sizes, *, trials=1000, seed=17000, effect=0, **scenario):
    estimates, intervals = [], []
    for i in range(trials):
        samples = formation_samples(seed + i, sizes, effect, **scenario)
        metric = summarize(samples, cohort_count=len(samples), formation=True)
        estimates.append(metric.score_pp)
        if metric.uncertainty_low_pp is not None:
            intervals.append((metric.uncertainty_low_pp, metric.uncertainty_high_pp))
    return summary(estimates, intervals, 100 * effect, trials)


def phase_history(seed, *, days=365, rho=0, missing=False, effect=True, nap=False, trend=False):
    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(4)]
    points = []
    beta = (0.08, -0.04) if effect else (0.0, 0.0)
    for i in range(days):
        values = [rho * x + math.sqrt(1 - rho * rho) * rng.gauss(0, 1) for x in values]
        total_noise, deep_noise, rem_noise, error = values
        day = START + timedelta(days=i)
        month = (day.year - START.year) * 12 + day.month - 1
        seasonal = 8 * math.sin(month * 0.7) if trend else 0
        total = (90 if nap else 420) + (10 if nap else 30) * total_noise
        deep = (
            (25 if nap else 80) + 0.2 * (total - (90 if nap else 420)) + 6 * deep_noise + seasonal
        )
        rem = (22 if nap else 100) + 0.1 * (total - (90 if nap else 420)) + 5 * rem_noise - seasonal
        light = total - deep - rem
        # Known fixed-total partial effects, plus a total-duration effect and
        # optional source-month intercepts. Error is independent of predictors.
        score = 0.04 * total + beta[0] * deep + beta[1] * rem + 2 * error
        if trend:
            score += 2 * seasonal
        if min(light, deep, rem) <= 0:
            continue
        # Missingness is driven by measured predictors/calendar, not the error.
        if missing and (i % 97 < 12 or rng.random() < 0.2 + 0.1 * (total_noise > 0)):
            continue
        points.append(PhasePoint(day, total, light, deep, rem, score, 80))
    return PhaseCohort("nap" if nap else "main", "formation", "before", points), (
        10 * beta[0],
        10 * beta[1],
        10 * (beta[0] - beta[1]),
    )


def phase_experiment(*, trials=80, seed=29000, replicates=199, **scenario):
    estimates = [[], [], []]
    intervals = [[], [], []]
    settings = InsightSettings(bootstrap_replicates=replicates)
    for i in range(trials):
        cohort, truth = phase_history(seed + i, **scenario)
        result = compare_phases(cohort, settings)
        for j, value in enumerate(result.values()):
            if value.effect_pp is not None:
                estimates[j].append(value.effect_pp)
            if value.low_pp is not None:
                intervals[j].append((value.low_pp, value.high_pp))
    return {
        contrast: summary(estimates[j], intervals[j], truth[j], trials)
        for j, contrast in enumerate(("deep_for_light", "rem_for_light", "deep_for_rem"))
    }


def run(formation_trials=1000, phase_trials=80, replicates=199, *, group="all", seed_offset=0):
    result = {
        "synthetic_only": True,
        "seed_version": 1,
        "seed_offset": seed_offset,
        "bootstrap_replicates": replicates,
        "formation": {},
        "phase": {},
    }
    if group in ("all", "formation"):
        for label, sizes in (
            ("three_days", [100] * 3),
            ("five_days", [60] * 5),
            ("twelve_days", [25] * 12),
            ("many_days", [10] * 40),
            ("dominant_day", [240] + [10] * 6),
            ("unequal_days", [150, 80, 40, 20, 10]),
        ):
            result["formation"][label] = formation_experiment(
                sizes, trials=formation_trials, seed=17000 + seed_offset
            )
            print(json.dumps({"formation": label, **result["formation"][label]}), flush=True)
        for label, sizes, kwargs in (
            ("high_recall", [25] * 12, {"probability": 0.9}),
            ("varying_predictions", [25] * 12, {"vary_predictions": True}),
            ("unequal_supported", [100, 80, 60, 40, 20], {}),
            ("positive_effect", [25] * 12, {"effect": 0.06}),
            ("negative_effect", [25] * 12, {"effect": -0.06}),
        ):
            result["formation"][label] = formation_experiment(
                sizes, trials=formation_trials, seed=17000 + seed_offset, **kwargs
            )
            print(json.dumps({"formation": label, **result["formation"][label]}), flush=True)
    if group in ("all", "phase"):
        for label, scenario in (
            ("iid_effect", {}),
            ("total_only_null", {"effect": False}),
            ("serial_effect", {"rho": 0.7}),
            ("serial_null", {"rho": 0.7, "effect": False}),
            ("gaps_and_months", {"missing": True, "trend": True, "rho": 0.5}),
            ("nap_effect", {"nap": True, "missing": True}),
        ):
            start = time.perf_counter()
            result["phase"][label] = phase_experiment(
                trials=phase_trials, replicates=replicates, seed=29000 + seed_offset, **scenario
            )
            print(
                json.dumps(
                    {
                        "phase": label,
                        "seconds": time.perf_counter() - start,
                        "results": result["phase"][label],
                    }
                ),
                flush=True,
            )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formation-trials", type=int, default=1000)
    parser.add_argument("--phase-trials", type=int, default=80)
    parser.add_argument("--replicates", type=int, default=199)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--group", choices=("all", "formation", "phase"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.formation_trials, args.phase_trials) < 1 or args.replicates < 199:
        parser.error("Positive trial counts and at least 199 bootstrap replicates required")
    result = run(
        args.formation_trials,
        args.phase_trials,
        args.replicates,
        group=args.group,
        seed_offset=args.seed_offset,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
