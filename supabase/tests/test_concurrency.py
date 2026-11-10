"""Run explicitly against a marked, disposable local PostgreSQL cluster.

These tests use independent server processes and observe actual lock waits.
They never connect to Supabase or use provider/research credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from addon.research_crypto import seal_package

ROOT = Path(__file__).resolve().parents[2]
HASH = "a" * 64
RECEIPT = "c" * 64


def connect(settings, *, actor=False, name="qc_concurrency"):
    connection = psycopg.connect(
        **settings, autocommit=True, row_factory=dict_row, application_name=name,
        connect_timeout=5,
    )
    connection.execute("set statement_timeout='15s'; set lock_timeout='12s'")
    if actor:
        connection.execute("set role service_role")
    return connection


@pytest.fixture(scope="session")
def cluster():
    dsn = os.environ.get("QC_PG_TEST_DSN")
    if not dsn:
        pytest.fail("Set QC_PG_TEST_DSN for a disposable local PostgreSQL cluster")
    settings = conninfo_to_dict(dsn)
    if settings.get("host") not in {"127.0.0.1", "::1"}:
        pytest.fail("Concurrency tests require an explicit loopback host")
    # Do not inherit a service file or caller-supplied startup options.
    if settings.keys() - {"host", "port", "user", "password", "dbname", "sslmode"}:
        pytest.fail("Unexpected connection options in QC_PG_TEST_DSN")
    template = "qc_test_" + uuid.uuid4().hex
    created_roles = []
    with connect(settings) as admin:
        marker = admin.execute(
            "select current_setting('quantified_cognition.test_cluster',true) as value"
        ).fetchone()["value"]
        if marker != "on":
            pytest.fail("Refusing a server without quantified_cognition.test_cluster=on")
        previous_roles = {row["rolname"] for row in admin.execute("select rolname from pg_roles")}
        try:
            for role in ("anon", "authenticated", "service_role"):
                if not admin.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone():
                    admin.execute(sql.SQL("create role {} {}").format(
                        sql.Identifier(role),
                        sql.SQL("bypassrls" if role == "service_role" else ""),
                    ))
                    created_roles.append(role)
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(template)))
            with connect({**settings, "dbname": template}) as source:
                source.execute("""create schema supabase_migrations;
                    create table supabase_migrations.schema_migrations(version text primary key)""")
                for migration in sorted((ROOT / "supabase/migrations").glob("*.sql")):
                    source.execute(migration.read_text(encoding="utf-8"))
                    source.execute("insert into supabase_migrations.schema_migrations values(%s)",
                                   (migration.name.split("_", 1)[0],))
                source.execute("""insert into qs_governance.protocols
                    (version,bundle_hash,bundle,notice_version,notice_hash,signature_method,
                     approved,approval_reference,enrollment_open)
                    values ('synthetic-v1',%s,'{"oura_authorization_monitoring":"access-token-v1"}',
                            'test-v1',%s,'explicit_consent',
                            true,'isolated synthetic test',true)""", ("b" * 64, HASH))
            created_roles.extend(role for role in ("qs_monitor_reader", "qs_backup_agent")
                                 if role not in previous_roles)
            yield admin, settings, template
        finally:
            admin.execute(sql.SQL("drop database if exists {} with (force)").format(
                sql.Identifier(template),
            ))
            for role in reversed(created_roles):
                admin.execute(sql.SQL("drop role {}").format(sql.Identifier(role)))


class Database:
    def __init__(self, settings):
        self.settings = settings
        self.admin = connect(settings)

    @contextmanager
    def session(self, *, actor=True, name="qc_concurrency"):
        with connect(self.settings, actor=actor, name=name) as connection:
            yield connection

    def one(self, statement, params=()):
        return self.admin.execute(statement, params).fetchone()

    def participant(self):
        participant = str(uuid.uuid4())
        evidence = {
            "enrollment_id": participant, "participant_id": participant,
            "version": "synthetic-v1", "bundle_hash": "b" * 64,
            "adult": True, "own_data": True, "explicit_consent": True,
            "signature_method": "explicit_consent",
            "signed_at_client": datetime.now(timezone.utc).isoformat(),
            "before_first_results": True, "choice_event_id": 1,
            "notice_version": "test-v1", "notice_hash": HASH,
        }
        with self.session() as connection:
            connection.execute("select public.qs_register(%s,%s)", (participant, HASH))
            connection.execute("select public.qs_enroll(%s,%s,%s)",
                               (participant, HASH, Jsonb(evidence)))
        return participant

    def packet(self, participant, *, value="2026-10-01", submission=None, oura=False):
        schema = json.loads((ROOT / "addon/research_schema.json").read_text())
        metadata = dict.fromkeys(schema["metadata"], "")
        metadata.update(
            {
                "research_participant_id": participant,
                "sharing_notice_version": "test-v1",
                "sharing_notice_hash": HASH,
                "research_consent_active": True,
                "sharing_current_choice": "opt_in",
            }
        )
        submission = submission or str(uuid.uuid4())
        payload = seal_package(
            {
                "schema_version": schema["package_version"],
                "enrollment_id": participant,
                "submission_id": submission,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata,
                "columns": schema["columns"],
                "rows": [[value if name == "date" else "100" if oura and name == "oura_steps"
                          else "" for name in schema["columns"]]],
                "reviews": {"columns": schema["review_columns"], "rows": [], "context": {
                    "prediction_basis": "reconstructed_current_fsrs", "anki_version": "synthetic",
                    "calculation_version": 7, "parameters_hash": HASH,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_history_status": "synthetic",
                }},
            }
        )
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return participant, HASH, submission, Jsonb(payload), digest

    @staticmethod
    def submit(connection, packet):
        return connection.execute(
            "select public.qs_submit(%s,%s,%s,%s,%s) as result", packet,
        ).fetchone()["result"]

    @staticmethod
    def delete(connection, participant):
        return connection.execute("select public.qs_delete_request(%s,%s,%s) as result",
                                  (participant, HASH, RECEIPT)).fetchone()["result"]

    def accounting(self):
        budget = self.one("select * from qs_private.storage_budget")
        stored = self.one("""select count(*)::int as n,
            coalesce(sum(payload_bytes),0) as bytes from qs_private.submissions""")
        assert budget["submissions"] == stored["n"]
        assert budget["payload_bytes"] == stored["bytes"]
        return budget

    def blocked(self, name, count):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            waiting = self.one("""select count(*) as n from pg_stat_activity
                where datname=current_database() and application_name=%s
                  and state='active' and wait_event_type='Lock'""", (name,))["n"]
            if waiting == count:
                return
            time.sleep(0.01)
        raise AssertionError(f"Expected {count} independent lock waiters, observed {waiting}")

    def race(self, lock, count, work, params=()):
        """Hold the contested row until every independent worker is blocked."""
        name = "qc_race_" + uuid.uuid4().hex

        def worker(index):
            with self.session(name=name) as connection:
                pid = connection.execute("select pg_backend_pid() as pid").fetchone()["pid"]
                try:
                    return pid, work(connection, index), None
                except psycopg.Error as error:
                    return pid, None, (error.sqlstate, error.diag.message_primary)

        with self.session(actor=False) as blocker, ThreadPoolExecutor(count) as pool:
            blocker.execute("begin")
            try:
                blocker.execute(lock, params)
                futures = [pool.submit(worker, index) for index in range(count)]
                self.blocked(name, count)
            finally:
                blocker.execute("rollback")
            results = [future.result(timeout=18) for future in futures]
        assert len({pid for pid, _, _ in results}) == count
        return results


@pytest.fixture
def db(cluster):
    admin, settings, template = cluster
    name = "qc_test_" + uuid.uuid4().hex
    admin.execute(sql.SQL("create database {} template {}").format(
        sql.Identifier(name), sql.Identifier(template),
    ))
    database = Database({**settings, "dbname": name})
    try:
        yield database
    finally:
        database.admin.close()
        admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


@pytest.mark.parametrize("same_actor,limit", [(False, 4), (True, 3)])
def test_concurrent_admission_never_overcharges_global_or_actor_budgets(db, same_actor, limit):
    db.admin.execute("""update qs_private.quota_policy set global_minute=%s,global_day=%s,
        actor_minute=%s,actor_day=%s where family='submit'""",
                     (100 if same_actor else limit,) * 2 + (3, 3))
    results = db.race("select 1 from qs_private.quota_policy where family='submit' for update",
                     24, lambda connection, index: connection.execute(
                         "select public.qs_admit('submit',%s) as result",
                         (HASH if same_actor else f"{index:064x}",),
                     ).fetchone()["result"])
    assert not any(error for _, _, error in results)
    assert sum(result["allowed"] for _, result, _ in results) == limit
    assert all(result["retry_after"] > 0 for _, result, _ in results if not result["allowed"])
    buckets = db.admin.execute("select * from qs_private.quota_buckets").fetchall()
    assert len(buckets) == (4 if same_actor else 2 + 2 * limit)
    assert all(row["used"] == limit for row in buckets if row["subject"] == "global")
    assert db.one("select sum(requests) as n from qs_private.ops_metrics")["n"] == 24 - limit


def test_simultaneous_uploads_share_storage_budget_and_rejections_roll_back(db):
    packets = [db.packet(db.participant()) for _ in range(16)]
    db.admin.execute("update qs_private.storage_budget set submission_limit=4")
    results = db.race("select 1 from qs_private.storage_budget for update", 16,
                     lambda connection, index: db.submit(connection, packets[index]))
    assert sum(error is None for _, _, error in results) == 4
    assert all(error is None or error[1] == "quota_exceeded" for _, _, error in results)
    budget = db.accounting()
    assert budget["submissions"] == budget["daily_submissions"] == 4
    assert budget["first_research_uploads"] == 4
    assert budget["payload_bytes"] == budget["daily_bytes"]


@pytest.mark.parametrize("conflicting", [False, True])
def test_simultaneous_duplicate_uploads_store_one_package_and_one_receipt(db, conflicting):
    person = db.participant()
    packet = db.packet(person)
    alternative = db.packet(person, value="2026-10-02", submission=packet[2])
    results = db.race("select 1 from qs_private.participants where id=%s for update", 12,
                     lambda connection, index: db.submit(
                         connection, alternative if conflicting and index % 2 else packet,
                     ), (person,))
    receipts = [result for _, result, error in results if error is None]
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(receipts) == (6 if conflicting else 12)
    assert all(error is None or error == ("23505", "submission conflict")
               for _, _, error in results)
    budget = db.accounting()
    assert budget["submissions"] == budget["daily_submissions"] == 1
    with db.session() as connection:
        db.submit(connection, db.packet(person, value="2026-10-03"))
    budget = db.accounting()
    assert budget["submissions"] == 2
    assert budget["first_research_uploads"] == 1


@pytest.mark.parametrize("revoke_first", [False, True])
def test_oura_revocation_and_upload_cannot_resurrect_measurements(db, revoke_first):
    db.admin.execute("update qs_governance.protocols set oura_permitted=true")
    person = db.participant()
    generation = str(uuid.uuid4())
    with db.session() as connection:
        connection.execute(
            "select public.qs_oura_authorization_bind(%s,%s,%s,%s,now()+interval '1 day',%s,true)",
            (person, HASH, generation, Jsonb({"nonce": "synthetic", "body": "encrypted"}), RECEIPT),
        )
        connection.execute("select public.qs_oura_authorization_result(%s,%s,'verified')",
                           (person, generation))
    packet = db.packet(person, oura=True)
    def upload(connection):
        return db.submit(connection, packet)
    def revoke(connection):
        return connection.execute("select public.qs_oura_authorization_result(%s,%s,'revoked')",
                                  (person, generation)).fetchone()
    _, _, error = ordered_race(db, revoke if revoke_first else upload,
                              upload if revoke_first else revoke)
    assert error == ("unauthorized" if revoke_first else None)
    assert db.one("select count(*)::int n from qs_private.submissions")["n"] == 0
    assert db.one("select deleted_at is not null deleted from qs_private.participants "
                  "where id=%s", (person,))["deleted"]
    db.accounting()


def ordered_race(db, first, second):
    """Run both meaningful serialization orders without timing-dependent sleeps."""
    name = "qc_ordered_" + uuid.uuid4().hex
    with db.session() as leader, db.session(name=name) as follower:
        leader.execute("begin")
        with ThreadPoolExecutor(1) as pool:
            try:
                first_result = first(leader)
                future = pool.submit(second, follower)
                db.blocked(name, 1)
            except BaseException:
                leader.execute("rollback")
                raise
            else:
                leader.execute("commit")
            try:
                second_result = future.result(timeout=18)
                error = None
            except psycopg.Error as exception:
                second_result = None
                error = exception.diag.message_primary
    return first_result, second_result, error


@pytest.mark.parametrize("delete_first", [False, True])
def test_upload_and_deletion_race_cannot_resurrect_measurements(db, delete_first):
    person = db.participant()
    packet = db.packet(person)
    upload = lambda connection: db.submit(connection, packet)  # noqa: E731
    deletion = lambda connection: db.delete(connection, person)  # noqa: E731
    first, second, error = ordered_race(
        db, deletion if delete_first else upload, upload if delete_first else deletion,
    )
    assert error == ("unauthorized" if delete_first else None)
    assert (first if delete_first else second)["complete"] is True
    budget = db.accounting()
    assert budget["submissions"] == 0
    assert budget["daily_submissions"] == (0 if delete_first else 1)
    assert budget["first_research_uploads"] == (0 if delete_first else 1)
    with db.session() as connection:
        assert connection.execute("select public.qs_register(%s,%s) as result",
                                  (person, HASH)).fetchone()["result"] is False
        with pytest.raises(psycopg.Error, match="unauthorized"):
            db.submit(connection, packet)


def test_simultaneous_deletion_retries_keep_one_receipt_and_consistent_counters(db):
    person = db.participant()
    with db.session() as connection:
        db.submit(connection, db.packet(person))
    results = db.race("select 1 from qs_private.participants where id=%s for update", 12,
                     lambda connection, _: db.delete(connection, person), (person,))
    assert not any(error for _, _, error in results)
    assert all(result == results[0][1] for _, result, _ in results)
    assert results[0][1]["complete"] is True
    assert db.accounting()["submissions"] == 0
    assert db.one("select count(*) as n from qs_governance.deletion_cases")["n"] == 1


def test_deadline_maintenance_and_withdrawal_use_a_consistent_lock_order(db):
    # Exceed the PL/pgSQL cursor's initial fetch batch, so a later participant
    # cannot be acquired after an earlier deletion has locked shared accounting.
    people = sorted(db.participant() for _ in range(64))
    db.admin.execute("update qs_governance.retention set mandatory_delete_at=now()-interval '1s'")
    last = people[-1]
    name = "qc_maintenance_" + uuid.uuid4().hex
    with (db.session() as withdrawing, db.session(actor=False, name=name) as maintenance,
          ThreadPoolExecutor(1) as pool):
        withdrawing.execute("begin")
        withdrawing.execute("select 1 from qs_private.participants where id=%s for update", (last,))
        future = pool.submit(maintenance.execute, "select qs_governance.maintain_research()")
        try:
            db.blocked(name, 1)
            receipt = db.delete(withdrawing, last)
            withdrawing.execute("commit")
        finally:
            withdrawing.execute("rollback")
        future.result(timeout=18)
    assert receipt["complete"] is True
    remaining = db.one("select count(*) as n from qs_private.participants where deleted_at is null")
    assert remaining["n"] == 0
    assert db.accounting()["submissions"] == 0


@pytest.mark.parametrize("delete_first", [False, True])
def test_extract_release_and_withdrawal_are_serialized_and_every_released_copy_is_tracked(
    db, delete_first,
):
    person = db.participant()
    with db.session() as connection:
        db.submit(connection, db.packet(person))
    extract = str(uuid.uuid4())
    db.admin.execute("""select qs_governance.register_extract(%s,'Synthetic copy','operator',
        null,'Validation','disposable test workspace',now()+interval '1 day',%s::uuid[])""",
                     (extract, [person]))

    def release(connection):
        # Operator action, distinct from the unprivileged Edge role.
        connection.execute("reset role")
        return connection.execute("select * from qs_governance.release_extract(%s)",
                                  (extract,)).fetchall()

    deletion = lambda connection: db.delete(connection, person)  # noqa: E731
    first, second, error = ordered_race(
        db, deletion if delete_first else release, release if delete_first else deletion,
    )
    if delete_first:
        assert first["complete"] is True
        assert error == "participant unavailable"
        assert db.one("select released_at from qs_governance.extracts")["released_at"] is None
    else:
        assert len(first) == 1
        assert error is None
        assert second["complete"] is False and second["pending_copies"] == 1
        db.admin.execute("select qs_governance.confirm_copy_deletion(%s,%s,%s)",
                         (extract, person, "synthetic workspace deletion confirmed"))
        with db.session() as connection:
            assert db.delete(connection, person)["complete"] is True
    assert db.accounting()["submissions"] == 0
