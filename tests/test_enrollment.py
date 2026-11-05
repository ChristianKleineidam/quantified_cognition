"""Study consent is distinct from the original sharing-choice/exposure audit."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import test_submissions
from test_submissions import CONFIG

from addon import enrollment, submissions
from addon.research import SharingConfig, audit_path, sharing_state
from addon.service import ServiceError

enrolled = test_submissions.setup


def test_old_preference_is_preserved_but_does_not_enroll(enrolled):
    source, _, _, _ = enrolled
    legacy = SharingConfig(notice_version="research-sharing-v5")
    other = source.parent / "legacy" / "collection.anki2"
    first = sharing_state(other, legacy, event_type="sharing_choice", choice="opt_in")
    sharing_state(other, legacy, event_type="results_exposed", surface="report")
    current = sharing_state(other, SharingConfig(), event_type="sharing_choice", choice="opt_in")
    assert (
        current.metadata()["sharing_first_choice_at_utc"]
        == first.metadata()["sharing_first_choice_at_utc"]
    )
    assert current.metadata()["sharing_opt_in_before_first_view"] is True
    assert current.metadata()["current_notice_consent_before_first_view"] is False
    with pytest.raises(ServiceError, match="Complete research enrollment"):
        enrollment.require_enrollment(other, CONFIG)


@pytest.mark.parametrize("flag", ["adult", "own_data", "explicit_consent"])
def test_eligibility_and_explicit_consent_are_mandatory_before_network(enrolled, monkeypatch, flag):
    source, sharing, _, _ = enrolled
    monkeypatch.setattr(
        enrollment, "call_service", lambda *a, **kw: pytest.fail("network before eligibility")
    )
    answers = {
        "adult": True,
        "own_data": True,
        "explicit_consent": True,
    }
    answers[flag] = False
    with pytest.raises(ServiceError, match="Confirm eligibility"):
        enrollment.enroll(source, CONFIG, sharing, **answers)


def test_consent_uses_profile_id_and_copy_contains_exact_documents(
    enrolled, tmp_path
):
    source, sharing, _, _ = enrolled
    record = enrollment.latest_enrollment(source)
    packet = submissions.prepare_submission(source, CONFIG, sharing)[0]["package"]
    assert packet["enrollment_id"] == record["id"]
    assert "signature" not in record["record"]
    assert record["record"]["participant_id"] == sharing_state(source, sharing).participant_id
    assert "signature" not in packet["metadata"]
    copy = tmp_path / "consent.json"
    enrollment.save_consent_copy(source, copy)
    saved = json.loads(copy.read_text("utf-8"))
    assert saved["information"] == enrollment.load_bundle()
    assert saved["enrollment"]["record"]["explicit_consent"] is True
    assert saved["enrollment"]["record"]["signature_method"] == "explicit_consent"
    assert (
        saved["enrollment"]["record"]["participant_id"]
        == packet["metadata"]["research_participant_id"]
    )
    assert saved["enrollment"]["receipt"]["enrollment_id"] == record["id"]


def test_receipt_loss_retries_exact_signed_evidence_and_does_not_change_first_choice(
    enrolled, monkeypatch
):
    source, sharing, _, _ = enrolled
    original = enrollment.call_service
    before = sharing_state(source, sharing).metadata()
    sent = []

    def server(config, route, data=None, **kwargs):
        if route == "/research/enrollment":
            sent.append(data)
            if len(sent) == 1:
                raise ServiceError("Lost enrollment response")
        return original(config, route, data, **kwargs)

    monkeypatch.setattr(enrollment, "call_service", server)
    enrollment.withdraw_enrollment(source)
    args = dict(adult=True, own_data=True, explicit_consent=True)
    with pytest.raises(ServiceError):
        enrollment.enroll(source, CONFIG, sharing, **args)
    enrollment.enroll(source, CONFIG, sharing, **args)
    assert sent[0] == sent[1]
    after = sharing_state(source, sharing).metadata()
    assert before == after


def test_actual_consent_timing_is_late_while_original_preference_stays_early(enrolled):
    source, sharing, _, _ = enrolled
    sharing_state(source, sharing, event_type="results_exposed", surface="report")
    enrollment.withdraw_enrollment(source)
    record = enrollment.enroll(
        source,
        CONFIG,
        sharing,
        adult=True,
        own_data=True,
        explicit_consent=True,
    )
    assert record["record"]["before_first_results"] is False
    assert sharing_state(source, sharing).metadata()["sharing_opt_in_before_first_view"] is True


def test_withdrawal_blocks_enrollment_and_tracks_pending_copy_deletion(enrolled, monkeypatch):
    source, sharing, secrets, _ = enrolled
    submissions.withdraw_sharing(source, sharing, "declined")
    record = enrollment.latest_enrollment(source)
    assert record["withdrawn_at"]
    with pytest.raises(ServiceError):
        enrollment.require_enrollment(source, CONFIG)
    requests = []

    def server(config, route, data=None, **kwargs):
        requests.append((route, data, kwargs))
        return {"active_storage_deleted": True, "complete": len(requests) > 1, "pending_copies": 1}

    monkeypatch.setattr(submissions, "call_service", server)
    assert submissions.retry_deletion(source) is False
    assert secrets["research"]["active_storage_deleted"]
    assert "secret" not in secrets["research"]  # upload capability no longer retained
    assert "copies" in submissions.submission_summary(source)
    with pytest.raises(ServiceError):
        enrollment.require_enrollment(source, CONFIG)
    assert submissions.retry_deletion(source) is True
    assert requests[0][0] == "/research/participant"
    assert requests[1][0] == "/research/deletion-status"
    assert requests[0][1]["receipt_secret"] in requests[1][2]["token"]
    assert secrets["research"] is None


def test_repeated_deletion_before_receipt_reuses_separate_receipt_secret(enrolled, monkeypatch):
    source, _, secrets, _ = enrolled
    submissions.queue_deletion(source)
    receipt_key = secrets["research"]["deletion_secret"]
    submissions.queue_deletion(source)
    assert secrets["research"]["deletion_secret"] == receipt_key
    attempts = []

    def offline(config, route, data=None, **kwargs):
        attempts.append(data)
        raise ServiceError("offline")

    monkeypatch.setattr(submissions, "call_service", offline)
    for _ in range(2):
        with pytest.raises(ServiceError):
            submissions.retry_deletion(source)
    assert attempts == [{"receipt_secret": receipt_key}] * 2


def test_closed_enrollment_and_changed_information_cannot_be_signed(enrolled, monkeypatch):
    source, sharing, _, status = enrolled
    status["research_enabled"] = False
    before = enrollment.latest_enrollment(source)
    with pytest.raises(ServiceError, match="not open"):
        enrollment.enroll(
            source,
            CONFIG,
            sharing,
            adult=True,
            own_data=True,
            explicit_consent=True,
        )
    assert enrollment.latest_enrollment(source) == before
    status["research_enabled"] = True
    changed = replace(sharing, notice_version="changed")
    with pytest.raises(ServiceError, match="changed"):
        enrollment.enrollment_information(source, CONFIG, changed)


def test_new_software_can_save_original_document_copy(enrolled, monkeypatch, tmp_path):
    source, _, _, _ = enrolled
    original = enrollment.load_bundle()
    monkeypatch.setattr(enrollment, "load_bundle", lambda: {"sha256": "new", "bundle": {}})
    destination = tmp_path / "old-consent.json"
    enrollment.save_consent_copy(source, destination)
    assert json.loads(destination.read_text())["information"] == original


def test_legacy_signature_copy_is_preserved_and_new_consent_keeps_profile_id(enrolled, tmp_path):
    source, sharing, _, _ = enrolled
    current = enrollment.load_bundle()
    old_bundle = {
        **current["bundle"], "version": "legacy-test", "signature_method": "typed_pseudonym"
    }
    canonical = json.dumps(old_bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    old = {"bundle": old_bundle, "sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    previous = enrollment.latest_enrollment(source)
    legacy = {
        **previous["record"],
        "version": "legacy-test",
        "bundle_hash": old["sha256"],
        "signature_method": "typed_pseudonym",
        "signature": "Original synthetic signature",
    }
    with sqlite3.connect(audit_path(source)) as database:
        database.execute(
            "insert into metadata values (?, ?)",
            ("enrollment_bundle:" + old["sha256"], json.dumps(old)),
        )
        database.execute(
            "update enrollments set record=? where id=?", (json.dumps(legacy), previous["id"])
        )
    destination = tmp_path / "legacy-consent.json"
    enrollment.save_consent_copy(source, destination)
    saved = json.loads(destination.read_text())
    assert saved["information"] == old
    assert saved["enrollment"]["record"] == legacy
    renewed = enrollment.enroll(
        source, CONFIG, sharing, adult=True, own_data=True, explicit_consent=True
    )
    assert renewed["id"] != previous["id"]
    assert renewed["record"]["participant_id"] == legacy["participant_id"]
    assert "signature" not in renewed["record"]
    with sqlite3.connect(audit_path(source)) as database:
        preserved = database.execute(
            "select record from enrollments where id=?", (previous["id"],)
        ).fetchone()
    assert json.loads(preserved[0]) == legacy


def test_unknown_exposure_is_not_made_known_by_signing(enrolled):
    source, sharing, _, _ = enrolled
    with sqlite3.connect(audit_path(source)) as database:
        database.execute("update metadata set value='unknown_legacy' where key='exposure_history'")
    enrollment.withdraw_enrollment(source)
    record = enrollment.enroll(
        source,
        CONFIG,
        sharing,
        adult=True,
        own_data=True,
        explicit_consent=True,
    )
    assert record["record"]["before_first_results"] is None


def test_frozen_documents_match_source_and_seed():
    frozen = enrollment.load_bundle()
    root = Path(__file__).resolve().parents[1]
    for name, content in frozen["bundle"]["documents"].items():
        assert content == (root / "docs" / name).read_text("utf-8")
    seed = (root / "supabase/migrations/20261105113158_research_protocol_v8.sql").read_text("utf-8")
    assert frozen["sha256"] in seed
