"""Install the reviewed operations agent; secrets arrive through systemd credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import socket
import subprocess
from pathlib import Path

RUNTIME = Path("/opt/quantified-cognition-ops")
CONFIG = Path("/etc/quantified-cognition-ops")
PYTHON = "/opt/quantified-cognition-tests/venv/bin/python"
RESEARCH_COUNT_DIR = Path("/opt/homeassistant/config/quantified_cognition_research")


def units(mode):
    backup = mode != "monitor"
    user = "qcbackup" if backup else "qcmonitor"
    state = "quantified-cognition-backup" if backup else "quantified-cognition-monitor"
    research_count = "" if backup else f" --research-count-file {RESEARCH_COUNT_DIR}/first_uploads"
    write_path = "" if backup else f"ReadWritePaths={RESEARCH_COUNT_DIR}\n"
    service = f"""[Unit]
Description=Quantified Cognition {mode}
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={user}
Group={user}
WorkingDirectory={RUNTIME}
ExecStart={PYTHON} -m tools.backend_ops {mode} --config %d/config{research_count}
LoadCredentialEncrypted=config:{CONFIG / ("backup.credential" if backup else "monitor.credential")}
StateDirectory={state}
StateDirectoryMode=0755
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
{write_path}ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=256M
CPUQuota=50%
Nice=15
TimeoutStartSec={"10min" if backup else "60s"}
LimitCORE=0
LimitFSIZE=512M
"""
    cadence = (
        "OnCalendar=*-*-* 02:15:00 Europe/Berlin\nPersistent=yes"
        if mode == "backup"
        else "OnBootSec=120s\nOnUnitInactiveSec=5min"
    )
    timer = f"""[Unit]
Description=Schedule Quantified Cognition {mode}

[Timer]
{cadence}
RandomizedDelaySec=15s
Unit=quantified-cognition-{mode}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def main():
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "silentworker":
        raise SystemExit("Run the operations installer as root on silentworker")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--enable-monitor", action="store_true")
    args = parser.parse_args()
    for user in ("qcbackup", "qcmonitor"):
        try:
            account = pwd.getpwnam(user)
            if account.pw_uid >= 1000 or account.pw_dir != "/nonexistent":
                raise RuntimeError("Unexpected existing operations account")
        except KeyError:
            subprocess.run(
                [
                    "useradd",
                    "--system",
                    "--user-group",
                    "--no-create-home",
                    "--home-dir",
                    "/nonexistent",
                    "--shell",
                    "/usr/sbin/nologin",
                    user,
                ],
                check=True,
            )
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    (RUNTIME / "tools").mkdir(mode=0o755, exist_ok=True)
    CONFIG.mkdir(mode=0o700, exist_ok=True)
    RESEARCH_COUNT_DIR.mkdir(mode=0o755, exist_ok=True)
    shutil.chown(RESEARCH_COUNT_DIR, user="qcmonitor", group="qcmonitor")
    recorded = {}
    for name in ("backend_ops.py", "backend_recovery.py"):
        source = Path(__file__).resolve().parent.parent / name
        target = RUNTIME / "tools" / name
        shutil.copyfile(source, target)
        target.chmod(0o644)
        recorded[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    certificate = RUNTIME / "supabase-ca.pem"
    shutil.copyfile(Path(__file__).resolve().parent / certificate.name, certificate)
    certificate.chmod(0o644)
    recorded[certificate.name] = hashlib.sha256(certificate.read_bytes()).hexdigest()
    (RUNTIME / "source-hashes.json").write_text(json.dumps(recorded, indent=2) + "\n")
    for mode in ("monitor", "ledger", "backup"):
        for suffix, text in zip(("service", "timer"), units(mode), strict=True):
            target = Path(f"/etc/systemd/system/quantified-cognition-{mode}.{suffix}")
            target.write_text(text)
            target.chmod(0o644)
            subprocess.run(["systemd-analyze", "verify", str(target)], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if args.enable or args.enable_monitor:
        modes = ("monitor", "ledger", "backup") if args.enable else ("monitor",)
        # The existing Home Assistant health automation owns notification delivery.
        inventory = Path("/opt/random_projects/silentworker/expected_state.toml")
        original = inventory.read_text()
        addition = ""
        for mode in modes:
            if f'name = "quantified-cognition-{mode}.timer"' not in original:
                addition += f"""\n[[systemd_timers]]
name = "quantified-cognition-{mode}.timer"
require_enabled = true
require_active = true

[[systemd_services]]
name = "quantified-cognition-{mode}.service"
require_result = "success"
allow_inactive = true
"""
        if addition:
            import tomllib

            tomllib.loads(original + addition)
            shutil.copyfile(inventory, CONFIG / "health-inventory-before.toml")
            inventory.write_text(original + addition)
        for mode in modes:
            subprocess.run(
                ["systemctl", "enable", "--now", f"quantified-cognition-{mode}.timer"], check=True
            )


if __name__ == "__main__":
    main()
