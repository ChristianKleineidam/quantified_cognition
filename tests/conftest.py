"""Unit tests use disposable in-memory health keys, independent of OS keyrings."""

import secrets
from pathlib import Path

import pytest

from addon import private_storage

REAL_LOCAL_KEY = private_storage._key


@pytest.fixture(autouse=True)
def isolated_health_keys(monkeypatch):
    keys = {}

    def key(path, *, create=False):
        identity = str(Path(path).resolve().parent)
        if identity not in keys:
            if not create:
                raise private_storage.LockedHealthData("Synthetic key unavailable")
            keys[identity] = secrets.token_bytes(32)
        return keys[identity]

    monkeypatch.setattr(private_storage, "_key", key)
    return keys
