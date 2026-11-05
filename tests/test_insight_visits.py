"""Visit checkpoints track displayed history, not background calculations."""

import copy
import csv
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from test_insights import AS_OF, FAST, inputs

from addon import insight_visits as visits
from addon import insights, private_storage, storage


def metric(count=40, cohort=100, pending=60, *, ready=False, excluded=0):
    return dict(count=count, cohort=cohort, pending=pending, excluded=excluded, ready=ready)


def day():
    return dict(
        formation=metric(),
        formation_short_term=metric(20, 20, 0),
        formation_long_term=metric(20, 20, 0),
    )


def estimate(low=-1, high=1, *, ready=True, days=70, scope="main"):
    return dict(
        low=low if ready else None,
        high=high if ready else None,
        point=0 if ready else None,
        status="exploratory estimate" if ready else "insufficient data",
        days=days,
        scope=scope,
    )


def snapshot(provider="oura"):
    return dict(
        version=1,
        provider=provider,
        as_of="2026-07-01",
        analysis=dict(
            method=4,
            settings=asdict(FAST),
            memory={
                "parameters_hash": "a",
                "rollover_hour": 4,
                "timezone": "CEST",
                "anki_version": "26.9.3",
                "minimum_reviews": 30,
                "minimum_formation_outcome_days": 3,
            },
            window={"kind": "fixed", "start": None, "end": None},
        ),
        study={"2026-06-01": day(), "2026-06-03": day()},
        health={"2026-06-01": "a" * 64, "2026-06-03": "b" * 64},
        estimates={
            "phase/main/deep_for_light/formation/before": estimate(),
            "association/deep_sleep/formation/before": estimate(-0.5, 0.5),
        },
    )


def previous(value=None):
    return dict(
        copy.deepcopy(value or snapshot()),
        ticket=1,
        revision=1,
        viewed_at_utc="2026-07-01T10:00:00+00:00",
    )


def test_first_visit_reserves_ticket_but_does_not_claim_a_display(tmp_path):
    source = tmp_path / "collection.anki2"
    state = visits.begin_visit(source, "oura")
    assert state["previous"] is None
    from addon.private_storage import read_json

    assert read_json(visits._path(source))["providers"] == {}
    assert visits.begin_visit(source, "oura", state) == state
    assert visits.compare_visits(snapshot(), None)["first_visit"]


def test_refreshes_keep_previous_visit_frozen_and_next_open_uses_last_display(tmp_path):
    source = tmp_path / "collection.anki2"
    state = visits.begin_visit(source, "oura")
    assert visits.save_displayed_visit(source, snapshot(), state, 1)
    current = snapshot()
    current["study"]["2026-07-01"] = day()
    assert visits.save_displayed_visit(source, current, state, 2)
    assert visits.begin_visit(source, "oura", state)["previous"] is None
    assert visits.begin_visit(source, "oura")["previous"]["study"] == current["study"]


def test_additions_backfill_updates_and_removals_are_counted_separately():
    current = snapshot()
    current["study"]["2026-05-01"] = day()
    current["study"]["2026-08-01"] = day()
    current["study"].pop("2026-06-01")
    current["health"]["2026-05-01"] = "c" * 64
    current["health"]["2026-08-01"] = "d" * 64
    current["health"]["2026-06-03"] = "e" * 64
    current["health"].pop("2026-06-01")
    change = visits.compare_visits(current, previous())
    assert change["added_study_days"] == change["added_health_days"] == 2
    assert change["backfilled_study_days"] == change["backfilled_health_days"] == 1
    assert change["removed_study_days"] == change["removed_health_days"] == 1
    assert change["updated_health_days"] == 1


def test_matured_outcomes_only_use_existing_stable_study_cohorts():
    current = snapshot()
    values = current["study"]["2026-06-01"]
    values["formation"].update(count=100, pending=0, ready=True)
    values["formation_short_term"].update(count=40, cohort=40, ready=True)
    values["formation_long_term"].update(count=60, cohort=60, ready=True)
    current["study"]["2026-09-01"] = copy.deepcopy(values)
    change = visits.compare_visits(current, previous())
    assert change["resolved_pending"] == 60 and change["followup_days"] == 1
    assert change["short_followups"] == 20 and change["long_followups"] == 40
    assert change["no_pending_days"] == 1
    assert all(count == 1 for count in change["newly_ready"].values())
    assert "60 fewer pending" in visits.overview_changes(change)


@pytest.mark.parametrize("revised", ["cohort", "excluded", "pending"])
def test_revised_counts_do_not_masquerade_as_matured_followups(revised):
    current = snapshot()
    values = current["study"]["2026-06-01"]["formation"]
    values["count"], values["pending"] = 90, 10
    values[revised] += 1 if revised != "pending" else 60
    change = visits.compare_visits(current, previous())
    assert change["resolved_pending"] == 0
    assert change["revised_formation_days"] == 1


