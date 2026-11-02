import os
from datetime import date, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("aqt")
from aqt import gui_hooks  # noqa: E402
from aqt.qt import QApplication, QWidget  # noqa: E402

from addon import daily  # noqa: E402
from addon.storage import DailySettings, database_path, save_daily_settings  # noqa: E402


class QueuedJob:
    requests = []
    calculate = None

    def __init__(self, main, parent, prepare, success, failure, *, idle_only=False):
        self.main, self.col = main, main.col
        self.prepare, self.success, self.failed = prepare, success, failure
        self.cancelled = False

    def start(self):
        self.requests.append(self)

    def cancel(self):
        self.cancelled = True

    def complete(self, col):
        from addon.backend import UpdateCancelled
        from addon.update_job import UpdateRequest

        try:
            if self.cancelled or col is not self.col:
                raise UpdateCancelled()
            result = self.prepare(col.get_preferences().scheduling.rollover)
            if isinstance(result, UpdateRequest):
                result = type(self).calculate(
                    col,
                    result.settings,
                    daily=result.daily,
                    scheduled_day=result.scheduled_day,
                    sharing=result.sharing,
                )
        except Exception as exc:
            self.failed(exc)
        else:
            self.success(result)
            return result


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def updater(app, tmp_path, monkeypatch):
    main = QWidget()
    main.col = SimpleNamespace(
        path=str(tmp_path / "profile" / "collection.anki2"),
        get_preferences=lambda: SimpleNamespace(scheduling=SimpleNamespace(rollover=4)),
    )
    main.addonManager = SimpleNamespace(getConfig=lambda name: None)
    QueuedJob.requests = []
    monkeypatch.setattr(daily, "UpdateJob", QueuedJob)
    QueuedJob.calculate = None

    def file_job(task, success, failure):
        try:
            result = task()
        except Exception as error:
            failure(error)
        else:
            success(result)

    monkeypatch.setattr(daily, "run_file_job", file_job)
    controller = daily.DailyUpdater(main, "addon")
    yield controller
    controller.stop()
    gui_hooks.profile_did_open.remove(controller.start)
    gui_hooks.profile_will_close.remove(controller.close_profile)
    gui_hooks.state_did_change.remove(controller.state_changed)
    gui_hooks.reviewer_did_answer_card.remove(controller.card_answered)
    gui_hooks.reviewer_will_end.remove(controller.review_ending)
    gui_hooks.sync_will_start.remove(controller.sync_starting)
    gui_hooks.sync_did_finish.remove(controller.sync_finished)


