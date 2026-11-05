"""Historical joins and statistical checks with synthetic daily measurements."""

import csv
import json
import math
import random
import statistics
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from test_storage import report_with_followup

from addon import insights, private_storage, storage
from addon.backend import UpdateCancelled
from addon.health import DailyHealth, HealthSnapshot
from addon.insights import InsightSettings, Point, Series, build_series, correlate
from addon.metrics import DailyResult, Metric, Settings
from addon.sleep_sessions import SleepSession

AS_OF = date(2026, 10, 25)
FAST = InsightSettings(bootstrap_replicates=199)


def metric(score=0, count=90, *, cohort=100, pending=10, outcome_days=15):
    return Metric(
        count,
        cohort,
        pending,
        cohort - count - pending,
        outcome_days,
        85,
        85 + score,
        score,
        score - 4,
        score + 4,
        "on par",
    )


def daily(day, score=0, formation=None):
    return DailyResult(
        day,
        metric(score, count=100, pending=0),
        formation or metric(score),
        metric(score, count=50, cohort=50, pending=0),
        metric(score, count=40, cohort=40, pending=0),
    )


def inputs(days=360, start=date(2024, 1, 1)):
    rng = random.Random(481)
    results, health, sessions, windows = [], {}, [], {}
    for i in range(days):
        day = start + timedelta(days=i)
        x = rng.gauss(0, 1)
        y = 0.6 * x + 0.8 * rng.gauss(0, 1)
        results.append(daily(day, y))
        values = {
            "oura_day_basis": "provider_assigned_day",
            "oura_sleep_status": "durations available",
            "oura_total_sleep_seconds": (7 + x / 4) * 3600,
            "oura_light_sleep_seconds": (3 + x / 6) * 3600,
            "oura_deep_sleep_seconds": (1.5 + x / 8) * 3600,
            "oura_rem_sleep_seconds": (2 + x / 7) * 3600,
            "oura_awake_seconds": (1 + x / 9) * 3600,
            "oura_steps": int(7000 + 1000 * x),
        }
        health[day] = DailyHealth(values)
        windows[day.isoformat()] = [
            datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp()
            for hour in (10, 11)
        ]
        wake = datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc)
        sessions.append(
            SleepSession(
                day.isoformat(),
                "oura",
                day.isoformat(),
                (wake - timedelta(hours=8)).timestamp(),
                wake.timestamp(),
                "main",
                {
                    key: values[f"oura_{key}_seconds"]
                    for key in ("total_sleep", "light_sleep", "deep_sleep", "rem_sleep", "awake")
                },
            )
        )
    last = sessions[-1]
    sessions.append(
        replace(
            last,
            key="boundary",
            day=(start + timedelta(days=days)).isoformat(),
            start=last.start + 86400,
            end=last.end + 86400,
        )
    )
    report = replace(
        report_with_followup(), days=results, settings=Settings(), study_windows=windows
    )
    coverage = {
        "oura": (
            (
                (start - timedelta(days=4)).isoformat(),
                (start + timedelta(days=days + 4)).isoformat(),
            ),
        )
    }
    return report, HealthSnapshot("synthetic", health, tuple(sessions), coverage)


def series_for(xs, ys, *, gap=1, start=date(2024, 1, 1)):
    return Series(
        "deep_sleep",
        "recall",
        "before",
        [
            Point(start + timedelta(days=i * gap), start + timedelta(days=i * gap), x, y, 100)
            for i, (x, y) in enumerate(zip(xs, ys, strict=True))
        ],
    )


def test_both_formation_nights_are_joined_to_source_days_and_recall_never_looks_ahead():
    day = date(2026, 1, 10)
    first = datetime(2026, 1, 10, 10, tzinfo=timezone.utc).timestamp()
    report = replace(
        report_with_followup(),
        days=[daily(day)],
        study_windows={day.isoformat(): [first, first + 3600]},
    )
    records = {}
    for offset, sleep, steps in ((-1, 6, 1000), (0, 7, 2000), (1, 8, 3000)):
        records[day + timedelta(days=offset)] = DailyHealth(
            dict(
                oura_sleep_status="durations available",
                oura_day_basis="provider_assigned_day",
                oura_total_sleep_seconds=sleep * 3600,
                oura_steps=steps,
            )
        )
    data = build_series(
        report,
        HealthSnapshot(
            "x",
            records,
            (
                SleepSession(
                    "a",
                    "oura",
                    day.isoformat(),
                    first - 11 * 3600,
                    first - 3 * 3600,
                    "main",
                    {"total_sleep": 7 * 3600},
                ),
                SleepSession(
                    "b",
                    "oura",
                    (day + timedelta(days=1)).isoformat(),
                    first + 13 * 3600,
                    first + 21 * 3600,
                    "main",
                    {"total_sleep": 8 * 3600},
                ),
            ),
        ),
        "oura",
        as_of=AS_OF,
        settings=replace(FAST, adjust_month=False),
    )
    assert "total_sleep/recall/after" not in data
    assert data["total_sleep/recall/before"].points[0].x == 7
    for name in insights.METRICS[1:]:
        before, after = data[f"total_sleep/{name}/before"], data[f"total_sleep/{name}/after"]
        assert before.points[0].x == 7 and after.points[0].x == 8
        assert before.points[0].day == after.points[0].day == day
        assert (
            after.points[0].health_day is None
        )  # Physical session match, not a provider-date join.
    assert data["steps/recall/previous_day"].points[0].x == 1000
    assert data["steps/formation/study_day"].points[0].x == 2000


