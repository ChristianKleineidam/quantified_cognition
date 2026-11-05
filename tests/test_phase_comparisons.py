"""Identifiable substitutions, shared cohorts and genuinely confounded synthetic data."""

import csv
import json
import random
from dataclasses import replace
from datetime import date, timedelta

import pytest
from test_insights import AS_OF, FAST, inputs

from addon import insights, private_storage, storage
from addon.backend import UpdateCancelled
from addon.insights import build_series
from addon.phase_comparisons import (
    PhaseCohort,
    PhasePoint,
    build_phase_cohorts,
    compare_phases,
)


def cohort(n=240, *, noise=0, constant_total=False, gap=1, deep_effect=0.04, rem_effect=-0.02):
    rng = random.Random(83)
    points = []
    for i in range(n):
        day = date(2023, 1, 1) + timedelta(days=i * gap)
        total = 450 if constant_total else 450 + rng.gauss(0, 35)
        deep = 80 + 0.25 * (total - 450) + rng.gauss(0, 14)
        rem = 90 + 0.2 * (total - 450) + rng.gauss(0, 16)
        score = (
            0.07 * (total - 450)
            + deep_effect * deep
            + rem_effect * rem
            + day.month * 3
            + rng.gauss(0, noise)
        )
        points.append(PhasePoint(day, total, total - deep - rem, deep, rem, score, 100))
    return PhaseCohort("main", "formation", "before", points)


@pytest.mark.parametrize("constant_total", [False, True])
def test_exact_substitutions_hold_total_and_remaining_phase_fixed(constant_total):
    data = cohort(constant_total=constant_total)
    result = compare_phases(data, FAST)
    for key, expected in (("deep_for_light", 0.4), ("rem_for_light", -0.2), ("deep_for_rem", 0.6)):
        value = result[key]
        assert value.status == "exploratory estimate"
        assert value.effect_pp == pytest.approx(expected, abs=1e-8)
        assert value.low_pp == pytest.approx(expected, abs=1e-8)
        assert value.high_pp == pytest.approx(expected, abs=1e-8)
        assert value.days == 240 and value.reviews == 24000


def test_total_sleep_confounding_and_month_shifts_do_not_become_phase_effects():
    data = cohort(deep_effect=0, rem_effect=0)
    result = compare_phases(data, FAST)
    assert all(value.status == "exploratory estimate" for value in result.values())
    assert all(abs(value.effect_pp) < 1e-8 for value in result.values())
    unadjusted = compare_phases(data, replace(FAST, adjust_month=False))
    assert abs(unadjusted["deep_for_light"].effect_pp) > 1e-3


def test_uncertainty_is_deterministic_and_calendar_gaps_preserve_span():
    data = cohort(noise=2, gap=3)
    result = compare_phases(data, FAST)
    assert result == compare_phases(data, FAST)
    assert result["deep_for_light"].low_pp < result["deep_for_light"].high_pp
    assert result["deep_for_light"].span_days == 718
    assert result["deep_for_light"].days == 240
    assert result["deep_for_light"].low_pp < 0.4 < result["deep_for_light"].high_pp


@pytest.mark.parametrize("n", [0, 1, 59])
def test_short_cohorts_never_receive_ready_labels(n):
    assert all(
        value.status == "insufficient data" for value in compare_phases(cohort(n), FAST).values()
    )


@pytest.mark.parametrize("jitter", [0, 0.01])
def test_proportional_or_nearly_dependent_phases_are_insufficient(jitter):
    data = cohort()
    rng = random.Random(1)
    data.points = [
        replace(
            p, deep=p.total * 0.2 + rng.gauss(0, jitter), rem=p.total * 0.3, light=p.total * 0.5
        )
        for p in data.points
    ]
    result = compare_phases(data, FAST)
    assert all(
        value.status == "insufficient data"
        and value.low_pp is None
        and "independent variation" in value.reason
        for value in result.values()
    )


def test_ten_minute_reallocation_needs_observed_donor_support():
    data = cohort()
    data.points = [
        replace(p, rem=5 + (p.rem - 90) / 10, light=p.total - p.deep - (5 + (p.rem - 90) / 10))
        for p in data.points
    ]
    result = compare_phases(data, FAST)
    assert result["deep_for_light"].status == "exploratory estimate"
    assert result["deep_for_rem"].status == "insufficient data"
    assert "replaced phase" in result["deep_for_rem"].reason
    assert len({value.days for value in result.values()}) == 1


def test_bootstrap_cancellation_interrupts_model_work():
    def cancel():
        raise UpdateCancelled("Closed")

    with pytest.raises(UpdateCancelled):
        compare_phases(cohort(), FAST, check_cancelled=cancel)


