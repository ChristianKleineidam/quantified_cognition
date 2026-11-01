"""Compare deleted-card replay directly with native replay of the same surviving cards."""

from datetime import date, timedelta

import pytest
from anki.collection import Collection
from test_backend import add_card, live_state

from addon.backend import (
    CALCULATION_VERSION,
    UpdateCancelled,
    build_report,
    collection_snapshot,
    input_fingerprint,
)
from addon.deleted_history import deleted_history_policy
from addon.storage import database_path, load_report, update_results


@pytest.fixture
def collection(tmp_path):
    col = Collection(str(tmp_path / "collection.anki2"))
    col.set_config("fsrs", True)
    try:
        yield col
    finally:
        col.close()


def orphan(col, cid):
    # Anki normally removes revlogs too; retained or imported orphan logs are
    # the case under test. Keep the logs exactly as found and delete the card.
    col.db.execute("delete from cards where id=?", cid)


@pytest.mark.parametrize("kind", ("learning", "truncated", "reset", "preview"))
def test_deleted_reconstruction_matches_existing_card_native_states(collection, kind):
    day = date(2026, 6, 1)
    events = [
        (day, 3, 0 if kind != "truncated" else 1, 2500),
        (day + timedelta(days=2), 1, 1, 2500),
        (day + timedelta(days=2), 3, 2, 2500),
        (day + timedelta(days=45), 3, 1, 2500),
    ]
    if kind == "reset":
        events[2:2] = [(day + timedelta(days=2), 0, 4, 0)]
        events[3] = (day + timedelta(days=3), 3, 0, 2500)
    elif kind == "preview":
        events[2] = (day + timedelta(days=3), 3, 3, 0)
    cid, _ = add_card(collection, events)
    expected = build_report(collection)
    orphan(collection, cid)
    before = live_state(collection)
    actual = build_report(collection)
    assert live_state(collection) == before
    assert actual.days == expected.days
    assert actual.study_windows == expected.study_windows
    assert actual.reconstructed_deleted_cards == 1
    assert actual.reconstructed_deleted_reviews == sum(event[1] in (1, 2, 3, 4) for event in events)
    assert actual.deleted_card_reviews == 0
    assert actual.analyzed_cards == 1
    assert actual.calculation_version == CALCULATION_VERSION


@pytest.mark.parametrize("field", ("parameters", "retention", "cutoff"))
def test_conflicting_presets_skip_deleted_only(collection, field):
    day = date(2026, 6, 1)
    removed, _ = add_card(collection, [(day, 3, 0, 2500)])
    add_card(collection, [(day + timedelta(days=1), 3, 0, 2500)])
    orphan(collection, removed)
    other = collection.decks.add_config("Unused but potentially relevant to deleted cards")
    if field == "parameters":
        other["fsrsParams6"] = list(
            collection.decks.get_deck_configs_for_update(1).defaults.config.fsrs_params_6
        )
        other["fsrsParams6"][0] *= 2
    elif field == "retention":
        other["sm2Retention"] = 0.8
    else:
        other["ignoreRevlogsBeforeDate"] = "2026-05-01"
    collection.decks.update_config(other)
    before = live_state(collection)
    report = build_report(collection)
    assert live_state(collection) == before
    assert report.reconstructed_deleted_cards == report.reconstructed_deleted_reviews == 0
    assert report.deleted_card_reviews == 1
    assert report.analyzed_cards == 1
    assert report.days[0].day == day + timedelta(days=1)
    assert "Presets differ:" in report.deleted_history_status


def test_native_defaults_agree_with_explicit_defaults_and_ignore_unrelated_settings(collection):
    other = collection.decks.add_config("Explicit defaults")
    other["fsrsParams6"] = list(
        collection.decks.get_deck_configs_for_update(1).defaults.config.fsrs_params_6
    )
    other["desiredRetention"] = 0.75
    other["new"]["perDay"] = 3
    # A stale, inactive parameter generation does not affect current replay.
    other["fsrsParams5"] = [1.0] * 19
    collection.decks.update_config(other)
    assert deleted_history_policy(collection).deck_id is not None


def test_deleted_histories_work_without_remaining_notes(collection):
    cid, _ = add_card(collection, [(date(2026, 6, 1), 3, 0, 2500)])
    orphan(collection, cid)
    collection.db.execute("delete from notes")
    before = live_state(collection)
    report = build_report(collection)
    assert live_state(collection) == before
    assert report.reconstructed_deleted_cards == 1
    assert len(report.days) == 1


@pytest.mark.parametrize("card_type", (0, 1, 3))
def test_lost_current_card_state_does_not_change_historical_predictions(collection, card_type):
    day = date(2026, 6, 1)
    cid, _ = add_card(collection, [(day, 3, 0, 2500), (day + timedelta(days=5), 1, 1, 2500)])
    collection.db.execute("update cards set type=?, queue=? where id=?", card_type, card_type, cid)
    expected = build_report(collection)
    orphan(collection, cid)
    assert build_report(collection).days == expected.days