def test_eligibility_excludes_recent_or_incomplete_formation_without_turning_pending_into_failure():
    report, health = inputs(120, date(2026, 7, 1))
    results = list(report.days)
    results[0] = daily(results[0].day, formation=metric(count=79, pending=21))
    report = replace(report, days=results)
    data = build_series(report, health, "oura", as_of=AS_OF, settings=FAST)
    formation = data["deep_sleep/formation/before"]
    assert results[0].day not in {p.day for p in formation.points}
    assert all((AS_OF - p.day).days >= 30 for p in formation.points)
    assert formation.omitted["formation follow-ups incomplete"] == 1
    assert formation.omitted["formation still recent"] > 0
    assert len(data["deep_sleep/recall/before"].points) > len(formation.points)
    assert report.days[0].formation.pending_count == 21


def test_history_is_not_truncated_to_installation_date_and_providers_never_pool():
    report, health = inputs(740, date(2022, 1, 1))
    for row in health.days.values():
        row.values.update(
            google_health_day_basis="sleep_end_local_date_steps_civil_date",
            google_health_sleep_status="durations available",
            google_health_deep_sleep_seconds=7200,
        )
    health = replace(
        health,
        sessions=health.sessions
        + tuple(
            replace(
                session,
                provider="google_health",
                key="g" + session.key,
                values={**session.values, "deep_sleep": 7200},
            )
            for session in health.sessions
        ),
    )
    old = build_series(report, health, "oura", as_of=AS_OF, settings=FAST)
    assert old["deep_sleep/recall/before"].points[0].day == date(2022, 1, 1)
    assert len(old["deep_sleep/recall/before"].points) == 740
    google = build_series(report, health, "google_health", as_of=AS_OF, settings=FAST)
    assert {p.x for p in google["deep_sleep/recall/before"].points} == {2}
    assert len(old["deep_sleep/recall/before"].points) == 740


def test_missing_values_zero_steps_and_ambiguous_sleep_are_distinct():
    report, health = inputs(90)
    first, second = report.days[0].day, report.days[1].day
    health.days[first].values["oura_steps"] = 0
    health.days[second].values["oura_steps"] = None
    health.days[first].values["oura_sleep_status"] = "overlapping periods"
    health = replace(
        health,
        sessions=tuple(
            replace(s, status="ambiguous sleep sessions") if s.day == first.isoformat() else s
            for s in health.sessions
        ),
    )
    data = build_series(report, health, "oura", as_of=AS_OF, settings=FAST)
    steps = data["steps/recall/previous_day"]
    assert steps.points[0].x == 0 and steps.points[0].health_day == first
    assert second not in {p.health_day for p in steps.points}
    assert first not in {p.day for p in data["deep_sleep/recall/before"].points}


def test_pearson_matches_independent_standard_library_and_known_population():
    rng = random.Random(5)
    xs = [rng.gauss(0, 1) for _ in range(600)]
    ys = [0.6 * x + 0.8 * rng.gauss(0, 1) for x in xs]
    result = correlate(series_for(xs, ys), replace(FAST, adjust_month=False))
    assert result.correlation == pytest.approx(statistics.correlation(xs, ys), abs=1e-12)
    assert result.low < 0.6 < result.high
    assert -1 <= result.low <= result.high <= 1 and result.status == "exploratory estimate"


def test_month_adjustment_removes_shared_slow_shifts_and_is_reflected_in_plot():
    xs, ys = [], []
    for i in range(360):
        # Orthogonal within-month patterns with a large shared month-level shift.
        month = (date(2024, 1, 1) + timedelta(days=i)).month
        xs.append(month * 20 + math.sin(i * 1.75))
        ys.append(month * 15 + math.cos(i * 2.41))
    series = series_for(xs, ys)
    raw = correlate(series, replace(FAST, adjust_month=False))
    adjusted = correlate(series, FAST)
    assert raw.correlation > 0.99 and abs(adjusted.correlation) < 0.1
    assert adjusted.low < 0 < adjusted.high
    pairs = insights.plotted_points(series, True)
    assert statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs]) == pytest.approx(
        adjusted.correlation
    )


