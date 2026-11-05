"""One synthetic upload/retry/delete through an isolated hosted deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def query(cli, project, statement):
    completed = subprocess.run(
        [str(cli), "db", "query", "--linked", "--project-ref", project, statement],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode:
        raise RuntimeError("Isolated test database query failed; output withheld")
    value = json.loads(completed.stdout)
    for _ in range(5):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            value = json.loads(value)
            continue
        if isinstance(value, dict):
            for key in ("rows", "result", "data"):
                if key in value:
                    value = value[key]
                    break
            else:
                break
    raise ValueError("Unexpected isolated SQL result format")


def stored(cli, project, namespace):
    return query(
        cli,
        project,
        f"""select jsonb_build_object(
        'rows',(select count(*) from {namespace}private.submissions),
        'budget_rows',submissions,'budget_bytes',payload_bytes,
        'daily_rows',daily_submissions,'daily_bytes',daily_bytes)
        as snapshot from {namespace}private.storage_budget where singleton""",
    )[0]["snapshot"]


def check(config, cli):
    project = config["project_ref"]
    namespace = config["namespace"]
    if not re.fullmatch(r"[a-z]{20}", project) or not re.fullmatch(r"qst_[0-9a-f]{8}_", namespace):
        raise ValueError("Refusing an invalid project or non-test namespace")
    function_name = "qs-service-smoke-" + namespace[4:12]
    if config["function_name"] != function_name:
        raise ValueError("Refusing a non-test function")
    run_id = str(uuid.UUID(config["run_id"]))
    participant = str(uuid.UUID(config["participant_id"]))
    marker = query(
        cli,
        project,
        f"""select run_id::text,
        expires_at>now() as current from {namespace}private.hosted_smoke_guard""",
    )
    if len(marker) != 1 or marker[0] != {"run_id": run_id, "current": True}:
        raise ValueError("Isolated deployment marker is missing, expired or different")
    baseline = stored(cli, project, namespace)
    if baseline["rows"] != 0 or baseline["budget_rows"] != 0:
        raise ValueError("Refusing a test database containing research submissions")
    service = f"https://{project}.supabase.co/functions/v1/{function_name}"
    checks = []

    def request(name, route, data=None, *, method="POST", secret=None, expected=200):
        headers = {"Content-Type": "application/json", "x-qs-smoke-key": config["access_key"]}
        if secret is not None:
            headers["Authorization"] = f"Bearer {participant}:{secret}"
        encoded = None if data is None else json.dumps(data, separators=(",", ":")).encode()
        started = time.monotonic()
        try:
            response = build_opener(NoRedirect()).open(
                Request(service + route, encoded, headers, method=method),
                timeout=35,
            )
        except HTTPError as error:
            response = error
        with response:
            result = json.loads(response.read(65536))
            checks.append(
                {
                    "step": name,
                    "status": response.code,
                    "milliseconds": round((time.monotonic() - started) * 1000),
                }
            )
            if response.code != expected:
                raise AssertionError(f"{name}: expected HTTP {expected}, received {response.code}")
        return result

    status = request("test_status", "/status", method="GET")
    if (
        status.get("oura_enabled") is not False
        or status.get("google_health_enabled") is not False
        or status.get("research_enabled") is not True
        or status.get("operator") != "Synthetic hosted smoke test"
    ):
        raise ValueError("Unexpected test service configuration")
    info = request("synthetic_terms", "/research/enrollment-information", method="GET")
    now = datetime.now(timezone.utc).isoformat()
    evidence = {
        "enrollment_id": participant,
        "participant_id": participant,
        "version": info["version"],
        "bundle_hash": info["bundle_hash"],
        "adult": True,
        "own_data": True,
        "explicit_consent": True,
        "signature_method": "explicit_consent",
        "signed_at_client": now,
        "before_first_results": True,
        "choice_event_id": 1,
        "notice_version": info["notice_version"],
        "notice_hash": info["notice_hash"],
    }
    schema = json.loads((ROOT / "supabase/functions/qs-service/schema.json").read_text())
    metadata = dict.fromkeys(schema["metadata"], "")
    metadata.update(
        research_participant_id=participant,
        research_consent_active=True,
        sharing_current_choice="opt_in",
        sharing_notice_version=info["notice_version"],
        sharing_notice_hash=info["notice_hash"],
    )
    packet = {
        "schema_version": schema["package_version"],
        "enrollment_id": participant,
        "submission_id": str(uuid.uuid4()),
        "created_at": now,
        "metadata": metadata,
        "columns": schema["columns"],
        "rows": [["2026-10-01" if name == "date" else "" for name in schema["columns"]]],
    }
    secret, receipt = config["upload_secret"], config["receipt_secret"]
    registered = False
    deleted = False
    try:
        request("register_synthetic_identity", "/research/participant", {}, secret=secret)
        registered = True
        request("enroll_synthetic_identity", "/research/enrollment", evidence, secret=secret)
        first = request("upload", "/research/submissions", packet, secret=secret)
        after_upload = stored(cli, project, namespace)
        assert after_upload["rows"] == after_upload["budget_rows"] == 1
        assert after_upload["daily_rows"] == baseline["daily_rows"] + 1
        assert after_upload["budget_bytes"] > 0
        second = request("retry_same_package", "/research/submissions", packet, secret=secret)
        after_retry = stored(cli, project, namespace)
        assert first == second, "Retry changed the original receipt"
        assert after_retry == after_upload, "Retry created a row or charged storage again"
        removed = request(
            "delete",
            "/research/participant",
            {"receipt_secret": receipt},
            method="DELETE",
            secret=secret,
        )
        assert removed["active_storage_deleted"] is True and removed["complete"] is True
        deleted = True
        after_delete = stored(cli, project, namespace)
        assert (
            after_delete["rows"] == after_delete["budget_rows"] == after_delete["budget_bytes"] == 0
        )
        assert after_delete["daily_rows"] == after_upload["daily_rows"]
        confirmed = request("deletion_receipt", "/research/deletion-status", {}, secret=receipt)
        assert confirmed["complete"] is True
        request(
            "stale_upload_rejected", "/research/submissions", packet, secret=secret, expected=401
        )
        assert stored(cli, project, namespace) == after_delete
        return {
            "status": "passed",
            "project_ref": project,
            "namespace": namespace,
            "function_name": function_name,
            "synthetic_only": True,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "same_receipt_on_retry": True,
            "upload_state": after_upload,
            "retry_state": after_retry,
            "deleted_state": after_delete,
            "package_sha256": hashlib.sha256(json.dumps(packet).encode()).hexdigest(),
        }
    finally:
        if registered and not deleted:
            request(
                "cleanup_after_failure",
                "/research/participant",
                {"receipt_secret": receipt},
                method="DELETE",
                secret=secret,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--supabase", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = check(json.loads(args.config.read_text()), args.supabase)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("status", "project_ref", "same_receipt_on_retry", "deleted_state")
            }
        )
    )


if __name__ == "__main__":
    main()
