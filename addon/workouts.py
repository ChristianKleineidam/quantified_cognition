"""Selected Oura workouts and daily summaries matched to actual study times."""

from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

WORKOUT_VERSION = 1
INTENSITIES = ("easy", "moderate", "hard")
SOURCES = (
    "manual",
    "autodetected",
    "confirmed",
    "workout_heart_rate",
    "live_third_party_heart_rate",
    "live_oura_heart_rate",
)
STATUSES = (
    "available",
    "workout history unavailable",
    "workout history incomplete",
    "review times unavailable",
    "overlapping workouts",
    "workout overlaps study boundary",
)
VALUES = ("recorded", "count", "duration_seconds", *(v + "_seconds" for v in INTENSITIES))
WORKOUT_DAILY_FIELDS = tuple("oura_workout_" + v for v in (*VALUES, "status"))
WORKOUT_STUDY_FIELDS = tuple(
    f"oura_workout_{timing}_{value}"
    for timing in ("before", "after")
    for value in (*VALUES, "gap_seconds", "status")
) + ("oura_between_review_workout_count", "oura_workout_matching_version")


@dataclass(frozen=True)
class Workout:
    key: str
    day: str
    start: str
    end: str
    intensity: str | None
    source: str

    @property
    def start_time(self):
        return datetime.fromisoformat(self.start).timestamp()

    @property
    def end_time(self):
        return datetime.fromisoformat(self.end).timestamp()


def parse_workouts(records):
    selected = {}
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("Oura returned an incomplete workout record.")
        try:
            day = date.fromisoformat(row["day"]).isoformat()
            start, end = (
                datetime.fromisoformat(row[field].replace("Z", "+00:00"))
                for field in ("start_datetime", "end_datetime")
            )
            if (
                start.tzinfo is None
                or end.tzinfo is None
                or not 0 < (end - start).total_seconds() <= 86400
            ):
                raise ValueError()
            if abs((start.date() - date.fromisoformat(day)).days) > 1:
                raise ValueError()
            if not isinstance(row.get("intensity"), str) or not isinstance(row.get("source"), str):
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError(
                "Oura returned invalid workout dates, duration or categories."
            ) from None
        # No labels, activity names, calories, distance, heart rate or raw IDs.
        workout = Workout(
            hashlib.sha256(row["id"].encode()).hexdigest(),
            day,
            start.isoformat(),
            end.isoformat(),
            row["intensity"] if row["intensity"] in INTENSITIES else None,
            row["source"] if row["source"] in SOURCES else "other",
        )
        previous = selected.get(workout.key)
        if previous is not None and previous != workout:
            raise ValueError("Oura returned conflicting workout records.")
        selected[workout.key] = workout
    return tuple(selected.values())


def summarize(records):
    """Do not double-count duplicate/overlapping recordings from multiple sources."""
    unique = {}
    for row in records:
        identity = (row.start_time, row.end_time)
        previous = unique.get(identity)
        if previous and previous.intensity != row.intensity:
            return {}, "overlapping workouts"
        unique[identity] = row
    ordered = sorted(unique.values(), key=lambda row: row.start_time)
    if any(b.start_time < a.end_time for a, b in zip(ordered, ordered[1:], strict=False)):
        return {}, "overlapping workouts"
    out = dict(recorded=int(bool(ordered)), count=len(ordered))
    out["duration_seconds"] = sum(row.end_time - row.start_time for row in ordered)
    for intensity in INTENSITIES:
        # Missing/new intensity categories do not silently become easy/moderate/hard.
        out[intensity + "_seconds"] = (
            sum(row.end_time - row.start_time for row in ordered if row.intensity == intensity)
            if all(row.intensity is not None for row in ordered)
            else None
        )
    return out, "available"


class WorkoutIndex:
    def __init__(self, snapshot):
        self.rows = sorted(snapshot.workouts, key=lambda row: row.start_time)
        self.starts = [row.start_time for row in self.rows]
        self.windows = snapshot.workout_windows
        self.observed_days = {
            day for day, record in snapshot.days.items() if record.values.get("oura_day_basis")
        }

    def covered(self, start, end):
        # API windows use provider dates; guard offsets and midnight assignment.
        lower = (datetime.fromtimestamp(start, timezone.utc).date() - timedelta(days=1)).isoformat()
        upper = (datetime.fromtimestamp(end, timezone.utc).date() + timedelta(days=2)).isoformat()
        if not any(a <= lower and b >= upper for a, b in self.windows):
            return False
        # A query back to 2000 does not establish device coverage before the
        # person's Oura history or on dates without measurements. This UTC-date
        # availability guard is conservative; workout matching uses instants.
        first = datetime.fromtimestamp(start, timezone.utc).date()
        last = datetime.fromtimestamp(max(start, end - 0.000001), timezone.utc).date()
        return all(
            first + timedelta(days=i) in self.observed_days for i in range((last - first).days + 1)
        )

    def within(self, start, end):
        lower = bisect.bisect_left(self.starts, start - 86400)
        upper = bisect.bisect_left(self.starts, end)
        return [row for row in self.rows[lower:upper] if row.end_time > start]

    def features(self, window):
        out = dict.fromkeys(WORKOUT_STUDY_FIELDS)
        out["oura_workout_matching_version"] = WORKOUT_VERSION

        def unavailable(status):
            for timing in ("before", "after"):
                out[f"oura_workout_{timing}_status"] = status
            return out

        if not self.windows:
            return unavailable("workout history unavailable")
        if (
            not isinstance(window, (list, tuple))
            or len(window) != 2
            or any(type(t) not in (int, float) or not math.isfinite(t) for t in window)
            or not 0 <= window[1] - window[0] <= 48 * 3600
        ):
            return unavailable("review times unavailable")
        first, last = window
        for timing, start, end in (("before", first - 86400, first), ("after", last, last + 86400)):
            prefix = f"oura_workout_{timing}_"
            if not self.covered(start, end):
                out[prefix + "status"] = "workout history incomplete"
                continue
            rows = self.within(start, end)
            if any(row.start_time < start or row.end_time > end for row in rows):
                out[prefix + "status"] = "workout overlaps study boundary"
                continue
            values, status = summarize(rows)
            out[prefix + "status"] = status
            out.update({prefix + key: value for key, value in values.items()})
            if rows and status == "available":
                out[prefix + "gap_seconds"] = (
                    first - max(row.end_time for row in rows)
                    if timing == "before"
                    else min(row.start_time for row in rows) - last
                )
        if self.covered(first, last):
            values, status = summarize(self.within(first, last))
            if status == "available":
                out["oura_between_review_workout_count"] = values["count"]
        return out


def study_workouts(report, snapshot):
    index = WorkoutIndex(snapshot)
    return (
        {
            day.day: index.features(report.study_windows.get(day.day.isoformat()))
            for day in report.days
        }
        if report
        else {}
    )
