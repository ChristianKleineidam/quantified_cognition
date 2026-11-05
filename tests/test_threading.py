"""Integration tests use Anki's real worker pool and Qt main-thread dispatcher."""

import csv
import os
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("aqt")
import aqt  # noqa: E402
from anki.collection import Collection  # noqa: E402
from aqt import gui_hooks  # noqa: E402
from aqt.operations import QueryOp  # noqa: E402
from aqt.qt import QApplication, QMainWindow, QTimer  # noqa: E402
from aqt.taskman import TaskManager  # noqa: E402
from test_storage import report_with_followup  # noqa: E402

from addon import daily, storage, ui, update_job  # noqa: E402
from addon.background import run_file_job  # noqa: E402
from addon.storage import (  # noqa: E402
    DailySettings,
    database_path,
    save_daily_settings,
    save_report,
)


def pump_until(app, predicate):
    # Native snapshots/replay perform real disk I/O, including on slower drives.
    # Responsiveness is checked separately by Qt ticks and concurrent answers.
    deadline = time.monotonic() + 30
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Background task did not complete")
        time.sleep(0.005)
    app.processEvents()


class MainWindow(QMainWindow):
    def __init__(self, collection):
        super().__init__()
        self.col = collection
        self.state = "deckBrowser"
        self.owner = threading.get_ident()
        self._background_op_count = 0
        self.taskman = TaskManager(self)
        self.config_threads = []
        self.addonManager = SimpleNamespace(getConfig=self.get_config)

    def weakref(self):
        return weakref.proxy(self)

    def get_config(self, _name):
        self.config_threads.append(threading.get_ident())
        return None

    def _increase_background_ops(self):
        assert threading.get_ident() == self.owner
        self._background_op_count += 1

    def _decrease_background_ops(self):
        assert threading.get_ident() == self.owner
        self._background_op_count -= 1


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def runtime(app, tmp_path, monkeypatch):
    collection = Collection(str(tmp_path / "collection.anki2"))
    collection.set_config("fsrs", True)
    assert collection.decks.get_deck_configs_for_update(1).fsrs
    main = MainWindow(collection)
    monkeypatch.setattr(aqt, "mw", main)
    monkeypatch.setattr(ui, "mw", main)
    notices = []
    monkeypatch.setattr(ui, "showWarning", lambda message, **kw: notices.append(message))
    yield main, notices
    main.taskman._collection_executor.shutdown(wait=True)
    main.taskman._no_collection_executor.shutdown(wait=True)
    app.processEvents()
    collection.close()
    main.close()


def test_file_worker_and_main_thread_callbacks_leave_qt_responsive(runtime, app):
    main, _ = runtime
    started, release = threading.Event(), threading.Event()
    task_threads, callback_threads, ticks = [], [], []

    def work():
        task_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return 42

    future = run_file_job(
        work,
        lambda result: callback_threads.append((threading.get_ident(), result)),
        lambda error: pytest.fail(str(error)),
    )
    try:
        pump_until(app, started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(threading.get_ident()))
        pump_until(app, lambda: bool(ticks))
        assert not future.done()
        assert main._background_op_count == 0  # File work does not block Anki's collection.
    finally:
        release.set()
    pump_until(app, lambda: bool(callback_threads))
    assert task_threads[0] != main.owner
    assert callback_threads == [(main.owner, 42)]
    assert ticks == [main.owner]


def test_file_failure_is_reported_on_the_main_thread(runtime, app):
    main, _ = runtime
    callbacks = []

    def fail():
        assert threading.get_ident() != main.owner
        raise OSError("Destination unavailable")

    run_file_job(
        fail,
        lambda result: pytest.fail("Unexpected success"),
        lambda error: callbacks.append((threading.get_ident(), str(error))),
    )
    pump_until(app, lambda: bool(callbacks))
    assert callbacks == [(main.owner, "Destination unavailable")]


def test_daily_checks_native_calculation_database_and_csv_use_worker(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    save_daily_settings(main.col.path, DailySettings(True, str(tmp_path / "daily.csv")))
    observed = {}

    def record(module, name):
        original = getattr(module, name)

        def wrapped(*args, **kwargs):
            observed[name] = threading.get_ident()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapped)

    for module, name in [
        (daily, "load_daily_settings"),
        (daily, "daily_update_due"),
        (storage, "build_report"),
        (storage, "save_report"),
        (storage, "export_csv_atomic"),
    ]:
        record(module, name)
    delivered = []
    updater = daily.DailyUpdater(
        main, "addon", lambda report: delivered.append(threading.get_ident())
    )
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert delivered == [main.owner]
        assert len(observed) == 5
        assert all(thread != main.owner for thread in observed.values())
        assert main.config_threads and all(t != main.owner for t in main.config_threads)
        assert database_path(main.col.path).is_file()
        assert (tmp_path / "daily.csv").is_file()
        assert not notices
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)


