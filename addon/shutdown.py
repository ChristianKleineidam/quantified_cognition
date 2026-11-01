"""Durable export handoff, independent of Qt and the live collection after capture."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import asdict
from datetime import date
from pathlib import Path

from anki.collection import Collection

from .backend import capture_snapshot
from .metrics import Settings, anki_day
from .research import SharingConfig, sharing_state
from .storage import (
    database_path,
    load_daily_settings,
    load_report,
    profile_job_lock,
    reserve_snapshot_sequence,
    results_directory,
    retry_fixed_csv,
    saved_snapshot_sequence,
    update_results,
)

logger = logging.getLogger(__name__)


def pending_directory(collection_path: str | Path) -> Path:
    return results_directory(collection_path) / "pending-exports"


def capture_close_job(collection, config: dict | None, *, require_choice: bool) -> Path | None:
    """Run on Anki's collection queue, before its final close, after closing sync."""
    source = collection.path
    daily = load_daily_settings(source)
    if not daily.enabled:
        return None
    daily.validated_path()
    sharing = SharingConfig.from_config(config)
    audit = sharing_state(source, sharing)
    if require_choice and audit.current_choice is None:
        return None
    day = anki_day(time.time(), collection.get_preferences().scheduling.rollover)
    directory = pending_directory(source) / uuid.uuid4().hex
    directory.mkdir(parents=True)
    try:
        snapshot = directory / "snapshot.anki2"
        capture_snapshot(collection, snapshot)
        # The request is committed only after its complete frozen input is durable.
        with snapshot.open("r+b") as handle:
            os.fsync(handle.fileno())
        sequence = reserve_snapshot_sequence(source, sharing)
        request = {
            "version": 1,
            "sequence": sequence,
            "scheduled_day": day.isoformat(),
            "settings": asdict(Settings.from_config(config)),
        }
        with (directory / "request.tmp").open("w", encoding="utf-8") as handle:
            json.dump(request, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(directory / "request.tmp", directory / "request.json")
        return directory
    except Exception:
        _remove_job(directory)
        raise


def _remove_job(directory: Path) -> None:
    # Delete only this module's known files, never an unchecked recursive path.
    for name in ("snapshot.anki2", "request.tmp", "request.json", "last-error.txt"):
        (directory / name).unlink(missing_ok=True)
    directory.rmdir()


def finish_pending_exports(
    collection_path: str | Path, sharing: SharingConfig, *, require_choice: bool
) -> None:
    """Recover saved work using only local files and an independent native backend.

    A frozen input is never opened by Anki: native historical replay mutates its
    disposable working copy. Export retries use the already saved report.
    """
    root = pending_directory(collection_path)
    if not root.exists():
        return
    with profile_job_lock(collection_path):
        if not root.exists():
            return
        requests = [
            (json.loads(path.read_text(encoding="utf-8")), path.parent)
            for path in root.glob("*/request.json")
        ]
        ordered = sorted(requests, key=lambda item: item[0]["sequence"], reverse=True)
        for request, directory in ordered:
            try:
                daily = load_daily_settings(collection_path)
                if not daily.enabled:
                    _remove_job(directory)
                    continue
                if require_choice:
                    if sharing_state(collection_path, sharing).current_choice is None:
                        return
                if request["version"] != 1:
                    raise ValueError("Unsupported pending export version.")
                sequence = int(request["sequence"])
                path = database_path(collection_path)
                if sequence <= saved_snapshot_sequence(collection_path) and load_report(path):
                    # Calculation committed before a failed export or interrupted exit.
                    # Retry the latest saved report, never publish the older snapshot.
                    result = retry_fixed_csv(collection_path, sharing, only_if_pending=True)
                else:
                    with tempfile.TemporaryDirectory(prefix="anki-qs-export-") as work:
                        working_path = Path(work) / "working.anki2"
                        shutil.copyfile(directory / "snapshot.anki2", working_path)
                        snapshot = Collection(str(working_path))
                        try:
                            result = update_results(
                                snapshot,
                                Settings.from_config(request["settings"]),
                                daily=daily,
                                scheduled_day=date.fromisoformat(request["scheduled_day"]),
                                sharing=sharing,
                                source_collection_path=collection_path,
                                snapshot_sequence=sequence,
                            )
                        finally:
                            snapshot.close()
                if result.csv_error:
                    raise OSError(result.csv_error)
                _remove_job(directory)
            except Exception as error:
                (directory / "last-error.txt").write_text(str(error), encoding="utf-8")
                raise


def start_export_worker(
    collection_path: str | Path,
    sharing: SharingConfig | Callable[[], SharingConfig] | None = None,
    *,
    require_choice: bool,
    capture: Future | None = None,
) -> threading.Thread:
    """An owned non-daemon thread outlives Qt and Python's normal main-thread exit."""
    source = str(collection_path)

    def run():
        try:
            if capture is not None:
                study = capture.result()  # No Qt callback/event delivery is involved.
            else:
                study = sharing() if callable(sharing) else sharing
            if study is None:
                raise ValueError("Missing sharing configuration for pending export.")
            for attempt in range(3):
                try:
                    finish_pending_exports(source, study, require_choice=require_choice)
                    return
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        except Exception:
            # Keep the frozen job for automatic recovery next opening. No dialogs.
            logger.exception("Quantified Cognition export remains pending for %s", source)

    worker = threading.Thread(target=run, name="Quantified Cognition final export", daemon=False)
    worker.start()
    return worker


def arm_close_export(main, config_provider, *, require_choice: bool) -> None:
    """Install one handoff at the actual collection-close commit point.

    profile_will_close precedes cancellable dialogs and closing sync. Wrapping
    this collection instance captures after both, without affecting full-sync
    close/reopen operations or the next profile. Only the snapshot is awaited;
    calculation and publication never extend the window's shutdown sequence.
    """
    collection = main.col
    if collection is None or getattr(collection, "_qs_close_export_armed", False):
        return
    original_close = collection.close
    collection._qs_close_export_armed = True

    def close(*args, **kwargs):
        collection.close = original_close
        collection._qs_close_export_armed = False
        try:
            # TaskManager serializes this with any last answer/sync. No on_done:
            # the worker must not depend on a disappearing Qt dispatcher.
            def preserve():
                config = config_provider()
                capture_close_job(collection, config, require_choice=require_choice)
                return SharingConfig.from_config(config)

            capture = main.taskman.run_in_background(
                preserve,
                uses_collection=True,
            )
            start_export_worker(collection.path, require_choice=require_choice, capture=capture)
            capture.result()
        except Exception:
            logger.exception("Quantified Cognition could not preserve the closing collection")
        finally:
            original_close(*args, **kwargs)

    collection.close = close
