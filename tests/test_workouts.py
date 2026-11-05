"""Synthetic workout history, uncertainty and aggregate-only exports."""

import csv
import io
from dataclasses import replace
from datetime import date, timedelta

import pytest
from test_insights import AS_OF, FAST, inputs, series_for

from addon import health, insights, storage
from addon.export import CSV_FIELDS, export_csv
from addon.oura import parse_oura_records
from addon.private_storage import private_database
from addon.research import SharingConfig, sharing_state
from addon.workouts import (
    WORKOUT_DAILY_FIELDS,
    WORKOUT_STUDY_FIELDS,
    parse_workouts,
    study_workouts,
)

DAY = date(2026, 1, 10)


def workout(**changes):
    return (
        dict(
            id="synthetic-workout",
            day=DAY.isoformat(),
            start_datetime="2026-01-10T08:00:00+00:00",
            end_datetime="2026-01-10T08:45:00+00:00",
            intensity="moderate",
            source="autodetected",
            label="PRIVATE LABEL",
            activity="PRIVATE ACTIVITY",
            calories=300,
            distance=5000,
        )
        | changes
    )


def saved(source, rows, **kwargs):
    return health.save_oura_import(
        source,
        parse_oura_records(
            [],
            [
                {"day": (DAY + timedelta(days=offset)).isoformat(), "steps": 8000}
                for offset in (-1, 0, 1)
            ],
            rows,
        ),
        authoritative_window=("2025-01-01", "2027-01-01"),
        **kwargs,
    )


def test_selected_workouts_are_encrypted_and_reimports_reconcile_without_replay(
    tmp_path, monkeypatch
):
    source = tmp_path / "collection.anki2"
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    first = saved(source, [workout()])
    repeated = saved(source, [workout()])
    assert first.snapshot == repeated.snapshot and repeated.changed_records == 0
    path = health.health_database_path(source)
    assert path.read_bytes().startswith(b"QCLOCAL")
    with private_database(path) as db:
        raw = db.execute("select payload from oura_workouts").fetchone()[0]
    assert "PRIVATE" not in raw and "synthetic-workout" not in raw
    values = first.snapshot.days[DAY].values
    assert values["oura_workout_duration_seconds"] == 2700
    assert values["oura_workout_moderate_seconds"] == 2700
    assert values["oura_workout_hard_seconds"] == 0
    corrected = saved(source, [workout(end_datetime="2026-01-10T09:00:00+00:00")])
    assert corrected.changed_records == 1
    assert corrected.snapshot.revision != first.snapshot.revision
    deleted = saved(source, [])
    assert deleted.changed_records == 1 and not deleted.snapshot.workouts
    assert deleted.snapshot.days[DAY].values["oura_workout_count"] == 0
    assert not source.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"start_datetime": "2026-01-10T08:00:00"},
        {"end_datetime": "2026-01-10T07:00:00Z"},
        {"day": "2026-02-10"},
        {"end_datetime": "2026-01-12T08:00:00Z"},
        {"intensity": True},
    ],
)
def test_invalid_workouts_fail_before_saving(changes):
    with pytest.raises(ValueError):
        parse_oura_records([], [], [workout(**changes)])


def test_duplicates_overlaps_and_unknown_intensity_are_not_double_counted(tmp_path):
    source = tmp_path / "collection.anki2"
    same = saved(source, [workout(), workout(id="duplicate", source="manual")]).snapshot
    assert same.days[DAY].values["oura_workout_count"] == 1
    overlap = saved(
        source, [workout(), workout(id="overlap", start_datetime="2026-01-10T08:30:00Z")]
    ).snapshot
    assert overlap.days[DAY].values["oura_workout_status"] == "overlapping workouts"
    assert overlap.days[DAY].values["oura_workout_duration_seconds"] is None
    unknown = saved(source, [workout(intensity="new-provider-category")]).snapshot
    assert unknown.days[DAY].values["oura_workout_duration_seconds"] == 2700
    assert unknown.days[DAY].values["oura_workout_hard_seconds"] is None
    with pytest.raises(ValueError, match="conflicting"):
        parse_workouts([workout(), workout(intensity="hard")])


def test_24_hour_windows_use_instants_not_dates_and_recall_never_looks_ahead(tmp_path):
    report, _ = inputs(1, start=DAY)
    source = tmp_path / "collection.anki2"
    snapshot = saved(
        source,
        [
            workout(
                start_datetime="2026-01-10T09:00:00+01:00", end_datetime="2026-01-10T09:45:00+01:00"
            ),
            workout(
                id="later",
                start_datetime="2026-01-10T18:00:00Z",
                end_datetime="2026-01-10T19:00:00Z",
                intensity="hard",
            ),
            workout(
                id="during",
                start_datetime="2026-01-10T10:15:00Z",
                end_datetime="2026-01-10T10:45:00Z",
            ),
        ],
    ).snapshot
    features = study_workouts(report, snapshot)[DAY]
    assert features["oura_workout_before_duration_seconds"] == 2700
    assert features["oura_workout_before_gap_seconds"] == 4500
    assert features["oura_workout_after_hard_seconds"] == 3600
    assert features["oura_between_review_workout_count"] == 1
    series = insights.build_series(
        report, snapshot, "oura", as_of=AS_OF, settings=replace(FAST, adjust_month=False)
    )
    assert "workout_duration/recall/after" not in series
    assert series["workout_duration/recall/before"].points[0].x == 45
    for metric in insights.METRICS[1:]:
        assert series[f"workout_duration/{metric}/before"].points[0].x == 45
        assert series[f"workout_duration/{metric}/after"].points[0].x == 60
    google = insights.build_series(report, snapshot, "google_health", as_of=AS_OF, settings=FAST)
    assert not any(key.startswith("workout_") for key in google)


