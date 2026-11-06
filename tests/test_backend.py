from datetime import date, datetime, timedelta

import pytest
from anki.collection import Collection

from addon.backend import CompatibilityError, build_report, collection_snapshot
from addon.metrics import anki_day


@pytest.fixture
def collection(tmp_path):
    col = Collection(str(tmp_path / "collection.anki2"))
    col.set_config("fsrs", True)
    assert col.decks.get_deck_configs_for_update(1).fsrs
    try:
        yield col
    finally:
        col.close()


def add_card(col, days):
    note = col.new_note(col.models.by_name("Basic"))
    note["Front"] = "Synthetic test question"
    note["Back"] = "Synthetic answer"
    col.add_note(note, 1)
    card = note.cards()[0]
    card.type, card.queue, card.ivl, card.factor = 2, 2, 3, 2500
    col.update_card(card)
    ids = []
    for index, (day, rating, kind, factor) in enumerate(days):
        rid = int(datetime.combine(day, datetime.min.time()).replace(hour=12).timestamp() * 1000)
        rid += index * 600_000 + (card.id % 1000)
        # Original SM-2 factor 2500: no FSRS memory state was ever recorded.
        col.db.execute(
            "insert into revlog values (?,?,?,?,?,?,?,?,?)",
            rid,
            card.id,
            -1,
            rating,
            3,
            3,
            factor,
            1000,
            kind,
        )
        ids.append(rid)
    return card.id, ids


def live_state(col):
    return {
        table: col.db.all(f"select * from {table} order by id")
        for table in ("cards", "notes", "revlog")
    }


def test_sm2_and_synced_logs_native_predictions_live_collection_unchanged(collection):
    monday, friday = date(2026, 9, 7), date(2026, 9, 11)
    cid, ids = add_card(
        collection,
        [
            (monday - timedelta(days=3), 3, 0, 2500),
            (monday, 1, 1, 2500),
            (monday, 3, 2, 2500),
            (friday, 3, 1, 2500),
        ],
    )
    before = live_state(collection)
    report = build_report(collection)
    assert live_state(collection) == before
    assert report.study_windows[monday.isoformat()] == [ids[1] / 1000, ids[2] / 1000]
    assert report.study_windows[friday.isoformat()] == [ids[3] / 1000, ids[3] / 1000]
    by_day = {day.day: day for day in report.days}
    assert by_day[monday].recall.recalled_percent == 0
    assert by_day[monday].recall.count == 1
    assert by_day[monday].formation.count == 1
    assert by_day[monday].formation.score_pp == by_day[friday].recall.score_pp
    assert by_day[friday].formation.pending_count == 1
    # Compare the probability against a separate invocation of Anki's native
    # historical state and native retrievability, rather than a copy of FSRS.
    with collection_snapshot(collection) as scratch:
        stats = scratch.card_stats_data(cid)
        last_state = list(reversed(stats.revlog))[2].memory_state
        import json

        data = json.dumps(
            {
                "s": last_state.stability,
                "d": last_state.difficulty,
                "decay": scratch.compute_memory_state(cid).decay,
                "lrt": ids[2] // 1000,
            }
        )
        predicted = scratch.db.scalar(
            "select extract_fsrs_retrievability(?, ?, 1, 0, 0, ?)",
            data,
            ids[2] // 1000 + 1,
            ids[3] // 1000,
        )
        assert by_day[friday].recall.predicted_percent == pytest.approx(predicted * 100)


def test_cancelled_reconstruction_rolls_back_partial_snapshot_writes(collection):
    from addon.backend import UpdateCancelled

    monday = date(2026, 9, 7)
    add_card(
        collection,
        [
            (monday, 3, 0, 2500),
            (monday + timedelta(days=2), 3, 1, 2500),
            (monday + timedelta(days=3), 0, 4, 0),
            (monday + timedelta(days=4), 3, 0, 2500),
        ],
    )
    add_card(collection, [(monday, 3, 0, 2500)])
    with collection_snapshot(collection) as scratch:
        before = live_state(scratch)
        calls = 0

        def cancel_after_first_card():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise UpdateCancelled("Cancelled after the first card")

        with pytest.raises(UpdateCancelled):
            build_report(scratch, is_snapshot=True, check_cancelled=cancel_after_first_card)
        assert live_state(scratch) == before
        # Rollback also releases the transaction, leaving the snapshot usable.
        assert len(build_report(scratch, is_snapshot=True).days) == 3