def test_block_range_wider_than_treating_repeated_days_as_independent():
    rng = random.Random(120)
    independent_x, independent_y = [], []
    for _ in range(30):
        x = rng.gauss(0, 1)
        independent_x.append(x)
        independent_y.append(0.3 * x + rng.gauss(0, 1))
    series = series_for(
        [x for x in independent_x for _ in range(14)], [y for y in independent_y for _ in range(14)]
    )
    result = correlate(series, replace(FAST, adjust_month=False))
    r = result.correlation
    z = math.atanh(r)
    iid_half = 1.96 / math.sqrt(len(series.points) - 3)
    iid_width = math.tanh(z + iid_half) - math.tanh(z - iid_half)
    assert result.high - result.low > 1.8 * iid_width


@pytest.mark.parametrize("days", [0, 1, 30, 59])
def test_insufficient_days_do_not_publish_a_confident_label(days):
    series = series_for(list(range(days)), [i % 3 for i in range(days)])
    result = correlate(series, FAST)
    assert result.status == "insufficient data" and result.low is None and result.high is None
    assert str(days) in result.reason


def test_short_span_constant_series_and_constant_within_month_are_insufficient():
    result = correlate(series_for(list(range(70)), [i % 4 for i in range(70)]), FAST)
    assert result.status == "insufficient data" and "calendar" in result.reason
    result = correlate(series_for([1] * 180, list(range(180))), FAST)
    assert result.status == "insufficient data" and "variation" in result.reason
    xs = [(date(2024, 1, 1) + timedelta(days=i)).month for i in range(180)]
    result = correlate(series_for(xs, [float(x * 2) for x in xs]), FAST)
    assert result.status == "insufficient data" and result.raw_correlation == pytest.approx(1)


def test_sparse_calendar_blocks_preserve_gaps_instead_of_compressing_time():
    series = series_for(list(range(90)), [i % 11 for i in range(90)], gap=5)
    result = correlate(series, replace(FAST, adjust_month=False))
    assert result.span_days == 446 and result.blocks == 32
    assert result.days == 90 and result.low is not None


def test_reproducible_bootstrap_and_cancellation():
    report, health = inputs()
    series = build_series(report, health, "oura", as_of=AS_OF, settings=FAST)[
        "deep_sleep/recall/before"
    ]
    assert correlate(series, FAST) == correlate(series, FAST)

    def cancel():
        raise UpdateCancelled("Closed")

    with pytest.raises(UpdateCancelled):
        correlate(series, FAST, check_cancelled=cancel)


@pytest.fixture
def cached(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    report, health = inputs(180)
    # Exercise the real saved report format and mock only provider fixture storage.
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda path: health)
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    return source, report, health


def test_cached_analysis_reused_across_later_visits_and_invalidated_by_older_corrections(
    cached, monkeypatch
):
    source, report, health = cached
    result = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert not result.reused
    original = insights.correlate
    monkeypatch.setattr(insights, "correlate", lambda *a, **k: pytest.fail("Repeated bootstrap"))
    second = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF + timedelta(days=60))
    assert second.reused and second.associations == result.associations
    monkeypatch.setattr(insights, "correlate", original)
    health.days[report.days[10].day].values["oura_deep_sleep_seconds"] += 1000
    health.sessions[10].values["deep_sleep"] += 1000
    third = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert not third.reused and third.associations != result.associations


def test_new_followups_new_health_data_and_month_filter_refresh_history(cached):
    source, report, health = cached
    first = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    day = report.days[-1].day
    changed = replace(report, days=[*report.days[:-1], daily(day, 20)])
    storage.save_report(changed, storage.database_path(source))
    assert not insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF).reused
    restricted = insights.load_insights(
        source, "oura", settings=FAST, as_of=AS_OF, start=day - timedelta(days=89)
    )
    assert not restricted.reused
    assert (
        restricted.associations["deep_sleep/recall/before"].days
        < first.associations["deep_sleep/recall/before"].days
    )


def test_corrupted_cache_recovers_and_no_partial_cache_on_cancel(cached):
    source, _, _ = cached
    path = storage.results_directory(source) / "personal-insights.json"
    path.write_text("[]", encoding="utf-8")
    result = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert not result.reused
    previous = path.read_bytes()

    def cancel():
        raise UpdateCancelled("Closed")

    with pytest.raises(UpdateCancelled):
        insights.load_insights(
            source,
            "oura",
            settings=replace(FAST, adjust_month=False),
            as_of=AS_OF,
            check_cancelled=cancel,
        )
    assert path.read_bytes() == previous