def test_manual_recalculation_and_saved_report_load_use_workers(runtime, app, monkeypatch):
    main, notices = runtime
    save_report(report_with_followup(), database_path(main.col.path))
    observed = []
    for module, name in ((ui, "load_report"), (update_job, "update_results")):
        original = getattr(module, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            observed.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapped)
    dialog = ui.MemoryReportDialog(main, "addon")
    dialog.show()
    assert not main.config_threads  # Constructor does not read add-on config on the UI thread.
    dialog.load_and_refresh()
    pump_until(app, lambda: not dialog.busy)
    assert {name for name, _ in observed} == {"load_report", "update_results"}
    assert all(thread != main.owner for _, thread in observed)
    assert not notices
    dialog.close()


def test_manual_csv_export_uses_file_worker(runtime, app, tmp_path, monkeypatch):
    main, notices = runtime
    destination = tmp_path / "manual.csv"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a, **kw: (str(destination), ""))
    successes, work_threads = [], []
    monkeypatch.setattr(
        ui, "showInfo", lambda message, **kw: successes.append(threading.get_ident())
    )
    original = ui.export_csv_atomic

    def export(*args, **kwargs):
        work_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(ui, "export_csv_atomic", export)
    dialog = ui.MemoryReportDialog(main, "addon")
    dialog.accept_report(report_with_followup())
    dialog.all_history.setChecked(True)
    dialog.export()
    pump_until(app, lambda: not dialog.busy)
    assert work_threads and work_threads[0] != main.owner
    assert successes == [main.owner]
    assert main._background_op_count == 0
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2
    assert not notices
    dialog.close()


def test_csv_failure_shows_fresh_scores_and_retry_uses_only_file_worker(
    runtime, app, tmp_path, monkeypatch
):
    from datetime import date

    from test_backend import add_card

    main, notices = runtime
    add_card(main.col, [(date(2026, 9, 7), 3, 0, 2500), (date(2026, 9, 11), 3, 1, 2500)])
    destination = tmp_path / "fixed.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    real_export = storage.export_csv_atomic

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    dialog = ui.MemoryReportDialog(main, "addon")
    dialog.show()
    dialog.refresh()
    pump_until(app, lambda: not dialog.busy)
    assert dialog.report.days
    assert dialog.report == storage.load_report(database_path(main.col.path))
    assert "Scores saved" in dialog.export_status.text()
    assert "CSV locked" in dialog.export_status.text()
    assert dialog.retry_button.isVisible()
    report = dialog.report

    def unexpected(*args, **kwargs):
        pytest.fail("File-only retry accessed collection inputs or reconstructed results")

    for name in ("input_fingerprint", "build_report", "save_report"):
        monkeypatch.setattr(storage, name, unexpected)
    work_threads = []

    def export(*args, **kwargs):
        work_threads.append(threading.get_ident())
        return real_export(*args, **kwargs)

    monkeypatch.setattr(storage, "export_csv_atomic", export)
    dialog.retry_csv()
    pump_until(app, lambda: not dialog.busy)
    assert work_threads and work_threads[0] != main.owner
    assert main._background_op_count == 0
    assert destination.is_file() and dialog.report == report
    assert not dialog.retry_button.isVisible()
    assert dialog.export_status.text() == "Fixed CSV updated."
    assert not notices
    dialog.close()


def test_daily_export_failure_delivers_report_and_retains_retry_backoff(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    save_daily_settings(main.col.path, DailySettings(True, str(tmp_path / "fixed.csv")))

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert len(delivered) == 1 and delivered[0].csv_error == "CSV locked"
        assert delivered[0].report == storage.load_report(database_path(main.col.path))
        assert updater.retry_after > time.monotonic()
        assert not notices
        assert "Memory scores are saved" in updater.last_error
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)


