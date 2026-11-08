"""Encrypted daily/review snapshots, explicit uploads, and durable deletion retries."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .enrollment import require_enrollment, withdraw_enrollment
from .export import RESEARCH_PACKAGE_COLUMNS as PACKAGE_COLUMNS
from .export import export_csv
from .health import load_health
from .private_storage import read_json, write_json
from .research import sharing_state
from .research_crypto import providers as package_providers
from .research_crypto import public_configuration, seal_package
from .review_data import prepare_reviews
from .service import ServiceError, call_service
from .storage import (
    database_path,
    load_report,
    load_review_records,
    profile_job_lock,
    results_directory,
)
from .vault import read_secret, write_secret

PACKAGE_VERSION = 7


def _path(source):
    return results_directory(source) / "pending-submission.json"


def _atomic(path, value):
    if path.name == "pending-submission.json":
        write_json(path, value)
        return
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
    public = public_configuration()
    expected_encryption = {key: public[key] for key in ("algorithm", "key_id")}
    if status.get("research_encryption") != expected_encryption:
        raise ServiceError(
            "The research encryption key changed. Update the add-on before uploading."
        )
    if (
        status.get("notice_version") != sharing.notice_version
        or status.get("notice_hash") != sharing.notice_hash
    ):
        raise ServiceError("The research notice changed. Update the add-on before uploading.")
    if not status.get("operator") or not str(status.get("privacy_url", "")).startswith("https://"):
        raise ServiceError("The research service has not published its privacy information yet.")
    with profile_job_lock(source, "submission"), profile_job_lock(source):
        snapshot = _consent(source, sharing)
        enrollment = require_enrollment(source, config)
        pending = _path(source)
        if reuse and pending.exists():
            saved = read_json(pending)
            if (
                saved["service"] == config.base_url
                and saved["package"]["metadata"]["sharing_notice_hash"] == sharing.notice_hash
                and saved["consent_event"] == snapshot.choices[-1]["id"]
                and saved["package"].get("schema_version") == PACKAGE_VERSION
                and saved["package"].get("columns") == PACKAGE_COLUMNS
                and saved["package"].get("enrollment_id") == enrollment["id"]
                and saved.get("envelope", {}).get("key_id") == public["key_id"]
            ):
                return saved, status
        with io.StringIO(newline="") as handle:
            health = load_health(source, include_google_health=False)
            report = load_report(database_path(source))
            if report is None:
                raise ServiceError(
                    "Open the memory report to calculate your history before sharing."
                )
            export_csv(
                report,
                handle,
                health_snapshot=health,
                include_google_health=False,
            )
            handle.seek(0)
            rows = [[row[key] for key in PACKAGE_COLUMNS] for row in csv.DictReader(handle)]
            # The personal CSV also describes missing session matches. Those
            # algorithm-only placeholders do not count as imported Oura data.
            if not (health.days or health.sessions or health.sleep_windows):
                indices = [i for i, key in enumerate(PACKAGE_COLUMNS) if key.startswith("oura_")]
                for row in rows:
                    for index in indices:
                        row[index] = ""
        if not rows:
            raise ServiceError("There are no saved daily measurements to submit yet.")
        package = {
            "schema_version": PACKAGE_VERSION,
            "enrollment_id": enrollment["id"],
            "submission_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": snapshot.metadata(),
            "columns": PACKAGE_COLUMNS,
            "rows": rows,
            "reviews": prepare_reviews(
                source, snapshot.participant_id, report,
                load_review_records(database_path(source)),
            ),
        }
        saved = {
            "service": config.base_url,
            "consent_event": snapshot.choices[-1]["id"],
            "package": package,
            "envelope": seal_package(package),
        }
        _atomic(pending, saved)
        return saved, status


def upload_submission(source, config, sharing, submission_id):
    # Serialize explicit uploads against local withdrawal/deletion. This lock is
    # file/network-only and never used by Anki's collection or ordinary CSV jobs.
    with profile_job_lock(source, "submission"):
        snapshot = _consent(source, sharing)
        enrollment = require_enrollment(source, config)
        saved = read_json(_path(source))
        package = saved["package"]
        if (
            package["submission_id"] != submission_id
            or saved["service"] != config.base_url
            or saved["consent_event"] != snapshot.choices[-1]["id"]
            or package["metadata"]["sharing_notice_hash"] != sharing.notice_hash
            or package.get("schema_version") != PACKAGE_VERSION
            or package.get("columns") != PACKAGE_COLUMNS
            or package.get("enrollment_id") != enrollment["id"]
        ):
            raise ServiceError("Your sharing choice changed. Prepare and review a new package.")
        connection = read_secret(source, "research")
        if connection and connection.get("delete_pending"):
            raise ServiceError("Deletion is pending. Complete it before submitting again.")
        if connection and connection["service"] != config.base_url:
            raise ServiceError("Delete data from the previous research service before changing it.")
        if not connection:
            raise ServiceError("Complete research enrollment before sending data.")
        token = connection["id"] + ":" + connection["secret"]
        if "oura" in package_providers(package):
            from .oura_research import renew_authorization

            renew_authorization(source, config, required=True)
        if not connection.get("registered"):
            call_service(config, "/research/participant", {}, token=token)
            connection["registered"] = True
            write_secret(source, "research", connection)
        envelope = saved.get("envelope")
        if not envelope or envelope.get("key_id") != public_configuration()["key_id"]:
            raise ServiceError("Prepare a new encrypted research package before uploading.")
        receipt = call_service(config, "/research/submissions", envelope, token=token)
        if receipt.get("submission_id") != submission_id or not receipt.get("received_at"):
            raise ServiceError("The receipt was incomplete. Retry the same package to confirm it.")
        _atomic(results_directory(source) / "last-submission-receipt.json", receipt)
        _path(source).unlink(missing_ok=True)
        return receipt


def queue_deletion(source):
    import secrets

    with profile_job_lock(source, "submission"):
        connection = read_secret(source, "research")
        _path(source).unlink(missing_ok=True)
        if connection:
            connection["delete_pending"] = True
            connection.setdefault("deletion_secret", secrets.token_urlsafe(32))
            write_secret(source, "research", connection)
        withdraw_enrollment(source)
        return connection is not None


def retry_deletion(source):
    from .service import ServiceConfig

    with profile_job_lock(source, "submission"):
        connection = read_secret(source, "research")
        if not connection or not connection.get("delete_pending"):
            return False
        result = None
        if not connection.get("active_storage_deleted"):
            try:
                result = call_service(
                    ServiceConfig(connection["service"]),
                    "/research/participant",
                    {"receipt_secret": connection["deletion_secret"]},
                    method="DELETE",
                    token=connection["id"] + ":" + connection["secret"],
                )
            except ServiceError as error:
                if error.status not in (401, 410):
                    raise
                # Automatic Oura withdrawal already invalidated the upload
                # capability. The independently saved receipt still works.
        if result is None:
            result = call_service(
                ServiceConfig(connection["service"]),
                "/research/deletion-status",
                {},
                token=connection["id"] + ":" + connection["deletion_secret"],
            )
        if result.get("active_storage_deleted") is not True:
            raise ServiceError("The deletion receipt was incomplete. Deletion remains queued.")
        connection["active_storage_deleted"] = True
        connection.pop("secret", None)
        write_secret(source, "research", connection)
        _atomic(results_directory(source) / "research-deletion-receipt.json", result)
        if result.get("complete") is not True:
            return False
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
        if connection.get("active_storage_deleted"):
            return "Active research storage deleted; deletion of registered copies is pending."
        return "Deletion is pending and retries while Anki is open. New uploads are blocked."
    receipt = results_directory(source) / "last-submission-receipt.json"
    if receipt.exists():
        return "Last submission received: " + json.loads(receipt.read_text())["received_at"]
    return "No research submission is recorded for this profile."
