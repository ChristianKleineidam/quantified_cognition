import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_storage import report_with_followup

from addon import storage, submissions
from addon.research import SharingConfig, sharing_state
from addon.service import ServiceConfig, ServiceError

CONFIG = ServiceConfig("https://example.supabase.co/functions/v1/qs-service")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    sharing = SharingConfig()
    sharing_state(source, sharing, event_type="sharing_choice", choice="opt_in")
    storage.save_report(
        replace(report_with_followup(), parameters_hash="a" * 64), storage.database_path(source)
    )
    secrets = {}
    monkeypatch.setattr(submissions, "read_secret", lambda path, slot: secrets.get(slot))
    monkeypatch.setattr(
        submissions, "write_secret", lambda path, slot, value: secrets.update({slot: value})
    )
    status = {
        "research_enabled": True,
        "operator": "Synthetic study",
        "privacy_url": "https://example.org/privacy",
        "notice_version": sharing.notice_version,
        "notice_hash": sharing.notice_hash,
    }
    monkeypatch.setattr(submissions, "call_service", lambda *a, **kw: status)
    return source, sharing, secrets, status


def test_package_is_immutable_reused_aggregate_only_and_does_not_mark_first_view(setup):
    source, sharing, _, _ = setup
    saved, _ = submissions.prepare_submission(source, CONFIG, sharing)
    assert sharing_state(source, sharing).first_exposure is None
    packet = saved["package"]
    assert packet["metadata"]["research_consent_before_first_view"] is True
    assert packet["columns"] == submissions.PACKAGE_COLUMNS
    assert not any(
        key in packet["columns"] for key in ("card_id", "deck_id", "bedtime_start", "access_token")
    )
    storage.save_report(report_with_followup(), storage.database_path(source))
    assert submissions.prepare_submission(source, CONFIG, sharing)[0] == saved
    fresh = submissions.prepare_submission(source, CONFIG, sharing, reuse=False)[0]
    assert fresh["package"]["submission_id"] != packet["submission_id"]


def test_upload_requires_current_consent_and_uses_separate_capability_and_same_packet_on_retry(
    setup, monkeypatch
):
    source, sharing, secrets, _ = setup
    saved, _ = submissions.prepare_submission(source, CONFIG, sharing)
    identity = saved["package"]["submission_id"]
    received = []

    def server(config, route, data=None, *, token):
        assert token.split(":")[0] != saved["package"]["metadata"]["research_participant_id"]
        if route.endswith("participant"):
            return {"registered": True}
        received.append(json.dumps(data, sort_keys=True))
        if len(received) == 1:
            raise ServiceError("Lost response after storing packet")
        return {"submission_id": identity, "received_at": "2026-10-23T00:00:00Z"}

    monkeypatch.setattr(submissions, "call_service", server)
    with pytest.raises(ServiceError):
        submissions.upload_submission(source, CONFIG, sharing, identity)
    receipt = submissions.upload_submission(source, CONFIG, sharing, identity)
    assert receipt["submission_id"] == identity and received[0] == received[1]
    assert not submissions._path(source).exists()
    assert secrets["research"]["secret"] not in received[0]


def test_withdrawal_queues_deletion_blocks_old_upload_and_keeps_original_timing(setup, monkeypatch):
    source, sharing, secrets, _ = setup
    submissions.prepare_submission(source, CONFIG, sharing)
    first = sharing_state(source, sharing).metadata()["research_consent_first_given_at_utc"]
    secrets["research"] = {
        "id": "synthetic-id",
        "secret": "synthetic-secret",
        "service": CONFIG.base_url,
    }
    snapshot = submissions.withdraw_sharing(source, sharing, "declined")
    assert snapshot.current_choice == "declined" and secrets["research"]["delete_pending"]
    assert not submissions._path(source).exists()
    assert snapshot.metadata()["research_consent_first_given_at_utc"] == first
    with pytest.raises(ServiceError, match="Choose Yes"):
        submissions.upload_submission(source, CONFIG, sharing, "old-packet")
    monkeypatch.setattr(
        submissions, "call_service", lambda *a, **kw: (_ for _ in ()).throw(ServiceError("offline"))
    )
    with pytest.raises(ServiceError):
        submissions.retry_deletion(source)
    assert secrets["research"]["delete_pending"]
    monkeypatch.setattr(submissions, "call_service", lambda *a, **kw: {"deleted": False})
    with pytest.raises(ServiceError):
        submissions.retry_deletion(source)
    monkeypatch.setattr(submissions, "call_service", lambda *a, **kw: {"deleted": True})
    assert submissions.retry_deletion(source) is True and secrets["research"] is None


