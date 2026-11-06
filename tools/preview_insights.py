"""Render the real personal-insights window with synthetic historical inputs."""

from __future__ import annotations

import copy
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.health import DailyHealth, HealthSnapshot  # noqa: E402
from addon.insight_visits import compare_visits, make_snapshot  # noqa: E402
from addon.insights import (  # noqa: E402
    Insights,
    InsightSettings,
    build_series,
    correlate,
    history_coverage,
    summarize_main_sleep,
)
from addon.insights_ui import InsightsDialog  # noqa: E402
from addon.metrics import DailyResult, Metric, Settings  # noqa: E402
from addon.phase_comparisons import build_phase_cohorts, compare_phases  # noqa: E402
from addon.research import SharingConfig  # noqa: E402
from addon.sleep_sessions import SleepSession  # noqa: E402


def sample_metric(score, count):
    return Metric(count, count, 0, 0, 20, 85, 85 + score, score, score - 4, score + 4, "on par")


app = QApplication([])
if os.name == "nt":
    font_id = QFontDatabase.addApplicationFont(
        str(Path(os.environ["WINDIR"]) / "Fonts/segoeui.ttf")
    )
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
parent = QWidget()
parent.col = SimpleNamespace(path=str(root / ".reference/preview.anki2"))
provider = "google_health" if "--google" in sys.argv else "oura"
dialog = InsightsDialog(parent, "addon", SharingConfig(), provider)
records, days, sessions, study_windows = {}, [], [], {}
rng = random.Random(381)
for i in range(700):
    day = date(2024, 6, 1) + timedelta(days=i)
    deep = rng.gauss(1.6, 0.3)
    recall = (deep - 1.6) * 3 + rng.gauss(0, 2)
    overall = rng.gauss(0, 2)
    days.append(
        DailyResult(
            day,
            sample_metric(recall, 100),
            sample_metric(overall, 120),
            sample_metric(overall + 0.2, 70),
            sample_metric(overall - 0.4, 50),
        )
    )
    values = {
        f"{provider}_day_basis": "synthetic",
        f"{provider}_sleep_status": "durations available",
        f"{provider}_total_sleep_seconds": rng.gauss(7.5, 0.7) * 3600,
        f"{provider}_light_sleep_seconds": rng.gauss(3.8, 0.5) * 3600,
        f"{provider}_deep_sleep_seconds": deep * 3600,
        f"{provider}_rem_sleep_seconds": rng.gauss(1.9, 0.3) * 3600,
        f"{provider}_awake_seconds": rng.gauss(0.6, 0.1) * 3600,
        f"{provider}_steps": max(0, int(rng.gauss(8000, 2300))),
    }
    if provider == "google_health":
        values[f"{provider}_restless_seconds"] = None
    staged = sum(
        values[f"{provider}_{phase}_seconds"]
        for phase in ("light_sleep", "deep_sleep", "rem_sleep")
    )
    values[f"{provider}_total_sleep_seconds"] = staged
    records[day] = DailyHealth(values)
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
    study_windows[day.isoformat()] = [midnight + 10 * 3600, midnight + 18 * 3600]
    sessions.append(
        SleepSession(
            str(i),
            provider,
            day.isoformat(),
            midnight - 3600,
            midnight + 7 * 3600,
            "main",
            {
                key: values.get(f"{provider}_{key}_seconds")
                for key in (
                    "total_sleep",
                    "light_sleep",
                    "deep_sleep",
                    "rem_sleep",
                    "awake",
                    "restless",
                )
            },
        )
    )
    for suffix, lower, upper in (("pre", 7.2, 9.8), ("post", 18.2, 22.8)):
        if rng.random() < 0.5:
            duration = rng.uniform(15, 75) * 60
            start = midnight + rng.uniform(lower, upper - 1.3) * 3600
            weights = [rng.uniform(0.2, 1) for _ in range(3)]
            sessions.append(
                SleepSession(
                    f"{i}-{suffix}",
                    provider,
                    day.isoformat(),
                    start,
                    start + duration,
                    "nap",
                    {
                        "total_sleep": duration,
                        "light_sleep": duration * weights[0] / sum(weights),
                        "deep_sleep": duration * weights[1] / sum(weights),
                        "rem_sleep": duration * weights[2] / sum(weights),
                    }
                    if provider == "oura"
                    else {"total_sleep": duration},
                )
            )
report = SimpleNamespace(
    days=days,
    settings=Settings(),
    study_windows=study_windows,
    generated_at="2026-10-25T10:00:00+00:00",
)
settings = InsightSettings()
snapshot = HealthSnapshot(
    "synthetic", records, tuple(sessions), {provider: (("2024-05-25", "2026-06-01"),)}
)
series = build_series(
    report,
    snapshot,
    provider,
    as_of=date(2026, 10, 25),
    settings=settings,
)
cohorts = build_phase_cohorts(series, settings)
phases = {}
for cohort in cohorts.values():
    for contrast, value in compare_phases(cohort, settings).items():
        phases[f"{cohort.scope}/{contrast}/{cohort.metric}/{cohort.timing}"] = value
result = Insights(
    provider,
    settings,
    series,
    {key: correlate(value, settings) for key, value in series.items()},
    "2026-10-25T10:00:00+00:00",
    report.generated_at,
    len(records),
    phase_cohorts=cohorts,
    phase_associations=phases,
    coverage=history_coverage(report, snapshot, provider, as_of=date(2026, 10, 25)),
    main_sleep=summarize_main_sleep(snapshot, provider),
)
if "--returning" in sys.argv:
    current = make_snapshot(result, report, snapshot, method_version=4, as_of=date(2026, 10, 25))
    previous = copy.deepcopy(current)
    previous.update(ticket=1, revision=1, viewed_at_utc="2026-07-20T10:00:00+00:00")
    for key in sorted(previous["study"])[-80:]:
        previous["study"].pop(key)
    for key in sorted(previous["health"])[-90:]:
        previous["health"].pop(key)
    for key in list(previous["study"])[10:15]:
        metrics = previous["study"][key]
        metrics["formation"].update(count=70, pending=50, ready=False)
        metrics["formation_short_term"].update(count=40, cohort=40, ready=False)
        metrics["formation_long_term"].update(count=30, cohort=30, ready=False)
    for i, value in enumerate(previous["estimates"].values()):
        if value["status"] == "exploratory estimate":
            middle = (value["low"] + value["high"]) / 2
            half = (value["high"] - value["low"]) / 2 * (0.7 if i % 4 == 0 else 1.5)
            value.update(low=middle - half, high=middle + half, days=max(60, value["days"] - 80))
    result.visit_changes = compare_visits(current, previous)
dialog.show_result(result)
if "--naps" in sys.argv:
    dialog.section.setCurrentIndex(1)
if "--stages" in sys.argv:
    dialog.stage_button.setChecked(True)
dialog.show()
dialog.resize(1280, 940)
dialog.table.setCurrentCell(0, 1)
app.processEvents()
destination = (
    root
    / ".reference"
    / (provider + ("-naps-preview.png" if "--naps" in sys.argv else "-insights-preview.png"))
)
destination.parent.mkdir(exist_ok=True)
dialog.grab().save(str(destination))
print(destination)
