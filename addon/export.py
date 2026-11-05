"""Stable, machine-readable daily CSV export."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from .health import HEALTH_CSV_FIELDS, DailyHealth
from .metrics import SHORT_TERM_MAX_DAYS
from .research import RESEARCH_METADATA_FIELDS
from .sleep_sessions import SESSION_CSV_FIELDS, study_features
from .workouts import WORKOUT_DAILY_FIELDS, WORKOUT_STUDY_FIELDS, study_workouts

if TYPE_CHECKING:
    from .backend import Report

METRIC_FIELDS = (
    "score_pp",
    "count",
    "cohort_count",
    "predicted_percent",
    "recalled_percent",
    "uncertainty_low_pp",
    "uncertainty_high_pp",
    "status",
    "pending_count",
    "excluded_count",
    "outcome_days",
    "coverage_percent",
)
METRIC_PREFIXES = ("recall", "formation", "formation_short_term", "formation_long_term")
CSV_FIELDS = (
    ["date"]
    + [f"{metric}_{field}" for metric in ("recall", "formation") for field in METRIC_FIELDS]
    + [
        "generated_at_utc",
        "anki_version",
        "parameters_hash",
        "rollover_hour",
        "timezone",
        "minimum_reviews",
        "minimum_formation_outcome_days",
        "uncertainty_method",
    ]
    + [f"{metric}_{field}" for metric in METRIC_PREFIXES[2:] for field in METRIC_FIELDS]
    + ["formation_short_term_max_days"]
    + list(RESEARCH_METADATA_FIELDS)
    + ["formation_completeness"]
    + list(HEALTH_CSV_FIELDS)
    + list(SESSION_CSV_FIELDS)
    + list(WORKOUT_DAILY_FIELDS)
    + list(WORKOUT_STUDY_FIELDS)
)


def export_csv(
    report: Report | None,
    path: str | Path,
    start: date | None = None,
    end: date | None = None,
    *,
    research_metadata: dict | None = None,
    health_data: dict[date, DailyHealth] | None = None,
    health_snapshot=None,
) -> int:
    count = 0
    context = (
        nullcontext(path)
        if hasattr(path, "write")
        else Path(path).open("w", encoding="utf-8-sig", newline="")
    )
    with context as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        anki_days = {day.day: day for day in report.days} if report is not None else {}
        health_data = health_snapshot.days if health_snapshot is not None else (health_data or {})
        session_data = (
            {
                provider: study_features(report, health_snapshot, provider)
                for provider in ("oura", "google_health")
            }
            if health_snapshot is not None
            else {}
        )
        workout_data = (
            study_workouts(report, health_snapshot) if health_snapshot is not None else {}
        )
        for day_date in sorted(anki_days.keys() | health_data.keys()):
            if (start and day_date < start) or (end and day_date > end):
                continue
            day = anki_days.get(day_date)
            row = {"date": day_date.isoformat()}
            if report is not None:
                row.update(
                    {
                        "generated_at_utc": report.generated_at,
                        "anki_version": report.anki_version,
                        "parameters_hash": report.parameters_hash,
                        "rollover_hour": report.rollover_hour,
                        "timezone": report.timezone_name,
                        "minimum_reviews": report.settings.minimum_reviews,
                        "minimum_formation_outcome_days": (
                            report.settings.minimum_formation_outcome_days
                        ),
                        "uncertainty_method": (
                            "approx95_model_floor_empirical_formation_cv3_wilson_t_v2"
                            if report.calculation_version >= 5
                            else "approx95_model_floor_empirical_formation_day_cluster"
                        ),
                        "formation_short_term_max_days": SHORT_TERM_MAX_DAYS,
                    }
                )
            if day is not None:
                row["formation_completeness"] = day.formation.completeness
            for prefix in METRIC_PREFIXES:
                metric = getattr(day, prefix) if day is not None else None
                for field in METRIC_FIELDS:
                    value = (
                        getattr(metric, field)
                        if metric is not None
                        else "not calculated"
                        if field == "status" and day is not None
                        else None
                    )
                    row[f"{prefix}_{field}"] = (
                        round(value, 6) if isinstance(value, float) else value
                    )
            row.update(
                {
                    key: ("true" if value else "false") if isinstance(value, bool) else value
                    for key, value in (research_metadata or {}).items()
                    if key in RESEARCH_METADATA_FIELDS
                }
            )
            if day_date in health_data:
                row.update(health_data[day_date].values)
            for daily_features in session_data.values():
                row.update(daily_features.get(day_date, {}))
            row.update(workout_data.get(day_date, {}))
            writer.writerow(row)
            count += 1
    return count


def export_csv_atomic(
    report: Report | None,
    path: str | Path,
    start: date | None = None,
    end: date | None = None,
    *,
    research_metadata: dict | None = None,
    before_replace: Callable[[], None] | None = None,
    health_data: dict[date, DailyHealth] | None = None,
    health_snapshot=None,
) -> int:
    """Replace the destination only after a complete CSV has been written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        count = export_csv(
            report,
            temporary,
            start,
            end,
            research_metadata=research_metadata,
            health_data=health_data,
            health_snapshot=health_snapshot,
        )
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
