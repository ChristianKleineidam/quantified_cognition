import csv
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from test_storage import report_with_followup

from addon import research, storage
from addon.research import (
    SharingConfig,
    audit_path,
    finish_csv_exposure,
    prepare_csv_exposure,
    sharing_state,
)


@pytest.fixture(autouse=True)
def fake_input_scan(monkeypatch):
    monkeypatch.setattr("addon.storage.input_fingerprint", lambda col, settings: "inputs-v2")


def choose(path, value="opt_in", config=None):
    return sharing_state(path, config, event_type="sharing_choice", choice=value)


def expose(path, surface="report", config=None):
    return sharing_state(path, config, event_type="results_exposed", surface=surface)


def test_early_consent_survives_reopen_and_withdrawal(tmp_path):
    path = tmp_path / "collection.anki2"
    first = choose(path)
    assert first.history == "tracked"
    assert first.metadata()["research_consent_before_first_view"] is True
    viewed = expose(path)
    initial_time = viewed.metadata()["research_consent_first_given_at_utc"]
    assert viewed.metadata()["research_consent_before_first_view"] is True
    reopened = sharing_state(path)
    assert reopened.participant_id == first.participant_id
    withdrawn = choose(path, "declined")
    assert withdrawn.metadata()["research_consent_active"] is False
    assert withdrawn.metadata()["research_consent_before_first_view"] is True
    assert withdrawn.metadata()["research_consent_first_given_at_utc"] == initial_time
    assert withdrawn.events[-1]["event_type"] == "consent_withdrawn"


@pytest.mark.parametrize("initial", ["declined", "deferred"])
def test_later_opt_in_does_not_become_pre_view_consent(tmp_path, initial):
    path = tmp_path / "collection.anki2"
    choose(path, initial)
    expose(path)
    later = choose(path)
    assert later.metadata()["sharing_first_choice"] == initial
    assert later.metadata()["research_consent_active"] is True
    assert later.metadata()["research_consent_before_first_view"] is False


def test_changed_choice_before_any_data_is_still_early(tmp_path):
    path = tmp_path / "collection.anki2"
    choose(path, "deferred")
    choose(path)
    assert expose(path).metadata()["research_consent_before_first_view"] is True


def test_existing_untracked_results_do_not_manufacture_early_consent(tmp_path):
    path = tmp_path / "collection.anki2"
    storage.save_report(report_with_followup(), storage.database_path(path))
    state = choose(path)
    assert state.history == "unknown_legacy"
    assert expose(path).metadata()["research_consent_before_first_view"] is None


def test_clock_changes_do_not_change_event_order_classification(tmp_path, monkeypatch):
    times = iter(
        ["2026-10-08T13:00:00+00:00", "2026-10-08T12:00:00+00:00", "2026-10-08T11:00:00+00:00"]
    )
    monkeypatch.setattr(research, "_now", lambda: next(times))
    path = tmp_path / "collection.anki2"
    choose(path)
    state = expose(path)
    assert state.metadata()["research_consent_before_first_view"] is True
    assert (
        state.metadata()["research_consent_first_given_at_utc"]
        > (state.metadata()["first_results_exposed_at_utc"])
    )


def test_new_notice_requires_new_choice_and_preserves_original_early_consent(tmp_path):
    path = tmp_path / "collection.anki2"
    old_notice = SharingConfig(notice_version="research-sharing-v6")
    original = choose(path, config=old_notice)
    expose(path, config=old_notice)
    new_notice = SharingConfig.from_config(
        {"research_sharing": {"notice_version": "research-sharing-v6", "notice_text": ""}}
    )
    assert new_notice.notice_version == SharingConfig().notice_version
    state = sharing_state(path, new_notice)
    assert state.current_choice is None
    assert state.metadata()["research_consent_active"] is False
    assert state.metadata()["research_consent_before_first_view"] is True
    renewed = choose(path, config=new_notice)
    assert renewed.metadata()["current_notice_consent_before_first_view"] is False
    assert renewed.metadata()["first_consent_notice_version"] == "research-sharing-v6"
    assert renewed.participant_id == original.participant_id
    assert renewed.events[: len(original.events)] == original.events
    assert (
        renewed.metadata()["sharing_first_choice_at_utc"]
        == original.metadata()["sharing_first_choice_at_utc"]
    )
    with closing(sqlite3.connect(audit_path(path))) as connection:
        assert connection.execute("select count(*) from notices").fetchone()[0] == 2