@pytest.mark.parametrize(
    "phase", ["input_fingerprint", "build_report", "save_report", "export_csv_atomic"]
)
def test_reviewing_can_answer_a_card_while_automatic_analysis_or_export_runs(
    runtime, app, tmp_path, monkeypatch, phase
):
    from datetime import date

    from anki.cards import Card
    from anki.scheduler.v3 import CardAnswer
    from test_backend import add_card

    main, notices = runtime
    add_card(main.col, [(date(2026, 9, 7), 3, 0, 2500), (date(2026, 9, 11), 3, 1, 2500)])
    # A separate new card is available for normal reviewing during the update.
    note = main.col.new_note(main.col.models.by_name("Basic"))
    note["Front"], note["Back"] = "Concurrent question", "Answer"
    main.col.add_note(note, 1)
    before = main.col.db.scalar("select count(*) from revlog")
    destination = tmp_path / "daily.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    started, release = threading.Event(), threading.Event()
    original = getattr(storage, phase)
    copy_paths = []
    original_copy = update_job.capture_snapshot

    def copied(col, path):
        assert col is main.col
        copy_paths.append(path)
        original_copy(col, path)

    def paused(*args, **kwargs):
        assert threading.get_ident() != main.owner
        if phase in {"input_fingerprint", "build_report"}:
            assert args[0] is not main.col
            assert args[0]._backend is not main.col._backend
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(update_job, "capture_snapshot", copied)
    monkeypatch.setattr(storage, phase, paused)
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    try:
        updater.check()
        pump_until(app, started.is_set)
        assert main._background_op_count == 0
        assert len(copy_paths) == 1  # No second snapshot inside build_report.
        main.state = "review"
        answers, failures = [], []

        def answer(col):
            queued = col.sched.get_queued_cards()
            top = queued.cards[0]
            card = Card(col, backend_card=top.card)
            card.start_timer()
            col.sched.answer_card(
                col.sched.build_answer(card=card, states=top.states, rating=CardAnswer.GOOD)
            )
            return col.db.scalar("select count(*) from revlog")

        QueryOp(parent=main, op=answer, success=answers.append).failure(
            failures.append
        ).run_in_background()
        pump_until(app, lambda: bool(answers or failures))
        assert not failures
        assert answers == [before + 1]
        assert not release.is_set() and updater.busy
    finally:
        release.set()
        pump_until(app, lambda: not updater.busy)
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)
    assert delivered and destination.is_file()
    assert main.col.db.scalar("select count(*) from revlog") == before + 1
    assert not copy_paths[0].parent.exists()
    assert not notices


def test_profile_close_cancels_independent_analysis_without_publishing(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    destination = tmp_path / "daily.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    started, release = threading.Event(), threading.Event()
    original = storage.input_fingerprint

    def pause(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "input_fingerprint", pause)
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    try:
        updater.check()
        pump_until(app, started.is_set)
        job = updater.job
        directory = job.snapshot_path.parent
        updater.stop()  # Cancels a normal job; shutdown handoff is tested separately.
        assert job.cancelled.is_set()
        main.col = None
    finally:
        release.set()
        pump_until(app, lambda: job.directory is None)
        main.col = job.collection
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)
    assert not delivered and not destination.exists()
    assert storage.load_report(database_path(main.col.path)) is None
    assert not directory.exists() and not notices


def test_automatic_csv_retry_needs_no_collection_queue_even_during_review(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    destination = tmp_path / "daily.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    real_export = storage.export_csv_atomic

    def locked(*args, **kwargs):
        raise PermissionError("CSV locked")

    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert updater.last_error and len(delivered) == 1

        def unexpected(*args, **kwargs):
            pytest.fail("CSV retry queued collection work or rebuilt the analysis")

        monkeypatch.setattr(daily, "UpdateJob", unexpected)
        for name in ("input_fingerprint", "build_report", "save_report"):
            monkeypatch.setattr(storage, name, unexpected)
        monkeypatch.setattr(storage, "export_csv_atomic", real_export)
        main.state = "review"
        main._background_op_count = 1
        updater.retry_after = 0
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert main._background_op_count == 1
        assert destination.is_file() and len(delivered) == 2
        assert delivered[1].csv_exported and not delivered[1].recalculated
        assert updater.last_error is None and not notices
    finally:
        main._background_op_count = 0
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)


def test_review_started_during_preparation_defers_copy_and_cleans_temp_files(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    save_daily_settings(main.col.path, DailySettings(True, str(tmp_path / "daily.csv")))
    started, release = threading.Event(), threading.Event()
    original = daily.daily_update_due

    def pause(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def unexpected(*args, **kwargs):
        pytest.fail("Automatic copying must wait while reviewing")

    monkeypatch.setattr(daily, "daily_update_due", pause)
    monkeypatch.setattr(update_job, "capture_snapshot", unexpected)
    updater = daily.DailyUpdater(main, "addon")
    try:
        updater.check()
        pump_until(app, started.is_set)
        job = updater.job
        main.state = "review"
    finally:
        release.set()
        pump_until(app, lambda: not updater.busy)
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)
    assert job.snapshot_path is not None
    assert not job.snapshot_path.parent.exists()
    assert not database_path(main.col.path).exists()
    assert updater.last_error is None and not notices


def test_failed_copy_cleans_partial_snapshot_and_releases_collection_queue(
    runtime, app, tmp_path, monkeypatch
):
    main, notices = runtime
    save_daily_settings(main.col.path, DailySettings(True, str(tmp_path / "daily.csv")))
    paths = []

    def broken(col, path):
        paths.append(path)
        path.write_text("partial snapshot", encoding="utf-8")
        raise OSError("No space for snapshot")

    monkeypatch.setattr(update_job, "capture_snapshot", broken)
    updater = daily.DailyUpdater(main, "addon")
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert main._background_op_count == 0
        assert updater.last_error == "No space for snapshot"
        assert updater.retry_after > time.monotonic()
        assert paths and not paths[0].parent.exists()
        assert not notices
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)