def test_incomplete_history_and_workouts_crossing_review_boundary_stay_unknown(tmp_path):
    report, _ = inputs(1, start=DAY)
    source = tmp_path / "collection.anki2"
    unavailable = health.save_oura_import(
        source, parse_oura_records([], [{"day": DAY.isoformat(), "steps": 1}])
    ).snapshot
    assert study_workouts(report, unavailable)[DAY]["oura_workout_before_recorded"] is None
    overlapping = saved(
        source,
        [workout(start_datetime="2026-01-10T09:30:00Z", end_datetime="2026-01-10T10:30:00Z")],
    ).snapshot
    assert (
        study_workouts(report, overlapping)[DAY]["oura_workout_before_status"]
        == "workout overlaps study boundary"
    )
    truncated = replace(overlapping, workout_windows=(("2026-01-10", "2026-01-11"),))
    assert (
        study_workouts(report, truncated)[DAY]["oura_workout_before_status"]
        == "workout history incomplete"
    )
    cleared = health.save_oura_import(source, parse_oura_records([], []), clear=True).snapshot
    assert not cleared.workouts and not cleared.workout_windows


def test_daily_and_study_csvs_append_fields_but_exclude_exact_times_and_labels(tmp_path):
    source = tmp_path / "collection.anki2"
    snapshot = saved(source, [workout()]).snapshot
    report, _ = inputs(1, start=DAY)
    stream = io.StringIO()
    export_csv(report, stream, health_snapshot=snapshot)
    text = stream.getvalue()
    assert "PRIVATE" not in text and "08:00:00" not in text and "synthetic-workout" not in text
    row = next(row for row in csv.DictReader(io.StringIO(text)) if row["date"] == DAY.isoformat())
    assert float(row["oura_workout_before_duration_seconds"]) == 2700
    assert float(row["oura_workout_duration_seconds"]) == 2700
    assert CSV_FIELDS[-len(WORKOUT_DAILY_FIELDS + WORKOUT_STUDY_FIELDS) :] == list(
        WORKOUT_DAILY_FIELDS + WORKOUT_STUDY_FIELDS
    )


def test_workout_indicator_has_group_threshold_and_block_uncertainty():
    xs = [int(i % 3 == 0) for i in range(180)]
    series = series_for(xs, [x + (i % 7) / 10 for i, x in enumerate(xs)])
    series.factor = "workout_recorded"
    estimate = insights.correlate(series, FAST)
    assert estimate.status == "exploratory estimate" and estimate.low < estimate.high
    assert estimate.workout_days == 60 and estimate.no_recorded_workout_days == 120
    rare = replace(
        series, points=[replace(point, x=int(i < 5)) for i, point in enumerate(series.points)]
    )
    assert "recorded-workout days" in insights.correlate(rare, FAST).reason


def test_v8_choice_does_not_cover_workouts_or_change_original_timing(tmp_path):
    source = tmp_path / "collection.anki2"
    old = SharingConfig("research-sharing-v8")
    before = sharing_state(source, old, event_type="sharing_choice", choice="opt_in")
    sharing_state(source, old, event_type="results_exposed", surface="report")
    current = SharingConfig.from_config(
        {"research_sharing": {"notice_version": "research-sharing-v8"}}
    )
    assert sharing_state(source, current).current_choice is None
    after = sharing_state(source, current, event_type="sharing_choice", choice="opt_in")
    assert (
        after.metadata()["sharing_first_choice_at_utc"]
        == before.metadata()["sharing_first_choice_at_utc"]
    )
    assert after.metadata()["sharing_opt_in_before_first_view"] is True
    assert after.metadata()["current_notice_consent_before_first_view"] is False


def test_query_coverage_does_not_fabricate_zero_workouts_before_device_history(tmp_path):
    source = tmp_path / "collection.anki2"
    snapshot = saved(source, []).snapshot
    report, _ = inputs(1, start=date(2020, 1, 10))
    snapshot = replace(snapshot, workout_windows=(("2000-01-01", "2027-01-01"),))
    features = study_workouts(report, snapshot)[date(2020, 1, 10)]
    assert features["oura_workout_before_recorded"] is None
    assert features["oura_workout_before_status"] == "workout history incomplete"
    report, _ = inputs(1, start=DAY)
    assert study_workouts(report, snapshot)[DAY]["oura_workout_before_recorded"] == 0
    missing_day = replace(snapshot, days={DAY: snapshot.days[DAY]})
    assert study_workouts(report, missing_day)[DAY]["oura_workout_before_recorded"] is None
