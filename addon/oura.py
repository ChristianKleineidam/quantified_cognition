"""Read selected Oura measurements from local exports; never retain account data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 200_000
SLEEP_FIELDS = (
    "total_sleep_duration",
    "light_sleep_duration",
    "deep_sleep_duration",
    "rem_sleep_duration",
    "awake_time",
)
ACCEPTED_SLEEP_TYPES = {"sleep", "long_sleep", "late_nap", "daily_summary"}


@dataclass(frozen=True)
class Sleep:
    key: str
    day: str
    kind: str
    bedtime_start: str | None
    bedtime_end: str | None
    total_sleep_duration: int | None
    light_sleep_duration: int | None
    deep_sleep_duration: int | None
    rem_sleep_duration: int | None
    awake_time: int | None
    algorithm: str | None


@dataclass(frozen=True)
class Activity:
    day: str
    steps: int | None


@dataclass(frozen=True)
class OuraImport:
    sleep: tuple[Sleep, ...]
    activity: tuple[Activity, ...]
    ignored_files: int = 0

    @property
    def days(self) -> list[str]:
        return sorted({row.day for row in (*self.sleep, *self.activity)})

    @property
    def description(self) -> str:
        days = self.days
        excluded = sum(row.kind not in ACCEPTED_SLEEP_TYPES for row in self.sleep)
        text = (
            f"{len(self.sleep):,} sleep records and {len(self.activity):,} activity days"
            f" · {days[0]} to {days[-1]}"
            if days
            else "No measurements found."
        )
        if excluded:
            text += f". {excluded:,} deleted, rejected or unclassified sleep records are excluded."
        return text


def _key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _missing(value) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in ("", "null", "n/a")
    )


def _integer(value, name: str) -> int | None:
    if _missing(value):
        return None
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError()
        if number > 2**63 - 1:
            raise ValueError()
        return int(number)
    except (ValueError, InvalidOperation):
        raise ValueError(
            f"{name} must be a nonnegative whole number (durations in seconds)."
        ) from None


def _timestamp(value) -> str | None:
    if _missing(value):
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ValueError(
            "Sleep times must use ISO dates and times, with offsets when available."
        ) from None


def timestamp_key(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp


def _record(row: dict) -> tuple[Sleep | None, Activity | None]:
    normalized = {_key(str(key)): value for key, value in row.items()}
    if len(normalized) != len(row):
        raise ValueError("Duplicate measurement fields after normalizing column names.")
    row = normalized
    # Ignore scores/contributors and other unrelated Oura collections.
    sleep_fields = any(key in row for key in SLEEP_FIELDS)
    sleep_tombstone = row.get("type") in ("deleted", "rest") and not _missing(row.get("id"))
    activity_fields = "steps" in row
    if not (sleep_fields or activity_fields or sleep_tombstone):
        return None, None
    value = row.get("day", row.get("date"))
    try:
        day = date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(
            "Each sleep or activity record needs an ISO day/date (YYYY-MM-DD)."
        ) from None
    activity = Activity(day, _integer(row["steps"], "Steps")) if activity_fields else None
    sleep = None
    if sleep_fields or sleep_tombstone:
        start, end = _timestamp(row.get("bedtime_start")), _timestamp(row.get("bedtime_end"))
        if start and end:
            try:
                if timestamp_key(end) <= timestamp_key(start):
                    raise ValueError("Sleep end must follow its start.")
            except TypeError:
                raise ValueError(
                    "Sleep start and end must both include an offset or both omit it."
                ) from None
        raw_id = str(row.get("id") or "").strip()
        # The older daily CSV has no record id/type. Its rows are already daily
        # summaries, even when bedtime columns are included.
        kind = str(row.get("type") or ("unknown" if raw_id else "daily_summary")).strip()
        if kind not in ACCEPTED_SLEEP_TYPES | {"deleted", "rest", "unknown"}:
            raise ValueError(f"Unrecognized Oura sleep type: {kind}.")
        identity = raw_id or (
            f"summary:{day}" if kind == "daily_summary" else f"period:{day}:{start}:{end}"
        )
        # Provider identifiers remain local and are never exported.
        key = hashlib.sha256(identity.encode()).hexdigest()
        algorithm = (
            None
            if _missing(row.get("sleep_algorithm_version"))
            else str(row["sleep_algorithm_version"])
        )
        if algorithm is not None and not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9._-]{0,31}", algorithm):
            raise ValueError("Unrecognized sleep algorithm version.")
        sleep = Sleep(
            key,
            day,
            kind,
            start,
            end,
            *[_integer(row.get(field), field) for field in SLEEP_FIELDS],
            algorithm,
        )
    return sleep, activity


def parse_oura_records(sleep_records, activity_records) -> OuraImport:
    """Validate complete API pages before committing any part of a sync."""
    nights, activities = {}, {}
    if len(sleep_records) + len(activity_records) > MAX_RECORDS:
        raise ValueError("The Oura response contains too many records.")
    for row in sleep_records:
        if not isinstance(row, dict) or not row.get("id") or not row.get("type"):
            raise ValueError("Oura returned an incomplete sleep record.")
        night, _ = _record(row)
        if night is None:
            raise ValueError("Oura returned an unrecognized sleep record.")
        previous = nights.get(night.key)
        if previous is not None and previous != night:
            if {previous.algorithm, night.algorithm} == {"v1", "v2"}:
                night = night if night.algorithm == "v2" else previous
            else:
                raise ValueError("Oura returned conflicting sleep records.")
        nights[night.key] = night
    for row in activity_records:
        if not isinstance(row, dict) or "steps" not in row:
            raise ValueError("Oura returned an incomplete activity record.")
        _, activity = _record({"day": row.get("day"), "steps": row["steps"]})
        if activity.day in activities and activities[activity.day] != activity:
            raise ValueError("Oura returned conflicting activity records.")
        activities[activity.day] = activity
    return OuraImport(tuple(nights.values()), tuple(activities.values()))


def _json_records(data) -> Iterable[dict]:
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Oura JSON collections must contain objects.")
            yield item
    elif isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            yield from _json_records(data["data"])
        elif any(key in data for key in (*SLEEP_FIELDS, "steps", "type")):
            yield data
        else:
            for key, value in data.items():
                if _key(key) in ("sleep", "sleep_periods", "daily_activity", "activity"):
                    yield from _json_records(value)


def _read_rows(data: bytes, suffix: str) -> Iterable[dict]:
    text = data.decode("utf-8-sig")
    if suffix == ".json":
        yield from _json_records(json.loads(text))
    else:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames and len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV contains duplicate column names.")
        for row in reader:
            if None in row:
                raise ValueError("CSV row has more values than its header.")
            yield row


def parse_oura_files(
    paths: Iterable[str | Path], *, check_cancelled: Callable[[], None] = lambda: None
) -> OuraImport:
    sleep: dict[str, Sleep] = {}
    activity: dict[str, Activity] = {}
    ignored = 0
    records = 0

    def consume(data: bytes, suffix: str, label: str):
        nonlocal ignored, records
        recognized = False
        try:
            for index, row in enumerate(_read_rows(data, suffix), 1):
                records += 1
                if records > MAX_RECORDS:
                    raise ValueError("Choose a smaller export (at most 200,000 records).")
                if index % 100 == 0:
                    check_cancelled()
                try:
                    night, steps = _record(row)
                    recognized |= night is not None or steps is not None
                    if night is not None:
                        previous = sleep.get(night.key)
                        if previous is not None and previous != night:
                            if {previous.algorithm, night.algorithm} == {"v1", "v2"}:
                                night = night if night.algorithm == "v2" else previous
                            else:
                                raise ValueError(
                                    "Conflicting sleep records. Import one export at a time."
                                )
                        sleep[night.key] = night
                    if steps is not None:
                        if steps.day in activity and activity[steps.day] != steps:
                            raise ValueError(
                                "Conflicting step counts. Import one export at a time."
                            )
                        activity[steps.day] = steps
                except ValueError as error:
                    raise ValueError(f"Record {index}: {error}") from error
        except (ValueError, UnicodeError, csv.Error) as error:
            raise ValueError(f"{label}: {error}") from error
        if not recognized:
            ignored += 1

    for selected in paths:
        check_cancelled()
        path = Path(selected)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = [
                    item
                    for item in archive.infolist()
                    if not item.is_dir()
                    and Path(item.filename).suffix.lower() in (".csv", ".json")
                    and not item.filename.startswith("__MACOSX/")
                ]
                if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Choose an Oura export smaller than 512 MiB uncompressed.")
                for item in members:
                    check_cancelled()
                    if item.file_size > MAX_FILE_BYTES:
                        raise ValueError(
                            f"{Path(item.filename).name} exceeds the 64 MiB file limit."
                        )
                    # Read members directly; never extract paths from an archive.
                    consume(
                        archive.read(item),
                        Path(item.filename).suffix.lower(),
                        Path(item.filename).name,
                    )
        elif path.suffix.lower() in (".csv", ".json"):
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Choose an Oura file smaller than 64 MiB.")
            consume(path.read_bytes(), path.suffix.lower(), path.name)
        else:
            raise ValueError("Choose downloaded Oura ZIP, CSV or JSON files.")
    check_cancelled()
    if not sleep and not activity:
        raise ValueError(
            "No sleep durations or daily steps found. Choose the sleep/activity data export, "
            "rather than a PDF report or daily sleep scores."
        )
    return OuraImport(tuple(sleep.values()), tuple(activity.values()), ignored)