def test_final_card_refreshes_existing_daily_csv_without_user_action(runtime, app, tmp_path):
    from anki.cards import Card
    from anki.scheduler.v3 import CardAnswer

    main, notices = runtime
    destination = tmp_path / "daily.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(destination)))
    delivered = []
    updater = daily.DailyUpdater(main, "addon", delivered.append)
    updater.timer.start()
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert destination.is_file() and len(delivered) == 1
        assert not delivered[0].report.days
        for index in range(2):
            note = main.col.new_note(main.col.models.by_name("Basic"))
            note["Front"], note["Back"] = f"Question {index}", "Answer"
            main.col.add_note(note, 1)
        main.state = "review"
        main.reviewer = SimpleNamespace(card=object())

        def answer(col):
            top = col.sched.get_queued_cards().cards[0]
            card = Card(col, backend_card=top.card)
            card.start_timer()
            col.sched.answer_card(
                col.sched.build_answer(
                    card=card,
                    states=top.states,
                    rating=CardAnswer.EASY,
                )
            )
            return bool(col.sched.get_queued_cards().cards)

        for expected_remaining in (True, False):
            remaining = []
            QueryOp(parent=main, op=answer, success=remaining.append).failure(
                lambda error: pytest.fail(str(error))
            ).run_in_background()
            pump_until(app, lambda values=remaining: bool(values))
            assert remaining == [expected_remaining]
            gui_hooks.reviewer_did_answer_card(main.reviewer, main.reviewer.card, 4)
            if expected_remaining:
                assert not updater.busy and len(delivered) == 1
        # This is Anki's normal nextCard()/cleanup/state-transition ordering.
        main.reviewer.card = None
        gui_hooks.reviewer_will_end()
        main.state = "overview"
        gui_hooks.state_did_change("overview", "review")
        pump_until(app, lambda: not updater.busy)
        assert len(delivered) == 2 and delivered[-1].csv_exported
        assert delivered[-1].report.analyzed_cards == 2
        with destination.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["recall_cohort_count"] == "2"
        assert rows[0]["formation_pending_count"] == "2"
        assert not notices
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.close_profile)
        gui_hooks.state_did_change.remove(updater.state_changed)
        gui_hooks.reviewer_did_answer_card.remove(updater.card_answered)
        gui_hooks.reviewer_will_end.remove(updater.review_ending)
        gui_hooks.sync_will_start.remove(updater.sync_starting)
        gui_hooks.sync_did_finish.remove(updater.sync_finished)


