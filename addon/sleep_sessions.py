"""Main sleep and naps matched to actual study timestamps, never date proxies."""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

SESSION_VERSION = 2
SLEEP_VALUES = ("total_sleep", "light_sleep", "deep_sleep", "rem_sleep", "awake", "restless")
NAP_STAGES = ("light_sleep", "deep_sleep", "rem_sleep")
STATUSES = (
    "available",
    "review times unavailable",
    "main sleep unavailable",
    "sleep timestamps unavailable",
    "ambiguous sleep sessions",
    "sleep overlaps reviews",
    "session history incomplete",
    "sleep processing",
    "unclassified sleep",
    "nap duration unavailable",
)
SESSION_CSV_FIELDS = tuple(
    field
    for provider in ("oura", "google_health")
    for field in (
        *(
            f"{provider}_main_{timing}_{value}_seconds"
            for timing in ("before", "after")
            for value in SLEEP_VALUES
        ),
        *(f"{provider}_main_{timing}_status" for timing in ("before", "after")),
        *(
            f"{provider}_nap_{timing}_{value}"
            for timing in ("before", "after")
            for value in ("recorded", "count", "duration_seconds", "gap_seconds", "status")
        ),
        f"{provider}_between_review_nap_count",
        f"{provider}_sleep_matching_version",
    )
)
# Append new measurements so existing CSV columns retain their positions.
SESSION_CSV_FIELDS += tuple(
    f"{provider}_nap_{timing}_{stage}_seconds"
    for provider in ("oura", "google_health")
    for timing in ("before", "after")
    for stage in NAP_STAGES
)


@dataclass(frozen=True)
class SleepSession:
    key: str
    provider: str
    day: str
    start: float | None
    end: float | None
    kind: str  # main, nap, other, discarded, ambiguous_main
    values: dict
    status: str = "available"
    algorithm: str | None = None
    wake_day: str | None = None


def utc_timestamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def oura_session(period):
    kind = (
        "main"
        if period.kind == "long_sleep"
        else "nap"
        if period.kind in ("sleep", "late_nap")
        else "discarded"
        if period.kind in ("rest", "deleted")
        else "other"
    )
    start, end = utc_timestamp(period.bedtime_start), utc_timestamp(period.bedtime_end)
    values = dict(
        zip(
            SLEEP_VALUES[:5],
            (
                period.total_sleep_duration,
                period.light_sleep_duration,
                period.deep_sleep_duration,
                period.rem_sleep_duration,
                period.awake_time,
            ),
            strict=True,
        )
    )
    status = (
        "available" if start is not None and end is not None else "sleep timestamps unavailable"
    )
    if start is not None and end is not None and not 0 < end - start <= 7 * 86400:
        start, end, status = None, None, "sleep timestamps unavailable"
    # Legacy daily summaries are not individual sessions, even with bedtime fields.
    if period.kind == "daily_summary":
        kind, status = "other", "unclassified sleep"
    wake_day = (
        datetime.fromisoformat(period.bedtime_end).date().isoformat() if end is not None else None
    )
    return SleepSession(
        period.key, "oura", period.day, start, end, kind, values, status, period.algorithm, wake_day
    )


def normalize_sessions(sessions):
    """Deduplicate algorithms, honor tombstones and flag overlap/split main sleeps."""
    blocked = {
        (s.provider, s.start, s.end)
        for s in sessions
        if s.kind == "discarded" and s.start is not None
    }
    unique = {}
    for session in sessions:
        if session.kind == "discarded" or (session.provider, session.start, session.end) in blocked:
            continue
        identity = (
            (session.provider, session.start, session.end)
            if session.start is not None
            else (session.provider, session.key)
        )
        old = unique.get(identity)
        if old:
            if {old.algorithm, session.algorithm} == {"v1", "v2"}:
                session = session if session.algorithm == "v2" else old
            elif (old.kind, old.values, old.status) != (
                session.kind,
                session.values,
                session.status,
            ):
                session = replace(session, status="ambiguous sleep sessions")
        unique[identity] = session
    grouped = defaultdict(list)
    for session in unique.values():
        grouped[session.provider].append(session)
    result = []
    for records in grouped.values():
        valid = sorted(
            (s for s in records if s.start is not None and s.end is not None),
            key=lambda s: (s.start, s.end),
        )
        occupied_end = -math.inf
        ambiguous = set()
        for index, session in enumerate(valid):
            if session.start < occupied_end or (
                index + 1 < len(valid) and session.end > valid[index + 1].start
            ):
                ambiguous.add(session.key)
            occupied_end = max(occupied_end, session.end)
        main_dates = defaultdict(list)
        for session in valid:
            if session.kind == "main":
                # Oura's >3h type is not a guaranteed unique main-sleep flag.
                # Multiple main sessions on one local wake date are ambiguous.
                main_dates[session.wake_day or session.day].append(session)
        ambiguous.update(s.key for rows in main_dates.values() if len(rows) > 1 for s in rows)
        for session in records:
            if session.key in ambiguous:
                session = replace(session, status="ambiguous sleep sessions")
            result.append(session)
    return tuple(sorted(result, key=lambda s: (s.provider, s.start or -math.inf, s.key)))


