"""Test the archive/execution boundary and retained results without a live server."""

import io
import json
import sys
import tarfile
from types import SimpleNamespace

import pytest

from tools.silentworker import poll


def archive(name="addon/metrics.py", *, kind=tarfile.REGTYPE, link=""):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as stream:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = link
        data = b"value = 1\n"
        member.size = len(data) if kind == tarfile.REGTYPE else 0
        stream.addfile(member, io.BytesIO(data) if member.size else None)
    return output.getvalue()


def test_archive_extracts_code_without_executing_or_preserving_special_permissions(tmp_path):
    poll.unpack_source(archive(), tmp_path)
    assert (tmp_path / "addon/metrics.py").read_text() == "value = 1\n"


@pytest.mark.parametrize("name,kind,link", [
    ("../../outside", tarfile.REGTYPE, ""), ("/etc/file", tarfile.REGTYPE, ""),
    (".git/config", tarfile.REGTYPE, ""), ("addon/link", tarfile.SYMTYPE, "/etc"),
    ("addon/hardlink", tarfile.LNKTYPE, "/etc/passwd"),
    ("addon/fifo", tarfile.FIFOTYPE, ""), ("..\\outside", tarfile.REGTYPE, ""),
])
def test_source_archive_rejects_escape_links_and_special_files(tmp_path, name, kind, link):
    with pytest.raises(ValueError, match="Unsafe"):
        poll.unpack_source(archive(name, kind=kind, link=link), tmp_path)


def test_executor_has_no_network_credentials_or_privileged_identity(tmp_path):
    command = poll.executor_command(tmp_path / "source", "test")
    for prop in (
        "User=qctests", "Group=qctests", "PrivateNetwork=yes", "ProtectHome=yes",
        "NoNewPrivileges=yes", "ProtectSystem=strict", "CapabilityBoundingSet=",
        "MemoryMax=1250M", "CPUQuota=75%",
    ):
        assert f"--property={prop}" in command
    assert not any("deploy_key" in part or "LoadCredential" in part for part in command)
    assert str(tmp_path / "source/tools/verify.py") in command


@pytest.mark.parametrize("status,expected", [("passed", 0), ("failed", 1), ("running", 1)])
def test_unchanged_commit_reuses_result_and_failure_stays_visible(
    tmp_path, monkeypatch, status, expected,
):
    revision = "a" * 40
    (tmp_path / "repository.git").mkdir()
    poll.atomic_json(tmp_path / "status.json", {"revision": revision, "status": status})
    monkeypatch.setattr(poll, "STATE", tmp_path)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace())
    calls = []
    monkeypatch.setattr(poll.subprocess, "run", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(poll.subprocess, "check_output", lambda *a, **kw: revision)
    assert poll.poll() == expected
    assert len(calls) == 1 and "fetch" in calls[0][0]
    assert json.loads((tmp_path / "poll.json").read_text())["tests_passed"] == (expected == 0)


def test_fetch_failure_does_not_replace_last_test_result(tmp_path, monkeypatch):
    (tmp_path / "repository.git").mkdir()
    previous = {"revision": "a" * 40, "status": "passed"}
    poll.atomic_json(tmp_path / "status.json", previous)
    monkeypatch.setattr(poll, "STATE", tmp_path)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace())

    def unavailable(*args, **kwargs):
        raise OSError("Network unavailable")

    monkeypatch.setattr(poll.subprocess, "run", unavailable)
    assert poll.poll() == 1
    assert json.loads((tmp_path / "status.json").read_text()) == previous
    assert json.loads((tmp_path / "poll.json").read_text())["status"] == "error"


def test_retention_only_removes_named_run_directories(tmp_path):
    runs = [tmp_path / f"20261102T00000{i}000000Z-{'a' * 12}" for i in range(8)]
    for path in runs:
        path.mkdir()
    unrelated = tmp_path / "operator-notes"
    unrelated.mkdir()
    poll.prune_runs(tmp_path, runs[-1], keep=3)
    assert unrelated.is_dir()
    assert [p for p in runs if p.exists()] == runs[-3:]