def test_constant_memory_scores_are_insufficient_instead_of_zero_width_certainty():
    data = cohort()
    data.points = [replace(p, score=5) for p in data.points]
    assert all(
        value.status == "insufficient data" and value.low_pp is None
        for value in compare_phases(data, FAST).values()
    )


def matched_inputs(days=180):
    report, health = inputs(days)
    for session in health.sessions:
        session.values.update(
            total_sleep=6 * 3600, light_sleep=3 * 3600, deep_sleep=3600, rem_sleep=2 * 3600
        )
    return report, health


def test_phase_specific_missingness_uses_intersection_and_no_future_recall():
    report, health = matched_inputs()
    health.sessions[4].values.pop("deep_sleep")
    health.sessions[9].values.pop("rem_sleep")
    raw = build_series(report, health, "oura", as_of=AS_OF, settings=FAST)
    groups = build_phase_cohorts(raw, FAST)
    common = groups["main/recall/before"]
    assert len(common.points) == 178
    assert common.omitted["one or more sleep phases unavailable"] == 2
    assert not any(c.metric == "recall" and c.timing == "after" for c in groups.values())
    dates = {p.day for p in common.points}
    assert dates == set.intersection(
        *(
            {p.day for p in raw[f"{phase}/recall/before"].points}
            for phase in ("total_sleep", "light_sleep", "deep_sleep", "rem_sleep")
        )
    )


def test_rounding_can_be_closed_but_large_mismatch_and_missing_stages_are_excluded():
    report, health = matched_inputs()
    health.sessions[4].values["deep_sleep"] += 120
    health.sessions[5].values["deep_sleep"] += 300
    health.sessions[6].values["deep_sleep"] = 0
    health.sessions[6].values["light_sleep"] += 3600
    groups = build_phase_cohorts(
        build_series(report, health, "oura", as_of=AS_OF, settings=FAST), FAST
    )
    common = groups["main/recall/before"]
    assert len(common.points) == 179
    assert common.omitted["phase durations do not match total sleep"] == 1
    assert any(p.deep == 0 for p in common.points)
    assert all(p.light + p.deep + p.rem == pytest.approx(p.total) for p in common.points)


def test_month_minimum_is_rechecked_after_common_day_intersection():
    report, health = matched_inputs(8)
    for i in (0, 1, 2):
        health.sessions[i].values.pop("deep_sleep")
    for i in (3, 4, 5):
        health.sessions[i].values.pop("rem_sleep")
    groups = build_phase_cohorts(
        build_series(report, health, "oura", as_of=AS_OF, settings=FAST), FAST
    )
    assert not groups["main/recall/before"].points
    assert groups["main/recall/before"].omitted["too few common days in calendar month"] == 2


def test_cached_phase_estimates_and_export_do_not_replay_fsrs(tmp_path, monkeypatch):
    report, health = matched_inputs(240)
    data = cohort(deep_effect=0.4)
    for day, p in zip(report.days, data.points, strict=True):
        # Keep source dates from the report while supplying identifiable stages.
        session = next(s for s in health.sessions if s.day == day.day.isoformat())
        session.values.update(
            total_sleep=p.total * 60,
            light_sleep=p.light * 60,
            deep_sleep=p.deep * 60,
            rem_sleep=p.rem * 60,
        )
    report = replace(
        report,
        days=[
            replace(day, recall=replace(day.recall, score_pp=p.score))
            for day, p in zip(report.days, data.points, strict=True)
        ],
    )
    source = tmp_path / "collection.anki2"
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda source: health)
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    first = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert first.phase_associations["main/deep_for_light/recall/before"].effect_pp > 1
    monkeypatch.setattr(
        insights, "compare_phases", lambda *a, **k: pytest.fail("Repeated phase bootstrap")
    )
    second = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert second.reused and second.phase_associations == first.phase_associations
    target = tmp_path / "insights.csv"
    assert insights.export_insights(second, target) == 130
    with target.open(encoding="utf-8-sig", newline="") as stream:
        rows = [r for r in csv.DictReader(stream) if r["analysis_kind"] == "phase_substitution"]
    assert len(rows) == 42
    assert all(r["shift_minutes"] == "10" and not r["correlation"] for r in rows)
    assert {r["phase_donor"] for r in rows} == {"light", "REM"}
    assert "PhasePoint" not in json.dumps(
        private_storage.read_json(storage.results_directory(source) / "personal-insights.json")
    )
    assert first.coverage["study_days"] == 240


def test_malformed_phase_cache_recovers(tmp_path, monkeypatch):
    report, health = matched_inputs()
    source = tmp_path / "collection.anki2"
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda source: health)
    insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    path = storage.results_directory(source) / "personal-insights.json"
    value = private_storage.read_json(path)
    next(iter(value["entries"].values()))["phase_associations"] = None
    path.write_text(json.dumps(value))
    assert not insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF).reused