@pytest.mark.parametrize(
    ("version", "text"),
    [("research-sharing-v6", "A custom study notice"), ("custom-study-v1", "")],
)
def test_custom_notice_configuration_keeps_its_explicit_version(version, text):
    config = SharingConfig.from_config(
        {"research_sharing": {"notice_version": version, "notice_text": text}}
    )
    assert config.notice_version == version
    assert config.custom_notice_text == text


def test_first_exposure_surface_is_not_overwritten_by_later_reports(tmp_path):
    path = tmp_path / "collection.anki2"
    choose(path)
    first = expose(path, "csv")
    assert expose(path).first_exposure == first.first_exposure
    assert expose(path).metadata()["first_results_exposure_surface"] == "csv"


def test_cancelled_csv_does_not_count_as_exposure_and_successful_csv_does(tmp_path):
    path = tmp_path / "collection.anki2"
    _snapshot, token, _metadata = prepare_csv_exposure(path)
    finish_csv_exposure(path, None, token, success=False)
    assert sharing_state(path).first_exposure is None
    choose(path)
    _snapshot, token, metadata = prepare_csv_exposure(path)
    finish_csv_exposure(path, None, token, success=True)
    assert metadata["research_consent_before_first_view"] is True
    state = sharing_state(path)
    assert state.metadata()["first_results_exposure_surface"] == "csv"
    assert (
        state.metadata()["first_results_exposed_at_utc"] == metadata["first_results_exposed_at_utc"]
    )


def test_choice_during_csv_release_is_conservatively_after_release_start(tmp_path):
    path = tmp_path / "collection.anki2"
    _snapshot, token, _metadata = prepare_csv_exposure(path)
    choose(path)
    finish_csv_exposure(path, None, token, success=True)
    assert sharing_state(path).metadata()["research_consent_before_first_view"] is False


def test_participant_ids_and_history_are_isolated_between_profiles(tmp_path):
    first = choose(tmp_path / "one" / "collection.anki2")
    second = sharing_state(tmp_path / "two" / "collection.anki2")
    assert first.participant_id != second.participant_id
    assert second.current_choice is None


def test_calculation_alone_does_not_count_as_viewing(tmp_path, monkeypatch):
    path = tmp_path / "collection.anki2"
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report_with_followup())
    storage.update_results(SimpleNamespace(path=str(path)))
    assert sharing_state(path).first_exposure is None
    assert sharing_state(path).history == "tracked"


def test_daily_csv_metadata_and_failed_first_export_has_no_view(tmp_path, monkeypatch):
    from addon import export

    path = tmp_path / "collection.anki2"
    collection = SimpleNamespace(path=str(path))
    choose(path)
    monkeypatch.setattr(storage, "build_report", lambda col, settings: report_with_followup())
    csv_path = tmp_path / "daily.csv"
    daily = storage.DailySettings(True, str(csv_path))

    def fail(*args):
        raise OSError("Destination unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(export.os, "replace", fail)
        result = storage.update_results(collection, daily=daily)
        assert result.csv_error == "Destination unavailable"
    assert sharing_state(path).first_exposure is None
    storage.update_results(collection, daily=daily)
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["research_consent_before_first_view"] == "true"
    assert row["research_consent_active"] == "true"
    assert row["first_results_exposure_surface"] == "csv"
    assert row["research_participant_id"] == sharing_state(path).participant_id


def test_invalid_event_is_rejected_without_committing_a_choice(tmp_path):
    path = tmp_path / "collection.anki2"
    with pytest.raises(ValueError):
        sharing_state(path, event_type="sharing_choice")
    assert sharing_state(path).current_choice is None


def test_interrupted_export_before_consent_is_unknown_until_resolved(tmp_path):
    path = tmp_path / "collection.anki2"
    _snapshot, token, _metadata = prepare_csv_exposure(path)
    assert choose(path).metadata()["research_consent_before_first_view"] is None
    finish_csv_exposure(path, None, token, success=False)
    assert sharing_state(path).metadata()["research_consent_before_first_view"] is True
