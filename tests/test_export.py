import csv
from dataclasses import replace
from datetime import date

from addon.backend import Report
from addon.export import export_csv
from addon.metrics import Attempt, DailyResult, Settings, daily_results


def test_csv_numeric_scores_blank_pending_metadata_and_source_date_filter(tmp_path):
    monday, friday = date(2026, 9, 7), date(2026, 9, 11)
    days = daily_results(
        [Attempt(1, 1, monday, 3, None, True), Attempt(2, 1, friday, 1, 0.7, True)]
    )
    report = Report(
        days, "2026-10-07T00:00:00+00:00", "26.9.3", "abc123", 4, Settings(), 1, 0, 0, "CEST"
    )
    path = tmp_path / "scores.csv"
    assert export_csv(report, path) == 2
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["recall_score_pp"] == ""
    assert float(rows[0]["formation_score_pp"]) == -70
    assert rows[1]["formation_score_pp"] == ""
    assert rows[1]["formation_pending_count"] == "1"
    assert rows[1]["formation_completeness"] == "provisional"
    assert rows[0]["formation_completeness"] == "no pending follow-ups"
    assert rows[0]["formation_uncertainty_low_pp"] == ""
    assert rows[0]["formation_uncertainty_high_pp"] == ""
    assert rows[0]["uncertainty_method"].endswith("formation_cv3_wilson_t_v2")
    assert rows[0]["parameters_hash"] == "abc123"
    assert rows[0]["rollover_hour"] == "4"
    assert export_csv(report, path, monday, monday) == 1
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["formation_count"] == "1"  # Friday is outside the displayed source dates.
    assert rows[0]["formation_short_term_count"] == "1"
    assert float(rows[0]["formation_short_term_score_pp"]) == -70
    assert rows[0]["formation_long_term_count"] == "0"
    assert rows[0]["formation_long_term_score_pp"] == ""
    assert rows[0]["formation_short_term_max_days"] == "30"
    export_csv(replace(report, calculation_version=4), path, monday, monday)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        legacy = next(csv.DictReader(handle))
    assert legacy["uncertainty_method"] == "approx95_model_floor_empirical_formation_day_cluster"


def test_legacy_csv_split_values_are_not_reported_as_zero(tmp_path):
    from test_storage import report_with_followup

    report = report_with_followup()
    legacy = replace(
        report, days=[DailyResult(day.day, day.recall, day.formation) for day in report.days]
    )
    path = tmp_path / "legacy.csv"
    export_csv(legacy, path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["formation_short_term_count"] == ""
    assert row["formation_short_term_score_pp"] == ""
    assert row["formation_short_term_status"] == "not calculated"