def test_parameter_change_recalculates_history(collection):
    monday = date(2026, 9, 7)
    add_card(collection, [(monday, 3, 0, 2500), (monday + timedelta(days=5), 3, 1, 2500)])
    first = build_report(collection)
    preset = collection.decks.get_config(1)
    preset["fsrsParams6"] = [
        0.212,
        1.2931,
        2.3065,
        8.2956,
        6.4133,
        0.8334,
        3.0194,
        0.001,
        1.8722,
        0.1666,
        0.796,
        1.4835,
        0.0614,
        0.2629,
        1.6483,
        0.6014,
        1.8729,
        0.5425,
        0.0912,
        0.0658,
        0.1542,
    ]
    preset["fsrsParams6"][2] = 10.0
    collection.decks.update_config(preset)
    second = build_report(collection)
    assert first.parameters_hash != second.parameters_hash
    assert first.days[-1].recall.score_pp != second.days[-1].recall.score_pp


def test_native_history_reconstructed_before_and_after_reset(collection):
    monday = date(2026, 9, 7)
    add_card(
        collection,
        [
            (monday, 3, 0, 2500),
            (monday + timedelta(days=2), 3, 1, 2500),
            (monday + timedelta(days=3), 0, 4, 0),
            (monday + timedelta(days=4), 3, 0, 2500),
            (monday + timedelta(days=7), 1, 1, 2500),
        ],
    )
    before = live_state(collection)
    report = build_report(collection)
    assert live_state(collection) == before
    assert len(report.days) == 4
    assert report.days[0].formation.count == 1
    assert report.days[1].recall.count == 1
    assert report.days[1].formation.excluded_count == 1
    assert report.days[2].formation.count == 1
    assert report.days[3].recall.count == 1


def test_preview_exposure_excluded_and_pair_broken(collection):
    monday = date(2026, 9, 7)
    add_card(
        collection,
        [
            (monday, 3, 0, 2500),
            (monday + timedelta(days=1), 3, 3, 0),
            (monday + timedelta(days=3), 3, 1, 2500),
        ],
    )
    report = build_report(collection)
    assert report.excluded_preview_reviews == 1
    assert len(report.days) == 2
    assert report.days[0].formation.excluded_count == 1
    assert report.days[1].recall.count == 0


def test_filtered_rescheduling_review_is_included(collection):
    monday = date(2026, 9, 7)
    add_card(collection, [(monday, 3, 0, 2500), (monday + timedelta(days=3), 2, 3, 2500)])
    report = build_report(collection)
    assert report.excluded_preview_reviews == 0
    assert report.days[1].recall.count == 1
    assert report.days[1].recall.recalled_percent == 100


def test_empty_collection(collection):
    report = build_report(collection)
    assert report.days == []
    assert report.analyzed_cards == 0


def test_fsrs_required(collection):
    collection.set_config("fsrs", False)
    assert not collection.decks.get_deck_configs_for_update(1).fsrs
    with pytest.raises(CompatibilityError, match="Enable FSRS"):
        build_report(collection)


def test_truncated_history_without_learning_is_not_fabricated(collection):
    monday = date(2026, 9, 7)
    add_card(collection, [(monday, 3, 1, 2500), (monday + timedelta(days=4), 1, 1, 2500)])
    report = build_report(collection)
    assert report.days[0].recall.excluded_count == 1
    assert report.days[1].recall.count == 1


def test_timestamp_uses_configured_rollover(collection):
    monday = date(2026, 9, 7)
    _, ids = add_card(collection, [(monday, 3, 0, 2500)])
    prefs = collection.get_preferences()
    prefs.scheduling.rollover = 14
    collection.set_preferences(prefs)
    report = build_report(collection)
    assert report.rollover_hour == 14
    assert report.days[0].day == anki_day(ids[0] / 1000, 14) == monday - timedelta(days=1)


def test_terminal_reset_excluded_instead_of_pending(collection):
    monday = date(2026, 9, 7)
    add_card(collection, [(monday, 3, 0, 2500), (monday + timedelta(days=2), 0, 4, 0)])
    report = build_report(collection)
    assert report.days[0].formation.excluded_count == 1
    assert report.days[0].formation.pending_count == 0


def test_legacy_unlogged_reset_separates_learning_episodes(collection):
    monday = date(2026, 9, 7)
    add_card(
        collection,
        [
            (monday, 3, 0, 2500),
            (monday + timedelta(days=2), 3, 1, 2500),
            (monday + timedelta(days=4), 3, 0, 2500),
            (monday + timedelta(days=7), 3, 1, 2500),
        ],
    )
    report = build_report(collection)
    assert report.days[0].formation.count == 1
    assert report.days[1].formation.excluded_count == 1
    assert report.days[2].formation.count == 1


def test_parameter_hash_is_stable_across_unchanged_recalculations(collection):
    add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    first, second = build_report(collection), build_report(collection)
    assert first.parameters_hash == second.parameters_hash
    assert first.days == second.days


