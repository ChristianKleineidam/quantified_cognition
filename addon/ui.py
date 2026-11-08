"""Native Qt report, available from Anki's Tools menu."""

from __future__ import annotations

import time
from datetime import date

from aqt import mw
from aqt.qt import (
    QAction,
    QCheckBox,
    QColor,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPainter,
    QPen,
    QPointF,
    QPushButton,
    QRectF,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo, showWarning

from .backend import Report, UpdateCancelled
from .background import run_file_job
from .daily import DailyUpdater
from .export import export_csv_atomic
from .health import load_health
from .metrics import FORMATION_METRICS, SHORT_TERM_MAX_DAYS, DailyResult, Metric, Settings, anki_day
from .research import SharingConfig, finish_csv_exposure, prepare_csv_exposure, sharing_state
from .sharing_ui import sharing_gate
from .storage import (
    DailySettings,
    UpdateResult,
    database_path,
    load_daily_settings,
    load_report,
    retry_fixed_csv,
    save_daily_settings,
)
from .update_job import UpdateJob, UpdateRequest


def _score(metric: Metric | None) -> str:
    return "—" if metric is None or metric.score_pp is None else f"{metric.score_pp:+.2f} pp"


def _uncertainty(metric: Metric | None) -> str:
    return (
        "—"
        if metric is None or metric.uncertainty_low_pp is None
        else (f"{metric.uncertainty_low_pp:+.2f} to {metric.uncertainty_high_pp:+.2f}")
    )


class ScoreChart(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.days: list[DailyResult] = []
        self.metric = "recall"
        self.filtered = False
        self.selected_day: date | None = None
        self.plot_points: list[tuple[date, QPointF]] = []
        self.on_day_selected = None
        self.setMinimumHeight(210)
        self.setAccessibleName("Daily memory scores chart")
        self.setToolTip(
            "Click a day to inspect its score, count and uncertainty. "
            "Hollow formation points still have pending follow-ups."
        )

    def mousePressEvent(self, event) -> None:
        if self.plot_points and self.on_day_selected is not None:
            clicked = event.position()
            day, _ = min(
                self.plot_points,
                key=lambda item: (item[1].x() - clicked.x()) ** 2
                + (item[1].y() - clicked.y()) ** 2,
            )
            self.on_day_selected(day)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().color(self.foregroundRole())
        painter.setPen(text_color)
        area = QRectF(56, 28, max(1, self.width() - 78), max(1, self.height() - 65))
        measured = [
            (day, metric) for day in self.days
            if (metric := getattr(day, self.metric)) is not None and metric.score_pp is not None
        ]
        self.plot_points = []
        if not measured:
            painter.drawText(
                area, Qt.AlignmentFlag.AlignCenter, "No evaluated scores yet"
            )
            return
        limit = max(abs(metric.score_pp) for _, metric in measured)
        selected = next((metric for day, metric in measured if day.day == self.selected_day), None)
        if selected is not None and selected.uncertainty_low_pp is not None:
            limit = max(limit, abs(selected.uncertainty_low_pp), abs(selected.uncertainty_high_pp))
        span = min(100.0, max(10.0, limit * 1.1))
        first_day, last_day = self.days[0].day, self.days[-1].day
        day_span = max(1, (last_day - first_day).days)

        def point(day: date, value: float) -> QPointF:
            return QPointF(
                area.left() + (day - first_day).days / day_span * area.width(),
                area.center().y() - value / span * area.height() / 2,
            )

        muted = QColor(text_color)
        muted.setAlpha(65)
        for value in (-span, -span / 2, 0.0, span / 2, span):
            y = point(first_day, value).y()
            painter.setPen(QPen(muted, 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(text_color)
            painter.drawText(
                QRectF(0, y - 10, 49, 20), Qt.AlignmentFlag.AlignRight,
                f"{value:+.0f}" if value else "0",
            )
        painter.drawText(
            QRectF(area.left(), area.center().y() - 21, area.width() - 4, 20),
            Qt.AlignmentFlag.AlignRight, "FSRS prediction",
        )
        formation = self.metric in FORMATION_METRICS
        color = QColor("#c27118" if formation else "#1683b5")
        soft_color = QColor(color)
        soft_color.setAlpha(140)
        painter.setPen(text_color)
        painter.drawText(
            58, 18, "Daily scores (pp)" if self.filtered else "Full history · daily scores (pp)"
        )
        for day, metric in measured:
            current = point(day.day, metric.score_pp)
            self.plot_points.append((day.day, current))
            provisional = formation and day.formation.pending_count > 0
            painter.setPen(QPen(soft_color, 1) if provisional else Qt.PenStyle.NoPen)
            painter.setBrush(Qt.BrushStyle.NoBrush if provisional else soft_color)
            painter.drawEllipse(current, 2.5, 2.5)
        if selected is not None:
            current = point(self.selected_day, selected.score_pp)
            painter.setPen(QPen(color, 1.5))
            if selected.uncertainty_low_pp is not None:
                low = point(self.selected_day, max(-span, selected.uncertainty_low_pp))
                high = point(self.selected_day, min(span, selected.uncertainty_high_pp))
                painter.drawLine(low, high)
                for end in (low, high):
                    painter.drawLine(end - QPointF(4, 0), end + QPointF(4, 0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(current, 6, 6)
        painter.setPen(text_color)
        if last_day.year - first_day.year >= 2:
            step = max(1, (last_day.year - first_day.year) // max(1, int(area.width() / 85)))
            for year in range(first_day.year, last_day.year + 1, step):
                tick = point(max(first_day, date(year, 1, 1)), 0).x()
                painter.drawText(
                    QRectF(tick - 22, area.bottom() + 8, 44, 24),
                    Qt.AlignmentFlag.AlignHCenter, str(year),
                )
            return
        painter.drawText(QRectF(area.left(), area.bottom() + 8, 140, 24), first_day.isoformat())
        painter.drawText(
            QRectF(area.right() - 140, area.bottom() + 8, 140, 24),
            Qt.AlignmentFlag.AlignRight,
            last_day.isoformat(),
        )


class CsvExportOptions(QWidget):
    def __init__(self, parent, collection_path: str, on_saved):
        super().__init__(parent)
        self.collection_path = collection_path
        self.on_saved = on_saved
        self.settings = DailySettings()
        self.disposed = False
        self.destroyed.connect(lambda: setattr(self, "disposed", True))
        layout = QVBoxLayout(self)
        self.scope = QLabel()
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.save_copy = QPushButton("Save a copy…")
        layout.addWidget(self.save_copy, alignment=Qt.AlignmentFlag.AlignLeft)
        self.enabled = QCheckBox("Keep a CSV updated automatically")
        layout.addWidget(self.enabled)
        explanation = QLabel(
            "Anki keeps the same file updated with all measures across your full history."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.path_row = QWidget()
        path_row = QHBoxLayout(self.path_row)
        path_row.setContentsMargins(0, 0, 0, 0)
        self.csv_input = QLineEdit()
        self.csv_input.setAccessibleName("Automatic CSV file location")
        self.csv_input.setReadOnly(True)
        path_row.addWidget(self.csv_input)
        self.browse_button = QPushButton("Change location…")
        self.browse_button.clicked.connect(self.browse)
        path_row.addWidget(self.browse_button)
        layout.addWidget(self.path_row)
        self.path_row.hide()
        self.enabled.toggled.connect(self.toggle_automatic)
        self.set_file_busy(True)
        run_file_job(lambda: load_daily_settings(collection_path), self.loaded, self.file_failed)

    def set_file_busy(self, busy: bool) -> None:
        self.file_busy = busy
        self.enabled.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)

    def loaded(self, settings: DailySettings) -> None:
        if self.disposed:
            return
        self.settings = settings
        self.enabled.blockSignals(True)
        self.enabled.setChecked(settings.enabled)
        self.enabled.blockSignals(False)
        self.path_row.setVisible(settings.enabled)
        self.csv_input.setText(settings.csv_path)
        self.set_file_busy(False)

    def file_failed(self, error: Exception) -> None:
        if self.disposed:
            return
        self.loaded(self.settings)
        showWarning(str(error), parent=self)

    def choose_location(self) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose the automatic CSV file",
            self.settings.csv_path or "anki-memory-scores.csv",
            "CSV files (*.csv)",
        )
        return path + ".csv" if path and not path.lower().endswith(".csv") else path

    def toggle_automatic(self, enabled: bool) -> None:
        path = self.settings.csv_path
        if enabled and not path:
            path = self.choose_location()
            if not path:
                self.loaded(self.settings)
                return
        self.save(DailySettings(enabled, path))

    def browse(self) -> None:
        path = self.choose_location()
        if path:
            self.save(DailySettings(True, path))

    def save(self, settings: DailySettings) -> None:
        if self.file_busy:
            return
        self.path_row.setVisible(settings.enabled)
        self.csv_input.setText(settings.csv_path)
        self.set_file_busy(True)
        on_saved = self.on_saved

        def saved(_result) -> None:
            self.loaded(settings)
            on_saved()

        run_file_job(
            lambda: save_daily_settings(self.collection_path, settings), saved, self.file_failed
        )


class MemoryReportDialog(QDialog):
    def __init__(self, parent, addon_name: str, sharing: SharingConfig | None = None):
        super().__init__(parent)
        self.addon_name = addon_name
        self.sharing_config = sharing
        self.exposure_recorded = False
        parent_collection = getattr(parent, "col", None)
        self.collection_path = parent_collection.path if parent_collection is not None else None
        self.report: Report | None = None
        self.update_job: UpdateJob | None = None
        self.busy = False
        self.close_when_finished = False
        self.disposed = False
        self.destroyed.connect(lambda: setattr(self, "disposed", True))
        self.setWindowTitle("Quantified Cognition · Memory")
        from .ui_layout import measurement_limitations, scrollable_report

        layout, outer, self.content_scroll = scrollable_report(
            self, 960, 710, "Memory report content"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.days: list[DailyResult] = []
        self.csv_error: str | None = None
        layout.setSpacing(12)
        title = QLabel("Your memory")
        font = title.font()
        font.setPointSize(font.pointSize() + 7)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        explanation = QLabel(
            "How your recall compares with FSRS predictions. Positive scores mean "
            "you recalled more than predicted; negative scores mean less."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        controls = QHBoxLayout()
        self.metric_selector = QComboBox()
        for label, key in (
            ("Recall · overall", "recall"),
            (f"Recall · last review ≤{SHORT_TERM_MAX_DAYS} days ago", "recall_short_term"),
            (f"Recall · last review >{SHORT_TERM_MAX_DAYS} days ago", "recall_long_term"),
            ("Memory formation · overall", "formation"),
            (f"Memory formation · up to {SHORT_TERM_MAX_DAYS} days", "formation_short_term"),
            (f"Memory formation · over {SHORT_TERM_MAX_DAYS} days", "formation_long_term"),
        ):
            self.metric_selector.addItem(label, key)
        self.metric_selector.setAccessibleName("Memory metric")
        controls.addWidget(self.metric_selector)
        self.range_selector = QComboBox()
        self.range_selector.setAccessibleName("Date range")
        self.range_selector.addItems(["All history", "Custom dates"])
        self.range_selector.setEnabled(False)
        controls.addWidget(self.range_selector)
        controls.addStretch()
        self.export_button = QPushButton("Export CSV…")
        self.export_button.setCheckable(True)
        self.export_button.toggled.connect(self.toggle_export_options)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)

        self.date_fields = QWidget()
        dates = QHBoxLayout(self.date_fields)
        dates.setContentsMargins(0, 0, 0, 0)
        self.start_date, self.end_date = QDateEdit(), QDateEdit()
        for label, name, editor in (
            ("From", "Start date", self.start_date),
            ("to", "End date", self.end_date),
        ):
            date_label = QLabel(label)
            date_label.setBuddy(editor)
            dates.addWidget(date_label)
            editor.setAccessibleName(name)
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            dates.addWidget(editor)
            editor.dateChanged.connect(self.render_report)
        dates.addStretch()
        self.date_fields.hide()
        layout.addWidget(self.date_fields)
        self.range_selector.currentIndexChanged.connect(
            lambda index: self.date_fields.setVisible(index == 1)
        )
        self.range_selector.currentIndexChanged.connect(self.render_report)
        self.export_container = QVBoxLayout()
        self.export_options: CsvExportOptions | None = None
        layout.addLayout(self.export_container)

        self.context = QLabel()
        self.context.setWordWrap(True)
        self.chart = ScoreChart(self)
        self.chart.on_day_selected = self.select_day
        layout.addWidget(self.chart, 1)

        inspector = QHBoxLayout()
        self.day_label = QLabel("Anki day")
        inspector.addWidget(self.day_label)
        self.day_selector = QComboBox()
        self.day_selector.setAccessibleName("Inspect Anki day")
        # The list is filled after the window is shown. The default policy sizes
        # only on first show and clips the day from dates loaded asynchronously.
        self.day_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.day_selector.setMinimumContentsLength(10)
        self.day_selector.currentIndexChanged.connect(self.render_selection)
        inspector.addWidget(self.day_selector)
        inspector.addStretch()
        self.inspect_button = QPushButton("Daily table")
        self.inspect_button.setCheckable(True)
        self.inspect_button.toggled.connect(self.toggle_table)
        inspector.addWidget(self.inspect_button)
        layout.addLayout(inspector)
        self.summary = QLabel("Loading memory scores…")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        score_font = self.summary.font()
        score_font.setPointSize(score_font.pointSize() + 6)
        score_font.setBold(True)
        self.summary.setFont(score_font)
        layout.addWidget(self.summary)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.details)
        self.completeness = QLabel()
        self.completeness.setWordWrap(True)
        self.completeness.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.completeness)
        self.table = QTableWidget(0, 7)
        self.table.setAccessibleName("Daily memory data")
        self.table.setHorizontalHeaderLabels(
            [
                "Anki day",
                "Score (pp)",
                "95% range (pp)",
                "Evaluated",
                "Outcome days",
                "Comparison",
                "Follow-up availability",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(210)
        self.table.cellClicked.connect(
            lambda row, _col: self.select_day(self.days[-1 - row].day)
        )
        self.table.hide()
        layout.addWidget(self.table, 1)
        limits_row = QHBoxLayout()
        limits = QLabel(
            "Experimental estimates. Use other evidence alongside them for major "
            "or medical decisions."
        )
        limits.setWordWrap(True)
        limits_row.addWidget(limits, 1)
        methods = QPushButton("Methods && limitations")
        methods.setCheckable(True)
        limits_row.addWidget(methods)
        layout.addLayout(limits_row)
        self.measurement_note = measurement_limitations(self)
        self.measurement_note.hide()
        self.context.hide()
        layout.addWidget(self.context)
        layout.addWidget(self.measurement_note)
        methods.toggled.connect(self.context.setVisible)
        methods.toggled.connect(self.measurement_note.setVisible)
        self.metadata = QLabel()
        self.metadata.setWordWrap(True)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.metadata)
        self.export_status = QLabel()
        self.export_status.setWordWrap(True)
        self.export_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.export_status)
        close_row = QHBoxLayout()
        insights_button = QPushButton("Personal insights…")
        insights_button.clicked.connect(
            lambda _checked=False: open_personal_insights(self.addon_name)
        )
        close_row.addWidget(insights_button)
        oura_button = QPushButton("Health data…")
        oura_button.clicked.connect(lambda _checked=False: open_oura_data(self.addon_name))
        close_row.addWidget(oura_button)
        sharing_button = QPushButton("Research sharing…")
        sharing_button.clicked.connect(
            lambda _checked=False: open_research_sharing(self.addon_name)
        )
        close_row.addWidget(sharing_button)
        self.retry_button = QPushButton("Retry CSV export")
        self.retry_button.clicked.connect(self.retry_csv)
        self.retry_button.hide()
        close_row.addWidget(self.retry_button)
        close_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        outer.addLayout(close_row)
        self.metric_selector.currentIndexChanged.connect(self.render_report)

    def metric_key(self) -> str:
        return self.metric_selector.currentData()

    def toggle_table(self, checked: bool) -> None:
        self.table.setVisible(checked)
        if checked:
            self.render_table()

    def toggle_export_options(self, checked: bool) -> None:
        if self.export_options is None and checked and self.collection_path is not None:
            def update_automatic_csv():
                if (
                    _daily_updater is not None
                    and mw is not None
                    and mw.col is not None
                    and mw.col.path == self.collection_path
                ):
                    _daily_updater.check(force=True)

            self.export_options = CsvExportOptions(self, self.collection_path, update_automatic_csv)
            self.export_options.save_copy.clicked.connect(self.export)
            self.export_container.addWidget(self.export_options)
        if self.export_options is not None:
            self.export_options.setVisible(checked)
            self.update_export_options()

    def update_export_options(self) -> None:
        if self.export_options is not None:
            self.export_options.scope.setText(
                "All measures across the selected dates."
                if self.range_selector.currentIndex() == 1
                else "All measures across your full history."
            )
            self.export_options.save_copy.setEnabled(self.report is not None and not self.busy)

    def refresh(self) -> None:
        if self.busy or mw is None or mw.col is None:
            return
        if self.collection_path is not None and mw.col.path != self.collection_path:
            self.close()
            return
        self.busy = True
        self.update_export_options()
        if self.report is None:
            self.summary.setText("Calculating memory scores…")
        collection_path = mw.col.path
        config_manager = mw.addonManager

        def prepare(rollover):
            config = config_manager.getConfig(self.addon_name)
            return UpdateRequest(
                Settings.from_config(config),
                load_daily_settings(collection_path),
                anki_day(time.time(), rollover),
                SharingConfig.from_config(config),
            )

        self.update_job = UpdateJob(mw, self, prepare, self.accept_update, self.failed)
        self.update_job.start()

    def load_and_refresh(self) -> None:
        if self.busy or mw is None or mw.col is None:
            return
        collection_path = mw.col.path
        self.busy = True
        self.update_export_options()
        self.summary.setText("Loading saved memory scores…")

        def loaded(report: Report | None) -> None:
            if self.disposed:
                return
            self.busy = False
            if (
                self.close_when_finished
                or mw is None
                or mw.col is None
                or mw.col.path != collection_path
            ):
                self.close()
                return
            if report is not None:
                self.accept_report(report, after_display=self.refresh)
            else:
                self.refresh()

        def missing_or_damaged(_error: Exception) -> None:
            # The subsequent calculation also checks the storage schema and
            # reports an error if it cannot safely update the stored results.
            loaded(None)

        run_file_job(
            lambda: load_report(database_path(collection_path)), loaded, missing_or_damaged
        )

    def accept_report(self, report: Report, *, after_display=None) -> None:
        if self.disposed:
            return
        self.busy = False
        if self.close_when_finished:
            self.close()
            return
        if (
            self.sharing_config is not None
            and self.collection_path is not None
            and report.days
            and not self.exposure_recorded
        ):
            self.busy = True
            self.update_export_options()
            self.summary.setText("Preparing your results…")

            def recorded(_snapshot) -> None:
                if self.disposed:
                    return
                self.exposure_recorded = True
                self.accept_report(report, after_display=after_display)

            run_file_job(
                lambda: sharing_state(
                    self.collection_path,
                    self.sharing_config,
                    event_type="results_exposed",
                    surface="report",
                ),
                recorded,
                self.failed,
            )
            return
        if report.days and not self.range_selector.isEnabled():
            self.start_date.setDate(report.days[0].day)
            self.end_date.setDate(report.days[-1].day)
            self.range_selector.setEnabled(True)
        self.report = report
        self.render_report()
        if after_display is not None:
            after_display()

    def failed(self, error: Exception) -> None:
        if self.disposed:
            return
        self.busy = False
        self.update_job = None
        if self.close_when_finished or (
            self.collection_path is not None
            and (mw is None or mw.col is None or mw.col.path != self.collection_path)
        ):
            self.close()
            return
        self.update_export_options()
        if isinstance(error, UpdateCancelled):
            self.render_report()
            return
        self.summary.setText("Calculation failed. Any previously shown data has not been updated.")
        showWarning(str(error), parent=self)

    def closeEvent(self, event) -> None:
        # QueryOp's callbacks still need their Qt parent. Defer deletion when the
        # user closes the window while a background calculation is running.
        if self.busy:
            if self.update_job is not None:
                self.update_job.cancel()
            self.close_when_finished = True
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self) -> None:
        if self.busy:
            if self.update_job is not None:
                self.update_job.cancel()
            self.close_when_finished = True
            self.hide()
            return
        super().reject()

    def date_range(self) -> tuple[date | None, date | None]:
        if self.range_selector.currentIndex() == 0:
            return None, None
        return self.start_date.date().toPyDate(), self.end_date.date().toPyDate()

    def render_report(self, *_args) -> None:
        if self.report is None:
            return
        previous_day = self.day_selector.currentData()
        start, end = self.date_range()
        days = self.report.days
        if start is not None and end is not None:
            days = [day for day in days if start <= day.day <= end]
        self.days = days
        formation = self.metric_key() in FORMATION_METRICS
        self.day_label.setText("Study day" if formation else "Review day")
        self.context.setText(
            "Formation belongs to the study day and is measured at the next review. "
            "Short and long term refer to the time until that review."
            if formation
            else "Recall uses the first attempt on each card per Anki day. Delay bands use "
            "the actual time since the previous review."
        )
        self.chart.days = days
        self.chart.metric = self.metric_key()
        self.chart.filtered = self.range_selector.currentIndex() == 1
        self.update_export_options()
        self.day_selector.blockSignals(True)
        self.day_selector.clear()
        for day in reversed(days):
            self.day_selector.addItem(day.day.isoformat(), day.day)
        preferred = self.day_selector.findText(previous_day.isoformat()) if previous_day else -1
        if preferred < 0:
            # Prefer the latest day with evaluated data, rather than an empty headline.
            preferred = next(
                (
                    i
                    for i, day in enumerate(reversed(days))
                    if getattr(day, self.metric_key()) is not None
                    and getattr(day, self.metric_key()).count > 0
                ),
                0,
            )
        if days:
            self.day_selector.setCurrentIndex(preferred)
        self.day_selector.blockSignals(False)
        self.day_selector.setEnabled(bool(days))
        self.render_selection()
        if self.inspect_button.isChecked():
            self.render_table()
        else:
            self.table.setRowCount(0)
        report = self.report
        self.metadata.setText(
            f"{report.analyzed_cards:,} cards · Analysis saved "
            f"{report.generated_at.replace('T', ' ')[:16]} UTC"
            + (
                f" · Includes {report.reconstructed_deleted_cards:,} deleted cards"
                if report.reconstructed_deleted_cards
                else (
                    f" · {report.deleted_card_reviews:,} deleted-card reviews skipped"
                    if report.deleted_card_reviews
                    else ""
                )
            )
        )
        self.metadata.setToolTip(
            f"Anki {report.anki_version} · Day boundary {report.rollover_hour:02d}:00 "
            f"({report.timezone_name}) · FSRS {report.parameters_hash[:12]}\n"
            f"Minimums: {report.settings.minimum_reviews} observations and "
            f"{report.settings.minimum_formation_outcome_days} formation outcome days. "
            f"Excluded {report.excluded_preview_reviews} preview/cram answers and "
            f"{report.deleted_card_reviews} reviews of deleted cards.\n"
            f"Deleted histories: {report.deleted_history_status}. "
            f"Included {report.reconstructed_deleted_reviews:,} review records from "
            f"{report.reconstructed_deleted_cards:,} deleted cards."
        )

    def select_day(self, day: date) -> None:
        # QVariant's comparison of Python date objects can use identity. Match
        # the stable label so a chart click/refresh preserves the selected day.
        index = self.day_selector.findText(day.isoformat())
        if index >= 0:
            self.day_selector.setCurrentIndex(index)

    def render_selection(self, *_args) -> None:
        selected = next(
            (day for day in self.days if day.day == self.day_selector.currentData()), None
        )
        self.chart.selected_day = selected.day if selected else None
        self.chart.update()
        if selected is None:
            self.summary.setText(
                "No eligible reviews in this date range."
                if self.range_selector.currentIndex() == 1
                else "No eligible reviews in your study history."
            )
            self.details.clear()
            self.completeness.clear()
            return
        key = self.metric_key()
        metric = getattr(selected, key)
        formation = key in FORMATION_METRICS
        if metric is None:
            self.summary.setText("— · not calculated")
            self.details.setText("Update to calculate this delay band from your review history.")
        else:
            score = metric.score_pp
            if score is None:
                self.summary.setText("No evaluated reviews")
            elif score == 0:
                self.summary.setText("Matches the predicted recall rate")
            else:
                direction = "above" if score > 0 else "below"
                self.summary.setText(f"{abs(score):.1f} percentage points {direction} prediction")
            comparison = (
                "No clear difference" if metric.status == "on par" else metric.status.capitalize()
            )
            if metric.count:
                self.details.setText(
                    f"{comparison} · Approx. 95% range: {_uncertainty(metric)} pp · "
                    f"{metric.count:,} evaluated"
                    + (
                        f" across {metric.outcome_days:,} outcome days"
                        if formation
                        else ""
                    )
                    + f"\nRecalled {metric.recalled_percent:.1f}% · "
                    f"FSRS predicted {metric.predicted_percent:.1f}%"
                    + (
                        "\nRange unavailable: follow-ups are concentrated on too few outcome days."
                        if formation and metric.uncertainty_low_pp is None
                        else ""
                    )
                )
            else:
                self.details.setText(comparison)
            self.details.setToolTip(
                "The approximate range accounts for model and sample variability. Formation "
                "groups outcomes by review day and corrects for few or unevenly represented days. "
                "‘On par’ means no clear difference; it does not establish equivalence. "
                "The range describes evaluated follow-ups, "
                "not the unknown outcomes of pending cards."
            )
        if formation:
            cohort = selected.formation
            self.completeness.setText(
                f"{cohort.completeness.capitalize()} · {cohort.count:,}/{cohort.cohort_count:,} "
                f"follow-ups evaluated ({cohort.coverage_percent:.0f}%) · "
                f"{cohort.pending_count:,} pending · {cohort.excluded_count:,} excluded"
                + (
                    "\nPending outcomes may change the score. "
                    "This availability applies to the whole study-day cohort."
                    if cohort.pending_count
                    else ""
                )
            )
        elif key == "recall" and selected.recall.excluded_count:
            self.completeness.setText(
                f"{selected.recall.excluded_count:,} cards excluded without a "
                "pre-review prediction."
            )
        else:
            self.completeness.clear()

    def render_table(self) -> None:
        key = self.metric_key()
        self.table.setRowCount(len(self.days))
        for row, day in enumerate(reversed(self.days)):
            metric = getattr(day, key)
            cohort = day.formation
            availability = (
                f"{cohort.count}/{cohort.cohort_count} evaluated; "
                f"{cohort.pending_count} pending; {cohort.excluded_count} excluded"
                if key in FORMATION_METRICS
                else f"{day.recall.excluded_count} excluded" if key == "recall" else "—"
            )
            values = [
                day.day.isoformat(),
                _score(metric),
                _uncertainty(metric),
                str(metric.count) if metric is not None else "—",
                str(metric.outcome_days) if metric is not None else "—",
                metric.status if metric is not None else "not calculated",
                availability,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def accept_update(self, result: UpdateResult) -> None:
        if self.disposed:
            return
        self.busy = False
        self.update_job = None
        if self.close_when_finished or (
            self.collection_path is not None
            and (mw is None or mw.col is None or mw.col.path != self.collection_path)
        ):
            self.close()
            return
        self.csv_error = result.csv_error
        if result.recalculated and self.collection_path is not None:
            notify_insights_updated(self.collection_path)
        self.export_status.setText(
            f"Scores saved. Automatic CSV export failed: {result.csv_error}"
            if result.csv_error else ""
        )
        self.retry_button.setVisible(result.csv_error is not None)
        self.retry_button.setEnabled(True)
        self.accept_report(result.report)

    def retry_csv(self) -> None:
        if self.busy or self.collection_path is None:
            return
        collection_path = self.collection_path
        self.busy = True
        self.update_export_options()
        self.retry_button.setEnabled(False)
        self.export_status.setText("Retrying the fixed CSV export…")

        def retry():
            return retry_fixed_csv(collection_path, self.sharing_config)

        def failed(error):
            self.busy = False
            if self.disposed:
                return
            if self.close_when_finished:
                self.close()
                return
            self.update_export_options()
            self.retry_button.setEnabled(True)
            self.export_status.setText(f"Scores saved. Automatic CSV export failed: {error}")

        run_file_job(retry, self.accept_update, failed)

    def export(self) -> None:
        if self.busy or self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export daily memory scores", "anki-memory-scores.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        report = self.report
        start, end = self.date_range()
        self.busy = True
        self.update_export_options()

        def finished() -> bool:
            if self.disposed:
                return False
            self.busy = False
            if self.close_when_finished:
                self.close()
                return False
            self.render_report()
            return True

        def exported(count: int) -> None:
            if not finished():
                return
            showInfo(f"Exported {count} day(s) to:\n{path}", parent=self)

        def export_failed(error: Exception) -> None:
            if finished():
                showWarning(f"Could not save the CSV: {error}", parent=self)

        def write_csv():
            if self.collection_path is None:
                return export_csv_atomic(report, path, start, end)
            health = load_health(self.collection_path)
            has_rows = any(
                (start is None or day >= start) and (end is None or day <= end)
                for day in {day.day for day in report.days} | health.days.keys()
            )
            if has_rows:
                _snapshot, token, metadata = prepare_csv_exposure(
                    self.collection_path, self.sharing_config
                )
            else:
                token = None
                metadata = sharing_state(self.collection_path, self.sharing_config).metadata()
            try:
                count = export_csv_atomic(
                    report, path, start, end, research_metadata=metadata, health_snapshot=health
                )
            except Exception:
                finish_csv_exposure(self.collection_path, self.sharing_config, token, success=False)
                raise
            finish_csv_exposure(self.collection_path, self.sharing_config, token, success=True)
            return count

        run_file_job(write_csv, exported, export_failed)


_dialog: MemoryReportDialog | None = None
_daily_updater: DailyUpdater | None = None
_oura_dialog = None
_insights_dialog = None
_network_updater = None


def notify_insights_updated(source):
    if (
        _insights_dialog is not None
        and not _insights_dialog.disposed
        and _insights_dialog.source == source
    ):
        _insights_dialog.inputs_changed()


def open_personal_insights(addon_name: str, provider=None) -> None:
    if mw is None or mw.col is None:
        showWarning("Open an Anki profile before viewing personal insights.")
        return

    def ready(config):
        global _insights_dialog
        from .insights_ui import InsightsDialog

        if _insights_dialog is not None:
            _insights_dialog.close()
        _insights_dialog = InsightsDialog(mw, addon_name, config, provider)
        opened = _insights_dialog

        def clear(_object=None):
            global _insights_dialog
            if _insights_dialog is opened:
                _insights_dialog = None

        opened.destroyed.connect(clear)
        opened.show()
        opened.refresh_memory()

    sharing_gate(mw, addon_name, ready)


def open_oura_data(addon_name: str) -> None:
    if mw is None or mw.col is None:
        showWarning("Open an Anki profile before importing health data.")
        return

    def ready(config):
        global _oura_dialog
        from .oura_ui import OuraDialog

        if _oura_dialog is not None:
            _oura_dialog.close()
        from .service import ServiceConfig

        services = ServiceConfig.from_config(mw.addonManager.getConfig(addon_name))
        _oura_dialog = OuraDialog(mw, config, services)
        _oura_dialog.addon_name = addon_name
        opened = _oura_dialog

        def clear(_object=None):
            global _oura_dialog
            if _oura_dialog is opened:
                _oura_dialog = None

        _oura_dialog.destroyed.connect(clear)
        _oura_dialog.show()
        _oura_dialog.load()

    sharing_gate(mw, addon_name, ready)


def open_research_sharing(addon_name: str) -> None:
    if mw is not None:
        sharing_gate(mw, addon_name, lambda _config: None, force=True)


def register(addon_name: str) -> None:
    global _daily_updater, _network_updater
    if mw is None:
        return

    def show_report(config: SharingConfig) -> None:
        global _dialog
        if mw.col is None:
            showWarning("Open an Anki profile before viewing memory scores.")
            return
        if _dialog is not None and _dialog.collection_path != mw.col.path:
            # An older profile's file worker may still be finishing. Its window
            # stays hidden until completion and must not replace the new window.
            _dialog.close()
            _dialog = None
        if _dialog is not None:
            _dialog.close_when_finished = False
            _dialog.show()
            _dialog.raise_()
            _dialog.activateWindow()
            return
        _dialog = MemoryReportDialog(mw, addon_name, sharing=config)
        opened_dialog = _dialog
        _dialog.destroyed.connect(lambda _object=None: clear_dialog(opened_dialog))
        _dialog.show()
        _dialog.load_and_refresh()

    def open_report() -> None:
        if mw.col is None:
            showWarning("Open an Anki profile before viewing memory scores.")
            return
        sharing_gate(mw, addon_name, show_report)

    def clear_dialog(closed_dialog) -> None:
        global _dialog
        if _dialog is closed_dialog:
            _dialog = None

    action = QAction("Quantified Cognition…", mw)
    action.triggered.connect(open_report)
    mw.form.menuTools.addAction(action)

    def daily_report_updated(result: UpdateResult) -> None:
        delivered = False
        if (
            _dialog is not None
            and not _dialog.busy
            and mw.col is not None
            and _dialog.collection_path == mw.col.path
        ):
            _dialog.accept_update(result)
            delivered = True
        if not delivered and result.recalculated and mw.col is not None:
            notify_insights_updated(mw.col.path)

    def offer_sharing() -> None:
        if mw.col is not None:
            sharing_gate(mw, addon_name, lambda _config: _daily_updater.check(force=True))

    def daily_error(error: str | None) -> None:
        if (
            _dialog is not None
            and not _dialog.busy
            and mw.col is not None
            and _dialog.collection_path == mw.col.path
        ):
            if error:
                _dialog.export_status.setText(
                    f"Automatic update: {error} Retrying in one hour while this profile is open."
                )
            elif _dialog.export_status.text().startswith("Automatic update:"):
                _dialog.export_status.clear()

    _daily_updater = DailyUpdater(mw, addon_name, daily_report_updated, offer_sharing, daily_error)
    from .network import NetworkUpdater

    _network_updater = NetworkUpdater(mw, addon_name)
    # A modeless report must not survive a profile switch with another user's data.
    from aqt import gui_hooks

    gui_hooks.profile_did_open.append(offer_sharing)

    def close_report() -> None:
        if _dialog is not None:
            _dialog.close()
        if _oura_dialog is not None:
            _oura_dialog.close()
        if _insights_dialog is not None:
            _insights_dialog.close()

    gui_hooks.profile_will_close.append(close_report)
