import os
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("aqt")
from aqt.qt import QApplication, QWidget  # noqa: E402

from addon.backend import Report  # noqa: E402
from addon.metrics import Attempt, DailyResult, Settings, daily_results  # noqa: E402
from addon.storage import DailySettings, load_daily_settings, save_daily_settings  # noqa: E402
from addon.ui import DailySettingsDialog, MemoryReportDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(app):
    parent = QWidget()
    parent.addonManager = type("Manager", (), {"getConfig": lambda self, name: None})()
    widget = MemoryReportDialog(parent, "addon")
    yield widget
    widget.busy = False
    widget.close()
    app.processEvents()


def test_native_report_opens_on_full_history_with_optional_date_filter(dialog, app):
    monday, friday = date(2015, 9, 7), date(2026, 9, 11)
    days = daily_results(
        [Attempt(1, 1, monday, 3, None, True), Attempt(2, 1, friday, 1, 0.7, True)]
    )
    report = Report(
        days, "2026-10-07T00:00:00+00:00", "26.9.3", "abc123", 4, Settings(), 1, 0, 0, "CEST"
    )
    dialog.accept_report(report)
    dialog.show()
    app.processEvents()
    assert not dialog.date_fields.isVisible()
    assert dialog.date_range() == (None, None)
    assert dialog.table.rowCount() == 0  # Details are lazy, collapsed by default.
    dialog.metric_selector.setCurrentIndex(1)
    assert [day.day for day in dialog.chart.days] == [monday, friday]
    dialog.inspect_button.setChecked(True)
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(1, 1).text() == "-70.00 pp"
    dialog.select_day(date.fromisoformat(monday.isoformat()))  # A new date instance.
    assert dialog.day_selector.currentData() == monday
    assert "-70.00 pp" in dialog.summary.text()
    assert "No pending follow-ups" in dialog.completeness.text()
    assert dialog.export_button.isEnabled()
    dialog.filter_dates.setChecked(True)
    assert dialog.date_fields.isVisible()
    assert dialog.date_range() == (monday, friday)
    dialog.start_date.setDate(friday)
    assert dialog.table.rowCount() == 1
    dialog.metric_selector.setCurrentIndex(0)
    assert dialog.date_range() == (friday, friday)
    dialog.filter_dates.setChecked(False)
    assert not dialog.date_fields.isVisible()
    assert [day.day for day in dialog.chart.days] == [monday, friday]
    assert dialog.table.rowCount() == 2
    dialog.accept_report(
        Report(
            [],
            report.generated_at,
            report.anki_version,
            report.parameters_hash,
            4,
            Settings(),
            0,
            0,
            0,
            "CEST",
        )
    )
    assert dialog.table.rowCount() == 0
    assert "No eligible reviews" in dialog.summary.text()


def test_close_while_calculating_defers_qt_deletion(dialog, app):
    dialog.busy = True
    dialog.show()
    dialog.close()
    assert dialog.close_when_finished
    assert not dialog.isVisible()
    # The parent is still alive for the QueryOp completion callback.
    assert dialog.windowTitle()
    dialog.busy = False


def test_async_day_labels_fit_after_empty_window_was_shown(dialog, app):
    from aqt.qt import QStyle, QStyleOptionComboBox

    dialog.show()
    app.processEvents()
    # Match background delivery: populate only after the first layout/show.
    dialog.day_selector.addItem("2026-12-31", date(2026, 12, 31))
    app.processEvents()
    option = QStyleOptionComboBox()
    dialog.day_selector.initStyleOption(option)
    text_area = dialog.day_selector.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField
    )
    assert text_area.width() >= dialog.day_selector.fontMetrics().horizontalAdvance("2026-12-31")
    assert dialog.start_date.accessibleName() == "Start date"
    assert dialog.end_date.accessibleName() == "End date"


def test_small_report_keeps_footer_reachable_and_content_scrollable(dialog, app):
    dialog.resize(800, 480)
    dialog.show()
    dialog.inspect_button.setChecked(True)
    app.processEvents()
    assert dialog.height() <= 480
    assert dialog.content_scroll.verticalScrollBar().maximum() > 0
    # The footer stays outside the scrolling content and inside the window.
    assert dialog.retry_button.parent() is dialog
    assert dialog.layout().itemAt(1).geometry().bottom() < dialog.height()


