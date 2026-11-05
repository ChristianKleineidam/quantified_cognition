"""HPKE research envelopes. The add-on contains only the recipient's public key."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import uuid
import zlib
from datetime import date, datetime
from pathlib import Path

from .crypto_runtime import primitives

ALGORITHM = "HPKE-X25519-HKDF-SHA256-AES256GCM"
ENVELOPE_VERSION = 1
MAX_PLAINTEXT = 64 * 1024 * 1024
MAX_COMPRESSED = 6 * 1024 * 1024 - 4096
INFO = b"Quantified Cognition research package v1"
HEADER_KEYS = (
    "envelope_version",
    "algorithm",
    "compression",
    "key_id",
    "enrollment_id",
    "submission_id",
    "created_at",
    "metadata",
    "providers",
    "row_count",
)
ENCRYPTION_CONFIG = Path(__file__).with_name("research_encryption.json")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def validate_package(package):
    """Apply the aggregate allowlist before encryption and after decryption."""
    schema = json.loads(Path(__file__).with_name("research_schema.json").read_text())
    expected = {
        "schema_version",
        "enrollment_id",
        "submission_id",
        "created_at",
        "metadata",
        "columns",
        "rows",
    }
    if not isinstance(package, dict) or set(package) != expected:
        raise ValueError("Unexpected research package fields")
    if (
        package["schema_version"] != schema["package_version"]
        or package["columns"] != schema["columns"]
        or not isinstance(package["metadata"], dict)
        or set(package["metadata"]) != set(schema["metadata"])
    ):
        raise ValueError("Unsupported research package schema")
    metadata = package["metadata"]
    for value in (
        package["enrollment_id"],
        package["submission_id"],
        metadata["research_participant_id"],
    ):
        if (
            not isinstance(value, str)
            or str(uuid.UUID(value)) != value
            or uuid.UUID(value).version != 4
        ):
            raise ValueError("Invalid research package identifier")
    if datetime.fromisoformat(package["created_at"].replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Missing package time zone")
    if (
        metadata["research_consent_active"] is not True
        or metadata["sharing_current_choice"] != "opt_in"
    ):
        raise ValueError("Research consent is required")
    if not re.fullmatch(r"[0-9a-f]{64}", metadata["sharing_notice_hash"]):
        raise ValueError("Invalid research notice fingerprint")
    if any(
        value is not None
        and type(value) not in (str, bool)
        or isinstance(value, str)
        and len(value) > 256
        for value in metadata.values()
    ):
        raise ValueError("Invalid research metadata")
    rows = package["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 36600:
        raise ValueError("Invalid research date count")
    previous = ""
    for row in rows:
        if not isinstance(row, list) or len(row) != len(schema["columns"]):
            raise ValueError("Invalid daily record length")
        if (
            not isinstance(row[0], str)
            or date.fromisoformat(row[0]).isoformat() != row[0]
            or row[0] <= previous
        ):
            raise ValueError("Research dates must be valid, unique and ordered")
        previous = row[0]
        for name, cell in zip(schema["columns"], row, strict=True):
            if not isinstance(cell, str) or len(cell) > 160:
                raise ValueError("Invalid research cell")
            if (
                cell
                and name in schema["numeric"]
                and (cell.strip() != cell or not math.isfinite(float(cell)))
            ):
                raise ValueError("Invalid numeric research cell")
            if name in schema["enums"] and cell not in schema["enums"][name]:
                raise ValueError("Invalid research category")
            if name == "parameters_hash" and cell and not re.fullmatch(r"[0-9a-f]{64}", cell):
                raise ValueError("Invalid parameter fingerprint")


def public_configuration():
    value = json.loads(ENCRYPTION_CONFIG.read_text(encoding="utf-8"))
    _, _, ECC = primitives()
    public = ECC.import_key(value["public_key"])
    if public.has_private() or public.curve != "Curve25519":
        raise ValueError("Invalid research encryption public key")
    if (
        value["algorithm"] != ALGORITHM
        or value["key_id"] != hashlib.sha256(public.export_key(format="DER")).hexdigest()
    ):
        raise ValueError("Research public-key fingerprint mismatch")
    return value


def providers(package):
    present = []
    for provider in ("oura", "google_health"):
        indices = [
            i for i, name in enumerate(package["columns"]) if name.startswith(provider + "_")
        ]
        if any(row[i] != "" for row in package["rows"] for i in indices):
            present.append(provider)
    return sorted(present)


def seal_package(package):
    validate_package(package)
    config = public_configuration()
    _, HPKE, ECC = primitives()
    header = {
        "envelope_version": ENVELOPE_VERSION,
        "algorithm": ALGORITHM,
        "compression": "zlib",
        "key_id": config["key_id"],
        "enrollment_id": package["enrollment_id"],
        "submission_id": package["submission_id"],
        "created_at": package["created_at"],
        "providers": providers(package),
        "row_count": len(package["rows"]),
        "metadata": {
            key: package["metadata"][key]
            for key in ("research_participant_id", "sharing_notice_version", "sharing_notice_hash")
        },
    }
    encoded = canonical(package)
    if len(encoded) > MAX_PLAINTEXT:
        raise ValueError("This research package exceeds the upload size limit.")
    encoded = zlib.compress(encoded)
    if len(encoded) > MAX_COMPRESSED:
        raise ValueError("This compressed research package exceeds the upload size limit.")
    cipher = HPKE.new(
        receiver_key=ECC.import_key(config["public_key"]), aead_id=HPKE.AEAD.AES256_GCM, info=INFO
    )
    ciphertext = cipher.seal(encoded, auth_data=canonical(header))
    return {
        **header,
        "enc": base64.b64encode(cipher.enc).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def open_package(envelope, private_key):
    """Operator-only operation; caller obtains the private key off the server."""
    _, HPKE, ECC = primitives()
    key = ECC.import_key(private_key)
    header = {name: envelope[name] for name in HEADER_KEYS}
    if set(envelope) != {*HEADER_KEYS, "enc", "ciphertext"}:
        raise ValueError("Unexpected encrypted package fields")
    if (
        header["envelope_version"] != ENVELOPE_VERSION
        or header["algorithm"] != ALGORITHM
        or header["compression"] != "zlib"
        or not key.has_private()
        or key.curve != "Curve25519"
        or header["key_id"] != hashlib.sha256(key.public_key().export_key(format="DER")).hexdigest()
    ):
        raise ValueError("Wrong research key or encryption version")
    enc = base64.b64decode(envelope["enc"], validate=True)
    ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    if len(enc) != 32 or not 16 <= len(ciphertext) <= MAX_COMPRESSED + 16:
        raise ValueError("Invalid encrypted package length")
    cipher = HPKE.new(receiver_key=key, aead_id=HPKE.AEAD.AES256_GCM, enc=enc, info=INFO)
    compressed = cipher.unseal(ciphertext, auth_data=canonical(header))
    decoder = zlib.decompressobj()
    try:
        plain = decoder.decompress(compressed, MAX_PLAINTEXT + 1)
    except zlib.error:
        raise ValueError("Invalid compressed research package") from None
    if len(plain) > MAX_PLAINTEXT or not decoder.eof or decoder.unused_data:
        raise ValueError("Decompressed research package exceeds its limit or is incomplete")
    package = json.loads(plain)
    validate_package(package)
    for name in ("enrollment_id", "submission_id", "created_at"):
        if package[name] != header[name]:
            raise ValueError("Encrypted package identity mismatch")
    if (
        any(package["metadata"][name] != value for name, value in header["metadata"].items())
        or len(package["rows"]) != header["row_count"]
        or providers(package) != header["providers"]
    ):
        raise ValueError("Encrypted package declaration mismatch")
    return package
