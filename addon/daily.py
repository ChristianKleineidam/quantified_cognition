"""Unattended daily updates: defer live copying during review, analyze independently."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from aqt import gui_hooks
from aqt.qt import QTimer

from .backend import UpdateCancelled
from .background import run_file_job
from .metrics import Settings, anki_day
from .research import SharingConfig, SharingSnapshot, sharing_state
from .shutdown import arm_close_export, start_export_worker
from .storage import (
    UpdateResult,
    csv_export_pending,
    daily_update_due,
    database_path,
    load_daily_settings,
    retry_fixed_csv,
)
from .update_job import UpdateJob, UpdateRequest

logger = logging.getLogger(__name__)


class DailyUpdater:
    def __init__(
        self,
        main_window,
        addon_name: str,
        on_report: Callable[[UpdateResult], None] | None = None,
        on_sharing_needed: Callable[[], None] | None = None,
        on_error: Callable[[str | None], None] | None = None,
    ):
        self.main_window = main_window
        self.addon_name = addon_name
        self.on_report = on_report
        self.on_sharing_needed = on_sharing_needed
        self.on_error = on_error
        self.busy = False
        self.retry_after = 0.0
        self.last_error: str | None = None
        self.job: UpdateJob | None = None
        self.generation = 0
        self.preflight_cancelled: threading.Event | None = None
        self.review_answers = False
        self.review_serial = 0
        self.exported_review_serial = 0
        self.completion_serial = 0
        self.completion_pending = False
        self.syncing = False
        self.closing_profile = False
        self.sync_serial = 0
        self.checked_sync_serial = 0
        self.exported_sync_serial = 0
        self.timer = QTimer(main_window)
        self.timer.setInterval(5 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        gui_hooks.profile_did_open.append(self.start)
        gui_hooks.profile_will_close.append(self.close_profile)
        gui_hooks.state_did_change.append(self.state_changed)
        gui_hooks.reviewer_did_answer_card.append(self.card_answered)
        gui_hooks.reviewer_will_end.append(self.review_ending)
        gui_hooks.sync_will_start.append(self.sync_starting)
        gui_hooks.sync_did_finish.append(self.sync_finished)

    def start(self) -> None:
        self.syncing = False
        self.closing_profile = False
        self.sync_serial = self.checked_sync_serial = self.exported_sync_serial = 0
        self.retry_after = 0.0
        self.last_error = None
        self.review_answers = False
        self.review_serial = 0
        self.exported_review_serial = 0
        self.completion_pending = False
        if self.main_window.col is not None:
            config_manager, addon_name = self.main_window.addonManager, self.addon_name
            start_export_worker(
                self.main_window.col.path,
                lambda: SharingConfig.from_config(config_manager.getConfig(addon_name)),
                require_choice=self.on_sharing_needed is not None,
            )
        self.timer.start()
        QTimer.singleShot(3000, self.check)

    def stop(self) -> None:
        self.timer.stop()
        self.generation += 1
        if self.job is not None:
            self.job.cancel()
        if self.preflight_cancelled is not None:
            self.preflight_cancelled.set()
        self.job = None
        self.busy = False
        self.review_answers = False
        self.completion_pending = False

    def close_profile(self) -> None:
        # A successful earlier export (or its queued Qt callback) cannot stand
        # in for answers recorded after that job started. Capture the latest
        # collection even when the final-card preflight has not run yet.
        dirty = (
            self.review_serial > self.exported_review_serial or self.completion_pending
            or self.sync_serial > self.exported_sync_serial
        )
        self.closing_profile = True
        self.stop()
        if dirty and self.main_window.col is not None:
            arm_close_export(
                self.main_window,
                lambda: self.main_window.addonManager.getConfig(self.addon_name),
                require_choice=self.on_sharing_needed is not None,
            )

    def sync_starting(self) -> None:
        # A full download temporarily closes/reopens the same Collection object.
        # Invalidate existing captures even when its Python identity is unchanged.
        self.syncing = True
        self.generation += 1
        if self.job is not None:
            self.job.cancel()
        if self.preflight_cancelled is not None:
            self.preflight_cancelled.set()
        self.job = None
        self.busy = False

    def sync_finished(self) -> None:
        self.syncing = False
        if self.main_window.col is None:
            return
        self.sync_serial += 1
        if self.closing_profile:
            # Closing sync runs after profile_will_close. Capture at the actual
            # close point, including imported reviews when there were no local answers.
            arm_close_export(
                self.main_window,
                lambda: self.main_window.addonManager.getConfig(self.addon_name),
                require_choice=self.on_sharing_needed is not None,
            )
            return
        generation = self.generation
        QTimer.singleShot(
            0, lambda: self.check(force=True) if generation == self.generation else None
        )

    def card_answered(self, reviewer, card, ease: int) -> None:
        # No database queries or extra collection operations for each answer.
        if reviewer is getattr(self.main_window, "reviewer", None):
            self.review_answers = True
            self.review_serial += 1

    def review_ending(self) -> None:
        reviewer = getattr(self.main_window, "reviewer", None)
        # Anki's nextCard() sets card=None before ending an exhausted queue.
        # On a manual exit with cards remaining, cleanup has not cleared it yet.
        if self.review_answers and reviewer is not None and reviewer.card is None:
            self.completion_serial += 1
            self.completion_pending = True
        self.review_answers = False

    def state_changed(self, new_state: str, old_state: str) -> None:
        if old_state == "review" and new_state != "review" and self.timer.isActive():
            if self.completion_pending:
                # Already outside review; request fresh results even if today's
                # periodic update ran before these answers were recorded.
                self.check(force=True)
            else:
                QTimer.singleShot(1000, self.check)

    def check(self, *, force: bool = False) -> None:
        main = self.main_window
        if self.busy or self.syncing or self.closing_profile or main.col is None:
            return
        if not force and time.monotonic() < self.retry_after:
            return
        collection = main.col
        collection_path = collection.path
        config_manager = main.addonManager
        generation = self.generation
        completion_serial = self.completion_serial
        review_serial = self.review_serial
        sync_serial = self.sync_serial
        analysis_started = False
        refresh_completed = self.completion_pending
        force = force or refresh_completed or sync_serial > self.checked_sync_serial
        self.busy = True
        cancelled = threading.Event()
        self.preflight_cancelled = cancelled

        def check_current():
            if cancelled.is_set() or main.col is not collection:
                raise UpdateCancelled("The profile closed or changed before CSV export.")

        def preflight():
            check_current()
            daily = load_daily_settings(collection_path)
            if not daily.enabled:
                return None
            csv_path = daily.validated_path()
            config = config_manager.getConfig(self.addon_name)
            sharing = SharingConfig.from_config(config)
            if self.on_sharing_needed is not None:
                snapshot = sharing_state(collection_path, sharing)
                if snapshot.current_choice is None:
                    return snapshot
            if not force and csv_export_pending(database_path(collection_path), csv_path):
                return retry_fixed_csv(
                    collection_path,
                    sharing,
                    check_cancelled=check_current,
                    only_if_pending=True,
                )
            return True  # Needs an eligibility check with the live rollover setting.

        def prepare(rollover):
            # File/config/audit access is independent of Anki's collection queue.
            current_daily = load_daily_settings(collection_path)
            if not current_daily.enabled:
                return None
            csv_path = current_daily.validated_path()
            day = anki_day(time.time(), rollover)
            if not force and not daily_update_due(database_path(collection_path), day, csv_path):
                return None
            config = config_manager.getConfig(self.addon_name)
            sharing = SharingConfig.from_config(config)
            if self.on_sharing_needed is not None:
                snapshot = sharing_state(collection_path, sharing)
                if snapshot.current_choice is None:
                    return snapshot
            return UpdateRequest(Settings.from_config(config), current_daily, day, sharing)

        def current() -> bool:
            return generation == self.generation and main.col is collection

        def success(result) -> None:
            if generation != self.generation:
                return
            self.busy = False
            self.job = None
            if not current():
                return
            self.retry_after = 0.0
            self.last_error = None
            if self.on_error is not None:
                self.on_error(None)
            if isinstance(result, SharingSnapshot):
                if self.on_sharing_needed is not None and getattr(main, "state", None) != "review":
                    self.on_sharing_needed()
                return
            if isinstance(result, UpdateResult):
                if analysis_started:
                    self.checked_sync_serial = max(self.checked_sync_serial, sync_serial)
                if result.csv_exported:
                    self.exported_sync_serial = max(
                        self.exported_sync_serial, self.checked_sync_serial
                    )
                if analysis_started and result.csv_exported:
                    self.exported_review_serial = max(self.exported_review_serial, review_serial)
                if self.on_report is not None:
                    self.on_report(result)
                if result.csv_error:
                    self.failed(f"Memory scores are saved. CSV export failed: {result.csv_error}")
            if isinstance(result, UpdateResult) or result is None:
                if completion_serial == self.completion_serial:
                    self.completion_pending = False
                elif self.completion_pending:
                    # A session finished after this job started. Its copy may
                    # predate the final answer, so one fresh follow-up is needed.
                    QTimer.singleShot(0, lambda: self.check(force=True))

        def failure(error: Exception) -> None:
            if generation != self.generation:
                return
            self.busy = False
            self.job = None
            if current() and not isinstance(error, UpdateCancelled):
                self.failed(str(error) or type(error).__name__)

        def ready(result):
            nonlocal analysis_started
            if not current():
                success(None)
                return
            if result is not True:
                success(result)
                return
            if getattr(main, "state", None) == "review" or getattr(main, "_background_op_count", 0):
                self.busy = False
                return
            self.job = UpdateJob(main, main, prepare, success, failure, idle_only=True)
            analysis_started = True
            self.job.start()

        run_file_job(preflight, ready, failure)

    def failed(self, error: str) -> None:
        self.retry_after = time.monotonic() + 60 * 60
        self.last_error = error
        logger.error("Quantified Cognition daily update failed: %s", error)
        # A locked CSV must never interrupt reviewing with a modal warning.
        # An open report shows the error inline; otherwise the log records it.
        if self.on_error is not None:
            self.on_error(error)