def test_profile_preferences_read_and_write_use_file_workers(runtime, app, tmp_path, monkeypatch):
    main, notices = runtime
    observed = []
    for module, name in ((ui, "load_daily_settings"), (ui, "save_daily_settings")):
        original = getattr(module, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            observed.append((_name, threading.get_ident()))
            return _original(*args, **kwargs)

        monkeypatch.setattr(ui, name, wrapped)
    dialog = ui.DailySettingsDialog(main, main.col.path)
    dialog.show()
    pump_until(app, lambda: not dialog.file_busy)
    dialog.enabled.setChecked(True)
    dialog.csv_input.setText(str(tmp_path / "configured.csv"))
    dialog.save()
    pump_until(app, lambda: not dialog.file_busy)
    assert dialog.result() == dialog.DialogCode.Accepted
    assert {name for name, _ in observed} == {"load_daily_settings", "save_daily_settings"}
    assert all(thread != main.owner for _, thread in observed)
    assert not notices


def test_closing_report_during_export_is_safe(runtime, app, tmp_path, monkeypatch):
    main, notices = runtime
    destination = tmp_path / "closed-window.csv"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *a, **kw: (str(destination), ""))
    monkeypatch.setattr(ui, "showInfo", lambda *a, **kw: pytest.fail("Closed window got a popup"))
    started, release = threading.Event(), threading.Event()
    original = ui.export_csv_atomic

    def export(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(ui, "export_csv_atomic", export)
    dialog = ui.MemoryReportDialog(main, "addon")
    dialog.accept_report(report_with_followup())
    dialog.show()
    dialog.export()
    try:
        pump_until(app, started.is_set)
        dialog.close()
        assert dialog.close_when_finished
        assert not dialog.disposed
    finally:
        release.set()
    pump_until(app, lambda: not dialog.busy)
    assert destination.is_file()
    assert not notices


def test_saved_report_load_cannot_cross_profiles(runtime, app, tmp_path, monkeypatch):
    main, notices = runtime
    started, release = threading.Event(), threading.Event()

    def load(_path):
        started.set()
        assert release.wait(3)
        return report_with_followup()

    monkeypatch.setattr(ui, "load_report", load)
    dialog = ui.MemoryReportDialog(main, "addon")
    dialog.show()
    dialog.load_and_refresh()
    try:
        pump_until(app, started.is_set)
        collection = main.col
        main.col = SimpleNamespace(path=str(tmp_path / "other-profile" / "collection.anki2"))
    finally:
        release.set()
    pump_until(app, lambda: not dialog.busy)
    assert dialog.report is None
    assert dialog.disposed or not dialog.isVisible()
    assert not notices
    main.col = collection


def test_oura_api_sync_and_combined_export_use_only_file_workers(
    runtime, app, tmp_path, monkeypatch
):
    from test_oura import sleep

    from addon import health, oura_ui
    from addon.oura import parse_oura_records
    from addon.research import SharingConfig, sharing_state

    main, notices = runtime
    source = main.col.path
    config = SharingConfig()
    sharing_state(source, config, event_type="sharing_choice", choice="opt_in")
    destination = tmp_path / "combined.csv"
    save_daily_settings(source, DailySettings(True, str(destination)))
    save_report(report_with_followup(), database_path(source))
    threads = []

    def synced(source, services, sharing, *, force, check_cancelled):
        threads.append(threading.get_ident())
        check_cancelled()
        return health.save_oura_import(
            source,
            parse_oura_records([sleep()], [{"day": "2026-09-07", "steps": 0}]),
            sharing=sharing,
        )

    monkeypatch.setattr(oura_ui, "sync_oura", synced)
    exported = tmp_path / "manual.csv"
    monkeypatch.setattr(
        oura_ui.QFileDialog, "getSaveFileName", lambda *a, **kw: (str(exported), "")
    )
    for name in ("export_csv_atomic",):
        original = getattr(oura_ui, name)

        def observed(*args, _original=original, **kwargs):
            threads.append(threading.get_ident())
            return _original(*args, **kwargs)

        monkeypatch.setattr(oura_ui, name, observed)

    def unexpected(*args, **kwargs):
        pytest.fail("Oura import queued collection work or replayed FSRS")

    monkeypatch.setattr(storage, "input_fingerprint", unexpected)
    monkeypatch.setattr(storage, "build_report", unexpected)
    dialog = oura_ui.OuraDialog(main, config)
    dialog.show()
    main.state = "review"
    main._background_op_count = 1  # Other Anki work does not prevent file imports.
    try:
        dialog.load()
        pump_until(app, lambda: not dialog.busy)
        dialog.sync()
        pump_until(app, lambda: not dialog.busy)
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "7h 00m"
        assert dialog.table.item(0, 7).text() == "0"
        assert destination.is_file()
        assert main._background_op_count == 1
        assert sharing_state(source).metadata()["research_consent_before_first_view"] is True
        dialog.export()
        pump_until(app, lambda: not dialog.busy)
        with exported.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["oura_total_sleep_seconds"] == "25200"
        assert all(thread != main.owner for thread in threads) and len(threads) == 2
        assert not notices
    finally:
        main._background_op_count = 0
        dialog.close()


def test_closing_during_oura_api_sync_cancels_without_importing(
    runtime, app, tmp_path, monkeypatch
):
    from addon import health, oura_ui
    from addon.research import SharingConfig

    main, _ = runtime
    started, release = threading.Event(), threading.Event()

    def pause(*args, **kwargs):
        started.set()
        assert release.wait(5)
        kwargs["check_cancelled"]()

    monkeypatch.setattr(oura_ui, "sync_oura", pause)
    dialog = oura_ui.OuraDialog(main, SharingConfig())
    dialog.show()
    dialog.sync()
    try:
        pump_until(app, started.is_set)
        dialog.close()
        assert not dialog.isVisible() and dialog.cancelled.is_set()
        assert dialog.busy
    finally:
        release.set()
    # The worker owns its input until completion; then Qt can delete the dialog.
    main.taskman._no_collection_executor.shutdown(wait=True)
    app.processEvents()
    assert not health.health_database_path(main.col.path).exists()


def test_network_sync_leaves_qt_and_real_reviews_available(runtime, app, monkeypatch):
    from anki.cards import Card
    from anki.scheduler.v3 import CardAnswer

    from addon import network

    main, notices = runtime
    started, release = threading.Event(), threading.Event()
    threads, ticks = [], []
    note = main.col.new_note(main.col.models.current())
    note.fields[0] = "Synthetic network concurrency check"
    main.col.add_note(note, 1)
    main.state = "review"
    main.addonManager.getConfig = lambda name: {
        "services": {"base_url": "https://example.supabase.co/functions/v1/qs-service"}
    }

    def sync(*args, **kwargs):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(network, "sync_oura", sync)
    updater = network.NetworkUpdater(main, "addon")
    try:
        updater.check()
        pump_until(app, started.is_set)
        QTimer.singleShot(0, lambda: ticks.append(threading.get_ident()))
        pump_until(app, lambda: bool(ticks))
        answered = []

        def answer(col):
            top = col.sched.get_queued_cards().cards[0]
            card = Card(col, backend_card=top.card)
            card.start_timer()
            col.sched.answer_card(
                col.sched.build_answer(card=card, states=top.states, rating=CardAnswer.GOOD)
            )
            return col.db.scalar("select count(*) from revlog")

        QueryOp(parent=main, op=answer, success=answered.append).run_in_background()
        pump_until(app, lambda: bool(answered))
        assert updater.busy and answered == [1] and ticks == [main.owner]
        assert threads[0] != main.owner and not notices
        updater.stop()  # Late completion must not change a new profile's state.
    finally:
        release.set()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.stop)
    main.taskman._no_collection_executor.shutdown(wait=True)
    app.processEvents()


