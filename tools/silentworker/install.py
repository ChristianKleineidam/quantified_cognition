"""Provision the dedicated Silentworker verifier; never enables online app features."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from poll import CONFIG, RUNTIME, STATE, unpack_source

SOURCE = Path(__file__).resolve().parent
PGLITE_URL = "https://registry.npmjs.org/@electric-sql/pglite/-/pglite-0.5.11.tgz"
PGLITE_INTEGRITY = (
    "SWuqsTjA4DDWqgZ05PC1RJaq8e00p5KACxbqaikDFQ8dIGA0SLRzuPniky7IW+q/"
    "c5RDNMdEXwv7mFBWIx9Hxw=="
)
HEALTH_ENTRIES = """

# Quantified Cognition verification, installed by tools/silentworker/install.py.
[[systemd_timers]]
name = "quantified-cognition-tests.timer"
require_enabled = true
require_active = true

[[systemd_services]]
name = "quantified-cognition-tests.service"
require_result = "success"
allow_inactive = true
"""


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def download(url: str, limit: int) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeError("Download exceeds expected size")
    return payload


def prepare() -> None:
    for path, mode in ((CONFIG, 0o700), (RUNTIME, 0o755), (STATE, 0o755),
                       (STATE / "runs", 0o755)):
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink: {path}")
        path.mkdir(mode=mode, parents=True, exist_ok=True)
        path.chmod(mode)
    key = CONFIG / "deploy_key"
    if not key.exists():
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
            "quantified-cognition-silentworker-read-only", "-f", str(key))
    key.chmod(0o600)
    # Pin GitHub's host keys from its authoritative HTTPS metadata, not ssh-keyscan.
    metadata = json.loads(download("https://api.github.com/meta", 1024 * 1024))
    keys = [key for key in metadata["ssh_keys"] if key.startswith("ssh-ed25519 ")]
    if not keys or any("\n" in key for key in keys):
        raise RuntimeError("No valid GitHub Ed25519 host key")
    (CONFIG / "known_hosts").write_text("".join(f"github.com {key}\n" for key in keys))


def provision(node_source: Path) -> None:
    import pwd

    try:
        account = pwd.getpwnam("qctests")
        if account.pw_dir != "/nonexistent" or account.pw_uid >= 1000:
            raise RuntimeError("Existing qctests account is not the expected system user")
    except KeyError:
        run("useradd", "--system", "--user-group", "--no-create-home", "--home-dir",
            "/nonexistent", "--shell", "/usr/sbin/nologin", "qctests")
    (RUNTIME / "bin").mkdir(exist_ok=True)
    node = RUNTIME / "bin/node"
    if not node.exists():
        shutil.copyfile(node_source.resolve(strict=True), node)
        node.chmod(0o755)
    node_version = subprocess.check_output([str(node), "--version"], text=True).strip()
    if int(node_version.lstrip("v").split(".")[0]) < 22:
        raise RuntimeError("Node 22 or later is required")
    pglite = RUNTIME / "pglite"
    if not pglite.exists():
        payload = download(PGLITE_URL, 48 * 1024 * 1024)
        actual = base64.b64encode(hashlib.sha512(payload).digest()).decode()
        if actual != PGLITE_INTEGRITY:
            raise RuntimeError("PGlite package integrity mismatch")
        with tempfile.TemporaryDirectory(prefix="pglite-", dir=RUNTIME) as scratch:
            unpack_source(payload, Path(scratch))
            shutil.move(str(Path(scratch) / "package"), str(pglite))
    if json.loads((pglite / "package.json").read_text())["version"] != "0.5.11":
        raise RuntimeError("Unexpected PGlite runtime version")
    if not (RUNTIME / "venv/bin/python").exists():
        run("python3", "-m", "venv", str(RUNTIME / "venv"))
    requirements = RUNTIME / "runtime-requirements.txt"
    shutil.copyfile(SOURCE / requirements.name, requirements)
    run(str(RUNTIME / "venv/bin/python"), "-m", "pip", "install", "--no-cache-dir",
        "--only-binary=:all:", "-r", str(requirements))
    pg_bin = RUNTIME / "pgsql/usr/lib/postgresql/17/bin"
    for executable in ("initdb", "pg_ctl", "postgres"):
        if not (pg_bin / executable).is_file():
            raise RuntimeError("Provision the root-owned PostgreSQL 17 runtime before installation")
    postgres_version = subprocess.check_output(
        [str(pg_bin / "postgres"), "--version"], text=True,
    ).strip()
    for name in ("poll.py",):
        shutil.copyfile(SOURCE / name, RUNTIME / name)
        (RUNTIME / name).chmod(0o644)
    for name in ("quantified-cognition-tests.service", "quantified-cognition-tests.timer"):
        shutil.copyfile(SOURCE / name, Path("/etc/systemd/system") / name)
    frozen = subprocess.check_output(
        [str(RUNTIME / "venv/bin/python"), "-m", "pip", "freeze"], text=True,
    )
    (RUNTIME / "installed-requirements.txt").write_text(frozen)
    (RUNTIME / "runtime.json").write_text(json.dumps({
        "installed_at": datetime.now(timezone.utc).isoformat(), "node": node_version,
        "node_sha256": hashlib.sha256(node.read_bytes()).hexdigest(), "pglite": "0.5.11",
        "pglite_sha512": PGLITE_INTEGRITY,
        "postgres": postgres_version, "postgres_bin": str(pg_bin),
    }, indent=2) + "\n")
    run("systemd-analyze", "verify", "/etc/systemd/system/quantified-cognition-tests.service",
        "/etc/systemd/system/quantified-cognition-tests.timer")
    run("systemctl", "daemon-reload")


def register_health() -> None:
    import tomllib

    inventory = Path("/opt/random_projects/silentworker/expected_state.toml")
    content = inventory.read_text()
    if 'name = "quantified-cognition-tests.timer"' not in content:
        updated = content + HEALTH_ENTRIES
        tomllib.loads(updated)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copyfile(inventory, STATE / f"health-inventory-before-{stamp}.toml")
        inventory.write_text(updated)


def main():
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "silentworker":
        raise SystemExit("Installer must run as root on silentworker")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--node-source", type=Path)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    prepare()
    if args.prepare_only:
        print((CONFIG / "deploy_key.pub").read_text().strip())
        return
    if args.node_source is None:
        parser.error("Pass an installed Node 22+ binary with --node-source")
    provision(args.node_source)
    if args.enable:
        # Keep failures visible through the existing machine-health publisher.
        register_health()
        run("systemctl", "enable", "--now", "quantified-cognition-tests.timer")


if __name__ == "__main__":
    main()
