import csv
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from addon.backend import Report
from addon.export import METRIC_FIELDS
from addon.metrics import Attempt, DailyResult, Settings, daily_results
from addon.storage import (
    DailySettings,
    daily_update_due,
    database_path,
    load_daily_settings,
    load_report,
    save_daily_settings,
    save_report,
    update_results,
)


@pytest.fixture(autouse=True)
def fake_input_scan(monkeypatch):
    # Storage unit tests use path-only fake collections; real input scanning is
    # covered by native collection integration tests in test_backend.py.
    monkeypatch.setattr("addon.storage.input_fingerprint", lambda col, settings: "inputs-v2")


def report_with_followup(followup=True):
    monday, friday = date(2026, 9, 7), date(2026, 9, 11)
    attempts = [Attempt(1, 1, monday, 3, None, True)]
    if followup:
        attempts.append(Attempt(2, 1, friday, 1, 0.7, True))
    return Report(
        daily_results(attempts),
        "2026-10-07T12:00:00+00:00",
        "26.9.3",
        "abc",
        4,
        Settings(),
        1,
        0,
        0,
        "CEST",
    )


def test_database_roundtrip_numeric_columns_and_revised_formation(tmp_path):
    path = database_path(tmp_path / "profile" / "collection.anki2")
    first, revised = report_with_followup(False), report_with_followup()
    save_report(first, path)
    assert load_report(path) == first
    save_report(revised, path)
    assert load_report(path) == revised
    with closing(sqlite3.connect(path)) as connection:
        count, pending, score = connection.execute(
            "select formation_count, formation_pending_count, formation_score_pp "
            "from daily_results where date = '2026-09-07'"
        ).fetchone()
        assert count == 1 and pending == 0
        assert score == pytest.approx(-70)
        assert connection.execute("select count(*) from daily_results").fetchone()[0] == 2
    # Replacing, rather than appending, also removes results no longer in the analysis.
    save_report(first, path)
    assert load_report(path) == first


def test_preferences_are_profile_specific_and_disabled_by_default(tmp_path):
    first = tmp_path / "first" / "collection.anki2"
    second = tmp_path / "second" / "collection.anki2"
    assert load_daily_settings(first) == DailySettings()
    enabled = DailySettings(True, str(tmp_path / "exports" / "first.csv"))
    save_daily_settings(first, enabled)
    assert load_daily_settings(first) == enabled
    assert load_daily_settings(second) == DailySettings()
    assert database_path(first) != database_path(second)


@pytest.mark.parametrize("path", ["", "relative.csv", "/absolute/file.anki2"])
def test_invalid_enabled_export_path_is_rejected(tmp_path, path):
    with pytest.raises(ValueError):
        save_daily_settings(tmp_path / "collection.anki2", DailySettings(True, path))
    assert load_daily_settings(tmp_path / "collection.anki2") == DailySettings()


