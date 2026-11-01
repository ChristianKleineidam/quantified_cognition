"""Physical sleep/review alignment with synthetic provider sessions."""

import csv
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from test_google_health import night, parsed, steps
from test_insights import AS_OF, FAST, daily, inputs, series_for
from test_oura import sleep
from test_storage import report_with_followup

from addon import health
from addon.export import export_csv
from addon.google_health import parse_google_health
from addon.health import HealthSnapshot
from addon.insights import build_series, correlate
from addon.oura import parse_oura_records
from addon.sleep_sessions import SessionIndex, SleepSession, normalize_sessions, oura_session

DAY = date(2026, 9, 7)


def stamp(hour):
    return datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp() + hour * 3600


def session(key, start, end, kind="nap", provider="oura", **kwargs):
    return SleepSession(
        key,
        provider,
        DAY.isoformat(),
        stamp(start),
        stamp(end),
        kind,
        {"total_sleep": (end - start) * 3600, "deep_sleep": min(3600, (end - start) * 3600)},
        **kwargs,
    )


def snapshot(*extra, covered=True, provider="oura"):
    before = replace(session("before", -1, 7, "main", provider), day="2026-09-07")
    after = replace(session("after", 23, 31, "main", provider), day="2026-09-08")
    return HealthSnapshot(
        "x",
        {},
        normalize_sessions((before, after, *extra)),
        {provider: (("2026-09-01", "2026-09-12"),)} if covered else {},
    )


def features(data, first=10, last=18, provider="oura"):
    return SessionIndex(data, provider).features([stamp(first), stamp(last)])


def test_main_sleep_is_separate_and_nap_durations_and_proximity_are_physical():
    data = snapshot(session("pre", 8, 8.5), session("during", 13, 13.5), session("post", 20, 21))
    values = features(data)
    assert values["oura_main_before_total_sleep_seconds"] == 8 * 3600
    assert values["oura_main_after_total_sleep_seconds"] == 8 * 3600
    assert values["oura_nap_before_duration_seconds"] == 1800
    assert values["oura_nap_before_gap_seconds"] == 1.5 * 3600
    assert values["oura_nap_after_duration_seconds"] == 3600
    assert values["oura_nap_after_gap_seconds"] == 2 * 3600
    assert values["oura_between_review_nap_count"] == 1


@pytest.mark.parametrize("provider", ["oura", "google_health"])
def test_nap_stages_sum_only_complete_reported_measurements_and_preserve_zero(provider):
    first = replace(
        session("one", 8, 8.5, provider=provider),
        values={"total_sleep": 1800, "light_sleep": 1200, "deep_sleep": 600, "rem_sleep": 0},
    )
    second = replace(
        session("two", 9, 9.5, provider=provider),
        values={"total_sleep": 1800, "light_sleep": 900, "deep_sleep": 600, "rem_sleep": 300},
    )
    values = features(snapshot(first, second, provider=provider), provider=provider)
    assert values[f"{provider}_nap_before_light_sleep_seconds"] == 2100
    assert values[f"{provider}_nap_before_deep_sleep_seconds"] == 1200
    assert values[f"{provider}_nap_before_rem_sleep_seconds"] == 300
    assert values[f"{provider}_nap_after_deep_sleep_seconds"] is None  # No recorded nap.
    partial = replace(second, values={"total_sleep": 1800, "deep_sleep": 0, "rem_sleep": 0})
    values = features(snapshot(first, partial, provider=provider), provider=provider)
    assert values[f"{provider}_nap_before_light_sleep_seconds"] is None
    assert values[f"{provider}_nap_before_deep_sleep_seconds"] == 600
    assert values[f"{provider}_nap_before_rem_sleep_seconds"] == 0
    assert values[f"{provider}_nap_before_duration_seconds"] == 3600


def test_oura_late_nap_uses_times_even_when_assigned_to_next_provider_day():
    raw = sleep(
        type="late_nap",
        day="2026-09-08",
        bedtime_start="2026-09-07T20:00:00+00:00",
        bedtime_end="2026-09-07T20:30:00+00:00",
        total_sleep_duration=1800,
        light_sleep_duration=900,
        deep_sleep_duration=300,
        rem_sleep_duration=600,
        awake_time=0,
    )
    nap = oura_session(parse_oura_records([raw], []).sleep[0])
    assert nap.kind == "nap" and nap.day == "2026-09-08"
    assert features(snapshot(nap))["oura_nap_after_duration_seconds"] == 1800
    assert features(snapshot(nap))["oura_nap_after_deep_sleep_seconds"] == 300
    assert features(snapshot(nap))["oura_nap_after_rem_sleep_seconds"] == 600


