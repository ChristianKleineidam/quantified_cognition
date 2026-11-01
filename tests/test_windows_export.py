"""Real filesystem failures and process interruption, using synthetic saved data."""

import csv
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_storage import report_with_followup

from addon import storage
from addon.export import export_csv_atomic


def prepare(tmp_path, monkeypatch):
    source = tmp_path / "profile" / "collection.anki2"
    destination = tmp_path / "scores.csv"
    report = report_with_followup()
    storage.save_report(report, storage.database_path(source))
    storage.save_daily_settings(source, storage.DailySettings(True, str(destination)))
    export_csv_atomic(report, destination)

    def unexpected(*_args, **_kwargs):
        pytest.fail("CSV retries must not inspect or recalculate the collection")

    monkeypatch.setattr(storage, "input_fingerprint", unexpected)
    monkeypatch.setattr(storage, "build_report", unexpected)
    return source, destination, report


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows file sharing semantics")
def test_real_exclusive_windows_lock_preserves_csv_then_retries(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    source, destination, report = prepare(tmp_path, monkeypatch)
    previous = destination.read_bytes()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(destination), 0x80000000, 0, None, 3, 128, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError):
            storage.retry_fixed_csv(source)
        assert (
            storage.load_report(storage.database_path(source)).generated_at == report.generated_at
        )
    finally:
        api.CloseHandle(handle)
    assert destination.read_bytes() == previous
    assert storage.retry_fixed_csv(source).csv_exported
    assert not list(tmp_path.glob("*.tmp"))


def test_unavailable_parent_recovers_without_recalculating(tmp_path, monkeypatch):
    source, previous, report = prepare(tmp_path, monkeypatch)
    before = previous.read_bytes()
    unavailable = tmp_path / "unavailable"
    unavailable.write_text("Simulated unavailable drive mount")
    destination = unavailable / "scores.csv"
    storage.save_daily_settings(source, storage.DailySettings(True, str(destination)))
    with pytest.raises(OSError):
        storage.retry_fixed_csv(source)
    assert previous.read_bytes() == before
    unavailable.unlink()
    unavailable.mkdir()
    assert storage.retry_fixed_csv(source).csv_exported
    assert storage.load_report(storage.database_path(source)).generated_at == report.generated_at
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_interrupted_atomic_publication_preserves_previous_csv_and_can_retry(tmp_path, monkeypatch):
    source, destination, _report = prepare(tmp_path, monkeypatch)
    before = destination.read_bytes()
    ready = tmp_path / "ready"
    script = """
import os,sys,time
from pathlib import Path
from addon.export import export_csv_atomic
from addon.storage import load_report,database_path
source,destination,ready = map(Path,sys.argv[1:])
original=os.replace
def interrupted(src,dst):
    if Path(dst)==destination:
        ready.write_text('complete temp file; publication not performed')
        time.sleep(300)
    original(src,dst)
os.replace=interrupted
export_csv_atomic(load_report(database_path(source)),destination)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(source), str(destination), str(ready)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert process.poll() is None, process.communicate()
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process.terminate()
        process.communicate(timeout=10)
        assert destination.read_bytes() == before
        assert storage.retry_fixed_csv(source).csv_exported
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