def test_research_preview_and_explicit_send_run_off_qt(runtime, app, monkeypatch):
    from addon import submission_ui
    from addon.research import SharingConfig
    from addon.service import ServiceConfig

    main, _ = runtime
    threads, sent = [], []
    package = {"submission_id": "synthetic", "rows": [["2026-10-01"], ["2026-10-02"]]}

    def prepare(*args, **kwargs):
        threads.append(threading.get_ident())
        return {"package": package}, {
            "operator": "Synthetic study",
            "privacy_url": "https://example.org/privacy",
        }

    def send(*args, **kwargs):
        threads.append(threading.get_ident())
        sent.append(args[-1])
        return {"submission_id": "synthetic"}

    monkeypatch.setattr(submission_ui, "prepare_submission", prepare)
    monkeypatch.setattr(submission_ui, "upload_submission", send)
    dialog = submission_ui.SubmissionDialog(
        main, main.col.path, ServiceConfig("https://example.org/service"), SharingConfig()
    )
    dialog.show()
    try:
        dialog.prepare()
        pump_until(app, lambda: not dialog.busy)
        assert not sent and dialog.send.isEnabled()
        assert "2 days" in dialog.summary.text() and "Synthetic study" in dialog.summary.text()
        dialog.send.click()
        pump_until(app, lambda: not dialog.busy)
        assert sent == ["synthetic"] and not dialog.send.isEnabled()
        assert all(thread != main.owner for thread in threads)
        assert main._background_op_count == 0
    finally:
        dialog.close()


def test_google_health_selector_and_background_sync_export_leave_collection_available(
    runtime, app, tmp_path, monkeypatch
):
    from test_google_health import night, parsed, steps
    from test_oura import sleep

    from addon import health, oura_ui
    from addon.oura import parse_oura_records
    from addon.research import SharingConfig, sharing_state

    main, notices = runtime
    source = main.col.path
    health.save_oura_import(source, parse_oura_records([sleep(day="2026-08-01")], []))
    sharing_state(source, event_type="sharing_choice", choice="opt_in")
    threads, started, release = [], threading.Event(), threading.Event()

    def sync(path, services, sharing, **kwargs):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        kwargs["check_cancelled"]()
        return health.save_google_health_import(
            path,
            parsed([night()], [steps(count="0")]),
            start="2026-09-01",
            end="2026-10-01",
            sharing=sharing,
        )

    monkeypatch.setattr(oura_ui, "sync_google_health", sync)
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    destination = tmp_path / "combined-google.csv"
    monkeypatch.setattr(
        oura_ui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), "")
    )
    dialog = oura_ui.OuraDialog(main, SharingConfig())
    dialog.show()
    try:
        dialog.provider_selector.setCurrentIndex(1)
        pump_until(app, lambda: not dialog.busy)
        assert dialog.table.rowCount() == 0  # Oura-only dates do not become blank Google rows.
        assert dialog.choose_button.text() == "Connect Google Health…"
        assert dialog.history_start.isVisible()
        dialog.sync()
        pump_until(app, started.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(threading.get_ident()))
        pump_until(app, lambda: bool(ticks))
        assert main._background_op_count == 0 and dialog.busy
        assert ticks == [main.owner] and threads[0] != main.owner
        release.set()
        pump_until(app, lambda: not dialog.busy)
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 1).text() == "7h 00m"
        assert dialog.table.item(0, 7).text() == "0"
        dialog.export()
        pump_until(app, lambda: not dialog.busy)
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            rows = {row["date"]: row for row in csv.DictReader(stream)}
        assert rows["2026-09-07"]["google_health_steps"] == "0"
        assert rows["2026-08-01"]["oura_total_sleep_seconds"] == "25200"
        assert "recall_score_pp" in rows["2026-09-07"]
        assert not notices
    finally:
        release.set()
        dialog.close()


def test_network_oura_failure_does_not_suppress_google_health(runtime, app, monkeypatch):
    from addon import network

    main, _ = runtime
    main.addonManager.getConfig = lambda name: {
        "services": {"base_url": "https://example.org/service"}
    }
    calls = []

    def failed(*args, **kwargs):
        calls.append("oura")
        raise OSError("Offline")

    def google(*args, **kwargs):
        calls.append("google")
        assert threading.get_ident() != main.owner

    monkeypatch.setattr(network, "sync_oura", failed)
    monkeypatch.setattr(network, "sync_google_health", google)
    updater = network.NetworkUpdater(main, "addon")
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert calls == ["oura", "google"]
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.stop)


