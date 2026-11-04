"""Trusted controller: fetch main, then run repository code in a credential-free unit.

Install this file root-owned. Never execute a fetched revision as the controller.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

STATE = Path("/var/lib/quantified-cognition-tests")
RUNTIME = Path("/opt/quantified-cognition-tests")
CONFIG = Path("/etc/quantified-cognition-tests")
REPOSITORY = "git@github.com:ChristianKleineidam/quantified_cognition.git"
RUN_PATTERN = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{12}")


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def unpack_source(payload: bytes, destination: Path) -> None:
    """Extract only bounded regular Git files: no links, traversal or special files."""
    total = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for number, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if (number >= 5000 or path.is_absolute() or ".." in path.parts
                    or ".git" in path.parts or "\\" in member.name or not path.parts
                    or member.name in seen or not (member.isfile() or member.isdir())):
                raise ValueError(f"Unsafe source archive member: {member.name}")
            seen.add(member.name)
            total += member.size
            if total > 64 * 1024 * 1024:
                raise ValueError("Source archive exceeds 64 MiB")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)


def executor_command(source: Path, run_id: str) -> list[str]:
    properties = {
        "User": "qctests", "Group": "qctests", "WorkingDirectory": str(source),
        "NoNewPrivileges": "yes", "PrivateNetwork": "yes", "PrivateTmp": "yes",
        "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes",
        "ProtectControlGroups": "yes", "RestrictSUIDSGID": "yes",
        "CapabilityBoundingSet": "", "ReadWritePaths": str(source),
        "InaccessiblePaths": f"-{CONFIG} -/run/credentials -/etc/credstore "
                             "-/etc/credstore.encrypted -/run/dbus -/run/systemd/private "
                             "-/etc/quantified-cognition-ops -/var/lib/quantified-cognition-backup",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "MemoryHigh": "768M", "MemoryMax": "1250M", "MemorySwapMax": "512M",
        "CPUQuota": "75%", "Nice": "15", "IOWeight": "10", "TasksMax": "128",
        "RuntimeMaxSec": "45min", "LimitFSIZE": "64M", "UMask": "0077",
    }
    command = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
               f"--unit=quantified-cognition-test-{run_id}", "--service-type=exec"]
    for key, value in properties.items():
        command.append(f"--property={key}={value}")
    for key, value in {
        "HOME": "/nonexistent", "PATH": f"{RUNTIME}/venv/bin:{RUNTIME}/bin:/usr/bin:/bin",
        "QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_DISABLE_SANDBOX": "1",
        "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1", "TZ": "Europe/Berlin",
    }.items():
        command.append(f"--setenv={key}={value}")
    return [*command, str(RUNTIME / "venv/bin/python"), str(source / "tools/verify.py"),
            "--node", str(RUNTIME / "bin/node"), "--pglite",
            str(RUNTIME / "pglite/dist/index.js"), "--postgres-bin",
            str(RUNTIME / "pgsql/usr/lib/postgresql/17/bin"), "--report",
            str(source / ".reference/verification/report.json")]


def prune_runs(root: Path, current: Path, keep: int = 5) -> None:
    candidates = sorted(
        (p for p in root.iterdir() if RUN_PATTERN.fullmatch(p.name)
         and p.is_dir() and not p.is_symlink()), reverse=True,
    )
    retained = {current, *candidates[:keep]}
    for path in candidates:
        if path not in retained:
            shutil.rmtree(path)


def poll(*, force: bool = False) -> int:
    import pwd

    now = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
    checked = now()
    result = None
    mirror = STATE / "repository.git"
    git = ["git", f"--git-dir={mirror}", "-c", "core.hooksPath=/dev/null"]
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_SSH_COMMAND": f"ssh -i {CONFIG}/deploy_key -o IdentitiesOnly=yes "
                              f"-o UserKnownHostsFile={CONFIG}/known_hosts "
                              "-o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=15"}
    try:
        if not mirror.exists():
            subprocess.run(["git", "init", "--bare", str(mirror)], check=True,
                           env=env, capture_output=True, timeout=30)
        subprocess.run(
            [*git, "fetch", "--no-tags", "--depth=1", REPOSITORY,
             "+refs/heads/main:refs/remotes/origin/main"],
            check=True, env=env, capture_output=True, timeout=120,
        )
        revision = subprocess.check_output(
            [*git, "rev-parse", "refs/remotes/origin/main"], env=env, text=True, timeout=30,
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Invalid main revision")
        status_path = STATE / "status.json"
        previous = json.loads(status_path.read_text()) if status_path.exists() else {}
        if previous.get("revision") == revision and not force:
            successful = previous.get("status") == "passed"
            atomic_json(STATE / "poll.json", {"checked_at": checked, "revision": revision,
                        "status": "unchanged", "tests_passed": successful})
            print(f"main {revision[:12]} unchanged; tests {previous.get('status')}", flush=True)
            return 0 if successful else 1
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + revision[:12]
        run_root = STATE / "runs" / run_id
        source = run_root / "source"
        source.mkdir(parents=True, mode=0o755)
        payload = subprocess.check_output(
            [*git, "archive", "--format=tar", revision], env=env, timeout=60,
        )
        unpack_source(payload, source)
        account = pwd.getpwnam("qctests")
        for parent, _directories, files in os.walk(source):
            os.chown(parent, account.pw_uid, account.pw_gid)
            for name in files:
                os.chown(Path(parent) / name, account.pw_uid, account.pw_gid)
        result = {"revision": revision, "run_id": run_id, "status": "running",
                  "started_at": now(), "log": str(run_root / "run.log")}
        atomic_json(status_path, result)
        print(f"Testing main {revision} in {run_id}", flush=True)
        with (run_root / "run.log").open("xb") as log:
            completed = subprocess.run(executor_command(source, run_id), stdout=log,
                                       stderr=subprocess.STDOUT, timeout=2800, check=False)
        result.update(status="passed" if completed.returncode == 0 else "failed",
                      exit_code=completed.returncode, finished_at=now())
        atomic_json(status_path, result)
        atomic_json(run_root / "result.json", result)
        atomic_json(STATE / "poll.json", {"checked_at": now(), "revision": revision,
                    "status": "tested", "tests_passed": completed.returncode == 0})
        prune_runs(STATE / "runs", run_root)
        print(json.dumps(result), flush=True)
        return 0 if completed.returncode == 0 else 1
    except Exception as exc:
        if result is not None:
            result.update(status="failed", error=str(exc), finished_at=now())
            atomic_json(STATE / "status.json", result)
            atomic_json(STATE / "runs" / result["run_id"] / "result.json", result)
        atomic_json(STATE / "poll.json", {"checked_at": now(), "status": "error",
                                          "error": str(exc)})
        print(f"Silentworker test controller failed: {exc}", flush=True)
        return 1


def main():
    import fcntl
    import socket

    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "silentworker":
        raise SystemExit("Run the installed controller as root on silentworker")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rerun an unchanged revision")
    args = parser.parse_args()
    with (STATE / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another test controller is already running") from None
        raise SystemExit(poll(force=args.force))


if __name__ == "__main__":
    main()