def test_age_eligibility_can_change_without_new_followups():
    current = snapshot()
    current["study"]["2026-06-01"]["formation"]["ready"] = True
    change = visits.compare_visits(current, previous())
    assert change["newly_ready"]["formation"] == 1 and change["resolved_pending"] == 0


def test_uncertainty_widths_are_compared_by_estimate_and_without_division_by_zero():
    old, current = snapshot(), snapshot()
    old["estimates"].update(
        {
            "phase/main/rem_for_light/formation/before": estimate(ready=False),
            "phase/nap/deep_for_rem/formation/after": estimate(0, 0, scope="nap"),
        }
    )
    current["estimates"]["phase/main/deep_for_light/formation/before"] = estimate(-0.5, 0.5)
    current["estimates"]["association/deep_sleep/formation/before"] = estimate(-0.8, 0.8)
    current["estimates"].update(
        {
            "phase/main/rem_for_light/formation/before": estimate(-2, 2),
            "phase/nap/deep_for_rem/formation/after": estimate(-1, 1, scope="nap"),
        }
    )
    change = visits.compare_visits(current, previous(old))
    narrower = change["estimates"]["phase/main/deep_for_light/formation/before"]
    assert narrower["trend"] == "narrower" and narrower["width_percent"] == -50
    assert change["estimates"]["association/deep_sleep/formation/before"]["trend"] == "wider"
    assert (
        change["estimates"]["phase/main/rem_for_light/formation/before"]["trend"] == "new estimate"
    )
    assert change["estimates"]["phase/nap/deep_for_rem/formation/after"]["width_percent"] is None
    assert "50% narrower" in visits.comparison_history(
        change, "phase/main/deep_for_light/formation/before"
    )
    assert "1 narrower, 0 wider" in visits.overview_changes(change)


def test_lost_estimates_and_unchanged_ranges_are_explicit():
    current = snapshot()
    current["estimates"]["phase/main/deep_for_light/formation/before"] = estimate(ready=False)
    change = visits.compare_visits(current, previous())
    assert (
        change["estimates"]["phase/main/deep_for_light/formation/before"]["trend"]
        == "no longer available"
    )
    assert change["estimates"]["association/deep_sleep/formation/before"]["trend"] == "unchanged"


@pytest.mark.parametrize(
    "field", ["settings", "window", "method", "parameters_hash", "rollover_hour"]
)
def test_different_methods_windows_fsrs_and_day_boundaries_suppress_uncertainty_claims(field):
    current = snapshot()
    if field == "settings":
        current["analysis"]["settings"]["adjust_month"] = False
    elif field in ("window", "method"):
        current["analysis"][field] = {"kind": "rolling", "days": 365} if field == "window" else 5
    else:
        current["analysis"]["memory"][field] = "changed"
    current["study"]["2026-08-01"] = day()
    change = visits.compare_visits(current, previous())
    assert change["not_comparable"] and not change["estimates"]
    assert change["added_study_days"] == 1
    assert "Uncertainty not compared" in visits.overview_changes(change)


def test_same_rolling_window_is_comparable_with_a_cohort_change_note():
    old, current = snapshot(), snapshot()
    old["analysis"]["window"] = current["analysis"]["window"] = {"kind": "rolling", "days": 365}
    current["as_of"] = "2026-10-01"
    change = visits.compare_visits(current, previous(old))
    assert not change["not_comparable"]
    assert "rolling window also drops older dates" in visits.overview_changes(change)


def test_old_workers_and_old_refreshes_cannot_overwrite_a_newer_display(tmp_path):
    source = tmp_path / "collection.anki2"
    older = visits.begin_visit(source, "oura")
    newer = visits.begin_visit(source, "oura")
    assert visits.save_displayed_visit(source, snapshot(), newer, 2)
    assert not visits.save_displayed_visit(source, snapshot(), older, 10)
    assert not visits.save_displayed_visit(source, snapshot(), newer, 1)
    assert visits.begin_visit(source, "oura")["previous"]["ticket"] == newer["ticket"]


def test_provider_deletion_preserves_other_provider_and_revokes_pending_workers(tmp_path):
    source = tmp_path / "collection.anki2"
    oura, google = visits.begin_visit(source, "oura"), visits.begin_visit(source, "google_health")
    visits.save_displayed_visit(source, snapshot(), oura, 1)
    visits.save_displayed_visit(source, snapshot("google_health"), google, 1)
    visits.delete_visit_history(source, "oura")
    assert not visits.save_displayed_visit(source, snapshot(), oura, 2)
    assert visits.begin_visit(source, "oura", oura)["previous"] is None
    assert visits.begin_visit(source, "google_health")["previous"] is not None
    visits.delete_visit_history(source)
    assert visits.begin_visit(source, "google_health", google)["previous"] is None