def test_enlarged_insight_headers_fit_or_scroll(app, tmp_path):
    from types import SimpleNamespace

    from addon.insights_ui import InsightsDialog
    from addon.research import SharingConfig

    parent = QWidget()
    parent.col = SimpleNamespace(path=str(tmp_path / "collection.anki2"))
    widget = InsightsDialog(parent, "addon", SharingConfig())
    font = widget.table.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)
    widget.table.setFont(font)
    widget.resize(900, 500)
    widget.result = SimpleNamespace(phase_cohorts={}, phase_associations={}, visit_changes={})
    widget.show_phase_results(widget.result)
    widget.show()
    app.processEvents()
    widget.detail.setText("Uncertainty and matched-day explanation. " * 20)
    widget.note.setText("Phase comparisons use the same days and total sleep. " * 15)
    app.processEvents()
    for index in range(widget.table.columnCount()):
        text = widget.table.horizontalHeaderItem(index).text()
        widest = max(
            widget.table.fontMetrics().horizontalAdvance(line) for line in text.splitlines()
        )
        assert widget.table.columnWidth(index) >= widest + 16
    assert widget.height() <= 500
    assert widget.content_scroll.verticalScrollBar().maximum() > 0
    assert not widget.table.geometry().intersects(widget.detail.geometry())
    assert not widget.detail.geometry().intersects(widget.note.geometry())
    widget.close()
    app.processEvents()


def test_one_metric_selector_shows_band_scores_and_overall_followup_availability(dialog):
    stamp = int(datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
    day = date(2026, 8, 1)
    attempts = [
        Attempt(stamp, 1, day, 3, None, True),
        Attempt(stamp + 20 * 86_400_000, 1, date(2026, 8, 21), 3, 0.7, True),
        Attempt(stamp + 1, 2, day, 3, None, True),
        Attempt(stamp + 1 + 40 * 86_400_000, 2, date(2026, 9, 10), 1, 0.9, True),
    ]
    report = Report(
        daily_results(attempts),
        "2026-10-08T12:00:00+00:00",
        "26.9.3",
        "abc",
        4,
        Settings(),
        2,
        0,
        0,
        "CEST",
    )
    dialog.accept_report(report)
    dialog.metric_selector.setCurrentIndex(1)
    dialog.inspect_button.setChecked(True)
    assert dialog.table.item(2, 1).text() == "-30.00 pp"
    dialog.select_day(day)
    assert "-30.00 pp" in dialog.summary.text()
    dialog.metric_selector.setCurrentIndex(2)
    assert dialog.table.item(2, 1).text() == "+30.00 pp"
    assert dialog.table.item(2, 3).text() == "1"
    assert dialog.chart.metric == "formation_short_term"
    assert "2/2 follow-ups" in dialog.completeness.text()
    dialog.metric_selector.setCurrentIndex(3)
    assert dialog.table.item(2, 1).text() == "-90.00 pp"
    assert dialog.chart.metric == "formation_long_term"
    assert "2/2 follow-ups" in dialog.completeness.text()


def test_legacy_saved_report_shows_uncalculated_bands(dialog):
    from test_storage import report_with_followup

    report = report_with_followup()
    legacy = replace(
        report, days=[DailyResult(day.day, day.recall, day.formation) for day in report.days]
    )
    dialog.accept_report(legacy)
    dialog.metric_selector.setCurrentIndex(2)
    dialog.inspect_button.setChecked(True)
    assert dialog.table.item(1, 1).text() == "—"
    assert dialog.table.item(1, 3).text() == "—"
    assert dialog.table.item(1, 5).text() == "not calculated"


def test_formation_shows_pending_followups_for_selected_day(dialog):
    from datetime import timedelta

    from addon.metrics import Sample, summarize

    source = date(2026, 9, 17)
    samples = [Sample(0.8, 1, source + timedelta(days=i + 1)) for i in range(30)]
    overall = summarize(samples, cohort_count=1000, pending_count=970, formation=True)
    short = summarize(samples, cohort_count=30, formation=True)
    empty = summarize([], cohort_count=0)
    report = Report(
        [DailyResult(source, empty, overall, short, empty)],
        "2026-10-22T12:00:00+00:00",
        "26.9.3",
        "abc",
        4,
        Settings(),
        1000,
        0,
        0,
        "CEST",
    )
    dialog.accept_report(report)
    dialog.metric_selector.setCurrentIndex(2)
    assert dialog.day_selector.currentData() == source
    assert "better" in dialog.summary.text()
    assert "Provisional" in dialog.completeness.text()
    assert "30/1,000" in dialog.completeness.text()
    assert "3%" in dialog.completeness.text()
    assert "970 pending" in dialog.completeness.text()
    assert "100%" not in dialog.completeness.text()


def test_daily_settings_dialog_restores_and_saves_profile_preferences(app, tmp_path, monkeypatch):
    from addon import ui

    def file_job(task, success, failure):
        try:
            result = task()
        except Exception as error:
            failure(error)
        else:
            success(result)

    monkeypatch.setattr(ui, "run_file_job", file_job)
    collection_path = str(tmp_path / "profile" / "collection.anki2")
    previous = DailySettings(True, str(tmp_path / "first.csv"))
    save_daily_settings(collection_path, previous)
    parent = QWidget()
    dialog = DailySettingsDialog(parent, collection_path)
    assert dialog.enabled.isChecked()
    assert dialog.csv_input.text() == previous.csv_path
    dialog.csv_input.setText(str(tmp_path / "second.csv"))
    dialog.save()
    assert load_daily_settings(collection_path).csv_path == str(tmp_path / "second.csv")
    dialog.close()
