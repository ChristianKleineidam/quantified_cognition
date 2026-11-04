"""Separate, durable study enrollment; never inferred from the sharing preference."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .research import audit_path, sharing_state
from .service import ServiceError, call_service
from .storage import profile_job_lock
from .vault import read_secret, write_secret


def load_bundle():
    frozen = json.loads(Path(__file__).with_name("research_enrollment.json").read_text("utf-8"))
    canonical = json.dumps(
        frozen["bundle"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if hashlib.sha256(canonical.encode()).hexdigest() != frozen["sha256"]:
        raise ServiceError("The saved research information failed its integrity check.")
    return frozen


def _database(source):
    connection = sqlite3.connect(audit_path(source))
    connection.row_factory = sqlite3.Row
    connection.execute("""create table if not exists enrollments (
        id text primary key, service text not null, record text not null,
        receipt text, withdrawn_at text)""")
    return connection


def latest_enrollment(source):
    # Initialize the existing audit first, preserving its participant/exposure history.
    sharing_state(source)
    with closing(_database(source)) as connection:
        row = connection.execute("select * from enrollments order by rowid desc limit 1").fetchone()
    if row is None:
        return None
    return {
        **dict(row),
        "record": json.loads(row["record"]),
        "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
    }


def enrollment_information(source, config, sharing):
    frozen = load_bundle()
    status = call_service(config, "/status")
    if not status.get("research_enabled"):
        raise ServiceError("Research enrollment is not open. Your personal results stay available.")
    info = call_service(config, "/research/enrollment-information")
    if (
        info.get("bundle_hash") != frozen["sha256"]
        or info.get("version") != frozen["bundle"]["version"]
        or info.get("signature_method") != frozen["bundle"]["signature_method"]
        or info.get("notice_version") != sharing.notice_version
        or info.get("notice_hash") != sharing.notice_hash
    ):
        raise ServiceError("The research information changed. Update the add-on before joining.")
    return info


def _connection(source, config):
    connection = read_secret(source, "research")
    if connection and connection.get("delete_pending"):
        raise ServiceError("Deletion is pending. Complete it before joining again.")
    if connection and connection["service"] != config.base_url:
        raise ServiceError("Delete data from the previous research service before changing it.")
    if not connection:
        connection = {
            "id": str(uuid.uuid4()),
            "secret": secrets.token_urlsafe(32),
            "service": config.base_url,
            "registered": False,
        }
        write_secret(source, "research", connection)
    return connection


def enroll(source, config, sharing, *, adult, own_data, explicit_consent):
    if adult is not True or own_data is not True or explicit_consent is not True:
        raise ServiceError("Confirm eligibility and explicit consent before joining.")
    with profile_job_lock(source, "submission"):
        enrollment_information(source, config, sharing)
        snapshot = sharing_state(source, sharing)
        if snapshot.current_choice != "opt_in":
            raise ServiceError("Choose Yes in Research sharing before joining.")
        frozen = load_bundle()
        previous = latest_enrollment(source)
        connection = _connection(source, config)
        # Persist consent linked to the existing profile ID before network I/O.
        # A lost receipt is retried with the same evidence and identity.
        reusable = (
            previous
            and not previous["withdrawn_at"]
            and (
                previous["service"] == config.base_url
                and previous["record"]["bundle_hash"] == frozen["sha256"]
                and previous["record"]["participant_id"] == snapshot.participant_id
                and previous["record"]["signature_method"] == "explicit_consent"
                and previous["record"]["choice_event_id"] == snapshot.choices[-1]["id"]
            )
        )
        if reusable:
            record = previous["record"]
        else:
            snapshot = sharing_state(source, sharing, event_type="enrollment_signed")
            event = snapshot.events[-1]
            record = {
                "enrollment_id": str(uuid.uuid4()),
                "participant_id": snapshot.participant_id,
                "version": frozen["bundle"]["version"],
                "bundle_hash": frozen["sha256"],
                "adult": True,
                "own_data": True,
                "explicit_consent": True,
                "signature_method": "explicit_consent",
                "signed_at_client": event["occurred_at"],
                "before_first_results": snapshot.before_first_view(event),
                "choice_event_id": snapshot.choices[-1]["id"],
                "notice_version": sharing.notice_version,
                "notice_hash": sharing.notice_hash,
            }
            with closing(_database(source)) as database, database:
                database.execute(
                    "insert or ignore into metadata values (?, ?)",
                    ("enrollment_bundle:" + frozen["sha256"], json.dumps(frozen)),
                )
                database.execute(
                    "insert into enrollments values (?, ?, ?, null, null)",
                    (record["enrollment_id"], config.base_url, json.dumps(record)),
                )
        token = connection["id"] + ":" + connection["secret"]
        if not connection.get("registered"):
            result = call_service(config, "/research/participant", {}, token=token)
            if result.get("registered") is not True:
                raise ServiceError("The research connection could not be confirmed.")
            connection["registered"] = True
            write_secret(source, "research", connection)
        receipt = call_service(config, "/research/enrollment", record, token=token)
        if receipt.get("enrollment_id") != record["enrollment_id"] or not receipt.get(
            "received_at"
        ):
            raise ServiceError("Enrollment was not confirmed. Retry to retrieve the same receipt.")
        with closing(_database(source)) as database, database:
            database.execute(
                "update enrollments set receipt=? where id=?",
                (json.dumps(receipt), record["enrollment_id"]),
            )
        return latest_enrollment(source)


def require_enrollment(source, config):
    enrollment = latest_enrollment(source)
    connection = read_secret(source, "research")
    if (
        not enrollment
        or enrollment["withdrawn_at"]
        or not enrollment["receipt"]
        or (
            enrollment["service"] != config.base_url
            or enrollment["record"]["bundle_hash"] != load_bundle()["sha256"]
            or not connection
            or connection.get("delete_pending")
        )
    ):
        raise ServiceError("Complete research enrollment before preparing or sending data.")
    return enrollment


def withdraw_enrollment(source):
    sharing_state(source)
    with closing(_database(source)) as database, database:
        database.execute(
            "update enrollments set withdrawn_at=? where withdrawn_at is null",
            (datetime.now(timezone.utc).isoformat(),),
        )


def save_consent_copy(source, destination):
    enrollment = latest_enrollment(source)
    if not enrollment:
        raise ServiceError("No signed enrollment record is saved for this profile.")
    # Include the originally signed document bundle even after a software update.
    with closing(_database(source)) as database:
        row = database.execute(
            "select value from metadata where key=?",
            ("enrollment_bundle:" + enrollment["record"]["bundle_hash"],),
        ).fetchone()
    frozen = json.loads(row[0]) if row else load_bundle()
    if frozen["sha256"] != enrollment["record"]["bundle_hash"]:
        raise ServiceError("The original document copy could not be found.")
    from .submissions import _atomic

    _atomic(Path(destination), {"information": frozen, "enrollment": enrollment})