def test_corrupt_history_starts_a_clear_new_baseline_and_rejects_old_workers(tmp_path):
    source = tmp_path / "collection.anki2"
    older = visits.begin_visit(source, "oura")
    visits._path(source).write_text("{broken")
    new = visits.begin_visit(source, "oura")
    assert new["previous"] is None and "could not be read" in new["problem"]
    assert not visits.save_displayed_visit(source, snapshot(), older, 1)
    assert visits.save_displayed_visit(source, snapshot(), new, 1)


def test_failed_checkpoint_preserves_previous_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    state = visits.begin_visit(source, "oura")
    visits.save_displayed_visit(source, snapshot(), state, 1)
    before = visits._path(source).read_bytes()
    monkeypatch.setattr(insights, "_atomic_json", lambda *a: (_ for _ in ()).throw(OSError("Full")))
    future = visits.queue_displayed_visit(source, snapshot(), state, 2)
    with pytest.raises(OSError):
        future.result(5)
    assert visits._path(source).read_bytes() == before


def test_checkpoint_does_not_wait_for_collection_update_or_export_lock(tmp_path):
    source = tmp_path / "collection.anki2"
    state = visits.begin_visit(source, "oura")
    with storage.profile_job_lock(source):
        assert visits.queue_displayed_visit(source, snapshot(), state, 1).result(5)


def test_owned_checkpoint_finishes_on_immediate_process_exit(tmp_path):
    source = tmp_path / "collection.anki2"
    script = """
import json, sys
from addon.insight_visits import begin_visit, queue_displayed_visit
from addon import private_storage
private_storage._key = lambda *args, **kwargs: bytes.fromhex(sys.argv[2])
source = sys.argv[1]
state = begin_visit(source, 'oura')
queue_displayed_visit(source, json.load(sys.stdin), state, 1)
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            private_storage._key(visits._path(source), create=True).hex(),
        ],
        input=json.dumps(snapshot()),
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert visits.begin_visit(source, "oura")["previous"]["study"] == snapshot()["study"]


def test_snapshot_contains_counts_and_hashes_not_raw_health_or_review_times(tmp_path, monkeypatch):
    report, health = inputs(90)
    source = tmp_path / "collection.anki2"
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda source: health)
    result = insights.load_insights(
        source, "oura", settings=FAST, as_of=AS_OF, start=report.days[-20].day
    )
    snap = result.visit_snapshot
    assert len(snap["study"]) == 90 and len(snap["health"]) == 90
    text = json.dumps(snap)
    assert "study_windows" not in text and "bedtime" not in text
    assert str(health.sessions[0].start) not in text
    assert "oura_deep_sleep_seconds" not in text
    changed = copy.deepcopy(health)
    changed.days[report.days[0].day].values["oura_imported_at_utc"] = "now"
    assert visits._health_days(changed, "oura") == visits._health_days(health, "oura")
    changed.sessions[0].values["deep_sleep"] += 60
    assert visits._health_days(changed, "oura") != visits._health_days(health, "oura")
    assert not visits._path(source).exists()  # Calculation/export is not a visit.
    result = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    old = previous(result.visit_snapshot)
    old["estimates"]["association/deep_sleep/recall/before"].update(low=-1, high=1)
    result.visit_changes = visits.compare_visits(result.visit_snapshot, old)
    export_path = tmp_path / "insights.csv"
    assert insights.export_insights(result, export_path) == 130
    with export_path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(
            r
            for r in csv.DictReader(stream)
            if r["factor"] == "deep_sleep"
            and r["metric"] == "recall"
            and r["analysis_kind"] == "correlation"
        )
    assert row["visit_comparison"] == "comparable" and row["uncertainty_change"] == "narrower"
    assert float(row["previous_low"]) == -1
    assert not visits._path(source).exists()


def test_seasonal_timezone_abbreviation_is_not_a_change_of_timezone_rules():
    from types import SimpleNamespace

    value = SimpleNamespace(
        provider="oura",
        settings=FAST,
        associations={},
        phase_associations={},
        memory_metadata={"timezone": "CEST"},
    )
    health = SimpleNamespace(days={}, sessions=())
    first = visits.make_snapshot(value, None, health, method_version=4, as_of=AS_OF)
    value.memory_metadata["timezone"] = "CET"
    second = visits.make_snapshot(value, None, health, method_version=4, as_of=AS_OF)
    assert not visits._context_changes(first["analysis"], second["analysis"])
    second["analysis"]["memory"]["timezone"]["standard_offset"] += 3600
    assert visits._context_changes(first["analysis"], second["analysis"])


def test_malformed_prior_settings_do_not_prevent_personal_results():
    old = previous()
    old["analysis"]["settings"] = None
    assert visits.compare_visits(snapshot(), old)["first_visit"]