def test_persistent_native_update_revises_formation_and_preserves_main(collection, tmp_path):
    import csv

    from addon.storage import DailySettings, database_path, load_report, update_results

    monday, friday = date(2026, 9, 7), date(2026, 9, 11)
    card_id, _ = add_card(collection, [(monday, 3, 0, 2500)])
    csv_path = tmp_path / "export" / "daily.csv"
    daily = DailySettings(True, str(csv_path))
    before = live_state(collection)
    first = update_results(collection, daily=daily, scheduled_day=date(2026, 10, 7)).report
    assert live_state(collection) == before
    assert first.days[0].formation.pending_count == 1
    rid = int(datetime(2026, 9, 11, 12).timestamp() * 1000)
    collection.db.execute(
        "insert into revlog values (?,?,?,?,?,?,?,?,?)", rid, card_id, -1, 3, 3, 3, 2500, 1000, 1
    )
    before = live_state(collection)
    revised = update_results(collection, daily=daily, scheduled_day=date(2026, 10, 8)).report
    assert live_state(collection) == before
    assert revised.days[0].formation.pending_count == 0
    assert revised.days[0].formation.count == 1
    assert load_report(database_path(collection.path)) == revised
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["date"] for row in rows] == [monday.isoformat(), friday.isoformat()]
    assert rows[0]["formation_count"] == "1"
    assert float(rows[0]["formation_score_pp"]) == pytest.approx(
        revised.days[0].formation.score_pp, abs=1e-6
    )


def test_native_history_split_uses_actual_delay_and_is_saved_and_exported(collection, tmp_path):
    import csv

    from addon.storage import DailySettings, database_path, load_report, update_results

    source = date(2026, 8, 1)
    add_card(collection, [(source, 3, 0, 2500), (source + timedelta(days=10), 3, 1, 2500)])
    add_card(collection, [(source, 3, 0, 2500), (source + timedelta(days=45), 1, 1, 2500)])
    # Both cards have a nominal 3-day interval in the synthetic revlog. The
    # actual later review determines the group, including an overdue long gap.
    before = live_state(collection)
    csv_path = tmp_path / "split.csv"
    report = update_results(collection, daily=DailySettings(True, str(csv_path))).report
    assert live_state(collection) == before
    day = next(day for day in report.days if day.day == source)
    assert day.formation.count == 2
    assert day.formation_short_term.count == 1
    assert day.formation_long_term.count == 1
    assert day.formation_short_term.score_pp > 0
    assert day.formation_long_term.score_pp < 0
    assert load_report(database_path(collection.path)) == report
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["formation_short_term_count"] == row["formation_long_term_count"] == "1"
    assert row["formation_short_term_max_days"] == "30"


def test_unchanged_inputs_reuse_saved_analysis_without_snapshot_or_write(collection, monkeypatch):
    import json
    import sqlite3
    from contextlib import closing

    from addon import storage

    add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    first = storage.update_results(collection)
    assert first.recalculated
    with closing(sqlite3.connect(storage.database_path(collection.path))) as db, db:
        (raw_report,) = db.execute("select value from metadata where key = 'report'").fetchone()
        metadata = json.loads(raw_report)
        metadata["settings"]["default_display_days"] = 180
        db.execute("update metadata set value = ? where key = 'report'", (json.dumps(metadata),))

    def unexpected(*args, **kwargs):
        pytest.fail("Unchanged inputs must not rebuild or rewrite the analysis")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "build_report", unexpected)
        patch.setattr(storage, "save_report", unexpected)
        cached = storage.update_results(collection)
    assert not cached.recalculated
    assert cached.report == first.report


@pytest.mark.parametrize(
    "change", ["edit", "delete", "late_insert", "parameters", "deck", "rollover"]
)
def test_changed_inputs_invalidate_cache_including_older_synced_history(collection, change):
    from addon.storage import update_results

    monday = date(2026, 9, 7)
    cid, ids = add_card(
        collection,
        [
            (monday, 3, 0, 2500),
            (monday + timedelta(days=2), 3, 1, 2500),
            (monday + timedelta(days=5), 1, 1, 2500),
        ],
    )
    first = update_results(collection)
    max_id = collection.db.scalar("select max(id) from revlog")
    if change == "edit":
        collection.db.execute("update revlog set ease=1 where id=?", ids[1])
    elif change == "delete":
        collection.db.execute("delete from revlog where id=?", ids[1])
    elif change == "late_insert":
        collection.db.execute(
            "insert into revlog values (?,?,?,?,?,?,?,?,?)",
            ids[1] + 86_400_000,
            cid,
            -1,
            3,
            3,
            3,
            2500,
            1000,
            1,
        )
    elif change == "parameters":
        preset = collection.decks.get_config(1)
        preset["ignoreRevlogsBeforeDate"] = "2026-09-08"
        collection.decks.update_config(preset)
    elif change == "deck":
        did = collection.decks.id("Other")
        collection.db.execute("update cards set did=? where id=?", did, cid)
    else:
        prefs = collection.get_preferences()
        prefs.scheduling.rollover = 14
        collection.set_preferences(prefs)
    assert collection.db.scalar("select max(id) from revlog") == max_id
    before = live_state(collection)
    updated = update_results(collection)
    assert updated.recalculated
    assert updated.report.generated_at != first.report.generated_at
    assert live_state(collection) == before
    assert not update_results(collection).recalculated