def test_successful_update_writes_full_csv_and_marks_day_only_after_success(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "profile" / "collection.anki2"))
    report = report_with_followup()
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report)
    daily = DailySettings(True, str(tmp_path / "exports" / "scores.csv"))
    today = date(2026, 10, 7)
    db = database_path(collection.path)
    assert daily_update_due(db, today, daily.csv_path)
    result = update_results(collection, daily=daily, scheduled_day=today)
    assert result.report == report
    assert result.recalculated and result.csv_exported and result.csv_error is None
    assert load_report(db) == report
    with open(daily.csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-09-07"
    assert float(rows[0]["formation_score_pp"]) == -70
    assert not daily_update_due(db, today, daily.csv_path)
    assert daily_update_due(db, date(2026, 10, 8), daily.csv_path)
    assert daily_update_due(db, today, tmp_path / "another.csv")


def test_locked_csv_preserves_previous_file_and_retries(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import export, storage

    collection = SimpleNamespace(path=str(tmp_path / "profile" / "collection.anki2"))
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report_with_followup())
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text("previous results", encoding="utf-8")
    daily = DailySettings(True, str(csv_path))

    def locked(source, target):
        raise PermissionError("CSV is open in another app")

    monkeypatch.setattr(export.os, "replace", locked)
    result = update_results(collection, daily=daily, scheduled_day=date(2026, 10, 7))
    assert result.csv_error == "CSV is open in another app"
    assert result.report == report_with_followup()
    assert csv_path.read_text(encoding="utf-8") == "previous results"
    assert load_report(database_path(collection.path)) == report_with_followup()
    assert daily_update_due(database_path(collection.path), date(2026, 10, 7), csv_path)
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_manual_calculation_persists_when_daily_mode_is_disabled(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report_with_followup())
    update_results(collection)
    assert load_report(database_path(collection.path)) == report_with_followup()
    assert not list(tmp_path.glob("*.csv"))


def make_legacy_database(path):
    """Construct the previous release's table shape and successful-day metadata."""
    report = report_with_followup()
    save_report(report, path)
    columns = ["date"] + [
        f"{prefix}_{field}" for prefix in ("recall", "formation") for field in METRIC_FIELDS
    ]
    with closing(sqlite3.connect(path)) as connection, connection:
        types = {row[1]: row[2] for row in connection.execute("pragma table_info(daily_results)")}
        definitions = [
            f"{column} {types[column]}" + (" primary key" if column == "date" else "")
            for column in columns
        ]
        connection.execute(f"create table legacy_results ({', '.join(definitions)})")
        connection.execute(
            f"insert into legacy_results select {', '.join(columns)} from daily_results"
        )
        connection.execute("drop table daily_results")
        connection.execute("alter table legacy_results rename to daily_results")
        connection.execute(
            "delete from metadata where key in "
            "('formation_short_term_max_days', 'last_daily_schema')"
        )
        connection.executemany(
            "insert or replace into metadata values (?, ?)",
            [
                ("last_daily_day", "2026-10-08"),
                ("last_daily_csv", str(path.parent / "scores.csv")),
            ],
        )
        connection.execute("pragma user_version=1")
    return report


def test_schema_upgrade_preserves_legacy_report_and_recalculates_same_day(tmp_path):
    db = tmp_path / "results.sqlite3"
    updated = make_legacy_database(db)
    legacy = load_report(db)
    assert legacy.days[0].formation == updated.days[0].formation
    assert legacy.days[0].formation_short_term is None
    assert legacy.days[0].formation_long_term is None
    assert daily_update_due(db, date(2026, 10, 8), tmp_path / "scores.csv")
    save_report(updated, db)
    assert load_report(db) == updated
    # Database migration alone is not proof that the upgraded CSV was written.
    assert daily_update_due(db, date(2026, 10, 8), tmp_path / "scores.csv")
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 2
        count, score = connection.execute(
            "select formation_short_term_count, "
            "formation_short_term_score_pp from daily_results where date='2026-09-07'"
        ).fetchone()
        assert count == 1 and score == pytest.approx(-70)
        stored = json.loads(
            connection.execute("select value from metadata where key='report'").fetchone()[0]
        )
        assert stored["parameters_hash"] == "abc"


def test_failed_report_write_rolls_back_schema_migration_and_old_results(tmp_path):
    db = tmp_path / "results.sqlite3"
    report = make_legacy_database(db)
    duplicate = replace(report, days=[report.days[0], report.days[0]])
    with pytest.raises(sqlite3.IntegrityError):
        save_report(duplicate, db)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("pragma user_version").fetchone()[0] == 1
        columns = {row[1] for row in connection.execute("pragma table_info(daily_results)")}
        assert "formation_short_term_count" not in columns
    assert load_report(db).days[0].formation == report.days[0].formation


def test_legacy_subgroups_remain_missing_if_saved_before_recalculation(tmp_path):
    report = report_with_followup()
    legacy = replace(
        report, days=[DailyResult(day.day, day.recall, day.formation) for day in report.days]
    )
    db = tmp_path / "results.sqlite3"
    save_report(legacy, db)
    assert load_report(db) == legacy


def test_failed_csv_after_schema_upgrade_stays_due_and_preserves_csv(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import export, storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    db = database_path(collection.path)
    report = make_legacy_database(db)
    csv_path = db.parent / "scores.csv"
    csv_path.write_text("old-format CSV", encoding="utf-8")
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report)

    def locked(*args):
        raise PermissionError("CSV locked")

    with monkeypatch.context() as patch:
        patch.setattr(export.os, "replace", locked)
        result = update_results(
            collection,
            daily=DailySettings(True, str(csv_path)),
            scheduled_day=date(2026, 10, 8),
        )
        assert result.csv_error == "CSV locked"
    assert csv_path.read_text(encoding="utf-8") == "old-format CSV"
    assert daily_update_due(db, date(2026, 10, 8), csv_path)
    update_results(
        collection, daily=DailySettings(True, str(csv_path)), scheduled_day=date(2026, 10, 8)
    )
    assert not daily_update_due(db, date(2026, 10, 8), csv_path)
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["formation_short_term_count"] == "1"


def test_csv_retry_is_file_only_and_does_not_mark_an_unchecked_later_day(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    daily = DailySettings(True, str(tmp_path / "scores.csv"))
    save_daily_settings(collection.path, daily)
    monkeypatch.setattr(storage, "build_report", lambda *args: report_with_followup())
    real_export = storage.export_csv_atomic

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    checked_day = date(2026, 10, 7)
    result = update_results(collection, daily=daily, scheduled_day=checked_day)
    assert result.recalculated and result.csv_error == "CSV locked"

    def unexpected(*args, **kwargs):
        pytest.fail("A CSV-only retry must not read inputs, replay FSRS, or save the report")

    for name in ("input_fingerprint", "build_report", "save_report"):
        monkeypatch.setattr(storage, name, unexpected)
    monkeypatch.setattr(storage, "export_csv_atomic", real_export)
    retry = storage.retry_fixed_csv(collection.path)
    assert retry.report == result.report
    assert not retry.recalculated and retry.csv_exported and retry.csv_error is None
    db = database_path(collection.path)
    assert not daily_update_due(db, checked_day, daily.csv_path)
    assert daily_update_due(db, date(2026, 10, 8), daily.csv_path)
    Path(daily.csv_path).unlink()
    assert daily_update_due(db, checked_day, daily.csv_path)


def test_automatic_retry_reuses_analysis_but_changed_inputs_and_force_rebuild(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    builds = []

    def build(*args):
        builds.append(True)
        return report_with_followup()

    monkeypatch.setattr(storage, "build_report", build)
    daily = DailySettings(True, str(tmp_path / "scores.csv"))
    real_export = storage.export_csv_atomic
    monkeypatch.setattr(storage, "export_csv_atomic", lambda *a, **k: 1 / 0)
    assert update_results(collection, daily=daily).csv_error
    monkeypatch.setattr(storage, "export_csv_atomic", real_export)
    retry = update_results(collection, daily=daily)
    assert retry.csv_exported and not retry.recalculated
    assert len(builds) == 1
    monkeypatch.setattr(storage, "input_fingerprint", lambda *args: "changed-history")
    assert update_results(collection, daily=daily).recalculated
    assert len(builds) == 2
    assert update_results(collection, force=True).recalculated
    assert len(builds) == 3


def test_invalid_csv_destination_does_not_discard_calculated_results(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    monkeypatch.setattr(storage, "build_report", lambda *args: report_with_followup())
    result = update_results(collection, daily=DailySettings(True, "relative.csv"))
    assert "absolute path" in result.csv_error
    assert result.recalculated and result.report == load_report(database_path(collection.path))


def test_failed_export_of_revised_scores_stays_due_after_earlier_same_day_success(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from addon import storage

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    daily = DailySettings(True, str(tmp_path / "scores.csv"))
    monkeypatch.setattr(storage, "build_report", lambda *args: report_with_followup(False))
    day = date(2026, 10, 7)
    assert update_results(collection, daily=daily, scheduled_day=day).csv_exported
    assert not daily_update_due(database_path(collection.path), day, daily.csv_path)
    monkeypatch.setattr(storage, "input_fingerprint", lambda *args: "new-follow-up")
    monkeypatch.setattr(storage, "build_report", lambda *args: report_with_followup())

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    result = update_results(collection, daily=daily, scheduled_day=day)
    assert result.recalculated and result.csv_error
    assert daily_update_due(database_path(collection.path), day, daily.csv_path)


def test_cancellation_before_csv_replace_preserves_file_and_exposure_audit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from addon import storage
    from addon.backend import UpdateCancelled
    from addon.research import sharing_state

    collection = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    monkeypatch.setattr(storage, "build_report", lambda *args: report_with_followup())
    update_results(collection, scheduled_day=date(2026, 10, 7))
    destination = tmp_path / "daily.csv"
    destination.write_text("previous CSV", encoding="utf-8")
    save_daily_settings(collection.path, DailySettings(True, str(destination)))
    checks = []

    def check():
        checks.append(True)
        if len(checks) == 2:
            raise UpdateCancelled("Profile closed before publication")

    with pytest.raises(UpdateCancelled):
        storage.retry_fixed_csv(collection.path, check_cancelled=check)
    assert destination.read_text(encoding="utf-8") == "previous CSV"
    assert sharing_state(collection.path).first_exposure is None
    assert storage.csv_export_pending(database_path(collection.path), destination)
    assert not list(tmp_path.glob(".*.tmp"))
