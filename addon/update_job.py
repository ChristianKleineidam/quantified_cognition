"""Copy briefly on Anki's queue; do all analysis and file I/O independently."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from anki.collection import Collection
from aqt.operations import QueryOp

from .backend import UpdateCancelled, capture_snapshot
from .background import run_file_job
from .metrics import Settings
from .research import SharingConfig
from .storage import DailySettings, reserve_snapshot_sequence, update_results


@dataclass(frozen=True)
class UpdateRequest:
    settings: Settings
    daily: DailySettings
    scheduled_day: date
    sharing: SharingConfig


class UpdateJob:
    """All callbacks run on Qt's thread; a worker owns/cleans the disposable copy.

    `prepare` runs off the collection queue and may return None (not due), a
    sharing snapshot (choice needed), or UpdateRequest (copy and calculate).
    No live Collection object is ever used by the independent worker.
    """

    def __init__(
        self,
        main,
        parent,
        prepare: Callable,
        success: Callable,
        failure: Callable,
        *,
        idle_only: bool = False,
    ):
        self.main = main
        self.parent = parent
        self.collection = main.col
        self.collection_path = self.collection.path
        self.prepare = prepare
        self.success = success
        self.failure = failure
        self.idle_only = idle_only
        self.cancelled = threading.Event()
        self.directory: tempfile.TemporaryDirectory | None = None
        self.snapshot_path: Path | None = None
        self.sequence: int | None = None

    def cancel(self) -> None:
        self.cancelled.set()

    def check_active(self) -> None:
        # Only plain Python identity/state reads here; no Qt or live DB calls.
        if self.cancelled.is_set() or self.main.col is not self.collection:
            raise UpdateCancelled("The Anki profile closed or changed.")

    def check_snapshot_allowed(self) -> None:
        self.check_active()
        if self.idle_only and getattr(self.main, "state", None) == "review":
            raise UpdateCancelled("The automatic snapshot will wait until reviewing finishes.")

    def start(self) -> None:
        def preferences(col):
            self.check_snapshot_allowed()
            if col is not self.collection:
                raise UpdateCancelled("The Anki profile changed before the update started.")
            return col.get_preferences().scheduling.rollover

        QueryOp(parent=self.parent, op=preferences, success=self._preferences_ready).failure(
            self._failed
        ).run_in_background()

    def _preferences_ready(self, rollover: int) -> None:
        def prepare():
            self.check_active()
            request = self.prepare(rollover)
            if isinstance(request, UpdateRequest):
                self.check_active()
                self.directory = tempfile.TemporaryDirectory(prefix="anki-quantified-self-")
                self.snapshot_path = Path(self.directory.name) / "snapshot.anki2"
            return request

        run_file_job(prepare, self._prepared, self._failed)

    def _prepared(self, request) -> None:
        try:
            self.check_active()
            if not isinstance(request, UpdateRequest):
                self.success(request)
                return
            self.check_snapshot_allowed()
        except Exception as error:
            self._failed(error)
            return

        def copy(col):
            self.check_snapshot_allowed()
            if col is not self.collection:
                raise UpdateCancelled("The Anki profile changed before it could be copied.")
            capture_snapshot(col, self.snapshot_path)
            self.sequence = reserve_snapshot_sequence(self.collection_path, request.sharing)

        def copied(_result):
            # QueryOp has released the collection queue/background-op guard
            # before this callback. Even a slow CSV cannot delay answering cards.
            run_file_job(lambda: self._calculate(request), self.success, self._failed)

        QueryOp(parent=self.parent, op=copy, success=copied).failure(
            self._failed
        ).run_in_background()

    def _calculate(self, request: UpdateRequest):
        try:
            self.check_active()
            snapshot = Collection(str(self.snapshot_path))
            try:
                return update_results(
                    snapshot,
                    request.settings,
                    daily=request.daily,
                    scheduled_day=request.scheduled_day,
                    sharing=request.sharing,
                    source_collection_path=self.collection_path,
                    snapshot_sequence=self.sequence,
                    check_cancelled=self.check_active,
                )
            finally:
                snapshot.close()
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def _failed(self, error: Exception) -> None:
        if self.directory is None:
            self.failure(error)
            return
        # Failure/cancellation before calculation still cleans files off Qt's
        # thread, including a partially written copy after an unsuccessful VACUUM.
        run_file_job(self._cleanup, lambda _: self.failure(error), self.failure)
