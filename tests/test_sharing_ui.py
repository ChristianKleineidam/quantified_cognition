"""Exercise the actual consent gate, background saves and pre-display barrier."""

import threading

import pytest

pytest.importorskip("aqt")
from aqt.progress import ProgressManager  # noqa: E402
from aqt.qt import QEvent, QKeyEvent, Qt, QTimer  # noqa: E402
from test_storage import report_with_followup  # noqa: E402
from test_threading import app as thread_app  # noqa: E402
from test_threading import pump_until
from test_threading import runtime as thread_runtime

from addon import sharing_ui, ui  # noqa: E402
from addon.research import SharingConfig, sharing_state  # noqa: E402
from addon.service import ServiceError  # noqa: E402
from addon.storage import database_path, save_report  # noqa: E402

app = thread_app
runtime = thread_runtime


@pytest.fixture(autouse=True)
def install_progress_manager(runtime, app):
    main, _notices = runtime
    main.app = app
    main.progress = ProgressManager(main)


@pytest.mark.parametrize("choice", ["opt_in", "declined", "deferred"])
def test_prompt_precedes_any_data_and_every_choice_allows_results(
    runtime, app, monkeypatch, choice
):
    main, notices = runtime
    prompts, displayed = [], []
    original_open = sharing_ui.SharingDialog.open

    def answer(dialog):
        state = sharing_state(main.col.path)
        assert state.first_exposure is None
        assert state.current_choice is None
        assert dialog.buttons[1].isDefault()
        assert not dialog.buttons[0].isDefault()
        prompts.append(threading.get_ident())
        QTimer.singleShot(0, lambda: dialog.choose(choice))
        original_open(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "open", answer)
    dialogs = []

    def ready(config):
        state = sharing_state(main.col.path, config)
        assert state.current_choice == choice
        assert state.first_exposure is None
        dialog = ui.MemoryReportDialog(main, "addon", sharing=config)
        dialogs.append(dialog)
        dialog.show()
        dialog.accept_report(report_with_followup(), after_display=lambda: displayed.append(True))
        assert dialog.report is None  # Exposure audit must commit before values reach widgets.

    sharing_ui.sharing_gate(main, "addon", ready)
    pump_until(app, lambda: bool(displayed))
    state = sharing_state(main.col.path)
    assert prompts == [main.owner]
    assert state.metadata()["research_consent_active"] == (choice == "opt_in")
    assert state.metadata()["research_consent_before_first_view"] == (
        True if choice == "opt_in" else None
    )
    assert state.first_exposure["surface"] == "report"
    assert not notices
    dialogs[0].close()


def test_later_opt_in_and_forced_reopen_preserve_first_decline(runtime, app, monkeypatch):
    main, _ = runtime
    sharing_state(main.col.path, event_type="sharing_choice", choice="declined")
    sharing_state(main.col.path, event_type="results_exposed", surface="report")
    original_open = sharing_ui.SharingDialog.open
    prompts, finished = [], []

    def answer(dialog):
        assert dialog.snapshot.current_choice == "declined"
        prompts.append(True)

        def choose_or_close():
            if dialog.busy:
                QTimer.singleShot(10, choose_or_close)
            elif dialog.snapshot.current_choice == "declined":
                dialog.choose("opt_in")
                QTimer.singleShot(10, choose_or_close)
            else:
                assert dialog.snapshot.current_choice == "opt_in"
                dialog.close_button.click()

        QTimer.singleShot(0, choose_or_close)
        original_open(dialog)

    def closed(*_args):
        raise ServiceError("Research enrollment is not open.")

    monkeypatch.setattr(sharing_ui, "enrollment_information", closed)
    monkeypatch.setattr(sharing_ui.SharingDialog, "open", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config), force=True)
    pump_until(app, lambda: bool(finished))
    state = sharing_state(main.col.path)
    assert state.metadata()["sharing_first_choice"] == "declined"
    assert state.metadata()["research_consent_before_first_view"] is False
    finished.clear()
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert len(prompts) == 1  # Ordinary report openings do not ask the same question again.