def test_insights_show_both_nights_use_full_history_and_do_not_block_reviews(
    runtime, app, tmp_path, monkeypatch
):
    from anki.cards import Card
    from anki.scheduler.v3 import CardAnswer
    from test_insights import inputs

    from addon import insights_ui
    from addon.research import SharingConfig, sharing_state

    main, notices = runtime
    source = main.col.path
    sharing_state(source, event_type="sharing_choice", choice="opt_in")
    report, health = inputs(180)
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda path: health)
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    started, release, threads = threading.Event(), threading.Event(), []
    original = insights_ui.load_insights

    def load(*args, **kwargs):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(insights_ui, "load_insights", load)
    exported = tmp_path / "insights.csv"
    monkeypatch.setattr(
        insights_ui.QFileDialog, "getSaveFileName", lambda *a, **k: (str(exported), "")
    )
    note = main.col.new_note(main.col.models.current())
    note.fields[0] = "Synthetic insight concurrency test"
    main.col.add_note(note, 1)
    main.state = "review"
    dialog = insights_ui.InsightsDialog(main, "addon", SharingConfig())
    dialog.show()
    try:
        assert dialog.period.currentData() is None
        dialog.load()
        pump_until(app, started.is_set)
        ticks, answered = [], []
        QTimer.singleShot(0, lambda: ticks.append(threading.get_ident()))
        pump_until(app, lambda: bool(ticks))

        def answer(col):
            top = col.sched.get_queued_cards().cards[0]
            card = Card(col, backend_card=top.card)
            card.start_timer()
            col.sched.answer_card(
                col.sched.build_answer(card=card, states=top.states, rating=CardAnswer.GOOD)
            )
            return col.db.scalar("select count(*) from revlog")

        QueryOp(parent=main, op=answer, success=answered.append).run_in_background()
        pump_until(app, lambda: bool(answered))
        assert dialog.busy and answered == [1] and ticks == [main.owner]
        assert main._background_op_count == 0 and threads[0] != main.owner
        release.set()
        pump_until(app, lambda: not dialog.busy)
        assert dialog.table.columnCount() == 8 and dialog.table.rowCount() == 3
        assert dialog.table.item(0, 0).text() == "Light → deep"
        assert not dialog.chart.isVisible()
        assert "completed study days" in dialog.summary.text()
        result = dialog.result
        dialog.all_button.setChecked(True)
        assert dialog.result is result and not dialog.busy
        assert dialog.table.rowCount() == 6
        assert not dialog.table.isColumnHidden(2) and not dialog.table.isColumnHidden(3)
        assert dialog.result.associations["deep_sleep/recall/before"].days == 180
        dialog.table.setCurrentCell(2, 2)
        assert "Main sleep before" in dialog.detail.text()
        dialog.table.setCurrentCell(2, 3)
        assert "Main sleep after" in dialog.detail.text()
        assert dialog.table.columnSpan(5, 2) == 2
        result = dialog.result
        dialog.section.setCurrentIndex(1)
        assert dialog.result is result and not dialog.busy
        assert dialog.table.rowCount() == 6 and dialog.table.columnCount() == 8
        assert dialog.table.item(1, 0).text() == "Nap duration"
        assert dialog.table.item(3, 0).text() == "Deep sleep in naps"
        assert dialog.table.item(4, 0).text() == "REM sleep in naps"
        assert "After studying" in dialog.table.horizontalHeaderItem(3).text()
        dialog.table.setCurrentCell(1, 3)
        assert "insufficient data" in dialog.detail.text().lower()
        assert "nap days only" in dialog.note.text()
        dialog.all_button.setChecked(False)
        dialog.section.setCurrentIndex(2)
        assert dialog.result is result and not dialog.busy
        assert dialog.table.rowCount() == 6
        assert dialog.table.item(0, 0).text() == "Recorded workout"
        assert dialog.table.item(1, 0).text() == "Workout duration"
        assert "24 hours" in dialog.note.text()
        assert "insufficient data" in dialog.detail.text().lower()
        assert "individual associations" in dialog.summary.text()
        assert dialog.chart.isVisible() and not dialog.all_button.isVisible()
        assert sharing_state(source).metadata()["research_consent_before_first_view"] is True
        dialog.export()
        pump_until(app, lambda: not dialog.busy)
        with exported.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert any(
            row["timing"] == "after" and row["metric"] == "formation_long_term" for row in rows
        )
        assert not notices
    finally:
        release.set()
        dialog.close()


