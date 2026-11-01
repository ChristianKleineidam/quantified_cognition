"""Immutable aggregate packages, explicit uploads, and durable deletion retries."""

from __future__ import annotations

import csv
import json
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .export import CSV_FIELDS, export_csv
from .health import load_health
from .research import RESEARCH_METADATA_FIELDS, sharing_state
from .service import ServiceError, call_service
from .storage import database_path, load_report, profile_job_lock, results_directory
from .vault import read_secret, write_secret

PACKAGE_COLUMNS = [key for key in CSV_FIELDS if key not in RESEARCH_METADATA_FIELDS]
PACKAGE_VERSION = 4


def _path(source):
    return results_directory(source) / "pending-submission.json"


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _consent(source, sharing):
    snapshot = sharing_state(source, sharing)
    if snapshot.current_choice != "opt_in":
        raise ServiceError("Choose Yes in Research sharing before preparing an upload.")
    return snapshot


def prepare_submission(source, config, sharing, *, reuse=True):
    config.validated_url()
    status = call_service(config, "/status")
    if not status.get("research_enabled"):
        raise ServiceError("Research uploads have not been enabled by the operator yet.")
    if (
        status.get("notice_version") != sharing.notice_version
        or status.get("notice_hash") != sharing.notice_hash
    ):
        raise ServiceError("The research notice changed. Update the add-on before uploading.")
    if not status.get("operator") or not str(status.get("privacy_url", "")).startswith("https://"):
        raise ServiceError("The research service has not published its privacy information yet.")
    with profile_job_lock(source, "submission"), profile_job_lock(source):
        snapshot = _consent(source, sharing)
        pending = _path(source)
        if reuse and pending.exists():
            saved = json.loads(pending.read_text(encoding="utf-8"))
            if (
                saved["service"] == config.base_url
                and saved["package"]["metadata"]["sharing_notice_hash"] == sharing.notice_hash
                and saved["consent_event"] == snapshot.choices[-1]["id"]
                and saved["package"].get("schema_version") == PACKAGE_VERSION
                and saved["package"].get("columns") == PACKAGE_COLUMNS
            ):
                return saved, status
        with tempfile.TemporaryDirectory(dir=results_directory(source)) as directory:
            path = Path(directory) / "aggregate.csv"
            export_csv(
                load_report(database_path(source)), path, health_snapshot=load_health(source)
            )
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = [[row[key] for key in PACKAGE_COLUMNS] for row in csv.DictReader(handle)]
        if not rows:
            raise ServiceError("There are no saved daily measurements to submit yet.")
        package = {
            "schema_version": PACKAGE_VERSION,
            "submission_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": snapshot.metadata(),
            "columns": PACKAGE_COLUMNS,
            "rows": rows,
        }
        saved = {
            "service": config.base_url,
            "consent_event": snapshot.choices[-1]["id"],
            "package": package,
        }
        _atomic(pending, saved)
        return saved, status


def upload_submission(source, config, sharing, submission_id):
    # Serialize explicit uploads against local withdrawal/deletion. This lock is
    # file/network-only and never used by Anki's collection or ordinary CSV jobs.
    with profile_job_lock(source, "submission"):
        snapshot = _consent(source, sharing)
        saved = json.loads(_path(source).read_text(encoding="utf-8"))
        package = saved["package"]
        if (
            package["submission_id"] != submission_id
            or saved["service"] != config.base_url
            or saved["consent_event"] != snapshot.choices[-1]["id"]
            or package["metadata"]["sharing_notice_hash"] != sharing.notice_hash
            or package.get("schema_version") != PACKAGE_VERSION
            or package.get("columns") != PACKAGE_COLUMNS
        ):
            raise ServiceError("Your sharing choice changed. Prepare and review a new package.")
        connection = read_secret(source, "research")
        if connection and connection.get("delete_pending"):
            raise ServiceError("Deletion is pending. Complete it before submitting again.")
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
        token = connection["id"] + ":" + connection["secret"]
        if not connection.get("registered"):
            call_service(config, "/research/participant", {}, token=token)
            connection["registered"] = True
            write_secret(source, "research", connection)
        receipt = call_service(config, "/research/submissions", package, token=token)
        if receipt.get("submission_id") != submission_id or not receipt.get("received_at"):
            raise ServiceError("The receipt was incomplete. Retry the same package to confirm it.")
        _atomic(results_directory(source) / "last-submission-receipt.json", receipt)
        _path(source).unlink(missing_ok=True)
        return receipt


def queue_deletion(source):
    with profile_job_lock(source, "submission"):
        connection = read_secret(source, "research")
        _path(source).unlink(missing_ok=True)
        if connection:
            connection["delete_pending"] = True
            write_secret(source, "research", connection)
        return connection is not None


def retry_deletion(source):
    from .service import ServiceConfig

    with profile_job_lock(source, "submission"):
        connection = read_secret(source, "research")
        if not connection or not connection.get("delete_pending"):
            return False
        result = call_service(
            ServiceConfig(connection["service"]),
            "/research/participant",
            method="DELETE",
            token=connection["id"] + ":" + connection["secret"],
        )
        if result.get("deleted") is not True:
            raise ServiceError("The deletion receipt was incomplete. Deletion remains queued.")
        write_secret(source, "research", None)
        (results_directory(source) / "last-submission-receipt.json").unlink(missing_ok=True)
        return True


def withdraw_sharing(source, sharing, choice):
    with profile_job_lock(source, "submission"):
        # Persist the deletion request before withdrawing permission, so a crash
        # at any following point cannot silently leave cloud data unqueued.
        if choice != "opt_in":
            queue_deletion(source)
        return sharing_state(source, sharing, event_type="sharing_choice", choice=choice)


def submission_summary(source):
    connection = read_secret(source, "research")
    if connection and connection.get("delete_pending"):
        return "Deletion is pending and retries while Anki is open. New uploads are blocked."
    receipt = results_directory(source) / "last-submission-receipt.json"
    if receipt.exists():
        return "Last submission received: " + json.loads(receipt.read_text())["received_at"]
    return "No research submission is recorded for this profile."
