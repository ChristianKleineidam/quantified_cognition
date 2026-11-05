import csv
import json
import zipfile
from datetime import date

import pytest
from test_storage import report_with_followup

from addon import health, storage
from addon.backend import UpdateCancelled
from addon.export import CSV_FIELDS, export_csv_atomic
from addon.oura import parse_oura_files


def sleep(**changes):
    row = {
        "id": "night-one",
        "day": "2026-09-07",
        "type": "long_sleep",
        "bedtime_start": "2026-09-06T23:00:00+02:00",
        "bedtime_end": "2026-09-07T07:00:00+02:00",
        "total_sleep_duration": 25200,
        "light_sleep_duration": 12600,
        "deep_sleep_duration": 5400,
        "rem_sleep_duration": 7200,
        "awake_time": 3600,
        "sleep_algorithm_version": "v2",
    }
    return row | changes


def json_file(tmp_path, records, name="sleep.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"data": records, "next_token": None}), encoding="utf-8")
    return path


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_archive_imports_only_selected_fields_and_stores_no_account_data(tmp_path):
    path = tmp_path / "oura.zip"
    private = "personal-email@example.com"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sleep/sleep.json", json.dumps({"data": [sleep(email=private)]}))
        archive.writestr("daily_activity.csv", "day,steps\n2026-09-07,12345\n2026-09-08,0\n")
        archive.writestr("personal_info.json", json.dumps({"email": private, "height": 180}))
    prepared = parse_oura_files([path])
    assert len(prepared.sleep) == 1 and len(prepared.activity) == 2
    assert prepared.ignored_files == 1
    source = tmp_path / "collection.anki2"
    result = health.save_oura_import(source, prepared)
    values = result.snapshot.days[date(2026, 9, 7)].values
    assert values["oura_total_sleep_seconds"] == 25200
    assert values["oura_rem_sleep_seconds"] == 7200
    assert values["oura_steps"] == 12345
    assert result.snapshot.days[date(2026, 9, 8)].values["oura_steps"] == 0
    assert not source.exists()  # No live Anki collection is required or modified.
    assert private.encode() not in health.health_database_path(source).read_bytes()
    from addon.private_storage import private_database

    with private_database(health.health_database_path(source)) as connection:
        stored = json.loads(connection.execute("select payload from oura_sleep").fetchone()[0])
    assert stored["bedtime_start"] == "2026-09-06T23:00:00+02:00"
    assert "id" not in stored and "email" not in stored


def test_daily_csv_headers_missing_values_and_seconds(tmp_path):
    path = tmp_path / "oura.csv"
    path.write_text(
        "Date,Total Sleep Duration,Light Sleep Duration,Deep Sleep Duration,"
        "REM Sleep Duration,Steps\n"
        "2026-09-07,25200,12600,,7200,0\n",
        encoding="utf-8-sig",
    )
    prepared = parse_oura_files([path])
    assert prepared.sleep[0].kind == "daily_summary"
    result = health.save_oura_import(tmp_path / "collection.anki2", prepared)
    values = result.snapshot.days[date(2026, 9, 7)].values
    assert values["oura_total_sleep_seconds"] == 25200
    assert values["oura_deep_sleep_seconds"] is None
    assert values["oura_sleep_status"] == "missing durations"
    assert values["oura_steps"] == 0


def test_reimports_corrected_values_and_deleted_sleep_are_idempotent(tmp_path):
    source = tmp_path / "collection.anki2"
    path = json_file(tmp_path, [sleep()])
    first = health.save_oura_import(source, parse_oura_files([path]))
    repeated = health.save_oura_import(source, parse_oura_files([path]))
    assert repeated.changed_records == 0
    assert repeated.snapshot == first.snapshot
    path = json_file(tmp_path, [sleep(deep_sleep_duration=5000)])
    revised = health.save_oura_import(source, parse_oura_files([path]))
    assert revised.changed_records == 1 and revised.snapshot.revision != first.snapshot.revision
    assert revised.snapshot.days[date(2026, 9, 7)].values["oura_deep_sleep_seconds"] == 5000
    path = json_file(tmp_path, [sleep(type="deleted")])
    removed = health.save_oura_import(source, parse_oura_files([path]))
    assert removed.changed_records == 1 and not removed.snapshot.days


def test_naps_summed_without_double_counting_daily_summary_or_algorithm_duplicates(tmp_path):
    source = tmp_path / "collection.anki2"
    path = json_file(
        tmp_path,
        [
            sleep(id="old-algorithm", sleep_algorithm_version="v1", total_sleep_duration=24000),
            sleep(),
            sleep(
                id="nap",
                type="sleep",
                bedtime_start="2026-09-07T13:00:00+02:00",
                bedtime_end="2026-09-07T13:30:00+02:00",
                total_sleep_duration=1500,
                light_sleep_duration=1200,
                deep_sleep_duration=300,
                rem_sleep_duration=0,
                awake_time=300,
            ),
            sleep(
                id="rejected",
                type="rest",
                bedtime_start="2026-09-07T14:00:00+02:00",
                bedtime_end="2026-09-07T14:30:00+02:00",
            ),
        ],
    )
    result = health.save_oura_import(source, parse_oura_files([path]))
    values = result.snapshot.days[date(2026, 9, 7)].values
    assert values["oura_total_sleep_seconds"] == 26700
    assert values["oura_sleep_period_count"] == 2
    assert values["oura_sleep_algorithm_versions"] == "v2"
    daily = tmp_path / "daily.csv"
    daily.write_text("day,total_sleep_duration\n2026-09-07,26700\n", encoding="utf-8")
    result = health.save_oura_import(source, parse_oura_files([daily]))
    assert result.snapshot.days[date(2026, 9, 7)].values["oura_total_sleep_seconds"] == 26700
    # A later tombstone must also suppress the duplicate from the older algorithm.
    removed = json_file(tmp_path, [sleep(type="deleted")])
    result = health.save_oura_import(source, parse_oura_files([removed]))
    assert result.snapshot.days[date(2026, 9, 7)].values["oura_total_sleep_seconds"] == 1500


def test_overlapping_periods_are_flagged_instead_of_inflating_sleep(tmp_path):
    path = json_file(
        tmp_path, [sleep(), sleep(id="overlap", bedtime_start="2026-09-07T06:00:00+02:00")]
    )
    result = health.save_oura_import(tmp_path / "collection.anki2", parse_oura_files([path]))
    values = result.snapshot.days[date(2026, 9, 7)].values
    assert values["oura_total_sleep_seconds"] is None
    assert values["oura_sleep_period_count"] == 2
    assert values["oura_sleep_status"] == "overlapping periods"


def test_corrected_day_does_not_resurrect_an_old_daily_summary(tmp_path):
    source = tmp_path / "collection.anki2"
    daily = tmp_path / "daily.csv"
    daily.write_text("day,total_sleep_duration\n2026-09-07,25200\n", encoding="utf-8")
    health.save_oura_import(source, parse_oura_files([daily]))
    health.save_oura_import(source, parse_oura_files([json_file(tmp_path, [sleep()])]))
    moved = json_file(tmp_path, [sleep(day="2026-09-08")])
    result = health.save_oura_import(source, parse_oura_files([moved]))
    assert date(2026, 9, 7) not in result.snapshot.days
    assert result.snapshot.days[date(2026, 9, 8)].values["oura_total_sleep_seconds"] == 25200


@pytest.mark.parametrize("value", [-1, "nan", "inf", "7h", 0.5, True])
def test_invalid_measurements_do_not_silently_become_zero(tmp_path, value):
    with pytest.raises(ValueError, match="Record 1"):
        parse_oura_files([json_file(tmp_path, [sleep(total_sleep_duration=value)])])


def test_offset_aware_dst_period_keeps_provider_day(tmp_path):
    row = sleep(
        day="2026-10-25",
        bedtime_start="2026-10-25T02:30:00+02:00",
        bedtime_end="2026-10-25T02:15:00+01:00",
    )
    prepared = parse_oura_files([json_file(tmp_path, [row])])
    assert prepared.sleep[0].day == "2026-10-25"
    assert prepared.sleep[0].bedtime_end.endswith("+01:00")


def test_conflicting_files_and_daily_scores_are_rejected(tmp_path):
    one = json_file(tmp_path, [sleep()], "one.json")
    two = json_file(tmp_path, [sleep(total_sleep_duration=20000)], "two.json")
    with pytest.raises(ValueError, match="Conflicting sleep records"):
        parse_oura_files([one, two])
    scores = json_file(tmp_path, [{"day": "2026-09-07", "score": 90, "contributors": {}}])
    with pytest.raises(ValueError, match="No sleep durations"):
        parse_oura_files([scores])


def test_cancellation_rolls_back_all_imported_records(tmp_path):
    source = tmp_path / "collection.anki2"
    prepared = parse_oura_files([json_file(tmp_path, [sleep()])])
    calls = []

    def cancel():
        calls.append(True)
        if len(calls) == 3:
            raise UpdateCancelled()

    with pytest.raises(UpdateCancelled):
        health.save_oura_import(source, prepared, check_cancelled=cancel)
    assert not health.load_health(source).days
    # Cancelling never leaves partially imported measurements.
    result = health.save_oura_import(source, prepared)
    assert result.changed_records == 1


def test_combined_csv_keeps_health_only_days_and_preserves_existing_columns(tmp_path):
    source = tmp_path / "collection.anki2"
    path = json_file(tmp_path, [sleep(), {"day": "2026-09-08", "steps": 10000}])
    snapshot = health.save_oura_import(source, parse_oura_files([path])).snapshot
    destination = tmp_path / "combined.csv"
    assert export_csv_atomic(report_with_followup(), destination, health_data=snapshot.days) == 3
    rows = read_csv(destination)
    assert [row["date"] for row in rows] == ["2026-09-07", "2026-09-08", "2026-09-11"]
    assert rows[0]["oura_total_sleep_seconds"] == "25200"
    assert rows[1]["oura_steps"] == "10000"
    assert rows[1]["recall_count"] == "" and rows[1]["formation_status"] == ""
    assert rows[2]["oura_total_sleep_seconds"] == ""
    from addon.sleep_sessions import SESSION_CSV_FIELDS

    previous_end = len(CSV_FIELDS) - len(SESSION_CSV_FIELDS)
    assert CSV_FIELDS[previous_end - len(health.HEALTH_CSV_FIELDS) : previous_end] == list(
        health.HEALTH_CSV_FIELDS
    )
    assert CSV_FIELDS[previous_end:] == list(SESSION_CSV_FIELDS)
    assert export_csv_atomic(None, destination, health_data=snapshot.days) == 2
    assert read_csv(destination)[0]["parameters_hash"] == ""


def test_health_only_change_exports_without_fsrs_and_retries_after_failure(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    csv_path = tmp_path / "combined.csv"
    storage.save_daily_settings(source, storage.DailySettings(True, str(csv_path)))
    report = report_with_followup()
    storage.save_report(report, storage.database_path(source), scheduled_day=date(2026, 10, 23))
    storage.retry_fixed_csv(source)
    before = csv_path.read_bytes()
    real_export = storage.export_csv_atomic

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    def unexpected(*args, **kwargs):
        pytest.fail("Oura imports must not scan or recalculate Anki history")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    monkeypatch.setattr(storage, "input_fingerprint", unexpected)
    monkeypatch.setattr(storage, "build_report", unexpected)
    prepared = parse_oura_files([json_file(tmp_path, [sleep()])])
    result = health.save_oura_import(source, prepared)
    assert result.csv_error == "CSV locked"
    assert csv_path.read_bytes() == before
    assert storage.csv_export_pending(storage.database_path(source), csv_path)
    monkeypatch.setattr(storage, "export_csv_atomic", real_export)
    repeated = health.save_oura_import(source, prepared)
    assert repeated.changed_records == 0 and repeated.csv_exported
    assert read_csv(csv_path)[0]["oura_total_sleep_seconds"] == "25200"
    assert storage.load_report(storage.database_path(source)) == report
    assert not storage.csv_export_pending(storage.database_path(source), csv_path)
    assert not storage.daily_update_due(storage.database_path(source), date(2026, 10, 23), csv_path)
