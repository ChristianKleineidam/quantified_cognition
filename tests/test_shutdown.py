"""Normal interpreter exit must finish final exports without Qt callback delivery."""

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from anki.collection import Collection
from test_backend import add_card

from addon import shutdown, storage
from addon.research import SharingConfig, sharing_state
from addon.storage import DailySettings, load_report, save_daily_settings


@pytest.fixture
def collection(tmp_path):
    col = Collection(str(tmp_path / "collection.anki2"))
    col.set_config("fsrs", True)
    yield col
    col.close()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_frozen_input_survives_failed_export_and_retry_does_not_recalculate(
    collection, tmp_path, monkeypatch
):
    destination = tmp_path / "scores.csv"
    save_daily_settings(collection.path, DailySettings(True, str(destination)))
    add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    job = shutdown.capture_close_job(collection, None, require_choice=False)
    frozen = job / "snapshot.anki2"
    before = hashlib.sha256(frozen.read_bytes()).digest()
    real_export = storage.export_csv_atomic

    def locked(*args, **kwargs):
        raise PermissionError("CSV is open elsewhere")

    destination.write_text("previous complete file", encoding="utf-8")
    monkeypatch.setattr(storage, "export_csv_atomic", locked)
    with pytest.raises(OSError, match="CSV is open elsewhere"):
        shutdown.finish_pending_exports(collection.path, SharingConfig(), require_choice=False)
    assert destination.read_text(encoding="utf-8") == "previous complete file"
    assert job.is_dir() and (job / "last-error.txt").is_file()
    assert hashlib.sha256(frozen.read_bytes()).digest() == before
    assert load_report(storage.database_path(collection.path)) is not None
    assert sharing_state(collection.path).first_exposure is None

    def unexpected(*args, **kwargs):
        pytest.fail("Publishing saved analysis must not copy, scan, or replay FSRS")

    monkeypatch.setattr(storage, "export_csv_atomic", real_export)
    monkeypatch.setattr(shutdown.shutil, "copyfile", unexpected)
    monkeypatch.setattr(storage, "input_fingerprint", unexpected)
    monkeypatch.setattr(storage, "build_report", unexpected)
    shutdown.finish_pending_exports(collection.path, SharingConfig(), require_choice=False)
    assert read_csv(destination)[0]["recall_cohort_count"] == "1"
    assert not job.exists()


def test_disabled_and_unanswered_sharing_choice_do_not_capture(collection, tmp_path):
    assert shutdown.capture_close_job(collection, None, require_choice=False) is None
    save_daily_settings(collection.path, DailySettings(True, str(tmp_path / "scores.csv")))
    assert shutdown.capture_close_job(collection, None, require_choice=True) is None
    assert not shutdown.pending_directory(collection.path).exists()
    assert not storage.database_path(collection.path).exists()


def test_newer_snapshot_supersedes_pending_input_even_in_another_process(collection, tmp_path):
    destination = tmp_path / "scores.csv"
    save_daily_settings(collection.path, DailySettings(True, str(destination)))
    add_card(collection, [(date(2026, 9, 7), 3, 0, 2500)])
    older = shutdown.capture_close_job(collection, None, require_choice=False)
    add_card(collection, [(date(2026, 9, 8), 4, 0, 2500)])
    newer = shutdown.capture_close_job(collection, None, require_choice=False)
    first = json.loads((older / "request.json").read_text())
    second = json.loads((newer / "request.json").read_text())
    assert second["sequence"] > first["sequence"]
    # A freshly launched process has no in-memory capture-order state.
    script = """
import sys
from addon.shutdown import finish_pending_exports
from addon.research import SharingConfig
finish_pending_exports(sys.argv[1], SharingConfig(), require_choice=False)
"""
    run_python(script, collection.path)
    assert len(read_csv(destination)) == 2
    assert not older.exists() and not newer.exists()
    assert load_report(storage.database_path(collection.path)).analyzed_cards == 2