def test_disabled_daily_setting_does_not_calculate(updater, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("Disabled daily updates must not calculate a report")

    monkeypatch.setattr(QueuedJob, "calculate", unexpected)
    updater.check()
    assert not QueuedJob.requests
    assert not updater.busy
    assert not database_path(updater.main_window.col.path).exists()


def test_queued_job_does_not_cross_profiles_or_run_after_disabling(updater, tmp_path):
    col = updater.main_window.col
    save_daily_settings(col.path, DailySettings(True, str(tmp_path / "scores.csv")))
    updater.check()
    assert len(QueuedJob.requests) == 1
    other = SimpleNamespace(path=str(tmp_path / "other" / "collection.anki2"))
    QueuedJob.requests[-1].complete(other)
    assert not database_path(other.path).exists()
    assert not database_path(col.path).exists()
    updater.check()
    save_daily_settings(col.path, DailySettings(False, str(tmp_path / "scores.csv")))
    QueuedJob.requests[-1].complete(col)
    assert not database_path(col.path).exists()


def test_day_boundary_daily_deduplication_and_catchup(updater, tmp_path, monkeypatch):
    from test_storage import report_with_followup

    from addon.storage import _mark_daily_success, save_report

    col = updater.main_window.col
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text("existing CSV", encoding="utf-8")
    save_daily_settings(col.path, DailySettings(True, str(csv_path)))
    db = database_path(col.path)
    save_report(report_with_followup(), db)
    _mark_daily_success(db, date(2026, 10, 6), csv_path)
    updated_days = []

    def update(col, settings, *, daily, scheduled_day, sharing=None):
        updated_days.append(scheduled_day)
        _mark_daily_success(db, scheduled_day, csv_path)
        return report_with_followup()

    monkeypatch.setattr(QueuedJob, "calculate", update)
    monkeypatch.setattr(daily.time, "time", lambda: datetime(2026, 10, 7, 3, 59).timestamp())
    updater.check()
    assert QueuedJob.requests[-1].complete(col) is None
    assert not updated_days
    monkeypatch.setattr(daily.time, "time", lambda: datetime(2026, 10, 7, 4, 0).timestamp())
    updater.check()
    assert len(QueuedJob.requests) == 2
    # Checks cannot enqueue a duplicate while the preceding job is outstanding.
    updater.check()
    assert len(QueuedJob.requests) == 2
    QueuedJob.requests[-1].complete(col)
    assert updated_days == [date(2026, 10, 7)]
    updater.check()
    assert QueuedJob.requests[-1].complete(col) is None
    assert updated_days == [date(2026, 10, 7)]
    monkeypatch.setattr(daily.time, "time", lambda: datetime(2026, 10, 12, 12, 0).timestamp())
    updater.check()
    QueuedJob.requests[-1].complete(col)
    assert updated_days == [date(2026, 10, 7), date(2026, 10, 12)]


def test_failed_jobs_back_off_and_report_errors_without_popups(updater, tmp_path, monkeypatch):
    warnings = []
    updater.on_error = lambda text: warnings.append(text) if text else None
    save_daily_settings(
        updater.main_window.col.path, DailySettings(True, str(tmp_path / "scores.csv"))
    )

    def fail(*args, **kwargs):
        raise OSError("Destination unavailable")

    monkeypatch.setattr(QueuedJob, "calculate", fail)
    updater.check()
    QueuedJob.requests[-1].complete(updater.main_window.col)
    assert len(warnings) == 1
    assert updater.retry_after > daily.time.monotonic()
    updater.check()
    assert len(QueuedJob.requests) == 1
    updater.check(force=True)
    QueuedJob.requests[-1].complete(updater.main_window.col)
    assert len(warnings) == 2
    assert updater.last_error == "Destination unavailable"


def test_scheduler_waits_for_anki_background_operations(updater, tmp_path):
    save_daily_settings(
        updater.main_window.col.path, DailySettings(True, str(tmp_path / "scores.csv"))
    )
    updater.main_window._background_op_count = 1
    updater.check()
    assert not QueuedJob.requests


def test_automatic_copy_waits_until_review_screen_is_left(updater, tmp_path, monkeypatch):
    save_daily_settings(
        updater.main_window.col.path, DailySettings(True, str(tmp_path / "scores.csv"))
    )
    updater.main_window.state = "review"
    updater.check(force=True)
    assert not QueuedJob.requests
    pending = []
    monkeypatch.setattr(
        daily.QTimer, "singleShot", lambda delay, callback: pending.append(callback)
    )
    updater.timer.start()
    updater.main_window.state = "overview"
    updater.state_changed("overview", "review")
    assert len(pending) == 1
    pending[0]()
    assert len(QueuedJob.requests) == 1


def test_registered_daily_job_waits_for_sharing_choice_before_csv(updater, tmp_path, monkeypatch):
    from test_storage import report_with_followup

    from addon.research import sharing_state

    requested, calculated = [], []
    updater.on_sharing_needed = lambda: requested.append(True)
    col = updater.main_window.col
    save_daily_settings(col.path, DailySettings(True, str(tmp_path / "scores.csv")))

    def calculate(*args, **kwargs):
        calculated.append(True)
        return report_with_followup()

    monkeypatch.setattr(QueuedJob, "calculate", calculate)
    updater.check()
    assert not QueuedJob.requests
    assert requested == [True]
    assert not calculated
    assert not (tmp_path / "scores.csv").exists()
    sharing_state(col.path, event_type="sharing_choice", choice="declined")
    updater.check(force=True)
    QueuedJob.requests[-1].complete(col)
    assert calculated == [True]


def test_finished_queue_refreshes_even_after_today_was_already_exported(
    updater, tmp_path, monkeypatch
):
    from test_storage import report_with_followup

    from addon.storage import UpdateResult, _mark_daily_success, save_report

    main = updater.main_window
    csv_path = tmp_path / "scores.csv"
    save_daily_settings(main.col.path, DailySettings(True, str(csv_path)))
    report = report_with_followup()
    db = database_path(main.col.path)
    save_report(report, db)
    csv_path.write_text("earlier daily export", encoding="utf-8")
    monkeypatch.setattr(daily.time, "time", lambda: datetime(2026, 10, 7, 12).timestamp())
    _mark_daily_success(db, date(2026, 10, 7), csv_path)
    updates = []
    monkeypatch.setattr(
        QueuedJob,
        "calculate",
        lambda *args, **kwargs: (
            updates.append(True) or UpdateResult(report, True, csv_exported=True)
        ),
    )
    main.reviewer = SimpleNamespace(card=object())
    main.state = "review"
    updater.timer.start()
    updater.card_answered(main.reviewer, main.reviewer.card, 3)
    assert not QueuedJob.requests  # No calculation or queue lookup for every answer.
    main.reviewer.card = None
    updater.review_ending()
    main.state = "overview"
    updater.state_changed("overview", "review")
    assert len(QueuedJob.requests) == 1  # Immediate; no timer delay.
    QueuedJob.requests[-1].complete(main.col)
    assert updates == [True]
    assert not updater.completion_pending


def test_leaving_review_early_does_not_force_completed_queue_update(updater):
    main = updater.main_window
    main.reviewer = SimpleNamespace(card=object())
    updater.card_answered(main.reviewer, main.reviewer.card, 3)
    updater.review_ending()  # A current card remains, as on a manual exit.
    assert not updater.completion_pending
    assert updater.completion_serial == 0


def test_queue_finished_during_existing_job_gets_one_fresh_followup(updater, tmp_path, monkeypatch):
    from test_storage import report_with_followup

    from addon.storage import UpdateResult

    main = updater.main_window
    save_daily_settings(main.col.path, DailySettings(True, str(tmp_path / "scores.csv")))
    monkeypatch.setattr(
        QueuedJob,
        "calculate",
        lambda *args, **kwargs: UpdateResult(report_with_followup(), True, csv_exported=True),
    )
    pending = []
    monkeypatch.setattr(
        daily.QTimer, "singleShot", lambda delay, callback: pending.append(callback)
    )
    updater.timer.start()
    updater.check(force=True)
    assert updater.busy
    main.reviewer = SimpleNamespace(card=None)
    updater.card_answered(main.reviewer, object(), 4)
    updater.review_ending()
    main.state = "overview"
    updater.state_changed("overview", "review")
    assert len(QueuedJob.requests) == 1
    QueuedJob.requests[0].complete(main.col)
    assert updater.completion_pending and len(pending) == 1
    pending.pop()()
    assert len(QueuedJob.requests) == 2
    QueuedJob.requests[1].complete(main.col)
    assert not updater.completion_pending and not pending


def test_queue_completion_respects_disabled_automatic_updates(updater):
    main = updater.main_window
    main.reviewer = SimpleNamespace(card=None)
    updater.timer.start()
    updater.card_answered(main.reviewer, object(), 4)
    updater.review_ending()
    main.state = "overview"
    updater.state_changed("overview", "review")
    assert not QueuedJob.requests and not updater.busy
    assert not updater.completion_pending


def test_sync_cancels_old_capture_without_its_callback_clearing_new_work(
    updater, tmp_path, monkeypatch,
):
    from test_storage import report_with_followup

    from addon.storage import UpdateResult

    save_daily_settings(
        updater.main_window.col.path, DailySettings(True, str(tmp_path / "scores.csv"))
    )
    monkeypatch.setattr(
        QueuedJob, "calculate",
        lambda *args, **kwargs: UpdateResult(report_with_followup(), True, csv_exported=True),
    )
    callbacks = []
    monkeypatch.setattr(
        daily.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback)
    )
    updater.check(force=True)
    old_job = QueuedJob.requests[-1]
    updater.sync_starting()
    assert old_job.cancelled
    updater.check(force=True)
    assert len(QueuedJob.requests) == 1
    updater.sync_finished()
    callbacks.pop()()
    assert len(QueuedJob.requests) == 2 and updater.busy
    old_job.complete(updater.main_window.col)
    assert updater.busy  # The cancelled generation cannot clear the new job.
    QueuedJob.requests[-1].complete(updater.main_window.col)
    assert not updater.busy
    assert updater.checked_sync_serial == updater.exported_sync_serial == 1
