"""Profile-local health measurements and reproducible daily aggregates."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .google_health import GOOGLE_HEALTH_CSV_FIELDS, GoogleHealthImport
from .oura import ACCEPTED_SLEEP_TYPES, SLEEP_FIELDS, OuraImport, Sleep, timestamp_key
from .private_storage import private_database
from .sleep_sessions import SleepSession, merge_windows, normalize_sessions, oura_session
from .workouts import WORKOUT_DAILY_FIELDS, Workout, summarize

HEALTH_DATABASE_NAME = "health.sqlite3"
HEALTH_VERSION = 4
HEALTH_CSV_FIELDS = (
    "oura_total_sleep_seconds",
    "oura_light_sleep_seconds",
    "oura_deep_sleep_seconds",
    "oura_rem_sleep_seconds",
    "oura_awake_seconds",
    "oura_sleep_period_count",
    "oura_sleep_status",
    "oura_sleep_algorithm_versions",
    "oura_steps",
    "oura_day_basis",
    "oura_imported_at_utc",
    "oura_data_version",
)
HEALTH_CSV_FIELDS = HEALTH_CSV_FIELDS + GOOGLE_HEALTH_CSV_FIELDS


def health_database_path(collection_path: str | Path) -> Path:
    from .storage import results_directory

    return results_directory(collection_path) / HEALTH_DATABASE_NAME


@dataclass(frozen=True)
class DailyHealth:
    values: dict


@dataclass(frozen=True)
class HealthSnapshot:
    revision: str
    days: dict[date, DailyHealth]
    sessions: tuple[SleepSession, ...] = ()
    sleep_windows: dict[str, tuple] = field(default_factory=dict)
    workouts: tuple[Workout, ...] = ()
    workout_windows: tuple = ()


@dataclass(frozen=True)
class ImportResult:
    changed_records: int
    snapshot: HealthSnapshot
    csv_exported: bool = False
    csv_error: str | None = None
    metadata_changed: bool = False


def _initialize(connection):
    version = connection.execute("pragma user_version").fetchone()[0]
    if version not in (0, 1, 2, 3, HEALTH_VERSION):
        raise ValueError("This health database needs a newer add-on version.")
    connection.execute("create table if not exists metadata (key text primary key, value text)")
    connection.execute("""create table if not exists oura_sleep (
        key text primary key, day text not null, payload text not null,
        imported_at text not null)""")
    connection.execute("""create table if not exists oura_activity (
        day text primary key, steps integer, imported_at text not null)""")
    connection.execute("create table if not exists oura_raw_days (day text primary key)")
    connection.execute("""create table if not exists oura_workouts (
        key text primary key, day text not null, payload text not null,
        imported_at text not null)""")
    connection.execute("""create table if not exists google_health_days (
        day text primary key, payload text not null, synced_at text not null)""")
    connection.execute("""create table if not exists google_health_sleep (
        key text primary key, day text not null, payload text not null)""")
    connection.execute("""create table if not exists sleep_windows (
        provider text not null, start_day text not null, end_day text not null,
        primary key(provider,start_day))""")
    if version != HEALTH_VERSION:
        connection.execute(f"pragma user_version={HEALTH_VERSION}")


def health_revision_at(path: str | Path) -> str:
    path = Path(path).resolve()
    if not path.exists():
        return ""
    with private_database(path) as connection:
        if connection.execute("pragma user_version").fetchone()[0] not in (1, 2, 3, HEALTH_VERSION):
            raise ValueError("Unsupported health database version.")
        row = connection.execute("select value from metadata where key='revision'").fetchone()
        return row[0] if row else ""


def _daily_sleep(periods: list[Sleep]) -> tuple[list[Sleep], str]:
    # A daily summary is an alternative representation, never an extra period.
    raw = [row for row in periods if row.kind != "daily_summary"]
    periods = raw or periods
    unique: dict[tuple, Sleep] = {}
    for row in periods:
        identity = (
            (
                timestamp_key(row.bedtime_start).isoformat(),
                timestamp_key(row.bedtime_end).isoformat(),
            )
            if row.bedtime_start and row.bedtime_end
            else (row.key,)
        )
        previous = unique.get(identity)
        if previous is not None and previous != row:
            if {previous.algorithm, row.algorithm} == {"v1", "v2"}:
                row = row if row.algorithm == "v2" else previous
            elif tuple(getattr(previous, field) for field in SLEEP_FIELDS) != tuple(
                getattr(row, field) for field in SLEEP_FIELDS
            ):
                return list(unique.values()) + [row], "conflicting periods"
        unique[identity] = row
    periods = list(unique.values())
    if len(periods) > 1:
        if any(not row.bedtime_start or not row.bedtime_end for row in periods):
            return periods, "ambiguous periods"
        try:
            ordered = sorted(periods, key=lambda row: timestamp_key(row.bedtime_start))
            if any(
                timestamp_key(right.bedtime_start) < timestamp_key(left.bedtime_end)
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                return periods, "overlapping periods"
        except TypeError:
            return periods, "ambiguous time offsets"
    status = (
        "durations available"
        if all(getattr(row, field) is not None for row in periods for field in SLEEP_FIELDS[:4])
        else "missing durations"
    )
    return periods, status


def load_health(collection_path: str | Path) -> HealthSnapshot:
    path = health_database_path(collection_path)
    if not path.exists():
        return HealthSnapshot("", {})
    with private_database(path) as connection:
        connection.execute("begin")
        version = connection.execute("pragma user_version").fetchone()[0]
        if version not in (1, 2, 3, HEALTH_VERSION):
            raise ValueError("Unsupported health database version.")
        row = connection.execute("select value from metadata where key='revision'").fetchone()
        revision = row[0] if row else ""
        grouped: dict[str, list[Sleep]] = defaultdict(list)
        dates_imported: dict[str, list[str]] = defaultdict(list)
        raw_days = {row[0] for row in connection.execute("select day from oura_raw_days")}
        blocked = defaultdict(set)
        oura_records = []
        for day, payload, imported in connection.execute(
            "select day,payload,imported_at from oura_sleep"
        ):
            period = Sleep(**json.loads(payload))
            oura_records.append(period)
            if period.kind != "daily_summary":
                raw_days.add(day)
            dates_imported[day].append(imported)
            if period.kind in ("deleted", "rest") and period.bedtime_start and period.bedtime_end:
                blocked[day].add(
                    (timestamp_key(period.bedtime_start), timestamp_key(period.bedtime_end))
                )
            if period.kind in ACCEPTED_SLEEP_TYPES:
                grouped[day].append(period)
        activities = {
            day: (steps, imported)
            for day, steps, imported in connection.execute(
                "select day,steps,imported_at from oura_activity"
            )
        }
        google = (
            list(connection.execute("select day,payload,synced_at from google_health_days"))
            if version >= 2
            else []
        )
        sessions = [
            oura_session(row)
            for row in oura_records
            if row.kind != "daily_summary" or row.day not in raw_days
        ]
        windows = defaultdict(list)
        workouts = []
        workout_days = defaultdict(list)
        if version >= 4:
            for day, payload, imported in connection.execute(
                "select day,payload,imported_at from oura_workouts"
            ):
                workout = Workout(**json.loads(payload))
                workouts.append(workout)
                workout_days[day].append(workout)
                dates_imported[day].append(imported)
        if version >= 3:
            sessions.extend(
                SleepSession(**json.loads(payload))
                for (payload,) in connection.execute("select payload from google_health_sleep")
            )
            for provider, lower, upper in connection.execute(
                "select provider,start_day,end_day from sleep_windows"
            ):
                windows[provider].append((lower, upper))
    daily = {}
    for day in sorted(grouped.keys() | activities.keys() | workout_days.keys()):
        values = dict.fromkeys((*HEALTH_CSV_FIELDS, *WORKOUT_DAILY_FIELDS))
        periods = grouped.get(day, [])
        if day in raw_days:
            periods = [row for row in periods if row.kind != "daily_summary"]
        periods = [
            row
            for row in periods
            if not (
                row.bedtime_start
                and row.bedtime_end
                and (timestamp_key(row.bedtime_start), timestamp_key(row.bedtime_end))
                in blocked[day]
            )
        ]
        if periods:
            periods, status = _daily_sleep(periods)
            values["oura_sleep_period_count"] = len(periods)
            values["oura_sleep_status"] = status
            values["oura_sleep_algorithm_versions"] = ";".join(
                sorted({row.algorithm for row in periods if row.algorithm})
            )
            if status in ("durations available", "missing durations"):
                for field, csv_field in zip(SLEEP_FIELDS, HEALTH_CSV_FIELDS[:5], strict=True):
                    durations = [getattr(period, field) for period in periods]
                    values[csv_field] = (
                        sum(durations) if all(v is not None for v in durations) else None
                    )
        if day in activities:
            values["oura_steps"], imported = activities[day]
            dates_imported[day].append(imported)
        if not periods and day not in activities and day not in workout_days:
            continue
        if any(lower <= day < upper for lower, upper in windows["oura_workout"]):
            summary, status = summarize(workout_days[day])
            values.update({"oura_workout_" + key: value for key, value in summary.items()})
            values["oura_workout_status"] = status
        else:
            values["oura_workout_status"] = "workout history unavailable"
        values["oura_day_basis"] = "provider_assigned_day"
        values["oura_imported_at_utc"] = max(dates_imported[day])
        values["oura_data_version"] = 1  # Provider format is independent of DB schema migrations.
        daily[date.fromisoformat(day)] = DailyHealth(values)
    for day, payload, synced_at in google:
        day = date.fromisoformat(day)
        values = (
            dict(daily[day].values)
            if day in daily
            else dict.fromkeys((*HEALTH_CSV_FIELDS, *WORKOUT_DAILY_FIELDS))
        )
        values.update(json.loads(payload))
        values["google_health_synced_at_utc"] = synced_at
        daily[day] = DailyHealth(values)
    return HealthSnapshot(
        revision,
        daily,
        normalize_sessions(sessions),
        {
            provider: merge_windows(rows)
            for provider, rows in windows.items()
            if provider != "oura_workout"
        },
        tuple(workouts),
        merge_windows(windows["oura_workout"]),
    )


def _save_window(connection, provider, start=None, end=None, clear=False):
    previous = tuple(
        connection.execute(
            "select start_day,end_day from sleep_windows where provider=? order by start_day",
            (provider,),
        )
    )
    wanted = () if clear else merge_windows((*previous, (start, end)))
    if previous == wanted:
        return False
    connection.execute("delete from sleep_windows where provider=?", (provider,))
    connection.executemany(
        "insert into sleep_windows values (?,?,?)",
        [(provider, lower, upper) for lower, upper in wanted],
    )
    return True


def save_google_health_import(
    collection_path,
    prepared: GoogleHealthImport,
    *,
    start,
    end,
    sharing=None,
    check_cancelled=lambda: None,
    clear=False,
):
    """Replace only validated Google days; preserve Oura and reuse cached FSRS."""
    from .research import sharing_state
    from .storage import (
        database_path,
        load_daily_settings,
        load_report,
        profile_job_lock,
        retry_fixed_csv,
    )

    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if (
        upper <= lower
        or (clear and (prepared.days or prepared.sessions))
        or any(
            not lower <= day < upper or set(values) != set(GOOGLE_HEALTH_CSV_FIELDS)
            for day, values in prepared.days.items()
        )
    ):
        raise ValueError("Invalid Google Health daily import.")
    if any(s.provider != "google_health" or not start <= s.day < end for s in prepared.sessions):
        raise ValueError("Invalid Google Health sleep-session window.")
    with profile_job_lock(collection_path):
        check_cancelled()
        sharing_state(collection_path, sharing)
        path = health_database_path(collection_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        now, changed = datetime.now(timezone.utc).isoformat(), 0
        metadata_changed = False
        with private_database(path, write=True) as connection, connection:
            connection.execute("pragma secure_delete=on")
            connection.execute("begin immediate")
            _initialize(connection)
            connection.commit()
            connection.execute("begin immediate")
            old = dict(
                connection.execute(
                    "select day,payload from google_health_days where day>=? and day<?",
                    (start, end),
                )
            )
            if clear:
                old = dict(connection.execute("select day,payload from google_health_days"))
            wanted = {}
            # Partial OAuth grants do not make the unqueried dataset's history
            # authoritative. Preserve it while reconciling only fetched fields.
            for day in old.keys() | {day.isoformat() for day in prepared.days}:
                previous = (
                    json.loads(old[day]) if day in old else dict.fromkeys(GOOGLE_HEALTH_CSV_FIELDS)
                )
                fresh = prepared.days.get(date.fromisoformat(day), {})
                values = dict(previous)
                for key in GOOGLE_HEALTH_CSV_FIELDS:
                    if key in (
                        "google_health_synced_at_utc",
                        "google_health_data_version",
                        "google_health_day_basis",
                    ):
                        continue
                    fetched = (
                        prepared.steps_fetched
                        if key == "google_health_steps"
                        else prepared.sleep_fetched
                    )
                    if fetched:
                        values[key] = fresh.get(key)
                if not clear and (
                    fresh
                    or values.get("google_health_sleep_period_count") is not None
                    or values.get("google_health_steps") is not None
                ):
                    values.update(
                        google_health_day_basis="sleep_end_local_date_steps_civil_date",
                        google_health_data_version=2,
                        google_health_synced_at_utc=None,
                    )
                    wanted[day] = json.dumps(values, sort_keys=True, allow_nan=False)
            for day in old.keys() - wanted.keys():
                connection.execute("delete from google_health_days where day=?", (day,))
                changed += 1
            for day, payload in wanted.items():
                check_cancelled()
                if old.get(day) != payload:
                    connection.execute(
                        "insert or replace into google_health_days values (?,?,?)",
                        (day, payload, now),
                    )
                    changed += 1
            if clear or prepared.sleep_fetched:
                old_sessions = dict(
                    connection.execute(
                        "select key,payload from google_health_sleep"
                        + ("" if clear else " where day>=? and day<?"),
                        () if clear else (start, end),
                    )
                )
                new_sessions = {
                    s.key: json.dumps(asdict(s), sort_keys=True, allow_nan=False)
                    for s in prepared.sessions
                }
                for key in old_sessions.keys() - new_sessions.keys():
                    connection.execute("delete from google_health_sleep where key=?", (key,))
                    changed += 1
                for session in prepared.sessions:
                    payload = new_sessions[session.key]
                    if old_sessions.get(session.key) != payload:
                        connection.execute(
                            "insert or replace into google_health_sleep values (?,?,?)",
                            (session.key, session.day, payload),
                        )
                        changed += 1
                metadata_changed = _save_window(connection, "google_health", start, end, clear)
            if changed or metadata_changed:
                connection.execute(
                    "insert or replace into metadata values ('revision',?)", (uuid.uuid4().hex,)
                )
            check_cancelled()
        snapshot = load_health(collection_path)
        if (
            load_daily_settings(collection_path).enabled
            and load_report(database_path(collection_path)) is not None
        ):
            try:
                result = retry_fixed_csv(
                    collection_path,
                    sharing,
                    check_cancelled=check_cancelled,
                    only_if_pending=not bool(changed or metadata_changed),
                )
                return ImportResult(
                    changed, snapshot, result.csv_exported, result.csv_error, metadata_changed
                )
            except Exception as error:
                return ImportResult(
                    changed, snapshot, csv_error=str(error), metadata_changed=metadata_changed
                )
        return ImportResult(changed, snapshot, metadata_changed=metadata_changed)


def save_oura_import(
    collection_path: str | Path,
    prepared: OuraImport,
    *,
    sharing=None,
    check_cancelled: Callable[[], None] = lambda: None,
    authoritative_window: tuple[str, str] | None = None,
    clear: bool = False,
    workouts_unavailable: bool = False,
) -> ImportResult:
    """Commit selected fields atomically, then retry the CSV without FSRS replay."""
    from .research import sharing_state
    from .storage import (
        database_path,
        load_daily_settings,
        load_report,
        profile_job_lock,
        retry_fixed_csv,
    )

    with profile_job_lock(collection_path):
        check_cancelled()
        sharing_state(collection_path, sharing)
        path = health_database_path(collection_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = 0
        metadata_changed = False
        now = datetime.now(timezone.utc).isoformat()
        with private_database(path, write=True) as connection, connection:
            connection.execute("pragma secure_delete=on")
            connection.execute("begin immediate")
            _initialize(connection)
            connection.commit()
            connection.execute("begin immediate")
            if clear or authoritative_window:
                # Compare selected records rather than delete/reinsert unchanged
                # data: a daily poll must not invalidate an identical fixed CSV.
                lower, upper = authoritative_window or ("0001-01-01", "9999-12-31")
                wanted_sleep = {row.key for row in prepared.sleep}
                wanted_activity = {row.day for row in prepared.activity}
                for (key,) in connection.execute(
                    "select key from oura_sleep where day >= ? and day < ?", (lower, upper)
                ).fetchall():
                    if clear or key not in wanted_sleep:
                        connection.execute("delete from oura_sleep where key=?", (key,))
                        changed += 1
                for (day,) in connection.execute(
                    "select day from oura_activity where day >= ? and day < ?", (lower, upper)
                ).fetchall():
                    if clear or day not in wanted_activity:
                        connection.execute("delete from oura_activity where day=?", (day,))
                        changed += 1
                connection.execute(
                    "delete from oura_raw_days where day >= ? and day < ?", (lower, upper)
                )
            for index, record in enumerate(prepared.sleep):
                if index % 100 == 0:
                    check_cancelled()
                payload = json.dumps(asdict(record), sort_keys=True)
                previous = connection.execute(
                    "select payload from oura_sleep where key=?", (record.key,)
                ).fetchone()
                old = Sleep(**json.loads(previous[0])) if previous else None
                for period in (record, old):
                    if period is not None and period.kind != "daily_summary":
                        connection.execute(
                            "insert or ignore into oura_raw_days values (?)", (period.day,)
                        )
                if previous and record.algorithm == "v1":
                    if old.algorithm == "v2" and record.kind in ACCEPTED_SLEEP_TYPES:
                        continue
                if previous is None or previous[0] != payload:
                    connection.execute(
                        "insert or replace into oura_sleep values (?,?,?,?)",
                        (record.key, record.day, payload, now),
                    )
                    changed += 1
            for record in prepared.activity:
                check_cancelled()
                previous = connection.execute(
                    "select steps from oura_activity where day=?", (record.day,)
                ).fetchone()
                if previous is None or previous[0] != record.steps:
                    connection.execute(
                        "insert or replace into oura_activity values (?,?,?)",
                        (record.day, record.steps, now),
                    )
                    changed += 1
            if clear or workouts_unavailable or prepared.workouts_fetched:
                lower, upper = authoritative_window or ("0001-01-01", "9999-12-31")
                old = dict(
                    connection.execute(
                        "select key,payload from oura_workouts"
                        + ("" if clear or workouts_unavailable else " where day>=? and day<?"),
                        () if clear or workouts_unavailable else (lower, upper),
                    )
                )
                wanted = (
                    {}
                    if clear or workouts_unavailable
                    else {
                        row.key: json.dumps(asdict(row), sort_keys=True)
                        for row in prepared.workouts
                    }
                )
                for key in old.keys() - wanted.keys():
                    connection.execute("delete from oura_workouts where key=?", (key,))
                    changed += 1
                for record in prepared.workouts if not (clear or workouts_unavailable) else ():
                    check_cancelled()
                    if old.get(record.key) != wanted[record.key]:
                        connection.execute(
                            "insert or replace into oura_workouts values (?,?,?,?)",
                            (record.key, record.day, wanted[record.key], now),
                        )
                        changed += 1
                if clear or workouts_unavailable or authoritative_window:
                    metadata_changed = _save_window(
                        connection, "oura_workout", lower, upper, clear or workouts_unavailable
                    )
            if clear or authoritative_window:
                lower, upper = authoritative_window or (None, None)
                metadata_changed = (
                    _save_window(connection, "oura", lower, upper, clear) or metadata_changed
                )
            if changed or metadata_changed:
                connection.execute(
                    "insert or replace into metadata values ('revision', ?)", (uuid.uuid4().hex,)
                )
            check_cancelled()
        snapshot = load_health(collection_path)
        daily = load_daily_settings(collection_path)
        if daily.enabled and load_report(database_path(collection_path)) is not None:
            try:
                result = retry_fixed_csv(
                    collection_path,
                    sharing,
                    check_cancelled=check_cancelled,
                    only_if_pending=not bool(changed or metadata_changed),
                )
                return ImportResult(
                    changed, snapshot, result.csv_exported, result.csv_error, metadata_changed
                )
            except Exception as error:
                return ImportResult(
                    changed, snapshot, csv_error=str(error), metadata_changed=metadata_changed
                )
        return ImportResult(changed, snapshot, metadata_changed=metadata_changed)
