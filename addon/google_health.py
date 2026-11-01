"""Daily Google Health aggregates; provider IDs and raw stages stay in memory."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .sleep_sessions import SleepSession

GOOGLE_HEALTH_VERSION = 2
GOOGLE_HEALTH_CSV_FIELDS = (
    "google_health_total_sleep_seconds",
    "google_health_light_sleep_seconds",
    "google_health_deep_sleep_seconds",
    "google_health_rem_sleep_seconds",
    "google_health_awake_seconds",
    "google_health_restless_seconds",
    "google_health_sleep_period_count",
    "google_health_sleep_status",
    "google_health_sleep_types",
    "google_health_steps",
    "google_health_day_basis",
    "google_health_synced_at_utc",
    "google_health_data_version",
)
STAGES = {
    "LIGHT": "light_sleep",
    "DEEP": "deep_sleep",
    "REM": "rem_sleep",
    "AWAKE": "awake",
    "RESTLESS": "restless",
}


def integer(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Google Health returned an invalid {name}.")
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
            raise ValueError()
        if parsed > 2**53:
            raise ValueError()
        return int(parsed)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Google Health returned an invalid {name}.") from None


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Google Health returned a missing timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise ValueError("Google Health timestamps need UTC offsets.") from None


def civil_date(value, *, end_of_day=False):
    try:
        raw = value["date"]
        result = date(
            integer(raw["year"], "year"), integer(raw["month"], "month"), integer(raw["day"], "day")
        )
        time = value.get("time", {})
        if not isinstance(time, dict):
            raise ValueError()
        clock = tuple(integer(time.get(k, 0), k) for k in ("hours", "minutes", "seconds", "nanos"))
        if clock != (0, 0, 0, 0):
            # The reference examples use an inclusive 23:59:59 end, whereas
            # the method's range is closed-open. Accept both full-day forms.
            if not end_of_day or clock[:3] != (23, 59, 59) or clock[3] > 999999999:
                raise ValueError()
            result += timedelta(days=1)
        return result
    except (KeyError, TypeError, ValueError):
        raise ValueError("Google Health returned an invalid daily date boundary.") from None


def sleep_day(interval, end):
    """Sleep is credited to the local wake date, independent of Anki rollover."""
    try:
        offset = interval["endUtcOffset"]
        if not isinstance(offset, str) or not offset.endswith("s"):
            raise ValueError()
        seconds = Decimal(offset[:-1])
        if not seconds.is_finite() or abs(seconds) > 18 * 3600:
            raise ValueError()
        day = (end + timedelta(seconds=float(seconds))).date()
        if "civilEndTime" in interval:
            raw = interval["civilEndTime"]["date"]
            supplied = date(
                integer(raw["year"], "year"),
                integer(raw["month"], "month"),
                integer(raw["day"], "day"),
            )
            if supplied != day:
                raise ValueError()
        return day
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError("Google Health returned inconsistent local sleep dates.") from None


@dataclass(frozen=True)
class SleepSummary:
    key: str
    day: date
    start: datetime
    end: datetime
    kind: str
    values: dict
    processing: bool
    main_sleep: bool = False
    nap: bool = False


@dataclass(frozen=True)
class GoogleHealthImport:
    days: dict[date, dict]
    sessions: tuple[SleepSession, ...] = ()
    sleep_fetched: bool = True
    steps_fetched: bool = True


def _sleep(record):
    if not isinstance(record, dict) or not isinstance(record.get("sleep"), dict):
        raise ValueError("Google Health returned an invalid sleep record.")
    raw = record["sleep"]
    interval = raw.get("interval", {})
    if not isinstance(interval, dict):
        raise ValueError("Google Health returned an invalid sleep interval.")
    start, end = timestamp(interval.get("startTime")), timestamp(interval.get("endTime"))
    if end <= start or (end - start) > timedelta(days=7):
        raise ValueError("Google Health returned an invalid sleep interval.")
    day = sleep_day(interval, end)
    kind = raw.get("type", "SLEEP_TYPE_UNSPECIFIED")
    if kind not in ("CLASSIC", "STAGES", "SLEEP_TYPE_UNSPECIFIED"):
        raise ValueError("Google Health returned an unknown sleep type.")
    metadata, summary = raw.get("metadata", {}), raw.get("summary", {})
    if not isinstance(metadata, dict) or not isinstance(summary, dict):
        raise ValueError("Google Health returned invalid sleep metadata.")
    processing = metadata.get("processed") is False
    if "processed" in metadata and not isinstance(metadata["processed"], bool):
        raise ValueError("Google Health returned an invalid processing status.")
    if any(key in metadata and type(metadata[key]) is not bool for key in ("mainSleep", "nap")):
        raise ValueError("Google Health returned an invalid sleep classification.")
    values = {}
    if not processing:
        for field, output in (("minutesAsleep", "total_sleep"), ("minutesAwake", "awake")):
            if field in summary:
                values[output] = integer(summary[field], field) * 60
        stages = summary.get("stagesSummary", [])
        if not isinstance(stages, list):
            raise ValueError("Google Health returned invalid sleep summaries.")
        seen = set()
        for stage in stages:
            if not isinstance(stage, dict) or stage.get("type") not in {
                *STAGES,
                "ASLEEP",
                "SLEEP_STAGE_TYPE_UNSPECIFIED",
            }:
                raise ValueError("Google Health returned an unknown sleep stage.")
            stage_type = stage["type"]
            if stage_type in seen:
                raise ValueError("Google Health returned duplicate sleep stage summaries.")
            seen.add(stage_type)
            seconds = integer(stage.get("minutes", "0"), "stage duration") * 60
            if stage_type in STAGES:
                key = STAGES[stage_type]
                # Google's summary already accounts for short awakenings. Never
                # add the overlapping raw stages or shortAwakenings a second time.
                if key in values and abs(values[key] - seconds) > 60:
                    raise ValueError("Google Health returned conflicting sleep summaries.")
                values[key] = seconds
        if kind != "STAGES":
            for key in ("light_sleep", "deep_sleep", "rem_sleep"):
                values.pop(key, None)  # Classic/restless is not a light/deep/REM measurement.
        if any(seconds > (end - start).total_seconds() + 60 for seconds in values.values()):
            raise ValueError("Google Health sleep duration exceeds its session.")
        if all(key in values for key in ("light_sleep", "deep_sleep", "rem_sleep", "total_sleep")):
            stages_total = sum(values[key] for key in ("light_sleep", "deep_sleep", "rem_sleep"))
            if abs(stages_total - values["total_sleep"]) > 180:
                raise ValueError("Google Health returned conflicting sleep totals.")
    identity = record.get("dataPointName") or f"{start.isoformat()}/{end.isoformat()}"
    if not isinstance(identity, str) or len(identity) > 2048:
        raise ValueError("Google Health returned an invalid sleep identifier.")
    return SleepSummary(
        hashlib.sha256(identity.encode()).hexdigest(),
        day,
        start,
        end,
        kind,
        values,
        processing,
        metadata.get("mainSleep", False),
        metadata.get("nap", False),
    )


def parse_google_health(
    sleep_records,
    step_records,
    *,
    start,
    end,
    filter_sleep_window=False,
    sleep_fetched=True,
    steps_fetched=True,
):
    """Parse reconciled sleep + dailyRollUp steps, all pages, as one window."""
    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if upper <= lower or len(sleep_records) + len(step_records) > 200000:
        raise ValueError("Invalid Google Health import window or record count.")
    nights, grouped, steps = {}, defaultdict(list), {}
    for raw in sleep_records:
        night = _sleep(raw)
        if not lower <= night.day < upper:
            if filter_sleep_window:
                continue
            raise ValueError("Google Health returned sleep outside the requested window.")
        if night.key in nights and nights[night.key] != night:
            raise ValueError("Google Health returned conflicting sleep records.")
        nights[night.key] = night
    for night in nights.values():
        grouped[night.day].append(night)
    for raw in step_records:
        if not isinstance(raw, dict):
            raise ValueError("Google Health returned an invalid steps rollup.")
        day = civil_date(raw.get("civilStartTime"))
        stop = civil_date(raw.get("civilEndTime"), end_of_day=True)
        if stop != day + timedelta(days=1) or not lower <= day < upper:
            raise ValueError("Google Health returned an invalid daily steps window.")
        value = None
        if "steps" in raw:
            if not isinstance(raw["steps"], dict):
                raise ValueError("Google Health returned an invalid steps aggregate.")
            # Missing payload means no observation; explicit protobuf zero is
            # observed zero, including an empty steps object whose countSum is 0.
            value = integer(raw["steps"].get("countSum", "0"), "step count")
        if day in steps and steps[day] != value:
            raise ValueError("Google Health returned conflicting daily step counts.")
        steps[day] = value
    daily = {}
    for day in sorted(grouped.keys() | steps.keys()):
        values = dict.fromkeys(GOOGLE_HEALTH_CSV_FIELDS)
        periods = list(
            {
                (n.start, n.end, n.kind, tuple(sorted(n.values.items())), n.processing): n
                for n in grouped.get(day, [])
            }.values()
        )
        ordered = sorted(periods, key=lambda n: n.start)
        if periods:
            status = "durations available"
            if any(a.end > b.start for a, b in zip(ordered, ordered[1:], strict=False)):
                status = "overlapping periods"
            elif any(n.processing for n in periods):
                status = "processing"
            elif any(n.kind != "STAGES" for n in periods):
                status = "stages unavailable"
            elif any(
                key not in n.values
                for n in periods
                for key in ("total_sleep", "light_sleep", "deep_sleep", "rem_sleep")
            ):
                status = "missing durations"
            values["google_health_sleep_status"] = status
            values["google_health_sleep_types"] = ";".join(sorted({n.kind for n in periods}))
            values["google_health_sleep_period_count"] = len(periods)
            if status not in ("overlapping periods", "processing"):
                for key in ("total_sleep", *STAGES.values()):
                    if all(key in n.values for n in periods):
                        values[f"google_health_{key}_seconds"] = sum(n.values[key] for n in periods)
        values["google_health_steps"] = steps.get(day)
        values["google_health_day_basis"] = "sleep_end_local_date_steps_civil_date"
        values["google_health_data_version"] = GOOGLE_HEALTH_VERSION
        daily[day] = values
    sessions = tuple(
        SleepSession(
            n.key,
            "google_health",
            n.day.isoformat(),
            n.start.timestamp(),
            n.end.timestamp(),
            "nap" if n.nap else "main" if n.main_sleep else "other",
            n.values,
            "sleep processing" if n.processing else "available",
            wake_day=n.day.isoformat(),
        )
        for n in nights.values()
    )
    return GoogleHealthImport(daily, sessions, sleep_fetched, steps_fetched)
