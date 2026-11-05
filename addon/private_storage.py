"""Authenticated local cache encryption; plaintext databases stay in memory."""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .crypto_runtime import primitives

MAGIC = b"QCLOCAL\x01"


class LockedHealthData(OSError):
    """A cache must never silently fall back to plaintext or a replacement key."""


def _key(path, *, create=False):
    from .storage import profile_job_lock
    from .vault import read_secret, write_secret

    # A stable profile-directory identity also works for revision-only readers,
    # which have a cache path rather than a live Anki collection handle.
    source = Path(path).parent.parent / ".qc-health"
    with profile_job_lock(source, "credentials"):
        value = read_secret(source, "local_health_key")
        marker = Path(path).parent / ".health-encryption-present"
        if value is None:
            if not create or marker.exists():
                raise LockedHealthData(
                    "The health-data key is unavailable in the OS credential store."
                )
            value = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
            write_secret(source, "local_health_key", value)
        marker.touch(exist_ok=True)
        try:
            key = base64.b64decode(value, validate=True)
            if len(key) != 32:
                raise ValueError()
            return key
        except (ValueError, TypeError):
            raise LockedHealthData("The saved health-data key is invalid.") from None


def _aad(path):
    return b"Quantified Cognition local cache v1\x00" + Path(path).name.encode("utf-8")


def encrypt(path, data):
    AES, _, _ = primitives()
    cipher = AES.new(_key(path, create=True), AES.MODE_GCM, nonce=secrets.token_bytes(12))
    cipher.update(_aad(path))
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return MAGIC + cipher.nonce + tag + ciphertext


def decrypt(path, data):
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 28:
        raise LockedHealthData("Unrecognized encrypted health cache.")
    AES, _, _ = primitives()
    offset = len(MAGIC)
    cipher = AES.new(_key(path), AES.MODE_GCM, nonce=data[offset : offset + 12])
    cipher.update(_aad(path))
    try:
        return cipher.decrypt_and_verify(data[offset + 28 :], data[offset + 12 : offset + 28])
    except ValueError:
        raise LockedHealthData("The health cache cannot be unlocked or has been altered.") from None


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    atomic_bytes(path, encrypt(path, encoded))


def read_json(path):
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(MAGIC):
        return json.loads(decrypt(path, raw))
    if not raw.lstrip().startswith((b"{", b"[")):
        raise LockedHealthData("Unrecognized private cache; the saved file was preserved.")
    value = json.loads(raw.decode("utf-8"))
    # Upgrade a managed legacy cache before returning its content.
    write_json(path, value)
    return value


def _serialize(connection):
    if hasattr(connection, "serialize"):
        raw = connection.serialize()
        # sqlite3_deserialize cannot open a WAL-mode image. SQLite documents
        # normalizing these format bytes after the backup merges committed WAL.
        return b"DB\x00" + raw[:18] + b"\x01\x01" + raw[20:]
    return b"SQL\x00" + "\n".join(connection.iterdump()).encode("utf-8")


@contextmanager
def private_database(path, *, write=False):
    from .storage import profile_job_lock

    path = Path(path).resolve()
    source = path.parent.parent / ".qc-health"
    with profile_job_lock(source, "health"):
        connection = sqlite3.connect(":memory:")
        connection.execute("pragma temp_store=memory")
        legacy = False
        before = None
        try:
            if path.exists():
                raw = path.read_bytes()
                if raw.startswith(b"SQLite format 3\x00"):
                    legacy = True
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as old:
                        old.backup(connection)
                    old.close()
                else:
                    before = decrypt(path, raw)
                    if before.startswith(b"DB\x00") and hasattr(connection, "deserialize"):
                        raw = before[3:]
                        connection.deserialize(raw[:18] + b"\x01\x01" + raw[20:])
                    elif before.startswith(b"SQL\x00"):
                        connection.executescript(before[4:].decode("utf-8"))
                    else:
                        raise LockedHealthData(
                            "This health cache needs a newer Anki Python runtime."
                        )
            elif not write:
                raise FileNotFoundError(path)
            yield connection
            connection.commit()
            if write or legacy:
                encoded = _serialize(connection)
                if encoded != before:
                    atomic_bytes(path, encrypt(path, encoded))
            # A crash after encrypted replacement can leave obsolete SQLite
            # sidecars. Only remove them after authenticating the main cache.
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        finally:
            connection.close()