def test_changed_consent_event_invalidates_preview_even_after_opt_in_again(setup):
    source, sharing, _, _ = setup
    saved, _ = submissions.prepare_submission(source, CONFIG, sharing)
    sharing_state(source, sharing, event_type="sharing_choice", choice="deferred")
    sharing_state(source, sharing, event_type="sharing_choice", choice="opt_in")
    with pytest.raises(ServiceError, match="changed"):
        submissions.upload_submission(source, CONFIG, sharing, saved["package"]["submission_id"])


def test_sharing_change_refreshes_csv_metadata_without_recalculating(setup, tmp_path, monkeypatch):
    from datetime import date

    from addon import research

    source, sharing, _, _ = setup
    destination = tmp_path / "fixed.csv"
    settings = storage.DailySettings(True, str(destination))
    storage.save_daily_settings(source, settings)
    report = storage.load_report(storage.database_path(source))
    storage.save_report(report, storage.database_path(source), scheduled_day=date.today())
    storage.retry_fixed_csv(source, sharing)
    assert not storage.csv_export_pending(storage.database_path(source), destination)
    assert not storage.daily_update_due(storage.database_path(source), date.today(), destination)
    research.sharing_state(source, sharing, event_type="sharing_choice", choice="declined")
    assert storage.csv_export_pending(storage.database_path(source), destination)
    monkeypatch.setattr(storage, "build_report", lambda *a, **kw: pytest.fail("FSRS replay"))
    storage.retry_fixed_csv(source, sharing, only_if_pending=True)
    assert not storage.csv_export_pending(storage.database_path(source), destination)
    assert "declined" in destination.read_text(encoding="utf-8-sig")


def test_service_allowlist_matches_export_and_real_packages(setup):
    source, sharing, _, _ = setup
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "supabase/functions/qs-service/schema.json"
        ).read_text()
    )
    packet = submissions.prepare_submission(source, CONFIG, sharing)[0]["package"]
    assert packet["columns"] == schema["columns"]
    assert set(packet["metadata"]) == set(schema["metadata"])
    for row in packet["rows"]:
        for key, allowed in schema["enums"].items():
            assert row[packet["columns"].index(key)] in allowed


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Cross-runtime server validation needs Node"
)
def test_real_python_package_passes_the_deployed_server_validator(setup, monkeypatch):
    from dataclasses import replace

    from test_google_health import night, parsed, steps
    from test_sleep_sessions import session, snapshot, stamp

    from addon.health import save_google_health_import

    source, sharing, _, _ = setup
    imported = save_google_health_import(
        source,
        parsed([night()], [steps(count="0")]),
        start="2026-09-01",
        end="2026-10-01",
        sharing=sharing,
    )
    report = storage.load_report(storage.database_path(source))
    storage.save_report(
        replace(report, study_windows={"2026-09-07": [stamp(10), stamp(18)]}),
        storage.database_path(source),
    )
    naps = snapshot(session("local-nap-only", 8, 9))
    combined = replace(
        imported.snapshot,
        sessions=(*imported.snapshot.sessions, *naps.sessions),
        sleep_windows={**imported.snapshot.sleep_windows, **naps.sleep_windows},
    )
    monkeypatch.setattr(submissions, "load_health", lambda source: combined)
    packet = submissions.prepare_submission(source, CONFIG, sharing)[0]["package"]
    google_index = packet["columns"].index("google_health_total_sleep_seconds")
    assert any(row[google_index] == "25200" for row in packet["rows"])
    nap_index = packet["columns"].index("oura_nap_before_duration_seconds")
    assert any(float(row[nap_index]) == 3600 for row in packet["rows"] if row[nap_index])
    deep_index = packet["columns"].index("oura_nap_before_deep_sleep_seconds")
    assert any(float(row[deep_index]) == 3600 for row in packet["rows"] if row[deep_index])
    assert "local-nap-only" not in json.dumps(packet) and str(stamp(10)) not in json.dumps(packet)
    script = """
      import { validatePackage } from './supabase/functions/qs-service/service.mjs';
      let text = ''; for await (const chunk of process.stdin) text += chunk;
      const packet = JSON.parse(text);
      validatePackage(packet, { RESEARCH_NOTICE_VERSION: packet.metadata.sharing_notice_version,
                               RESEARCH_NOTICE_HASH: packet.metadata.sharing_notice_hash });
      console.log('valid');
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(packet),
        text=True,
        capture_output=True,
        timeout=20,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "valid"
