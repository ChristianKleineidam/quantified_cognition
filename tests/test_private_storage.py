import json
import sqlite3
from pathlib import Path

import pytest
from conftest import REAL_LOCAL_KEY

from addon import private_storage as private
from addon import vault


def test_database_is_encrypted_and_roundtrips_without_plaintext_sidecars(tmp_path):
    path = tmp_path / "quantified-self-memory/health.sqlite3"
    with private.private_database(path, write=True) as connection:
        connection.execute("create table sleep(payload text)")
        connection.execute("insert into sleep values ('private sleep measurement')")
    assert path.read_bytes().startswith(private.MAGIC)
    assert b"private sleep measurement" not in path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError), sqlite3.connect(path) as connection:
        connection.execute("select * from sleep").fetchall()
    before = path.read_bytes()
    with private.private_database(path) as connection:
        assert (
            connection.execute("select payload from sleep").fetchone()[0]
            == "private sleep measurement"
        )
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [".health.lock", "health.sqlite3"]


def test_existing_sqlite_migration_keeps_revision_and_rolls_back_cancelled_writes(tmp_path):
    path = tmp_path / "quantified-self-memory/health.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.execute("create table metadata(key text,value text)")
        connection.execute("insert into metadata values('revision','original-revision')")
    connection.close()
    with private.private_database(path) as connection:
        assert connection.execute("select value from metadata").fetchone()[0] == "original-revision"
    encrypted = path.read_bytes()
    with pytest.raises(RuntimeError), private.private_database(path, write=True) as connection:
        connection.execute("delete from metadata")
        raise RuntimeError("cancelled")
    assert path.read_bytes() == encrypted


def test_corruption_wrong_profile_and_atomic_failure_preserve_saved_data(tmp_path, monkeypatch):
    path = tmp_path / "one/private.json"
    private.write_json(path, {"deep_sleep_seconds": 1234})
    saved = path.read_bytes()
    other = tmp_path / "two/private.json"
    other.parent.mkdir()
    other.write_bytes(saved)
    with pytest.raises(private.LockedHealthData):
        private.read_json(other)
    path.write_bytes(saved[:-1] + bytes([saved[-1] ^ 1]))
    with pytest.raises(private.LockedHealthData):
        private.read_json(path)
    path.write_bytes(saved)
    monkeypatch.setattr(
        private.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk error"))
    )
    with pytest.raises(OSError):
        private.write_json(path, {"deep_sleep_seconds": 9999})
    assert path.read_bytes() == saved
    assert len(list(path.parent.iterdir())) == 1


def test_json_migrates_and_authentication_binds_file_purpose(tmp_path):
    path = tmp_path / "insights.json"
    path.write_text(json.dumps({"point": 0.4}))
    assert private.read_json(path) == {"point": 0.4}
    assert path.read_bytes().startswith(private.MAGIC)
    renamed = tmp_path / "other.json"
    renamed.write_bytes(path.read_bytes())
    with pytest.raises(private.LockedHealthData):
        private.read_json(renamed)


def test_key_generation_preserves_other_vault_slots_and_never_replaces_lost_key(
    tmp_path, monkeypatch
):
    path = tmp_path / "quantified-self-memory/health.sqlite3"
    path.parent.mkdir()
    slots = {"oura": {"refresh_token": "synthetic-token"}}
    monkeypatch.setattr(vault, "read_secret", lambda source, slot: slots.get(slot))
    monkeypatch.setattr(
        vault, "write_secret", lambda source, slot, value: slots.update({slot: value})
    )
    first = REAL_LOCAL_KEY(path, create=True)
    assert len(first) == 32 and REAL_LOCAL_KEY(path) == first
    assert slots["oura"]["refresh_token"] == "synthetic-token"
    slots.pop("local_health_key")
    with pytest.raises(private.LockedHealthData):
        REAL_LOCAL_KEY(path, create=True)


def test_failed_legacy_migration_preserves_original_database(tmp_path, monkeypatch):
    path = tmp_path / "quantified-self-memory/health.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.execute("create table sleep(value integer)")
        connection.execute("insert into sleep values(20)")
    connection.close()
    original = path.read_bytes()
    monkeypatch.setattr(
        private, "encrypt", lambda *_: (_ for _ in ()).throw(OSError("vault locked"))
    )
    with pytest.raises(OSError), private.private_database(path) as connection:
        assert connection.execute("select value from sleep").fetchone()[0] == 20
    assert path.read_bytes() == original


def test_wal_database_migration_and_stale_sidecar_cleanup(tmp_path):
    path = tmp_path / "quantified-self-memory/health.sqlite3"
    path.parent.mkdir()
    connection = sqlite3.connect(path)
    connection.execute("pragma journal_mode=wal")
    connection.execute("create table sleep(value text)")
    connection.execute("insert into sleep values('synthetic sleep')")
    connection.commit()
    connection.close()
    with private.private_database(path) as connection:
        assert connection.execute("select value from sleep").fetchone()[0] == "synthetic sleep"
    with private.private_database(path) as connection:
        assert connection.execute("select value from sleep").fetchone()[0] == "synthetic sleep"
    sidecar = Path(str(path) + "-wal")
    sidecar.write_bytes(b"obsolete plaintext")
    with private.private_database(path):
        pass
    assert not sidecar.exists()
