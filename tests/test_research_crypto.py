import base64
import copy
import csv
import hashlib
import json
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest

from addon import research_crypto as crypto
from addon.crypto_runtime import primitives
from tools import research_crypto as operator


def empty_reviews():
    schema = json.loads(Path(crypto.__file__).with_name("research_schema.json").read_text())
    return {
        "columns": schema["review_columns"], "rows": [],
        "context": {
            "prediction_basis": "reconstructed_current_fsrs", "anki_version": "26.9.3",
            "calculation_version": 7, "parameters_hash": "a" * 64,
            "generated_at": "2026-11-07T00:00:00Z", "deleted_history_status": "synthetic",
        },
    }


def packet():
    schema = json.loads(Path(crypto.__file__).with_name("research_schema.json").read_text())
    metadata = dict.fromkeys(schema["metadata"], "")
    metadata.update(
        research_participant_id=str(uuid.uuid4()),
        research_consent_active=True,
        sharing_current_choice="opt_in",
        sharing_notice_version="synthetic-crypto-v1",
        sharing_notice_hash="a" * 64,
    )
    row = [
        "2026-10-01" if name == "date" else "5432" if name == "oura_deep_sleep_seconds" else ""
        for name in schema["columns"]
    ]
    return dict(
        schema_version=schema["package_version"],
        enrollment_id=str(uuid.uuid4()),
        submission_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata=metadata,
        columns=schema["columns"],
        rows=[row],
        reviews=empty_reviews(),
    )


def review_packet():
    data = packet()
    data["reviews"]["rows"] = [
        ["P" * 22, 1, "2026-10-01", 3, 1, 0.8, 86400, 1, 0, True, False, True, False],
        ["P" * 22, 2, "2026-10-01", 1, 0, 0.6, 600, 2, 0, False, True, True, False],
    ]
    return data


@pytest.fixture
def recipient(monkeypatch):
    _, _, ECC = primitives()
    key = ECC.generate(curve="Curve25519")
    config = dict(
        algorithm=crypto.ALGORITHM,
        key_id=hashlib.sha256(key.public_key().export_key(format="DER")).hexdigest(),
        public_key=key.public_key().export_key(format="PEM"),
    )
    monkeypatch.setattr(crypto, "public_configuration", lambda: config)
    return key.export_key(format="DER", use_pkcs8=True)


def test_hpke_roundtrip_randomness_and_minimal_readable_header(recipient):
    data = packet()
    one, two = crypto.seal_package(data), crypto.seal_package(data)
    assert one["ciphertext"] != two["ciphertext"] and one["enc"] != two["enc"]
    assert crypto.open_package(one, recipient) == data
    assert "rows" not in one and "columns" not in one
    assert "2026-10-01" not in json.dumps(one) and "oura_deep_sleep_seconds" not in json.dumps(one)
    assert one["providers"] == ["oura"]
    assert set(one["metadata"]) == {
        "research_participant_id",
        "sharing_notice_hash",
        "sharing_notice_version",
    }