def test_cached_old_results_still_wait_for_prompt_and_remain_unknown(runtime, app, monkeypatch):
    main, _ = runtime
    save_report(report_with_followup(), database_path(main.col.path))
    original_open = sharing_ui.SharingDialog.open
    finished = []

    def answer(dialog):
        assert dialog.snapshot.history == "unknown_legacy"
        QTimer.singleShot(0, lambda: dialog.choose("opt_in"))
        original_open(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "open", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert sharing_state(main.col.path).metadata()["research_consent_before_first_view"] is None


def test_escape_records_not_now_and_does_not_block_personal_use(runtime, app, monkeypatch):
    main, _ = runtime
    finished = []
    original_open = sharing_ui.SharingDialog.open

    def answer(dialog):
        QTimer.singleShot(0, dialog.reject)
        original_open(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "open", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert sharing_state(main.col.path).current_choice == "deferred"


def test_consent_database_writes_happen_off_the_ui_thread(runtime, app, monkeypatch):
    from addon import submissions

    main, _ = runtime
    original_state = sharing_ui.sharing_state
    original_open = sharing_ui.SharingDialog.open
    workers, finished = [], []

    def tracked(*args, **kwargs):
        workers.append(threading.get_ident())
        return original_state(*args, **kwargs)

    def answer(dialog):
        QTimer.singleShot(0, lambda: dialog.choose("declined"))
        original_open(dialog)

    monkeypatch.setattr(sharing_ui, "sharing_state", tracked)
    monkeypatch.setattr(submissions, "sharing_state", tracked)
    monkeypatch.setattr(sharing_ui.SharingDialog, "open", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert len(workers) == 3  # Initial load, prompt event, and choice event.
    assert all(worker != main.owner for worker in workers)


def test_sharing_waits_for_progress_and_leaves_callbacks_free(runtime, app, monkeypatch):
    main, _ = runtime
    waiting, prompts, ready, callbacks = [], [], [], []
    original_single_shot = main.progress.single_shot
    original_open = sharing_ui.SharingDialog.open

    def single_shot(*args, **kwargs):
        waiting.append(True)
        original_single_shot(*args, **kwargs)

    def opened(dialog):
        original_open(dialog)
        prompts.append(dialog)

    def blocking_exec(dialog):
        # Give the regression a failure instead of a nested loop that hangs pytest.
        prompts.append(dialog)
        return 0

    monkeypatch.setattr(main.progress, "single_shot", single_shot)
    monkeypatch.setattr(sharing_ui.SharingDialog, "open", opened)
    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", blocking_exec)
    main.progress.start(label="Checking…")
    sharing_ui.sharing_gate(main, "addon", ready.append)
    pump_until(app, lambda: bool(waiting or prompts))
    assert waiting and not prompts
    main.progress.finish()
    pump_until(app, lambda: bool(prompts))

    main.taskman.run_on_main(lambda: callbacks.append(True))
    pump_until(app, lambda: bool(callbacks))
    assert prompts[0].isVisible() and not ready
    prompts[0].choose("deferred")
    pump_until(app, lambda: bool(ready))
    assert sharing_state(main.col.path).current_choice == "deferred"


def test_profile_close_dismisses_sharing_without_recording_a_choice(runtime, app, monkeypatch):
    main, _ = runtime
    prompts, ready = [], []
    original_open = sharing_ui.SharingDialog.open

    def opened(dialog):
        original_open(dialog)
        prompts.append(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "open", opened)
    sharing_ui.sharing_gate(main, "addon", ready.append)
    pump_until(app, lambda: len(prompts) == 1)
    sharing_ui.close_sharing(main.col.path)
    assert not prompts[0].isVisible() and not ready
    assert sharing_state(main.col.path).current_choice is None

    sharing_ui.sharing_gate(main, "addon", ready.append)
    pump_until(app, lambda: len(prompts) == 2)
    prompts[1].choose("declined")
    pump_until(app, lambda: bool(ready))
    assert sharing_state(main.col.path).current_choice == "declined"


def test_enter_on_existing_sharing_choice_closes_without_changing_it(runtime, app):
    main, _ = runtime
    sharing = SharingConfig()
    snapshot = sharing_state(main.col.path, sharing, event_type="sharing_choice", choice="opt_in")
    dialog = sharing_ui.SharingDialog(main, main.col.path, snapshot)
    dialog.show()
    app.processEvents()
    assert dialog.close_button.isDefault()
    assert not any(button.isDefault() for button in dialog.buttons)
    app.sendEvent(
        dialog, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )
    assert not dialog.isVisible()
    after = sharing_state(main.col.path, sharing)
    assert after.current_choice == "opt_in" and after.events == snapshot.events


def test_exposure_write_failure_does_not_leak_a_personal_report(runtime, app, monkeypatch):
    main, notices = runtime
    dialog = ui.MemoryReportDialog(main, "addon", sharing=SharingConfig())
    dialog.show()

    def fail(*args, **kwargs):
        raise OSError("Audit file unavailable")

    monkeypatch.setattr(ui, "sharing_state", fail)
    dialog.accept_report(report_with_followup())
    pump_until(app, lambda: not dialog.busy)
    assert dialog.report is None
    assert dialog.table.rowCount() == 0
    assert notices
    dialog.close()
