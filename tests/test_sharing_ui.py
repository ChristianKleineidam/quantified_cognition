"""Exercise the actual consent gate, background saves and pre-display barrier."""

import threading

import pytest

pytest.importorskip("aqt")
from aqt.qt import QTimer  # noqa: E402
from test_storage import report_with_followup  # noqa: E402
from test_threading import app as thread_app  # noqa: E402
from test_threading import pump_until
from test_threading import runtime as thread_runtime

from addon import sharing_ui, ui  # noqa: E402
from addon.research import SharingConfig, sharing_state  # noqa: E402
from addon.storage import database_path, save_report  # noqa: E402

app = thread_app
runtime = thread_runtime


@pytest.mark.parametrize("choice", ["opt_in", "declined", "deferred"])
def test_prompt_precedes_any_data_and_every_choice_allows_results(
    runtime, app, monkeypatch, choice
):
    main, notices = runtime
    prompts, displayed = [], []
    original_exec = sharing_ui.SharingDialog.exec

    def answer(dialog):
        state = sharing_state(main.col.path)
        assert state.first_exposure is None
        assert state.current_choice is None
        assert dialog.buttons[1].isDefault()
        assert not dialog.buttons[0].isDefault()
        prompts.append(threading.get_ident())
        QTimer.singleShot(0, lambda: dialog.choose(choice))
        return original_exec(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", answer)
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
    original_exec = sharing_ui.SharingDialog.exec
    prompts, finished = [], []

    def answer(dialog):
        assert dialog.snapshot.current_choice == "declined"
        prompts.append(True)
        QTimer.singleShot(0, lambda: dialog.choose("opt_in"))
        return original_exec(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", answer)
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
    original_exec = sharing_ui.SharingDialog.exec
    finished = []

    def answer(dialog):
        assert dialog.snapshot.history == "unknown_legacy"
        QTimer.singleShot(0, lambda: dialog.choose("opt_in"))
        return original_exec(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert sharing_state(main.col.path).metadata()["research_consent_before_first_view"] is None


def test_escape_records_not_now_and_does_not_block_personal_use(runtime, app, monkeypatch):
    main, _ = runtime
    finished = []
    original_exec = sharing_ui.SharingDialog.exec

    def answer(dialog):
        QTimer.singleShot(0, dialog.reject)
        return original_exec(dialog)

    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert sharing_state(main.col.path).current_choice == "deferred"


def test_consent_database_writes_happen_off_the_ui_thread(runtime, app, monkeypatch):
    from addon import submissions

    main, _ = runtime
    original_state = sharing_ui.sharing_state
    original_exec = sharing_ui.SharingDialog.exec
    workers, finished = [], []

    def tracked(*args, **kwargs):
        workers.append(threading.get_ident())
        return original_state(*args, **kwargs)

    def answer(dialog):
        QTimer.singleShot(0, lambda: dialog.choose("declined"))
        return original_exec(dialog)

    monkeypatch.setattr(sharing_ui, "sharing_state", tracked)
    monkeypatch.setattr(submissions, "sharing_state", tracked)
    monkeypatch.setattr(sharing_ui.SharingDialog, "exec", answer)
    sharing_ui.sharing_gate(main, "addon", lambda config: finished.append(config))
    pump_until(app, lambda: bool(finished))
    assert len(workers) == 3  # Initial load, prompt event, and choice event.
    assert all(worker != main.owner for worker in workers)


def test_closing_existing_sharing_choice_does_not_withdraw_or_request_deletion(runtime, app):
    main, _ = runtime
    sharing = SharingConfig()
    snapshot = sharing_state(main.col.path, sharing, event_type="sharing_choice", choice="opt_in")
    dialog = sharing_ui.SharingDialog(main, main.col.path, snapshot)
    dialog.show()
    dialog.reject()
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
