"""Bounded recovery snapshots; plaintext is streamed, never staged on disk."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

PROJECT = "wfjlzecckihsgcvauqwt"
FORMAT = "quantified-cognition-recovery-v1"
MAX_BYTES = 512 * 1024 * 1024
MAX_LINE = 32 * 1024 * 1024
EPHEMERAL_TABLES = ("qs_private.oura_research_authorizations",)
TABLES = (
    "qs_private.participants",
    "qs_governance.protocols",
    "qs_governance.recipients",
    "qs_governance.enrollments",
    "qs_governance.retention",
    "qs_governance.extracts",
    "qs_governance.extract_members",
    "qs_governance.deletion_cases",
    "qs_governance.audit",
    "qs_private.submissions",
    "qs_private.quota_policy",
    "qs_private.quota_buckets",
    "qs_private.ops_metrics",
    "qs_private.ops_alerts",
    "qs_private.ops_state",
    "qs_private.storage_budget",
)
PARTICIPANT_TABLES = {
    "qs_governance.enrollments",
    "qs_governance.retention",
    "qs_governance.extract_members",
    "qs_governance.deletion_cases",
    "qs_private.submissions",
}


def identifier(table):
    if table not in TABLES + EPHEMERAL_TABLES:
        raise ValueError("Unexpected recovery table")
    return sql.Identifier(*table.split("."))


def encode(record):
    return (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()


def versions(connection):
    return [
        row[0]
        for row in connection.execute(
            "select version from supabase_migrations.schema_migrations order by version"
        )
    ]


def snapshot(connection, inventory, output):
    """Export one consistent snapshot, bounded to the previously tracked inventory."""
    inventory = [str(uuid.UUID(str(person))) for person in inventory]
    digest, counts, written = hashlib.sha256(), dict.fromkeys(TABLES, 0), 0

    def emit(stream, record, *, hashed=True):
        nonlocal written
        raw = encode(record)
        written += len(raw)
        if written > MAX_BYTES or len(raw) > MAX_LINE:
            raise ValueError("Recovery snapshot exceeds its size limit")
        if hashed:
            digest.update(raw)
        stream.write(raw)

    with connection.transaction():
        connection.execute("set transaction isolation level repeatable read, read only")
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            emit(
                stream,
                {
                    "type": "header",
                    "format": FORMAT,
                    "project": PROJECT,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "schema_versions": versions(connection),
                    "tables": list(TABLES),
                },
            )
            for number, table in enumerate(TABLES):
                predicate, parameters = sql.SQL(""), ()
                eligible = sql.SQL("""select id from qs_private.participants
                    where id=any(%s::uuid[]) or deleted_at is not null""")
                if table == "qs_private.participants":
                    predicate = sql.SQL("where id=any(%s::uuid[]) or deleted_at is not null")
                    parameters = (inventory,)
                elif table in PARTICIPANT_TABLES:
                    predicate = sql.SQL("where participant_id in ({})").format(eligible)
                    parameters = (inventory,)
                statement = sql.SQL("select to_jsonb(t) from {} t {}").format(
                    identifier(table),
                    predicate,
                )
                with connection.cursor(name=f"qc_snapshot_{number}") as cursor:
                    cursor.itersize = 1
                    cursor.execute(statement, parameters)
                    for row in cursor:
                        emit(stream, {"type": "row", "table": table, "row": row[0]})
                        counts[table] += 1
            emit(
                stream,
                {"type": "end", "counts": counts, "sha256": digest.hexdigest()},
                hashed=False,
            )
    return {
        "rows": sum(counts.values()),
        "uncompressed_bytes": written,
        "content_sha256": digest.hexdigest(),
    }


def deletion_ledger(connection):
    return {
        str(row[0]): row[1].isoformat()
        for row in connection.execute("""
        select p.id,coalesce(p.deleted_at,d.requested_at) from qs_private.participants p
        left join qs_governance.deletion_cases d on d.participant_id=p.id
        where p.deleted_at is not null or d.participant_id is not null""")
    }


def validate_ledger(ledger):
    if not isinstance(ledger, dict) or len(ledger) > 1000000:
        raise ValueError("Invalid deletion ledger")
    result = {}
    for person, when in ledger.items():
        identity = str(uuid.UUID(person))
        timestamp = datetime.fromisoformat(when)
        if timestamp.tzinfo is None:
            raise ValueError("Deletion timestamps require a timezone")
        result[identity] = timestamp.isoformat()
    return result


def restore(dsn, source, ledger):
    """Restore only into an explicitly marked, disposable loopback database."""
    settings = conninfo_to_dict(dsn)
    if settings.get("host") not in {"127.0.0.1", "::1"}:
        raise ValueError("Recovery rehearsal requires an explicit loopback host")
    if settings.keys() - {"host", "port", "user", "password", "dbname", "sslmode"}:
        raise ValueError("Unexpected recovery connection options")
    ledger = validate_ledger(ledger)
    with psycopg.connect(**settings) as target:
        marker = target.execute(
            "select current_setting('quantified_cognition.test_cluster',true)"
        ).fetchone()[0]
        if marker != "on":
            raise ValueError("Recovery target is not a marked disposable cluster")
        target.execute("set statement_timeout='120s'")
        expected_versions = versions(target)
        # Credentials are deliberately not backed up or restored. Clear their
        # FK rows and suspend only the Oura admission trigger inside this isolated
        # recovery transaction; closure and fresh verification follow below.
        target.execute("alter table qs_private.submissions disable trigger oura_research_upload")
        target.execute(
            sql.SQL("truncate {} restart identity").format(
                sql.SQL(",").join(identifier(table) for table in TABLES + EPHEMERAL_TABLES),
            )
        )
        digest, counts, total, last_table = hashlib.sha256(), dict.fromkeys(TABLES, 0), 0, -1
        with gzip.GzipFile(fileobj=source, mode="rb") as stream:
            raw = stream.readline(MAX_LINE + 1)
            if len(raw) > MAX_LINE:
                raise ValueError("Oversized recovery header")
            header = json.loads(raw)
            if (
                header.get("type") != "header"
                or header.get("format") != FORMAT
                or header.get("project") != PROJECT
                or header.get("tables") != list(TABLES)
                or header.get("schema_versions") != expected_versions
            ):
                raise ValueError("Recovery schema or project mismatch")
            digest.update(raw)
            total += len(raw)
            while True:
                raw = stream.readline(MAX_LINE + 1)
                total += len(raw)
                if not raw or len(raw) > MAX_LINE or total > MAX_BYTES:
                    raise ValueError("Truncated or oversized recovery snapshot")
                record = json.loads(raw)
                if record.get("type") == "end":
                    if (
                        record.get("counts") != counts
                        or record.get("sha256") != digest.hexdigest()
                        or stream.read(1)
                    ):
                        raise ValueError("Recovery integrity check failed")
                    break
                table = record.get("table")
                if (
                    record.get("type") != "row"
                    or table not in TABLES
                    or TABLES.index(table) < last_table
                    or not isinstance(record.get("row"), dict)
                ):
                    raise ValueError("Unexpected recovery record")
                last_table = TABLES.index(table)
                target.execute(
                    sql.SQL("""insert into {} overriding system value
                    select * from jsonb_populate_record(null::{},%s)""").format(
                        identifier(table),
                        identifier(table),
                    ),
                    (Jsonb(record["row"]),),
                )
                counts[table] += 1
                digest.update(raw)
        for person, when in ledger.items():
            target.execute(
                """insert into qs_private.participants(id,deleted_at) values(%s,%s)
                on conflict(id) do update set deleted_at=coalesce(
                qs_private.participants.deleted_at,
                excluded.deleted_at),capability_hash=null""",
                (person, when),
            )
        removed = target.execute("select qs_governance.replay_deletions()").fetchone()[0]
        target.execute("alter table qs_private.submissions enable trigger oura_research_upload")
        target.execute("""update qs_governance.protocols set approved=false,enrollment_open=false,
            oura_permitted=false,google_permitted=false""")
        target.execute("update qs_governance.retention set state='held' where state='active'")
        target.execute("update qs_governance.extracts set state='held' where state='released'")
        target.execute("""select setval('qs_governance.audit_id_seq',
            greatest(coalesce((select max(id) from qs_governance.audit),0),1),
            exists(select 1 from qs_governance.audit))""")
        # The context commits only after integrity, deletion replay and closure.
        return {
            "restored_rows": sum(counts.values()),
            "resurrected_submissions_removed": removed,
            "deletion_markers": len(ledger),
            "research_closed": True,
        }