def test_zero_nap_indicator_requires_main_sleep_and_completed_history_query():
    values = features(snapshot())
    assert values["oura_nap_before_recorded"] == 0
    assert values["oura_nap_before_duration_seconds"] is None
    assert values["oura_nap_before_gap_seconds"] is None
    missing = features(snapshot(covered=False))
    assert missing["oura_main_before_total_sleep_seconds"] == 8 * 3600
    assert missing["oura_nap_before_recorded"] is None
    assert missing["oura_nap_before_status"] == "session history incomplete"
    missing = features(HealthSnapshot("x", {}))
    assert missing["oura_nap_before_recorded"] is None
    assert missing["oura_main_before_status"] == "main sleep unavailable"


@pytest.mark.parametrize("kind,status", [("other", "available"), ("nap", "sleep processing")])
def test_unknown_or_processing_sleep_does_not_turn_into_no_nap(kind, status):
    data = snapshot(session("unknown", 8, 9, kind, status=status))
    values = features(data)
    assert values["oura_nap_before_recorded"] is None


def test_nap_straddling_first_review_is_not_a_before_predictor():
    values = features(snapshot(session("cross", 9.5, 10.5)))
    assert values["oura_nap_before_recorded"] is None
    assert values["oura_between_review_nap_count"] == 1


def test_main_sleep_overlapping_reviews_or_old_main_is_flagged():
    values = features(snapshot(), first=6, last=18)
    assert values["oura_main_before_status"] == "sleep overlaps reviews"
    data = HealthSnapshot("x", {}, (session("old", -50, -40, "main"),))
    assert features(data)["oura_main_before_status"] == "main sleep unavailable"


def test_offset_timestamps_shift_work_and_dst_do_not_require_a_night_date():
    raw = sleep(bedtime_start="2026-10-25T02:00:00+02:00", bedtime_end="2026-10-25T09:00:00+01:00")
    main = oura_session(parse_oura_records([raw], []).sleep[0])
    assert main.end - main.start == 8 * 3600
    data = HealthSnapshot("x", {}, (main,))
    values = SessionIndex(data, "oura").features([main.end + 3600, main.end + 7200])
    assert values["oura_main_before_status"] == "available"


def test_duplicate_algorithms_tombstones_and_overlapping_sleep_are_safe():
    old = session("v1", -1, 7, "main", algorithm="v1")
    new = replace(old, key="v2", algorithm="v2", values={"total_sleep": 25000})
    assert normalize_sessions([old, new]) == (new,)
    assert normalize_sessions([new, replace(new, key="deleted", kind="discarded")]) == ()
    overlap = normalize_sessions([old, session("overlap", 6, 8)])
    assert all(s.status == "ambiguous sleep sessions" for s in overlap)


@pytest.mark.parametrize("provider", ["oura", "google_health"])
def test_two_main_sleeps_on_same_local_date_are_ambiguous(provider):
    data = normalize_sessions(
        [session("a", 0, 4, "main", provider), session("b", 12, 16, "main", provider)]
    )
    assert all(s.status == "ambiguous sleep sessions" for s in data)


@pytest.mark.parametrize(
    "metadata,kind",
    [
        ({"processed": True, "mainSleep": True}, "main"),
        ({"processed": True, "mainSleep": False, "nap": True}, "nap"),
        ({"processed": True, "mainSleep": True, "nap": True}, "nap"),
        ({"processed": True}, "other"),
    ],
)
def test_google_classification_flags_survive_storage(tmp_path, metadata, kind):
    raw = night(metadata=metadata)
    prepared = parsed([raw])
    assert prepared.sessions[0].kind == kind
    source = tmp_path / "collection.anki2"
    result = health.save_google_health_import(
        source, prepared, start="2026-09-01", end="2026-10-01"
    )
    assert result.snapshot.sessions[0].kind == kind
    assert result.snapshot.sessions[0].start == stamp(-3)
    assert result.snapshot.sessions[0].values["deep_sleep"] == 5400
    assert result.snapshot.sessions[0].values["rem_sleep"] == 7200


def test_google_partial_scope_preserves_sleep_and_session_only_corrections_change_revision(
    tmp_path,
):
    source = tmp_path / "collection.anki2"
    first = health.save_google_health_import(
        source, parsed([night()], [steps()]), start="2026-09-01", end="2026-10-01"
    )
    step_only = parse_google_health(
        [], [steps(count="100")], start="2026-09-01", end="2026-10-01", sleep_fetched=False
    )
    second = health.save_google_health_import(
        source, step_only, start="2026-09-01", end="2026-10-01"
    )
    assert second.snapshot.sessions == first.snapshot.sessions
    assert second.snapshot.days[DAY].values["google_health_total_sleep_seconds"] == 25200
    updated = parsed([night(metadata={"processed": True, "nap": True})], [steps(count="100")])
    third = health.save_google_health_import(source, updated, start="2026-09-01", end="2026-10-01")
    assert third.snapshot.revision != second.snapshot.revision
    assert third.snapshot.sessions[0].kind == "nap"
    assert third.snapshot.days[DAY].values == second.snapshot.days[DAY].values