def test_fingerprint_ignores_content_and_sync_metadata_but_checks_engine_and_local_days(
    collection, monkeypatch
):
    from addon import backend
    from addon.metrics import Settings

    cid, _ids = add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    original = backend.input_fingerprint(collection)
    collection.db.execute("update revlog set usn=42, time=2000")
    collection.db.execute("update cards set usn=42, mod=42 where id=?", cid)
    collection.db.execute("update notes set flds='Changed question' || char(31) || 'Answer'")
    deck = collection.decks.get(1)
    deck["name"] = "Renamed"
    collection.decks.save(deck)
    assert backend.input_fingerprint(collection) == original
    assert backend.input_fingerprint(collection, Settings(minimum_reviews=40)) != original
    with monkeypatch.context() as patch:
        patch.setattr(backend, "CALCULATION_VERSION", backend.CALCULATION_VERSION + 1)
        assert backend.input_fingerprint(collection) != original
    original_day = backend.anki_day
    monkeypatch.setattr(backend, "anki_day", lambda *args: original_day(*args) + timedelta(days=1))
    assert backend.input_fingerprint(collection) != original


def test_fsrs_disabled_does_not_reuse_enabled_report(collection):
    from addon.storage import update_results

    update_results(collection)
    collection.set_config("fsrs", False)
    with pytest.raises(CompatibilityError, match="Enable FSRS"):
        update_results(collection)


def test_orphan_history_checks_reported_count_without_hashing_unused_fields(collection):
    from addon.backend import input_fingerprint

    conflicting = collection.decks.add_config("Different cutoff")
    conflicting["ignoreRevlogsBeforeDate"] = "2026-09-01"
    collection.decks.update_config(conflicting)
    cid, ids = add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    original = input_fingerprint(collection)
    orphan_id = ids[0] + 86_400_000
    collection.db.execute(
        "insert into revlog values (?,?,?,?,?,?,?,?,?)",
        orphan_id,
        cid + 1000,
        -1,
        3,
        3,
        3,
        2500,
        1000,
        1,
    )
    orphan = input_fingerprint(collection)
    assert orphan != original  # Excluded-review count increased.
    collection.db.execute(
        "update revlog set ease=4, ivl=20, factor=1500, id=id+1 where id=?", orphan_id
    )
    assert input_fingerprint(collection) == orphan
    collection.db.execute("update revlog set ease=0 where id=?", orphan_id + 1)
    assert input_fingerprint(collection) == original  # No longer counted as an answer.
    collection.db.execute("update revlog set cid=?, ease=3 where id=?", cid, orphan_id + 1)
    assert input_fingerprint(collection) not in (original, orphan)  # Restored history matters.


def test_native_dst_cutoff_phase_invalidates_cache_without_changing_history(
    collection, monkeypatch
):
    from addon.backend import input_fingerprint

    add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    scheduler_type = type(collection.sched)
    cutoff = collection.sched.day_cutoff
    before = input_fingerprint(collection)
    # An ordinary day with the same UTC cutoff phase reuses the history.
    monkeypatch.setattr(scheduler_type, "day_cutoff", property(lambda self: cutoff + 86_400))
    assert input_fingerprint(collection) == before
    # Anki's grouping of historical reviews can shift by an hour at DST.
    monkeypatch.setattr(scheduler_type, "day_cutoff", property(lambda self: cutoff + 82_800))
    assert input_fingerprint(collection) != before


def test_older_independent_snapshot_cannot_overwrite_newer_results(collection):
    from addon.storage import database_path, load_report, update_results

    monday = date(2026, 9, 7)
    cid, ids = add_card(collection, [(monday, 3, 0, 2500)])
    with collection_snapshot(collection) as older:
        collection.db.execute(
            "insert into revlog values (?,?,?,?,?,?,?,?,?)",
            ids[0] + 4 * 86_400_000,
            cid,
            -1,
            3,
            3,
            3,
            2500,
            1000,
            1,
        )
        with collection_snapshot(collection) as newer:
            latest = update_results(
                newer, source_collection_path=collection.path, snapshot_sequence=20
            )
        obsolete = update_results(
            older, source_collection_path=collection.path, snapshot_sequence=10
        )
    assert latest.report.days[0].formation.count == 1
    assert obsolete.report == latest.report and not obsolete.recalculated
    assert load_report(database_path(collection.path)) == latest.report