def merge_windows(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


class SessionIndex:
    def __init__(self, snapshot, provider):
        self.provider = provider
        self.undated = [
            s
            for s in snapshot.sessions
            if s.provider == provider and (s.start is None or s.end is None)
        ]
        self.records = sorted(
            (
                s
                for s in snapshot.sessions
                if s.provider == provider and s.start is not None and s.end is not None
            ),
            key=lambda s: s.start,
        )
        self.starts = [s.start for s in self.records]
        self.main_start = sorted(
            (s for s in self.records if s.kind == "main"), key=lambda s: s.start
        )
        self.main_end = sorted(self.main_start, key=lambda s: s.end)
        self.main_starts = [s.start for s in self.main_start]
        self.main_ends = [s.end for s in self.main_end]
        self.windows = merge_windows(snapshot.sleep_windows.get(provider, ()))

    def within(self, start, end):
        # Valid imported periods cannot exceed seven days. Include earlier
        # starts so overlap with a study window is never silently skipped.
        begin = bisect.bisect_left(self.starts, start - 7 * 86400)
        stop = bisect.bisect_right(self.starts, end)
        return [s for s in self.records[begin:stop] if s.end > start and s.start < end]

    def covered(self, start, end):
        # Query windows use provider dates, not UTC timestamps. Guard adjacent
        # dates for offsets, travel and Oura's late-nap day assignment.
        lower = (datetime.fromtimestamp(start, timezone.utc).date() - timedelta(days=1)).isoformat()
        upper = (datetime.fromtimestamp(end, timezone.utc).date() + timedelta(days=2)).isoformat()
        return any(a <= lower and b >= upper for a, b in self.windows)

    def features(self, window):
        p = self.provider
        out = {field: None for field in SESSION_CSV_FIELDS if field.startswith(p + "_")}
        out[f"{p}_sleep_matching_version"] = SESSION_VERSION

        def status_all(status):
            for timing in ("before", "after"):
                out[f"{p}_main_{timing}_status"] = status
                out[f"{p}_nap_{timing}_status"] = status
            return out

        if (
            not isinstance(window, (list, tuple))
            or len(window) != 2
            or any(type(t) not in (int, float) or not math.isfinite(t) for t in window)
            or window[1] < window[0]
            or window[1] - window[0] > 48 * 3600
        ):
            return status_all("review times unavailable")
        first, last = window
        lower_day = (
            datetime.fromtimestamp(first, timezone.utc).date() - timedelta(days=1)
        ).isoformat()
        upper_day = (
            datetime.fromtimestamp(last, timezone.utc).date() + timedelta(days=1)
        ).isoformat()
        if any(lower_day <= s.day <= upper_day for s in self.undated):
            return status_all("sleep timestamps unavailable")
        during = self.within(first, last + 0.001)
        if any(s.kind == "main" for s in during):
            return status_all("sleep overlaps reviews")
        i = bisect.bisect_right(self.main_ends, first) - 1
        before = self.main_end[i] if i >= 0 else None
        i = bisect.bisect_left(self.main_starts, last)
        after = self.main_start[i] if i < len(self.main_start) else None
        if before and first - before.end > 36 * 3600:
            before = None
        if after and after.start - last > 36 * 3600:
            after = None
        for timing, main in (("before", before), ("after", after)):
            status = main.status if main else "main sleep unavailable"
            out[f"{p}_main_{timing}_status"] = status
            if status == "available":
                for value in SLEEP_VALUES:
                    out[f"{p}_main_{timing}_{value}_seconds"] = main.values.get(value)
            lower, upper = (
                (main.end, first)
                if timing == "before" and main
                else (last, main.start)
                if main
                else (0, 0)
            )
            prefix = f"{p}_nap_{timing}_"
            out[prefix + "status"] = status
            if status != "available":
                continue
            if not self.covered(lower, upper):
                out[prefix + "status"] = "session history incomplete"
                continue
            records = self.within(lower, upper)
            if any(
                s.status != "available" or s.kind != "nap" or s.start < lower or s.end > upper
                for s in records
            ):
                out[prefix + "status"] = (
                    "unclassified sleep"
                    if any(s.kind != "nap" for s in records)
                    else "ambiguous sleep sessions"
                )
                continue
            out[prefix + "recorded"] = int(bool(records))
            out[prefix + "count"] = len(records)
            out[prefix + "status"] = "available"
            if records:
                for stage in NAP_STAGES:
                    stages = [s.values.get(stage) for s in records]
                    if all(value is not None for value in stages):
                        out[prefix + stage + "_seconds"] = sum(stages)
                durations = [s.values.get("total_sleep") for s in records]
                if all(value is not None for value in durations):
                    out[prefix + "duration_seconds"] = sum(durations)
                else:
                    out[prefix + "status"] = "nap duration unavailable"
                out[prefix + "gap_seconds"] = (
                    first - max(s.end for s in records)
                    if timing == "before"
                    else min(s.start for s in records) - last
                )
        if self.covered(first, last) and all(
            s.kind == "nap" and s.status == "available" for s in during
        ):
            out[f"{p}_between_review_nap_count"] = sum(
                s.kind == "nap" and s.status == "available" for s in during
            )
        return out


def study_features(report, snapshot, provider):
    index = SessionIndex(snapshot, provider)
    windows = report.study_windows if report is not None else {}
    return (
        {day.day: index.features(windows.get(day.day.isoformat())) for day in report.days}
        if report
        else {}
    )