def test_personal_summary_export_both_nights_and_failure_preserves_previous_file(cached, tmp_path):
    source, _, _ = cached
    result = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    destination = tmp_path / "insights.csv"
    assert insights.export_insights(result, destination) == 130
    with destination.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {
        r["timing"] for r in rows if r["metric"] == "formation" and r["factor"] == "deep_sleep"
    } == {"before", "after"}
    assert not any(r["timing"] == "after" for r in rows if r["metric"] == "recall")
    assert rows[0]["bootstrap_replicates"] == "199"
    before = destination.read_bytes()

    def fail():
        raise OSError("Profile closed")

    with pytest.raises(OSError):
        insights.export_insights(result, destination, before_replace=fail)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob("tmp*"))


def test_cache_contains_only_summaries_and_no_raw_points(cached):
    source, _, _ = cached
    insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    payload = private_storage.read_json(
        storage.results_directory(source) / "personal-insights.json"
    )
    saved = next(iter(payload["entries"].values()))
    assert set(saved) == {"generated_at", "associations", "phase_associations"}
    assert "points" not in json.dumps(saved)


def test_changed_source_during_analysis_does_not_publish_stale_cache(cached, monkeypatch):
    source, report, _ = cached
    original, changed = insights.correlate, []

    def race(*args, **kwargs):
        if not changed:
            changed.append(True)
            storage.save_report(
                replace(report, generated_at="2026-10-25T10:00:00Z"), storage.database_path(source)
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(insights, "correlate", race)
    with pytest.raises(insights.InputsChanged):
        insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert not (storage.results_directory(source) / "personal-insights.json").exists()
    monkeypatch.setattr(insights, "correlate", original)
    assert (
        insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF).memory_generated_at
        == "2026-10-25T10:00:00Z"
    )


@pytest.mark.parametrize("provider", ["oura", "google_health"])
def test_provider_disconnect_removes_derived_insight_cache(cached, monkeypatch, provider):
    from addon import google_health_api, oura_api
    from addon.research import SharingConfig
    from addon.service import ServiceConfig

    source, _, _ = cached
    insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    module = oura_api if provider == "oura" else google_health_api
    monkeypatch.setattr(module, "read_secret", lambda *args: None)
    monkeypatch.setattr(module, "write_secret", lambda *args: None)
    disconnect = module.disconnect_oura if provider == "oura" else module.disconnect_google_health
    disconnect(source, ServiceConfig("https://example.org/service"), SharingConfig())
    assert not (storage.results_directory(source) / "personal-insights.json").exists()


def test_followups_become_eligible_on_later_visit_without_installation_cutoff(
    tmp_path, monkeypatch
):
    source = tmp_path / "collection.anki2"
    report, health = inputs(150, date(2026, 6, 1))
    storage.save_report(report, storage.database_path(source))
    monkeypatch.setattr("addon.health.load_health", lambda path: health)
    early = insights.load_insights(source, "oura", settings=FAST, as_of=date(2026, 9, 15))
    later = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    key = "deep_sleep/formation/before"
    assert not later.reused
    assert later.associations[key].days > early.associations[key].days
    assert early.associations[key].first_day == later.associations[key].first_day == "2026-06-01"


def test_existing_google_history_selected_automatically_and_rollover_day_used(cached, monkeypatch):
    source, _, health = cached
    for day, record in list(health.days.items()):
        health.days[day] = DailyHealth(
            {key.replace("oura_", "google_health_"): value for key, value in record.values.items()}
        )
    health = replace(
        health, sessions=tuple(replace(s, provider="google_health") for s in health.sessions)
    )
    monkeypatch.setattr("addon.health.load_health", lambda path: health)
    calls = []

    def day_boundary(stamp, rollover):
        calls.append(rollover)
        return date(2024, 4, 1)

    monkeypatch.setattr("addon.metrics.anki_day", day_boundary)
    result = insights.load_insights(source, None, settings=FAST)
    assert result.provider == "google_health" and calls == [4]
    assert result.associations["deep_sleep/recall/before"].last_day == "2024-03-31"


@pytest.mark.parametrize("bad", ["wrong_structure", "nonfinite"])
def test_malformed_nested_cache_is_recalculated(cached, bad):
    source, _, _ = cached
    insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    path = storage.results_directory(source) / "personal-insights.json"
    payload = private_storage.read_json(path)
    entry = next(iter(payload["entries"].values()))
    if bad == "wrong_structure":
        entry["associations"] = []
    else:
        entry["associations"]["deep_sleep/recall/before"]["correlation"] = float("nan")
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = insights.load_insights(source, "oura", settings=FAST, as_of=AS_OF)
    assert not result.reused and math.isfinite(
        result.associations["deep_sleep/recall/before"].correlation
    )