def test_closing_insights_cancels_statistics_without_exposing_results(runtime, app, monkeypatch):
    from addon import insights_ui
    from addon.research import SharingConfig, sharing_state

    main, _ = runtime
    source = main.col.path
    sharing_state(source, event_type="sharing_choice", choice="opt_in")
    started, release = threading.Event(), threading.Event()

    def load(*args, **kwargs):
        started.set()
        assert release.wait(10)
        kwargs["check_cancelled"]()

    monkeypatch.setattr(insights_ui, "load_insights", load)
    dialog = insights_ui.InsightsDialog(main, "addon", SharingConfig())
    dialog.show()
    dialog.load()
    try:
        pump_until(app, started.is_set)
        dialog.close()
        assert dialog.busy and not dialog.isVisible()
        assert dialog.cancelled.is_set()
    finally:
        release.set()
    main.taskman._no_collection_executor.shutdown(wait=True)
    app.processEvents()
    assert sharing_state(source).first_exposure is None
    from addon.insight_visits import _path

    assert not _path(source).exists()


def test_returning_insights_show_changes_from_previous_visit_not_previous_refresh(
    runtime, app, monkeypatch
):
    from dataclasses import replace
    from datetime import timedelta

    from test_insights import daily, inputs

    from addon import insights_ui
    from addon.health import DailyHealth
    from addon.research import SharingConfig, sharing_state

    main, _ = runtime
    source = main.col.path
    sharing_state(source, event_type="sharing_choice", choice="declined")
    report, health = inputs(180)
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda source: health)
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    first = insights_ui.InsightsDialog(main, "addon", SharingConfig())
    first.show()
    first.load()
    pump_until(app, lambda: not first.busy)
    assert "First visit" in first.changes.text()
    assert first.visit_futures[-1].result(5)
    first.all_button.setChecked(True)
    assert not first.changes.isVisible()
    assert first.visit_revision == 1  # Rendering a different view is still this visit.
    first.close()

    old = report.days[0]
    mature = replace(
        old,
        formation=replace(old.formation, count=100, pending_count=0),
        formation_short_term=replace(old.formation_short_term, count=60, cohort_count=60),
    )
    new_day = report.days[-1].day + timedelta(days=1)
    report = replace(report, days=[mature, *report.days[1:], daily(new_day)])
    health.days[new_day] = DailyHealth(dict(health.days[old.day].values))
    storage.save_report(report, storage.database_path(source))
    returning = insights_ui.InsightsDialog(main, "addon", SharingConfig())
    returning.show()
    try:
        returning.load()
        pump_until(app, lambda: not returning.busy)
        assert "Since your last visit" in returning.changes.text()
        assert returning.changes.isVisible()
        assert "1 added study days" in returning.changes.text()
        assert "10 fewer pending" in returning.changes.text()
        assert returning.visit_futures[-1].result(5)
        returning.load()
        pump_until(app, lambda: not returning.busy)
        assert "1 added study days" in returning.changes.text()
        assert returning.result.reused
        assert returning.visit_futures[-1].result(5)
    finally:
        returning.close()


def test_open_insights_reloads_after_background_health_sync(runtime, app, monkeypatch):
    from types import SimpleNamespace

    from addon import network

    main, _ = runtime
    main.addonManager.getConfig = lambda name: {
        "services": {"base_url": "https://example.org/service"}
    }
    notifications = []
    monkeypatch.setattr(ui, "notify_insights_updated", notifications.append)
    monkeypatch.setattr(network, "sync_oura", lambda *a, **k: SimpleNamespace(changed_records=2))
    monkeypatch.setattr(network, "sync_google_health", lambda *a, **k: None)
    updater = network.NetworkUpdater(main, "addon")
    try:
        updater.check()
        pump_until(app, lambda: not updater.busy)
        assert notifications == [main.col.path]
    finally:
        updater.stop()
        gui_hooks.profile_did_open.remove(updater.start)
        gui_hooks.profile_will_close.remove(updater.stop)


def test_manual_memory_update_refreshes_insights_but_csv_retry_does_not(runtime, app, monkeypatch):
    main, _ = runtime
    notifications = []
    monkeypatch.setattr(ui, "notify_insights_updated", notifications.append)
    dialog = ui.MemoryReportDialog(main, "addon")
    try:
        dialog.accept_update(storage.UpdateResult(report_with_followup(), recalculated=True))
        assert notifications == [main.col.path]
        dialog.accept_update(storage.UpdateResult(report_with_followup(), recalculated=False))
        assert notifications == [main.col.path]
    finally:
        dialog.close()


def test_insights_entry_checks_native_memory_history_without_extra_user_action(runtime, app):
    from addon.research import sharing_state

    main, _ = runtime
    sharing_state(main.col.path, event_type="sharing_choice", choice="opt_in")
    opened = None
    try:
        ui.open_personal_insights("addon")
        pump_until(app, lambda: ui._insights_dialog is not None)
        opened = ui._insights_dialog
        pump_until(app, lambda: opened.result is not None and not opened.busy)
        assert opened.result.memory_generated_at
        assert opened.period.currentData() is None
        assert opened.table.columnCount() == 8
        assert all(
            result.status == "insufficient data" for result in opened.result.associations.values()
        )
    finally:
        if opened is not None:
            opened.close()
        ui._insights_dialog = None
