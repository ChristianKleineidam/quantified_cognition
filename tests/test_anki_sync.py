"""Synced review history must refresh today's export, including closing sync."""

import csv
import threading
from datetime import date, timedelta

import pytest
from aqt import gui_hooks
from test_backend import add_card
from test_threading import app as thread_app
from test_threading import pump_until
from test_threading import runtime as thread_runtime

from addon import daily
from addon.storage import DailySettings, save_daily_settings

app = thread_app
runtime = thread_runtime


@pytest.fixture
def sync_updater(runtime, tmp_path):
    main, _notices = runtime
    destination = tmp_path / "synced.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    yield main, updater, destination, delivered
    updater.stop()
    for hook, method in (
        (gui_hooks.profile_did_open, updater.start),
        (gui_hooks.profile_will_close, updater.close_profile),
        (gui_hooks.state_did_change, updater.state_changed),
        (gui_hooks.reviewer_did_answer_card, updater.card_answered),
        (gui_hooks.reviewer_will_end, updater.review_ending),
    ):
        hook.remove(method)
    for name, method_name in (("sync_will_start", "sync_starting"),
                              ("sync_did_finish", "sync_finished")):
        if hasattr(updater, method_name):
            getattr(gui_hooks, name).remove(getattr(updater, method_name))


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_newly_synced_history_refreshes_an_already_exported_day(sync_updater, app):
    main, updater, destination, delivered = sync_updater
    start = date.today() - timedelta(days=50)
    add_card(main.col, [(start, 3, 0, 2500), (start + timedelta(days=4), 3, 1, 2500)])
    updater.check()
    pump_until(app, lambda: len(delivered) == 1)
    before = len(rows(destination))
    gui_hooks.sync_will_start()
    # This is the native database edit a completed sync imports. This hook-level
    # regression makes no claim about real AnkiWeb transport or authentication.
    add_card(main.col, [(start + timedelta(days=20), 3, 0, 2500),
                       (start + timedelta(days=25), 3, 1, 2500)])
    gui_hooks.sync_did_finish()
    for _ in range(20):
        app.processEvents()
    assert updater.busy or len(delivered) == 2, "Sync did not check today's saved analysis"
    pump_until(app, lambda: len(delivered) == 2)
    assert delivered[-1].recalculated
    assert len(rows(destination)) > before


def test_unchanged_sync_reuses_saved_calculation(sync_updater, app, monkeypatch):
    from addon import storage

    _main, updater, _destination, delivered = sync_updater
    updater.check()
    pump_until(app, lambda: len(delivered) == 1)

    def unnecessary(*args, **kwargs):
        pytest.fail("An unchanged sync must reuse the saved FSRS calculation")

    monkeypatch.setattr(storage, "build_report", unnecessary)
    gui_hooks.sync_will_start()
    gui_hooks.sync_did_finish()
    for _ in range(20):
        app.processEvents()
    assert updater.busy or len(delivered) == 2, "Sync did not check for changed history"
    pump_until(app, lambda: len(delivered) == 2)
    assert not delivered[-1].recalculated


def test_closing_sync_preserves_imported_history_without_local_answers(sync_updater, app):
    main, updater, destination, delivered = sync_updater
    start = date.today() - timedelta(days=50)
    add_card(main.col, [(start, 3, 0, 2500), (start + timedelta(days=4), 3, 1, 2500)])
    updater.check()
    pump_until(app, lambda: len(delivered) == 1)
    before = len(rows(destination))
    updater.close_profile()  # Anki emits this before its optional closing sync.
    gui_hooks.sync_will_start()
    add_card(main.col, [(start + timedelta(days=20), 3, 0, 2500),
                       (start + timedelta(days=25), 3, 1, 2500)])
    gui_hooks.sync_did_finish()
    assert main.col._qs_close_export_armed
    existing = set(threading.enumerate())
    main.col.close()
    try:
        workers = [t for t in threading.enumerate() if t not in existing
                   and t.name == "Quantified Cognition final export"]
        for worker in workers:
            worker.join(20)  # Deliver no Qt callbacks after the collection closes.
            assert not worker.is_alive()
        assert len(rows(destination)) > before
    finally:
        main.col.reopen()


def test_synced_calculation_csv_failure_retries_without_another_scan(
    sync_updater, app, monkeypatch,
):
    from addon import storage

    main, updater, _destination, delivered = sync_updater
    updater.check()
    pump_until(app, lambda: len(delivered) == 1)
    original = storage.export_fixed_csv

    def locked(*args, **kwargs):
        raise PermissionError("Synthetic CSV lock")

    monkeypatch.setattr(storage, "export_fixed_csv", locked)
    gui_hooks.sync_will_start()
    add_card(main.col, [(date.today() - timedelta(days=5), 3, 0, 2500)])
    gui_hooks.sync_did_finish()
    pump_until(app, lambda: len(delivered) == 2)
    assert delivered[-1].csv_error
    monkeypatch.setattr(storage, "export_fixed_csv", original)

    def unnecessary(*args, **kwargs):
        pytest.fail("Retrying a saved synced report must not rescan review history")

    monkeypatch.setattr(storage, "input_fingerprint", unnecessary)
    updater.retry_after = 0
    updater.check()
    pump_until(app, lambda: len(delivered) == 3)
    assert delivered[-1].csv_exported and not delivered[-1].recalculated
    assert updater.exported_sync_serial == updater.sync_serial
