import base64
import copy
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
    )


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