def test_process_lock_serializes_publishers_without_blocking_close_capture(collection, tmp_path):
    destination = tmp_path / "scores.csv"
    save_daily_settings(collection.path, DailySettings(True, str(destination)))
    ready, acquired = tmp_path / "ready", tmp_path / "acquired"
    script = """
import sys
from pathlib import Path
from addon.storage import profile_job_lock
Path(sys.argv[2]).write_text('ready')
with profile_job_lock(sys.argv[1]):
    Path(sys.argv[3]).write_text('locked')
"""
    process = None
    try:
        with storage.profile_job_lock(collection.path):
            process = subprocess.Popen(
                [sys.executable, "-c", script, collection.path, str(ready), str(acquired)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 15
            while not ready.exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            time.sleep(0.1)
            assert not acquired.exists(), "Other process ignored the profile publication lock"
            # Simulate the serialized collection worker while an older publisher
            # still owns the lock. Capture must not acquire the publisher lock.
            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(
                    shutdown.capture_close_job, collection, None, require_choice=False
                ).result(timeout=10)
            assert (job / "request.json").is_file()
            assert not acquired.exists()
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stdout + stderr
        assert acquired.exists()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()


def run_python(script, *args):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", script, *map(str, args)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


QT_SETUP = """
import sys, threading, time, weakref
from pathlib import Path
from types import SimpleNamespace
import aqt
from aqt import gui_hooks
from aqt.qt import QApplication, QMainWindow, QTimer
from aqt.taskman import TaskManager
from anki.collection import Collection
from anki.cards import Card
from anki.scheduler.v3 import CardAnswer
from addon import shutdown, storage
from addon.daily import DailyUpdater

app = QApplication([])
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.col = Collection(sys.argv[1])
        self.col.set_config('fsrs', True)
        self.state = 'review'
        self._background_op_count = 0
        self.taskman = TaskManager(self)
        self.addonManager = SimpleNamespace(getConfig=lambda name: None)
        self.reviewer = SimpleNamespace(card=object())
    def weakref(self):
        return weakref.proxy(self)
    def _increase_background_ops(self):
        self._background_op_count += 1
    def _decrease_background_ops(self):
        self._background_op_count -= 1
main = Main()
aqt.mw = main
updater = DailyUpdater(main, 'addon')
"""


@pytest.mark.skipif(importlib.util.find_spec("aqt") is None, reason="Needs Anki desktop Qt")
@pytest.mark.parametrize("older_update", [False, True])
def test_immediate_exit_after_last_native_answer_finishes_export(tmp_path, older_update):
    source, destination = tmp_path / "collection.anki2", tmp_path / "daily.csv"
    script = (
        QT_SETUP
        + """
storage.save_daily_settings(main.col.path, storage.DailySettings(True, sys.argv[2]))
closed = threading.Event()
older_started = threading.Event()
real_build = storage.build_report
old_thread = None
def delayed_build(*args, **kwargs):
    assert closed.wait(10), 'Worker depended on a Qt callback'
    assert main.col is None, 'Live collection was not released before calculation'
    time.sleep(.2)  # Ensure Python's main thread returns before publication.
    return real_build(*args, **kwargs)
storage.build_report = delayed_build

def finish_review_and_close():
    note = main.col.new_note(main.col.models.by_name('Basic'))
    note['Front'], note['Back'] = 'Final question', 'Answer'
    main.col.add_note(note, 1)
    top = main.col.sched.get_queued_cards().cards[0]
    card = Card(main.col, backend_card=top.card)
    card.start_timer()
    main.col.sched.answer_card(main.col.sched.build_answer(
        card=card, states=top.states, rating=CardAnswer.EASY))
    assert not main.col.sched.get_queued_cards().cards
    gui_hooks.reviewer_did_answer_card(main.reviewer, card, 4)
    main.reviewer.card = None
    gui_hooks.reviewer_will_end()
    main.state = 'overview'
    gui_hooks.state_did_change('overview', 'review')
    # No event processing between the last card and requesting close.
    gui_hooks.profile_will_close()
    main.col.close()
    main.col = None
    main.hide()
    aqt.mw = None
    closed.set()
    app.quit()

updater.timer.start()
if sys.argv[3] == 'True':
    from addon.update_job import UpdateJob, UpdateRequest
    from addon.metrics import Settings, anki_day
    from addon.research import SharingConfig
    main.state = 'overview'
    original_fingerprint = storage.input_fingerprint
    def pause_older(col, *args, **kwargs):
        if threading.current_thread().name != 'Quantified Cognition final export':
            older_started.set()
            assert closed.wait(10)
        return original_fingerprint(col, *args, **kwargs)
    storage.input_fingerprint = pause_older
    updater.check(force=True)
    deadline = time.monotonic() + 10
    while not older_started.is_set():
        app.processEvents()
        assert time.monotonic() < deadline
        time.sleep(.005)
    main.state = 'review'
QTimer.singleShot(0, finish_review_and_close)
app.exec()
print('Qt stopped before export', flush=True)
assert not Path(sys.argv[2]).exists()
# No join, event pumping or explicit export here. Normal interpreter shutdown
# keeps the owned worker alive while the original collection/window are gone.
"""
    )
    result = run_python(script, source, destination, older_update)
    assert "Qt stopped before export" in result.stdout
    assert destination.exists(), result.stdout + result.stderr
    rows = read_csv(destination)
    assert len(rows) == 1 and rows[0]["recall_cohort_count"] == "1"
    assert rows[0]["formation_pending_count"] == "1"
    assert not list(shutdown.pending_directory(source).glob("*/request.json"))


@pytest.mark.skipif(importlib.util.find_spec("aqt") is None, reason="Needs Anki desktop Qt")
def test_interrupted_handoff_recovers_automatically_at_next_profile_open(tmp_path):
    source, destination = tmp_path / "collection.anki2", tmp_path / "daily.csv"
    interrupted = """
import os, sys
from datetime import date
sys.path.insert(0, 'tests')
from test_backend import add_card
from anki.collection import Collection
from addon.shutdown import capture_close_job
from addon.storage import DailySettings, save_daily_settings
col = Collection(sys.argv[1])
col.set_config('fsrs', True)
save_daily_settings(col.path, DailySettings(True, sys.argv[2]))
add_card(col, [(date(2026, 9, 7), 3, 0, 2500)])
assert capture_close_job(col, None, require_choice=False)
os._exit(0)  # Interrupt before analysis/publication, after durable capture.
"""
    run_python(interrupted, source, destination)
    assert not destination.exists()
    assert list(shutdown.pending_directory(source).glob("*/request.json"))
    # Profile opening alone launches recovery; no Qt event loop or user action.
    run_python(QT_SETUP + "\nupdater.start()\nmain.col.close()\nmain.col = None\n", source)
    assert read_csv(destination)[0]["recall_cohort_count"] == "1"
    assert not list(shutdown.pending_directory(source).glob("*/request.json"))
