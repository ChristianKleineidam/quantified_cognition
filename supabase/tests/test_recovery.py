"""Actual restricted roles, encrypted recovery and complete operator rehearsals."""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import subprocess
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from test_concurrency import HASH, RECEIPT
from test_concurrency import cluster as cluster
from test_concurrency import db as db

from tools.backend_ops import encrypted_snapshot, reconcile
from tools.backend_recovery import deletion_ledger, restore, snapshot


@pytest.fixture
def backup_connection(db):
    with psycopg.connect(**db.settings, autocommit=True) as connection:
        connection.execute("set role qs_backup_agent")
        yield connection


def test_monitor_and_backup_roles_cannot_mutate_research_or_read_unrelated_data(db):
    person = db.participant()
    with psycopg.connect(**db.settings, autocommit=True) as connection:
        connection.execute("set role qs_monitor_reader")
        connection.execute("select * from qs_private.ops_state").fetchall()
        assert connection.execute(
            "select first_research_uploads from qs_private.storage_budget where singleton"
        ).fetchone() == (0,)
        for statement in (
            "select * from qs_private.submissions",
            "select * from qs_private.participants",
            "select * from qs_governance.enrollments",
            "select public.qs_ops_record('submit',200,1)",
            "update qs_private.storage_budget set first_research_uploads=100 where singleton",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
        connection.execute("set role qs_backup_agent")
        connection.execute("select * from qs_private.participants").fetchall()
        for statement in (
            "update qs_private.participants set capability_hash=repeat('b',64)",
            "update qs_private.participants set deleted_at=now()",
            "update qs_governance.protocols set approved=true",
            "update qs_governance.enrollments set withdrawn_at=now()",
            "delete from qs_private.submissions",
            "select qs_governance.release_extract(gen_random_uuid())",
            f"select qs_governance.operator_withdraw('{person}','verified-test-001')",
            f"select public.qs_delete('{person}','{HASH}')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("""insert into qs_governance.extracts
                (id,label,holder,purpose,location_reference,access_until,review_due_at)
                values(gen_random_uuid(),'uncontrolled export','operator','test','elsewhere',
                now()+interval '1 day',now()+interval '1 day')""")


def test_encrypted_copy_is_tracked_removed_and_cannot_restore_a_withdrawn_participant(
    db,
    cluster,
    backup_connection,
    tmp_path,
):
    person, other = db.participant(), db.participant()
    db.admin.execute("update qs_private.ops_state set research_expected_enabled=true")
    db.admin.execute("update qs_governance.protocols set oura_permitted=true")
    with db.session() as actor:
        generation = str(uuid.uuid4())
        actor.execute(
            "select public.qs_oura_authorization_bind(%s,%s,%s,%s,now()+interval '1 day',%s,true)",
            (other, HASH, generation,
             Jsonb({"nonce": "synthetic", "body": "monitor-token-sentinel"}), RECEIPT),
        )
        actor.execute("select public.qs_oura_authorization_result(%s,%s,'verified')",
                      (other, generation))
        db.submit(actor, db.packet(person))
        db.submit(actor, db.packet(other, oura=True))
    key = subprocess.run(["age-keygen"], capture_output=True, check=True).stdout
    recipient = re.search(rb"# public key: (age1\S+)", key)[1].decode()
    key_file = tmp_path / "synthetic-identity.txt"
    key_file.write_bytes(key)
    os.chmod(key_file, 0o600)
    directory = tmp_path / "snapshots"
    directory.mkdir()
    encrypted = encrypted_snapshot(backup_connection, directory, recipient)
    assert db.one("select count(*) as n from qs_governance.extract_members")["n"] == 2
    # Keep a synthetic stale copy solely to rehearse an older recovery point.
    stale = tmp_path / "synthetic-stale-copy.age"
    stale.write_bytes(encrypted.read_bytes())
    with db.session() as actor:
        receipt = db.delete(actor, person)
        assert receipt["complete"] is False and receipt["pending_copies"] == 1
    ledger = deletion_ledger(backup_connection)
    reconcile(backup_connection, directory)
    assert not encrypted.exists()
    db.admin.execute("select qs_governance.maintain_research()")
    with db.session() as actor:
        assert db.delete(actor, person)["complete"] is True
    decrypted = subprocess.run(
        ["age", "--decrypt", "--identity", str(key_file), str(stale)],
        capture_output=True,
        check=True,
    ).stdout
    assert b"monitor-token-sentinel" not in gzip.decompress(decrypted)
    records = [json.loads(line) for line in gzip.decompress(decrypted).splitlines()]
    assert all(row.get("table") != "qs_private.oura_research_authorizations" for row in records)
    admin, settings, template = cluster
    name = "qc_test_" + uuid.uuid4().hex
    admin.execute(
        sql.SQL("create database {} template {}").format(
            sql.Identifier(name),
            sql.Identifier(template),
        )
    )
    try:
        result = restore(
            make_conninfo(**{**settings, "dbname": name}), io.BytesIO(decrypted), ledger
        )
        assert result["resurrected_submissions_removed"] == 1
        with psycopg.connect(**{**settings, "dbname": name}) as target:
            assert target.execute(
                "select research_expected_enabled from qs_private.ops_state"
            ).fetchone()[0] is False
            assert target.execute("select count(*) from qs_private.submissions").fetchone()[0] == 1
            assert target.execute(
                "select count(*) from qs_private.oura_research_authorizations"
            ).fetchone()[0] == 0
            assert target.execute(
                "select qs_governance.oura_authorized(%s)", (other,)
            ).fetchone()[0] is False
            assert (
                target.execute("select public.qs_register(%s,%s)", (person, HASH)).fetchone()[0]
                is False
            )
            assert (
                target.execute(
                    "select count(*) from qs_governance.protocols where approved or enrollment_open"
                ).fetchone()[0]
                == 0
            )
    finally:
        admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


def test_corrupt_recovery_rolls_back_and_a_late_participant_is_outside_the_copy_inventory(
    db,
    backup_connection,
):
    person = db.participant()
    with db.session() as actor:
        db.submit(actor, db.packet(person))
    copy = str(uuid.uuid4())
    inventory = backup_connection.execute(
        "select qs_governance.begin_recovery_copy(%s)", (copy,)
    ).fetchone()[0]
    late = db.participant()
    with db.session() as actor:
        db.submit(actor, db.packet(late))
    stream = io.BytesIO()
    snapshot(backup_connection, inventory, stream)
    records = [json.loads(line) for line in gzip.decompress(stream.getvalue()).splitlines()]
    participants = [
        item["row"]["id"] for item in records if item.get("table") == "qs_private.participants"
    ]
    assert person in participants and late not in participants
    records[-1]["sha256"] = "0" * 64
    corrupt = gzip.compress(b"\n".join(json.dumps(item).encode() for item in records) + b"\n")
    with pytest.raises(ValueError, match="integrity"):
        restore(make_conninfo(**db.settings), io.BytesIO(corrupt), {})
    assert db.accounting()["submissions"] == 2


def test_operator_copy_review_withdrawal_and_lost_credentials_do_not_bypass_authorization(db):
    person = db.participant()
    with db.session() as actor:
        db.submit(actor, db.packet(person))
    copy = str(uuid.uuid4())
    db.admin.execute(
        """select qs_governance.register_extract(%s,'Synthetic operator extract',
        'operator',null,'Validation','disposable workspace',now()+interval '1 day',%s::uuid[])""",
        (copy, [person]),
    )
    assert (
        len(db.admin.execute("select * from qs_governance.release_extract(%s)", (copy,)).fetchall())
        == 1
    )
    db.admin.execute("update qs_governance.retention set review_due_at=now()-interval '1s'")
    db.admin.execute("select qs_governance.maintain_research()")
    assert db.one("select state from qs_governance.retention")["state"] == "held"
    with db.session() as actor:
        with pytest.raises(psycopg.Error, match="unauthorized"):
            actor.execute("select public.qs_delete_request(%s,%s,%s)", (person, "b" * 64, RECEIPT))
        with pytest.raises(psycopg.Error, match="unauthorized"):
            actor.execute("select public.qs_deletion_status(%s,%s)", (person, RECEIPT))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            actor.execute(
                "select qs_governance.operator_withdraw(%s,%s)", (person, "verified-test-001")
            )
    withdrawal = db.one(
        "select qs_governance.operator_withdraw(%s,%s) as result", (person, "verified-test-001")
    )["result"]
    assert withdrawal["pending_copies"] == 1
    db.admin.execute(
        "select qs_governance.confirm_copy_deletion(%s,%s,%s)",
        (copy, person, "synthetic operator removal evidence"),
    )
    repeated = db.one(
        "select qs_governance.operator_withdraw(%s,%s) as result", (person, "verified-test-001")
    )["result"]
    assert repeated["complete"] is True
    assert (
        db.one("select count(*) as n from qs_governance.audit where action='operator_withdrawal'")[
            "n"
        ]
        == 1
    )
