"""Independent monitoring and encrypted recovery copies for Quantified Cognition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

import psycopg
from psycopg.conninfo import conninfo_to_dict

from tools.backend_recovery import PROJECT, deletion_ledger, snapshot, validate_ledger

FILE = re.compile(r"snapshot-([0-9a-f-]{36})\.age$")
STATE = Path("/var/lib/quantified-cognition-backup")
BUDGET = 1024 * 1024 * 1024


def utcnow():
    return datetime.now(timezone.utc)


def atomic(path, value, *, mode=0o600):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        os.chmod(temporary, mode)
        json.dump(value, output, separators=(",", ":"), allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    # Durably publish the rename, not only the file contents.
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def connection(config, *, inventory=False):
    settings = conninfo_to_dict(config["dsn"])
    host = settings.get("host", "")
    root = settings.get("sslrootcert")
    trusted = root == "system"
    if root == "/opt/quantified-cognition-ops/supabase-ca.pem":
        trusted = hashlib.sha256(Path(root).read_bytes()).hexdigest() == config.get("ca_sha256")
    if (
        not host.endswith(".pooler.supabase.com")
        or settings.get("sslmode") != "verify-full"
        or not trusted
    ):
        raise ValueError("Operations require a verified TLS pooler connection")
    role = "qs_backup_agent" if inventory else "qs_monitor_reader"
    if settings.get("user") != role + "." + PROJECT:
        raise ValueError("Unexpected operations role")
    result = psycopg.connect(
        **settings,
        autocommit=True,
        connect_timeout=10,
        application_name="qc_backup" if inventory else "qc_monitor",
    )
    if inventory:
        # Only the narrowly granted recovery-inventory operations can write.
        result.execute("set default_transaction_read_only=off")
    return result


def age_seconds(value, now):
    if value is None:
        return float("inf")
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return float("inf")
    return (now - value).total_seconds()


def assess(status, database, backup, now, *, backup_required=True):
    """Return fixed, non-sensitive codes suitable for the existing alert channel."""
    issues = []
    oura_expected = isinstance(database, dict) and database.get("oura_expected_enabled") is True
    research_expected = (
        isinstance(database, dict) and database.get("research_expected_enabled") is True
    )
    if not isinstance(status, dict):
        issues.append("public_status_unreachable")
    elif (
        status.get("oura_enabled") is not oura_expected
        or status.get("google_health_enabled") is not False
        or status.get("research_enabled") is not research_expected
    ):
        issues.append("feature_state_unexpected")
    if not isinstance(database, dict):
        issues.append("database_unreachable")
    else:
        if abs(age_seconds(database.get("server_time"), now)) > 120:
            issues.append("clock_difference")
        for field in ("maintenance_at", "probe_checked_at", "research_maintenance_at"):
            seconds = age_seconds(database.get(field), now)
            if seconds > 720 or seconds < -120:
                issues.append(field + "_stale")
        if database.get("oura_authorization_monitor_required") and age_seconds(
            database.get("oura_authorization_monitor_at"), now
        ) > 600:
            issues.append("oura_authorization_monitor_stale")
        if database.get("probe_ok") is not True:
            issues.append("database_probe_failed")
        issues.extend(
            code if re.fullmatch(r"[a-z0-9_]{1,80}", code) else "private_alert"
            for code in database.get("alerts", [])
        )
    if not backup_required:
        return sorted(set(issues))
    if not isinstance(backup, dict):
        issues.append("backup_state_unavailable")
    else:
        if age_seconds(backup.get("last_ledger_at"), now) > 720:
            issues.append("deletion_ledger_stale")
        if age_seconds(backup.get("last_backup_at"), now) > 26 * 3600:
            issues.append("backup_stale")
        if backup.get("status") != "healthy":
            issues.append("backup_job_failed")
    return sorted(set(issues))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def public_status(url):
    request = Request(url, headers={"User-Agent": "Quantified-Cognition-Operations/1"})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Status unavailable")
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError("Oversized public status")
        return json.loads(data)


def monitor(config):
    status = database = backup = None
    try:
        status = public_status(config["status_url"])
    except Exception:
        pass
    try:
        with connection(config) as db:
            row = db.execute("""select clock_timestamp(),maintenance_at,probe_checked_at,
                research_maintenance_at,probe_ok,oura_expected_enabled,research_expected_enabled,
                oura_authorization_monitor_at,oura_authorization_monitor_required
                from qs_private.ops_state where singleton""").fetchone()
            database = dict(
                zip(
                    (
                        "server_time",
                        "maintenance_at",
                        "probe_checked_at",
                        "research_maintenance_at",
                        "probe_ok",
                        "oura_expected_enabled",
                        "research_expected_enabled",
                        "oura_authorization_monitor_at",
                        "oura_authorization_monitor_required",
                    ),
                    row,
                    strict=True,
                )
            )
            database["alerts"] = [
                row[0]
                for row in db.execute(
                    "select code from qs_private.ops_alerts where active order by code"
                )
            ]
    except Exception:
        database = None
    try:
        backup = json.loads(Path(config["backup_status"]).read_text())
    except Exception:
        pass
    now = utcnow()
    required = config.get("backup_required", True)
    issues = assess(status, database, backup, now, backup_required=required)
    report = {
        "checked_at": now.isoformat(),
        "healthy": not issues,
        "issues": issues,
        "backup_monitoring": "enabled" if required else "not_commissioned",
    }
    atomic(Path(config["state"]) / "status.json", report, mode=0o644)
    print(json.dumps(report), flush=True)
    return 1 if issues else 0


def merge_ledger(path, fresh, *, existing_files):
    if path.exists():
        previous = json.loads(path.read_text())
        if previous.get("project") != PROJECT or previous.get("version") != 1:
            raise ValueError("Invalid independent deletion ledger")
        known = validate_ledger(previous["deletions"])
    elif existing_files:
        raise ValueError("Independent deletion ledger is missing")
    else:
        known = {}
    fresh = validate_ledger(fresh)
    changed = bool(set(fresh) - known.keys())
    for person, when in fresh.items():
        if person not in known or datetime.fromisoformat(when) < datetime.fromisoformat(
            known[person]
        ):
            known[person] = when
    atomic(
        path,
        {"version": 1, "project": PROJECT, "checked_at": utcnow().isoformat(), "deletions": known},
    )
    return known, changed


def files(directory):
    result = []
    for path in directory.glob("snapshot-*.age"):
        match = FILE.fullmatch(path.name)
        if match is None or path.is_symlink() or path.resolve().parent != directory.resolve():
            raise ValueError("Unexpected backup file")
        uuid.UUID(match[1])
        result.append(path)
    return sorted(result, key=lambda path: path.stat().st_mtime)


def remove_file(path):
    if FILE.fullmatch(path.name) is None or path.is_symlink():
        raise ValueError("Unexpected backup removal target")
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)


def reconcile(db, directory, *, purge=False):
    copies = db.execute("""select id from qs_governance.extracts e where holder='recovery'
        and location_reference='silentworker-qc:'||id::text and state<>'deleted'
        and (access_until<=now() or exists(select 1 from qs_governance.extract_members m
             where m.extract_id=e.id and m.deletion_requested_at is not null))""").fetchall()
    pending = {str(row[0]) for row in copies}
    for path in files(directory):
        identity = FILE.fullmatch(path.name)[1]
        if purge or identity in pending or time.time() - path.stat().st_mtime > 7 * 86400:
            remove_file(path)
    existing = {FILE.fullmatch(path.name)[1] for path in files(directory)}
    for row in db.execute("""select id from qs_governance.extracts where holder='recovery'
        and location_reference='silentworker-qc:'||id::text and state<>'deleted'""").fetchall():
        if str(row[0]) not in existing:
            db.execute("select qs_governance.confirm_recovery_removed(%s)", (row[0],))


def encrypted_snapshot(db, directory, recipient):
    identity = str(uuid.uuid4())
    inventory = db.execute("select qs_governance.begin_recovery_copy(%s)", (identity,)).fetchone()[
        0
    ]
    destination = directory / f"snapshot-{identity}.age"
    temporary = destination.with_suffix(".age.partial")
    try:
        with temporary.open("xb") as output:
            process = subprocess.Popen(
                ["age", "--encrypt", "--recipient", recipient],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
            )
            try:
                info = snapshot(db, inventory, process.stdin)
                process.stdin.close()
                if process.wait(timeout=60):
                    raise RuntimeError("Recovery encryption failed")
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                process.kill()
                process.wait(timeout=10)
                raise
        temporary.replace(destination)
        with destination.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        atomic(
            destination.with_suffix(".json"),
            {
                "copy_id": identity,
                "created_at": utcnow().isoformat(),
                "sha256": digest,
                **info,
            },
        )
        return destination
    except BaseException:
        temporary.unlink(missing_ok=True)
        if destination.exists():
            remove_file(destination)
        # A failed confirmation is retried as an orphan on the next pass.
        db.execute("select qs_governance.confirm_recovery_removed(%s)", (identity,))
        raise


def backup(config, *, force=False):
    import fcntl

    state = Path(config["state"])
    directory = state / "snapshots"
    directory.mkdir(mode=0o700, exist_ok=True)
    with (state / "agent.lock").open("a") as lock:
        # A timer retry waits for a running encryption rather than duplicating it.
        fcntl.flock(lock, fcntl.LOCK_EX)
        with connection(config, inventory=True) as db:
            for partial in directory.glob("snapshot-*.age.partial"):
                if (
                    partial.is_symlink()
                    or FILE.fullmatch(partial.name.removesuffix(".partial")) is None
                ):
                    raise ValueError("Unexpected partial recovery file")
                partial.unlink()
            ledger, changed = merge_ledger(
                state / "deletions.json", deletion_ledger(db), existing_files=bool(files(directory))
            )
            reconcile(db, directory, purge=changed)
            if (
                ledger
                and db.execute(
                    """select exists(select 1 from qs_private.participants
                where deleted_at is null and id=any(%s::uuid[]))""",
                    (list(ledger),),
                ).fetchone()[0]
            ):
                raise RuntimeError("Source restore requires deletion replay")
            existing = files(directory)
            due = not existing or time.time() - existing[-1].stat().st_mtime > 25 * 3600
            if force or due:
                encrypted_snapshot(db, directory, config["recipient"])
            _, changed = merge_ledger(
                state / "deletions.json", deletion_ledger(db), existing_files=bool(files(directory))
            )
            reconcile(db, directory, purge=changed)
            existing = files(directory)
            while len(existing) > 1 and sum(path.stat().st_size for path in existing) > BUDGET:
                remove_file(existing.pop(0))
            reconcile(db, directory)
            existing = files(directory)
            now = utcnow()
            last = (
                datetime.fromtimestamp(existing[-1].stat().st_mtime, timezone.utc)
                if existing
                else None
            )
            report = {
                "checked_at": now.isoformat(),
                "last_ledger_at": now.isoformat(),
                "last_backup_at": last.isoformat() if last else None,
                "status": "healthy" if existing else "awaiting_fresh_backup",
                "copies": len(existing),
                "bytes": sum(path.stat().st_size for path in existing),
            }
            atomic(state / "status.json", report, mode=0o644)
            print(json.dumps(report), flush=True)
            return 0 if existing else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("monitor", "ledger", "backup"))
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        result = (
            monitor(config)
            if args.mode == "monitor"
            else backup(
                config,
                force=args.mode == "backup",
            )
        )
    except Exception:
        # Connection exceptions can contain credentials or SQL; never log them.
        print(
            json.dumps({"event": "qs_ops_failure", "mode": args.mode, "code": "operation_failed"}),
            flush=True,
        )
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
