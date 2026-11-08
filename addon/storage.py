"""Profile-local results database and daily update preferences."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import date
from functools import wraps
from pathlib import Path

from .backend import CALCULATION_VERSION, Report, UpdateCancelled, build_report, input_fingerprint
from .export import METRIC_FIELDS, METRIC_PREFIXES, export_csv_atomic
from .health import HEALTH_DATABASE_NAME, health_revision_at, load_health
from .metrics import DEFAULT_SETTINGS, SHORT_TERM_MAX_DAYS, DailyResult, Metric, Settings
from .research import (
    SharingConfig,
    audit_path,
    finish_csv_exposure,
    prepare_csv_exposure,
    sharing_revision_at,
    sharing_state,
)

DIRECTORY_NAME = "quantified-self-memory"
DATABASE_NAME = "results.sqlite3"
PREFERENCES_NAME = "daily-settings.json"
SCHEMA_VERSION = 4
EXPORT_VERSION = 8
DAILY_COLUMNS = ["date"] + [
    f"{metric}_{field}" for metric in METRIC_PREFIXES for field in METRIC_FIELDS
]
_profile_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_held_locks = threading.local()


@contextmanager
def profile_job_lock(collection_path: str | Path, name: str = "update"):
    """Serialize publishers across threads and overlapping Anki processes.

    Snapshot capture never takes this lock: closing the live collection must
    not wait for an older calculation or a locked export destination.
    """
    if name not in (
        "update",
        "credentials",
        "oura",
        "google_health",
        "submission",
        "insights",
        "visits",
        "health",
    ):
        raise ValueError("Unknown profile lock.")
    key = os.path.normcase(str(Path(collection_path).resolve())) + ":" + name
    with _locks_guard:
        lock = _profile_locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held_locks, "keys", set())
        if key in held:
            yield
            return
        directory = results_directory(collection_path)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f".{name}.lock").open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if os.name == "nt":
                import errno
                import msvcrt

                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _held_locks.keys = held | {key}
            try:
                yield
            finally:
                _held_locks.keys = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _serialized_profile_job(function):
    """A file-only retry must not overwrite a concurrently updated fixed CSV."""

    @wraps(function)
    def run(collection_or_path, *args, **kwargs):
        path = kwargs.get("source_collection_path") or getattr(
            collection_or_path, "path", collection_or_path
        )
        with profile_job_lock(path):
            return function(collection_or_path, *args, **kwargs)

    return run


def results_directory(collection_path: str | Path) -> Path:
    return Path(collection_path).resolve().parent / DIRECTORY_NAME


def database_path(collection_path: str | Path) -> Path:
    return results_directory(collection_path) / DATABASE_NAME


@dataclass(frozen=True)
class DailySettings:
    enabled: bool = False
    csv_path: str = ""

    def validated_path(self) -> Path:
        if not self.csv_path.strip():
            raise ValueError("Choose a CSV file for daily updates.")
        path = Path(self.csv_path).expanduser()
        if not path.is_absolute():
            raise ValueError("The daily CSV location must be an absolute path.")
        if path.suffix.lower() != ".csv":
            raise ValueError("The daily export file must end in .csv.")
        if path.is_dir():
            raise ValueError("Choose a CSV file, rather than a folder.")
        return path.resolve()


def load_daily_settings(collection_path: str | Path) -> DailySettings:
    path = results_directory(collection_path) / PREFERENCES_NAME
    if not path.exists():
        return DailySettings()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The daily update settings file is invalid.")
    return DailySettings(
        enabled=data.get("enabled") is True,
        csv_path=data.get("csv_path", "") if isinstance(data.get("csv_path", ""), str) else "",
    )


def save_daily_settings(collection_path: str | Path, settings: DailySettings) -> None:
    if settings.enabled:
        settings.validated_path()
    directory = results_directory(collection_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PREFERENCES_NAME
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".daily-settings-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(asdict(settings), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _initialize(connection: sqlite3.Connection) -> None:
    version = connection.execute("pragma user_version").fetchone()[0]
    if version not in (0, 1, 2, 3, SCHEMA_VERSION):
        raise ValueError("The results database was created by a newer add-on version.")
    definitions = ["date text primary key"]
    for prefix in METRIC_PREFIXES:
        for field in METRIC_FIELDS:
            kind = (
                "text"
                if field == "status"
                else (
                    "integer"
                    if field
                    in {"count", "cohort_count", "pending_count", "excluded_count", "outcome_days"}
                    else "real"
                )
            )
            definitions.append(f"{prefix}_{field} {kind}")
    connection.execute(f"create table if not exists daily_results ({', '.join(definitions)})")
    connection.execute("create table if not exists metadata (key text primary key, value text)")
    connection.execute(
        "create table if not exists review_predictions (singleton integer primary key "
        "check(singleton=1), payload blob not null)"
    )
    available = {row[1] for row in connection.execute("pragma table_info(daily_results)")}
    for definition in definitions[1:]:
        column = definition.split()[0]
        if column not in available:
            if version == SCHEMA_VERSION:
                raise ValueError("The results database is missing required columns.")
            connection.execute(f"alter table daily_results add column {definition}")
    connection.execute(f"pragma user_version = {SCHEMA_VERSION}")


def reserve_snapshot_sequence(collection_path: str | Path, sharing=None) -> int:
    """Allocate durable capture order, independently of the calculation lock."""
    sharing_state(collection_path, sharing)
    path = database_path(collection_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
        connection.execute("begin immediate")
        _initialize(connection)
        row = connection.execute(
            "select value from metadata where key='capture_sequence'"
        ).fetchone()
        latest = connection.execute(
            "select value from metadata where key='last_snapshot_sequence'"
        ).fetchone()
        sequence = max(int(row[0]) if row else 0, int(latest[0]) if latest else 0) + 1
        connection.execute(
            "insert or replace into metadata values ('capture_sequence', ?)", (str(sequence),)
        )
    return sequence


def saved_snapshot_sequence(collection_path: str | Path) -> int:
    path = database_path(collection_path)
    if not path.exists():
        return -1
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "select value from metadata where key='last_snapshot_sequence'"
        ).fetchone()
    return int(row[0]) if row else -1


def save_report(
    report: Report,
    path: str | Path,
    *,
    fingerprint: str = "",
    snapshot_sequence: int | None = None,
    scheduled_day: date | None = None,
) -> None:
    """Atomically replace the latest analysis, including revised formation scores."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        field.name: getattr(report, field.name)
        for field in fields(Report) if field.name not in ("days", "review_records")
    }
    metadata["settings"] = asdict(report.settings)
    rows = []
    for day in report.days:
        rows.append(
            [day.day.isoformat()]
            + [
                (getattr(getattr(day, prefix), field) if getattr(day, prefix) is not None else None)
                for prefix in METRIC_PREFIXES
                for field in METRIC_FIELDS
            ]
        )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("begin")
        _initialize(connection)
        connection.execute("delete from daily_results")
        connection.execute("delete from review_predictions")
        if report.review_records is not None:
            connection.execute(
                "insert into review_predictions values (1, ?)",
                (zlib.compress(json.dumps(
                    report.review_records, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")),),
            )
        connection.executemany(
            f"insert into daily_results ({', '.join(DAILY_COLUMNS)}) "
            f"values ({', '.join('?' for _ in DAILY_COLUMNS)})",
            rows,
        )
        connection.execute(
            "insert or replace into metadata values ('report', ?)",
            (json.dumps(metadata, sort_keys=True),),
        )
        connection.execute(
            "insert or replace into metadata values ('formation_short_term_max_days', ?)",
            (str(SHORT_TERM_MAX_DAYS),),
        )
        connection.execute(
            "insert or replace into metadata values ('input_fingerprint', ?)", (fingerprint,)
        )
        connection.execute(
            "insert or replace into metadata values ('report_revision', ?)", (uuid.uuid4().hex,)
        )
        connection.execute(
            "insert or replace into metadata values ('calculation_version', ?)",
            (str(report.calculation_version),),
        )
        if snapshot_sequence is not None:
            connection.execute(
                "insert or replace into metadata values ('last_snapshot_sequence', ?)",
                (str(snapshot_sequence),),
            )
        if scheduled_day is not None:
            connection.execute(
                "insert or replace into metadata values ('last_analysis_day', ?)",
                (scheduled_day.isoformat(),),
            )


def _saved_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("pragma user_version").fetchone()[0] != SCHEMA_VERSION:
            return ""
        stored = dict(
            connection.execute(
                "select key, value from metadata where key in "
                "('input_fingerprint', 'calculation_version')"
            )
        )
    if stored.get("calculation_version") != str(CALCULATION_VERSION):
        return ""
    return stored.get("input_fingerprint", "")


def load_report(path: str | Path) -> Report | None:
    path = Path(path).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        version = connection.execute("pragma user_version").fetchone()[0]
        if version not in (1, 2, 3, SCHEMA_VERSION):
            raise ValueError("Unsupported results database version.")
        connection.row_factory = sqlite3.Row
        stored = connection.execute("select value from metadata where key = 'report'").fetchone()
        if stored is None:
            return None
        metadata = json.loads(stored[0])
        metadata.setdefault("calculation_version", 0)  # Unknown in legacy reports.
        metadata["settings"] = Settings.from_config(metadata["settings"])
        days = []
        for row in connection.execute("select * from daily_results order by date"):
            metrics = [
                (
                    Metric(
                        **{field.name: row[f"{prefix}_{field.name}"] for field in fields(Metric)}
                    )
                    if f"{prefix}_count" in row.keys() and row[f"{prefix}_count"] is not None
                    else None
                )
                for prefix in METRIC_PREFIXES
            ]
            days.append(DailyResult(date.fromisoformat(row["date"]), *metrics))
        return Report(days=days, **metadata)


def load_review_records(path: str | Path) -> list[dict]:
    """Read the review cache from the same transactionally saved report generation."""
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("pragma user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Open the memory report to update review predictions before sharing.")
        row = connection.execute(
            "select payload from review_predictions where singleton=1"
        ).fetchone()
    if row is None:
        raise ValueError("Open the memory report to calculate review predictions before sharing.")
    return json.loads(zlib.decompress(row[0]))


def daily_update_due(path: str | Path, day: date, csv_path: str | Path) -> bool:
    path = Path(path).resolve()
    if not path.exists():
        return True
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("pragma user_version").fetchone()[0] != SCHEMA_VERSION:
            return True
        stored = dict(
            connection.execute(
                "select key, value from metadata where key in "
                "('last_daily_day', 'last_daily_csv', 'last_daily_schema', "
                "'last_daily_export_version', 'report_revision', 'last_export_revision', "
                "'last_export_health_revision', 'last_export_sharing_revision', "
                "'calculation_version')"
            )
        )
    return (
        stored.get("calculation_version") != str(CALCULATION_VERSION)
        or stored.get("last_daily_day") != day.isoformat()
        or stored.get("last_daily_csv") != str(Path(csv_path).resolve())
        or stored.get("last_daily_schema") != str(SCHEMA_VERSION)
        or stored.get("last_daily_export_version") != str(EXPORT_VERSION)
        or stored.get("last_export_revision") != stored.get("report_revision")
        or stored.get("last_export_health_revision", "")
        != health_revision_at(path.parent / HEALTH_DATABASE_NAME)
        or stored.get("last_export_sharing_revision", "")
        != sharing_revision_at(path.parent / "study-sharing.sqlite3")
        or not Path(csv_path).is_file()
    )


def _mark_daily_success(
    path: Path, day: date, csv_path: Path, *, health_revision: str = "", sharing_revision: str = ""
) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        revision = connection.execute(
            "select value from metadata where key='report_revision'"
        ).fetchone()
        connection.executemany(
            "insert or replace into metadata values (?, ?)",
            [
                ("last_daily_day", day.isoformat()),
                ("last_daily_csv", str(csv_path.resolve())),
                ("last_daily_schema", str(SCHEMA_VERSION)),
                ("last_daily_export_version", str(EXPORT_VERSION)),
                ("last_export_revision", revision[0] if revision else ""),
                ("last_export_health_revision", health_revision),
                ("last_export_sharing_revision", sharing_revision),
            ],
        )


def csv_export_pending(path: str | Path, csv_path: str | Path) -> bool:
    """Whether saved, already-checked analysis needs only its file published."""
    path = Path(path).resolve()
    if not path.exists():
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("pragma user_version").fetchone()[0] != SCHEMA_VERSION:
            return False
        stored = dict(
            connection.execute(
                "select key, value from metadata where key in "
                "('last_analysis_day', 'report_revision', 'last_export_revision', "
                "'last_daily_csv', 'last_daily_export_version', 'last_export_health_revision', "
                "'last_export_sharing_revision', 'calculation_version')"
            )
        )
    # An old method needs analysis even if an old CSV publication also failed.
    if stored.get("calculation_version") != str(CALCULATION_VERSION):
        return False
    return bool(stored.get("last_analysis_day") and stored.get("report_revision")) and (
        stored.get("last_export_revision") != stored["report_revision"]
        or stored.get("last_export_health_revision", "")
        != health_revision_at(path.parent / HEALTH_DATABASE_NAME)
        or stored.get("last_export_sharing_revision", "")
        != sharing_revision_at(path.parent / "study-sharing.sqlite3")
        or stored.get("last_daily_csv") != str(Path(csv_path).resolve())
        or stored.get("last_daily_export_version") != str(EXPORT_VERSION)
        or not Path(csv_path).is_file()
    )


@dataclass(frozen=True)
class UpdateResult:
    report: Report
    recalculated: bool
    csv_error: str | None = None
    csv_exported: bool = False


def export_fixed_csv(
    report: Report,
    collection_path: str | Path,
    daily: DailySettings,
    *,
    scheduled_day: date | None = None,
    sharing: SharingConfig | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> None:
    """Retryable file-only step; never reads/reconstructs the Anki collection."""
    csv_path = daily.validated_path()
    health = load_health(collection_path)
    sharing_revision = sharing_revision_at(audit_path(collection_path))
    if report.days or health.days:
        _snapshot, token, metadata = prepare_csv_exposure(collection_path, sharing)
    else:
        token = None
        metadata = sharing_state(collection_path, sharing).metadata()
    try:
        if check_cancelled is None:
            export_csv_atomic(report, csv_path, research_metadata=metadata, health_snapshot=health)
        else:
            export_csv_atomic(
                report,
                csv_path,
                research_metadata=metadata,
                before_replace=check_cancelled,
                health_snapshot=health,
            )
    except Exception:
        finish_csv_exposure(collection_path, sharing, token, success=False)
        raise
    finish_csv_exposure(collection_path, sharing, token, success=True)
    if scheduled_day is not None:
        _mark_daily_success(
            database_path(collection_path),
            scheduled_day,
            csv_path,
            health_revision=health.revision,
            sharing_revision=sharing_revision,
        )


@_serialized_profile_job
def retry_fixed_csv(
    collection_path: str | Path,
    sharing: SharingConfig | None = None,
    *,
    check_cancelled: Callable[[], None] | None = None,
    only_if_pending: bool = False,
) -> UpdateResult:
    """Export saved analysis without collection access, using its last checked day."""
    daily = load_daily_settings(collection_path)
    if not daily.enabled:
        raise ValueError("Daily CSV export is disabled. Check Daily settings.")
    path = database_path(collection_path)
    if check_cancelled is not None:
        check_cancelled()
    report = load_report(path)
    if report is None:
        raise ValueError("No saved scores to export. Update the analysis first.")
    if only_if_pending and not csv_export_pending(path, daily.validated_path()):
        return UpdateResult(report, False)
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "select value from metadata where key='last_analysis_day'"
        ).fetchone()
    scheduled_day = date.fromisoformat(row[0]) if row else None
    export_fixed_csv(
        report,
        collection_path,
        daily,
        scheduled_day=scheduled_day,
        sharing=sharing,
        check_cancelled=check_cancelled,
    )
    return UpdateResult(report, False, csv_exported=True)


@_serialized_profile_job
def update_results(
    collection,
    settings: Settings = DEFAULT_SETTINGS,
    *,
    daily: DailySettings | None = None,
    scheduled_day: date | None = None,
    sharing: SharingConfig | None = None,
    force: bool = False,
    source_collection_path: str | Path | None = None,
    snapshot_sequence: int | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> UpdateResult:
    """Reuse unchanged analysis; export failures are independent of calculation.

    The full input scan detects historical sync edits/deletions as well as new
    reviews. Only changed inputs (or force=True) trigger the native replay. A
    failed CSV leaves fresh results available and the daily run due for retry.
    """
    # Initialize before creating new results, so a new installation is not
    # mistakenly classified as an untracked older installation on its next run.
    collection_path = source_collection_path or collection.path
    if check_cancelled is not None:
        check_cancelled()
    # Independent workers can start out of capture order. An older snapshot
    # must never replace the results/export produced by a newer one.
    if snapshot_sequence is not None and snapshot_sequence < saved_snapshot_sequence(
        collection_path
    ):
        latest = load_report(database_path(collection_path))
        if latest is not None:
            return UpdateResult(latest, False)
    sharing_state(collection_path, sharing)
    path = database_path(collection_path)
    fingerprint = (
        input_fingerprint(collection, settings, check_cancelled=check_cancelled)
        if check_cancelled is not None
        else input_fingerprint(collection, settings)
    )
    report = None
    if not force and _saved_fingerprint(path) == fingerprint:
        report = load_report(path)
    recalculated = report is None
    if recalculated:
        if source_collection_path is not None:
            report = build_report(
                collection, settings, is_snapshot=True, check_cancelled=check_cancelled
            )
        else:
            report = build_report(collection, settings)
        if check_cancelled is not None:
            check_cancelled()
        save_report(
            report,
            path,
            fingerprint=fingerprint,
            snapshot_sequence=snapshot_sequence,
            scheduled_day=scheduled_day,
        )
    if check_cancelled is not None:
        check_cancelled()
    if snapshot_sequence is not None:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "insert or replace into metadata values ('last_snapshot_sequence', ?)",
                (str(snapshot_sequence),),
            )
    if scheduled_day is not None:
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "insert or replace into metadata values ('last_analysis_day', ?)",
                (scheduled_day.isoformat(),),
            )
    if source_collection_path is not None and daily is not None:
        # The user can disable export or change its destination during replay.
        daily = load_daily_settings(collection_path)
    if daily is not None and daily.enabled:
        try:
            export_fixed_csv(
                report,
                collection_path,
                daily,
                scheduled_day=scheduled_day,
                sharing=sharing,
                check_cancelled=check_cancelled,
            )
        except UpdateCancelled:
            raise
        except Exception as error:
            return UpdateResult(report, recalculated, csv_error=str(error) or type(error).__name__)
        return UpdateResult(report, recalculated, csv_exported=True)
    return UpdateResult(report, recalculated)