def test_all_memory_metrics_receive_both_nap_timings_except_future_recall():
    report = replace(
        report_with_followup(),
        days=[daily(DAY)],
        study_windows={DAY.isoformat(): [stamp(10), stamp(18)]},
    )
    data = snapshot(session("pre", 8, 9), session("post", 20, 20.5))
    series = build_series(
        report, data, "oura", as_of=AS_OF, settings=replace(FAST, adjust_month=False)
    )
    assert "nap_duration/recall/after" not in series
    assert series["nap_duration/recall/before"].points[0].x == 60
    assert series["nap_deep_sleep/recall/before"].points[0].x == 60
    assert not series["nap_rem_sleep/recall/before"].points
    assert series["nap_rem_sleep/recall/before"].omitted["nap stage unavailable"] == 1
    assert "nap_deep_sleep/recall/after" not in series
    for metric in ("formation", "formation_short_term", "formation_long_term"):
        assert series[f"nap_duration/{metric}/before"].points[0].x == 60
        assert series[f"nap_duration/{metric}/after"].points[0].x == 30
        assert series[f"nap_gap/{metric}/after"].points[0].x == 2
        assert series[f"nap_deep_sleep/{metric}/before"].points[0].x == 60
        assert series[f"nap_deep_sleep/{metric}/after"].points[0].x == 30


def test_nap_stage_correlations_use_nap_days_and_publish_ranges():
    report, data = inputs(180)
    naps = []
    for i, day in enumerate(report.days):
        first = report.study_windows[day.day.isoformat()][0]
        naps.append(
            SleepSession(
                str(i) + "-nap",
                "oura",
                day.day.isoformat(),
                first - 7200,
                first - 3600,
                "nap",
                {
                    "total_sleep": 3600,
                    "deep_sleep": (15 + i % 11) * 60,
                    "rem_sleep": (10 + i % 7) * 60,
                },
            )
        )
    data = replace(data, sessions=(*data.sessions, *naps))
    series = build_series(report, data, "oura", as_of=AS_OF, settings=FAST)
    result = correlate(series["nap_deep_sleep/recall/before"], FAST)
    assert result.days == 180 and result.status == "exploratory estimate"
    assert result.low is not None and result.high > result.low
    missing = correlate(series["nap_light_sleep/recall/before"], FAST)
    assert missing.status == "insufficient data" and missing.days == 0


def test_nap_correlations_publish_uncertainty_and_require_both_presence_groups():
    xs = [float(i % 2) for i in range(180)]
    data = series_for(xs, [x + (i % 11) / 10 for i, x in enumerate(xs)])
    data.factor = "nap_recorded"
    result = correlate(data, FAST)
    assert result.status == "exploratory estimate"
    assert result.nap_days == 90 and result.no_recorded_nap_days == 90
    assert result.low is not None and result.high > result.low
    for point in data.points[18:]:
        object.__setattr__(point, "x", 0.0)
    result = correlate(data, FAST)
    assert result.status == "insufficient data" and "recorded-nap days" in result.reason


def test_daily_export_contains_derived_values_but_no_exact_session_or_review_times(tmp_path):
    report = replace(
        report_with_followup(),
        days=[daily(DAY)],
        study_windows={DAY.isoformat(): [stamp(10), stamp(18)]},
    )
    target = tmp_path / "daily.csv"
    export_csv(report, target, health_snapshot=snapshot(session("secret-session", 8, 9)))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert float(row["oura_nap_before_duration_seconds"]) == 3600
    assert row["oura_main_before_total_sleep_seconds"] == "28800"
    assert row["oura_nap_after_recorded"] == "0"
    assert row["oura_nap_before_deep_sleep_seconds"] == "3600"
    assert row["oura_nap_before_rem_sleep_seconds"] == ""
    text = target.read_text(encoding="utf-8-sig")
    assert "secret-session" not in text and str(stamp(10)) not in text
    assert "study_windows" not in text and "bedtime_start" not in text


def test_missing_daily_anchors_is_insufficient_not_a_date_proxy():
    report, data = inputs(180)
    report = replace(report, study_windows={})
    series = build_series(report, data, "oura", as_of=AS_OF, settings=FAST)
    result = correlate(series["nap_duration/recall/before"], FAST)
    assert result.status == "insufficient data"
    assert series["nap_duration/recall/before"].omitted["review times unavailable"] == 180