def test_encrypted_reviews_support_brier_scores_and_latest_snapshot_replacement(
    tmp_path, recipient, monkeypatch
):
    first = review_packet()
    revised = copy.deepcopy(first)
    revised["submission_id"] = str(uuid.uuid4())
    # A removed historical review must not survive by merging two snapshots.
    revised["reviews"]["rows"] = revised["reviews"]["rows"][:1]
    revised["reviews"]["rows"][0][10] = True
    records = []
    for data, day in ((first, "05"), (revised, "06")):
        encrypted = crypto.seal_package(data)
        assert crypto.open_package(encrypted, recipient) == data
        assert "P" * 22 not in json.dumps(encrypted)
        records.append({"package": encrypted, "received_at": f"2026-11-{day}T00:00:00Z"})
    source = tmp_path / "download.json"
    source.write_text(json.dumps(records))
    output = tmp_path / "analysis.csv"
    monkeypatch.setattr(operator, "load_private", lambda *a, **k: recipient)
    operator.decrypt_download(source, output, tmp_path / "synthetic.dpapi")
    with output.with_name("analysis-reviews.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["submission_id"] == revised["submission_id"]
    assert (float(rows[0]["success"]) - float(rows[0]["prediction"])) ** 2 == pytest.approx(0.04)
    assert rows[0]["prediction_basis"] == "reconstructed_current_fsrs"


@pytest.mark.parametrize("column,value", [
    ("card_id", "1234567890123"), ("ordinal", 2), ("prediction", float("nan")),
    ("prediction", 1.01), ("prediction", True), ("success", 0),
    ("first_on_day", 1), ("elapsed_seconds", -1), ("date", "2026-10-02"),
])
def test_review_field_allowlist_and_values_are_checked(recipient, column, value):
    data = review_packet()
    data["reviews"]["rows"][0][data["reviews"]["columns"].index(column)] = value
    with pytest.raises(ValueError):
        crypto.seal_package(data)


def test_unexpected_review_fields_are_rejected_and_missing_predictions_stay_missing(recipient):
    data = review_packet()
    data["reviews"]["rows"][0][5] = None
    assert crypto.open_package(crypto.seal_package(data), recipient) == data
    data["reviews"]["context"]["raw_card_id"] = 123
    with pytest.raises(ValueError):
        crypto.seal_package(data)


def test_legacy_daily_only_encrypted_packages_still_decrypt(tmp_path, recipient, monkeypatch):
    data = packet()
    data.pop("reviews")
    schema = json.loads(Path(crypto.__file__).with_name("research_schema.json").read_text())
    old_row = dict(zip(data["columns"], data["rows"][0], strict=True))
    data.update(schema_version=6, columns=schema["legacy_columns"],
                rows=[[old_row[name] for name in schema["legacy_columns"]]])
    encrypted = crypto.seal_package(data)
    assert crypto.open_package(encrypted, recipient) == data
    source, output = tmp_path / "legacy.json", tmp_path / "legacy.csv"
    source.write_text(json.dumps([encrypted]))
    monkeypatch.setattr(operator, "load_private", lambda *a, **k: recipient)
    operator.decrypt_download(source, output, tmp_path / "synthetic.dpapi")
    assert output.exists() and not output.with_name("legacy-reviews.csv").exists()


def test_workout_summaries_roundtrip_and_raw_workout_fields_are_rejected(recipient):
    data = packet()
    for field, value in {
        "oura_workout_count": "2",
        "oura_workout_duration_seconds": "3600",
        "oura_workout_before_hard_seconds": "1800",
        "oura_workout_before_status": "available",
    }.items():
        data["rows"][0][data["columns"].index(field)] = value
    encrypted = crypto.seal_package(data)
    assert crypto.open_package(encrypted, recipient) == data
    assert "oura_workout" not in json.dumps(encrypted)
    data["columns"] = [*data["columns"], "workout_label"]
    data["rows"][0].append("private label")
    with pytest.raises(ValueError):
        crypto.seal_package(data)


@pytest.mark.parametrize(
    "field", ["ciphertext", "enc", "metadata", "row_count", "providers", "created_at"]
)
def test_tampering_with_ciphertext_or_bound_header_is_rejected(recipient, field):
    encrypted = crypto.seal_package(packet())
    if field in ("ciphertext", "enc"):
        raw = base64.b64decode(encrypted[field])
        encrypted[field] = base64.b64encode(bytes([raw[0] ^ 1]) + raw[1:]).decode()
    elif field == "metadata":
        encrypted[field]["sharing_notice_hash"] = "b" * 64
    elif field == "providers":
        encrypted[field] = []
    elif field == "row_count":
        encrypted[field] += 1
    else:
        encrypted[field] = "2026-10-02T00:00:00Z"
    with pytest.raises(ValueError):
        crypto.open_package(encrypted, recipient)


def test_wrong_private_key_and_unexpected_fields_are_rejected(recipient):
    _, _, ECC = primitives()
    encrypted = crypto.seal_package(packet())
    wrong = ECC.generate(curve="Curve25519").export_key(format="DER", use_pkcs8=True)
    with pytest.raises(ValueError):
        crypto.open_package(encrypted, wrong)
    with pytest.raises(ValueError):
        crypto.open_package({**encrypted, "raw_sleep": []}, recipient)
    bad = packet()
    bad["raw_sleep"] = ["not an allowed measurement"]
    with pytest.raises(ValueError):
        crypto.seal_package(bad)


def test_decrypted_allowlist_and_authenticated_provider_declarations_are_checked(recipient):
    _, HPKE, ECC = primitives()
    data = packet()
    envelope = crypto.seal_package(data)
    data["raw_sleep"] = ["not allowed"]
    cipher = HPKE.new(
        receiver_key=ECC.import_key(recipient).public_key(),
        aead_id=HPKE.AEAD.AES256_GCM,
        info=crypto.INFO,
    )
    header = {name: envelope[name] for name in crypto.HEADER_KEYS}
    envelope["enc"] = base64.b64encode(cipher.enc).decode()
    envelope["ciphertext"] = base64.b64encode(
        cipher.seal(zlib.compress(crypto.canonical(data)), auth_data=crypto.canonical(header))
    ).decode()
    with pytest.raises(ValueError, match="Unexpected"):
        crypto.open_package(envelope, recipient)


def test_local_analysis_export_keeps_provenance_and_validates_all_before_writing(
    tmp_path, recipient, monkeypatch
):
    data = packet()
    encrypted = crypto.seal_package(data)
    record = dict(
        package=encrypted,
        submission_id=data["submission_id"],
        received_at="2026-11-05T12:00:00Z",
        consent_timing={
            "study_enrollment_id": data["enrollment_id"],
            "study_consent_before_first_results": True,
        },
    )
    source = tmp_path / "encrypted-download.json"
    output = tmp_path / "analysis.csv"
    source.write_text(json.dumps({"rows": [{"record": record}]}))
    monkeypatch.setattr(operator, "load_private", lambda *args, **kwargs: recipient)
    operator.decrypt_download(source, output, Path("synthetic-key.dpapi"))
    text = output.read_text(encoding="utf-8-sig")
    assert "5432" in text and "study_consent_before_first_results" in text and "received_at" in text
    with pytest.raises(ValueError):
        operator.decrypt_download(source, output, Path("synthetic-key.dpapi"))
    corrupt = copy.deepcopy(record)
    corrupt["package"]["row_count"] += 1
    source.write_text(json.dumps([record, corrupt]))
    other = tmp_path / "rejected.csv"
    with pytest.raises(ValueError):
        operator.decrypt_download(source, other, Path("synthetic-key.dpapi"))
    assert not other.exists()
    assert not other.with_name("rejected-reviews.csv").exists()


@pytest.mark.parametrize("limit", ["MAX_PLAINTEXT", "MAX_COMPRESSED"])
def test_oversized_packages_fail_instead_of_truncating_reviews(recipient, monkeypatch, limit):
    data = review_packet()
    original = copy.deepcopy(data)
    monkeypatch.setattr(crypto, limit, 1)
    with pytest.raises(ValueError, match="size limit"):
        crypto.seal_package(data)
    assert data == original


def test_bundled_wheels_match_pins_and_retain_upstream_licensing():
    folder = Path(crypto.__file__).parent / "crypto_wheels"
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["version"] == "3.24.1" and len(manifest["platforms"]) == 5
    for spec in manifest["platforms"].values():
        path = folder / spec["filename"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == spec["sha256"]
        with ZipFile(path) as archive:
            assert any("LICENSE" in name.upper() for name in archive.namelist())


def test_authenticated_compressed_payload_cannot_exceed_decompression_limit(recipient, monkeypatch):
    _, HPKE, ECC = primitives()
    envelope = crypto.seal_package(packet())
    header = {name: envelope[name] for name in crypto.HEADER_KEYS}
    cipher = HPKE.new(
        receiver_key=ECC.import_key(recipient).public_key(),
        aead_id=HPKE.AEAD.AES256_GCM,
        info=crypto.INFO,
    )
    envelope["enc"] = base64.b64encode(cipher.enc).decode()
    envelope["ciphertext"] = base64.b64encode(
        cipher.seal(zlib.compress(b"a" * 100000), auth_data=crypto.canonical(header))
    ).decode()
    monkeypatch.setattr(crypto, "MAX_PLAINTEXT", 1024)
    with pytest.raises(ValueError, match="Decompressed"):
        crypto.open_package(envelope, recipient)