def test_deleted_note_type_is_not_required_for_reconstruction(collection):
    day = date(2026, 6, 1)
    cid, _ = add_card(collection, [(day, 3, 0, 2500), (day + timedelta(days=5), 3, 1, 2500)])
    expected = build_report(collection)
    model_id = collection.db.scalar(
        "select mid from notes where id=(select nid from cards where id=?)", cid
    )
    orphan(collection, cid)
    collection.db.execute("delete from notes where mid=?", model_id)
    collection.models.remove(model_id)
    assert build_report(collection).days == expected.days


def test_cancelled_deleted_reconstruction_rolls_back_placeholders_and_truncation(collection):
    cid, _ = add_card(collection, [(date(2026, 6, 1), 3, 0, 2500)])
    orphan(collection, cid)
    with collection_snapshot(collection) as scratch:
        before = live_state(scratch)
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 2:  # Placeholders have been inserted.
                raise UpdateCancelled("Cancelled")

        with pytest.raises(UpdateCancelled):
            build_report(scratch, is_snapshot=True, check_cancelled=cancel)
        assert live_state(scratch) == before


@pytest.mark.parametrize("change", ("edit", "delete", "late_insert"))
def test_deleted_input_changes_recalculate_and_save_provenance(collection, change):
    day = date(2026, 6, 1)
    cid, ids = add_card(collection, [(day, 3, 0, 2500), (day + timedelta(days=5), 3, 1, 2500)])
    orphan(collection, cid)
    before = update_results(collection)
    assert before.report.reconstructed_deleted_cards == 1
    assert not update_results(collection).recalculated
    fingerprint = input_fingerprint(collection)
    if change == "edit":
        collection.db.execute("update revlog set ease=1 where id=?", ids[1])
    elif change == "delete":
        collection.db.execute("delete from revlog where id=?", ids[1])
    else:
        collection.db.execute(
            "insert into revlog values (?,?,?,?,?,?,?,?,?)",
            ids[1] - 86400000,
            cid,
            -1,
            1,
            3,
            3,
            2500,
            1000,
            1,
        )
    assert input_fingerprint(collection) != fingerprint
    after = update_results(collection)
    assert after.recalculated and after.report.days != before.report.days
    assert load_report(database_path(collection.path)) == after.report


def test_preset_conflict_and_resolution_invalidate_deleted_history_cache(collection):
    cid, _ = add_card(collection, [(date(2026, 6, 1), 3, 0, 2500)])
    orphan(collection, cid)
    assert update_results(collection).report.reconstructed_deleted_cards == 1
    other = collection.decks.add_config("Conflicting")
    other["ignoreRevlogsBeforeDate"] = "2026-01-01"
    collection.decks.update_config(other)
    skipped = update_results(collection)
    assert skipped.recalculated and skipped.report.deleted_card_reviews == 1
    other["ignoreRevlogsBeforeDate"] = ""
    collection.decks.update_config(other)
    included = update_results(collection)
    assert included.recalculated and included.report.reconstructed_deleted_cards == 1


def test_legacy_report_upgrade_is_due_even_after_today_csv_succeeded(collection, tmp_path):
    import json
    import sqlite3
    from contextlib import closing

    from addon.storage import DailySettings, csv_export_pending, daily_update_due

    cid, _ = add_card(collection, [(date(2026, 6, 1), 3, 0, 2500)])
    orphan(collection, cid)
    day = date(2026, 10, 26)
    daily = DailySettings(True, str(tmp_path / "daily.csv"))
    update_results(collection, daily=daily, scheduled_day=day)
    path = database_path(collection.path)
    assert not daily_update_due(path, day, daily.csv_path)
    with closing(sqlite3.connect(path)) as connection, connection:
        metadata = json.loads(
            connection.execute("select value from metadata where key='report'").fetchone()[0]
        )
        for key in (
            "calculation_version",
            "reconstructed_deleted_cards",
            "reconstructed_deleted_reviews",
            "deleted_history_status",
        ):
            metadata.pop(key)
        connection.execute(
            "update metadata set value=? where key='report'", (json.dumps(metadata),)
        )
        connection.execute("delete from metadata where key='calculation_version'")
    legacy = load_report(path)
    assert legacy.calculation_version == 0
    assert legacy.reconstructed_deleted_cards == 0
    assert "Legacy" in legacy.deleted_history_status
    assert daily_update_due(path, day, daily.csv_path)
    # Failed/absent old CSVs must not route the updater into an export-only retry.
    (tmp_path / "daily.csv").unlink()
    assert not csv_export_pending(path, daily.csv_path)
    updated = update_results(collection, daily=daily, scheduled_day=day)
    assert updated.recalculated and updated.report.reconstructed_deleted_cards == 1
    assert not daily_update_due(path, day, daily.csv_path)
