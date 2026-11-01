"""One historical overview: both sleep timings, four outcomes, no daily check-in."""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from aqt.qt import (
    QCheckBox,
    QColor,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPainter,
    QPen,
    QPointF,
    QPushButton,
    QRectF,
    Qt,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .backend import UpdateCancelled
from .background import run_file_job
from .insight_visits import (
    begin_visit,
    compare_visits,
    comparison_history,
    overview_changes,
    queue_displayed_visit,
)
from .insights import (
    FACTOR_LABELS,
    FACTORS,
    MAIN_FACTORS,
    NAP_FACTORS,
    InputsChanged,
    InsightSettings,
    export_insights,
    load_insights,
    plotted_points,
)
from .metrics import Settings, anki_day
from .phase_comparisons import CONTRASTS, SHIFT_MINUTES
from .phase_comparisons import LABELS as PHASE_LABELS
from .research import finish_csv_exposure, prepare_csv_exposure, sharing_state
from .storage import load_daily_settings
from .update_job import UpdateJob, UpdateRequest

COLUMNS = (
    ("Recall\nMain sleep before", "recall", "before"),
    ("Formation · overall\nMain sleep before", "formation", "before"),
    ("Formation · overall\nMain sleep after", "formation", "after"),
    ("Formation · ≤30 days\nMain sleep before", "formation_short_term", "before"),
    ("Formation · ≤30 days\nMain sleep after", "formation_short_term", "after"),
    ("Formation · >30 days\nMain sleep before", "formation_long_term", "before"),
    ("Formation · >30 days\nMain sleep after", "formation_long_term", "after"),
)


def number(value):
    return "—" if value is None else f"{value:+.2f}"


class AssociationChart(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.pairs = []
        self.x_label = ""
        self.setMinimumHeight(145)
        self.setAccessibleName("Health measurement versus memory score scatter plot")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(self.foregroundRole())
        painter.setPen(color)
        area = QRectF(56, 20, max(1, self.width() - 80), max(1, self.height() - 62))
        if not self.pairs:
            painter.drawText(
                area, Qt.AlignmentFlag.AlignCenter, "Select a comparison to see its matched days"
            )
            return
        xs, ys = zip(*self.pairs, strict=True)
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        if xmax == xmin:
            xmin, xmax = xmin - 1, xmax + 1
        if ymax == ymin:
            ymin, ymax = ymin - 1, ymax + 1

        def point(x, y):
            return QPointF(
                area.left() + (x - xmin) / (xmax - xmin) * area.width(),
                area.bottom() - (y - ymin) / (ymax - ymin) * area.height(),
            )

        muted = QColor(color)
        muted.setAlpha(70)
        painter.setPen(QPen(muted, 1))
        painter.drawRect(area)
        if ymin <= 0 <= ymax:
            painter.drawLine(point(xmin, 0), point(xmax, 0))
        painter.setPen(color)
        painter.drawText(
            QRectF(0, area.top() - 8, 50, 20), Qt.AlignmentFlag.AlignRight, f"{ymax:+.1f}"
        )
        painter.drawText(
            QRectF(0, area.bottom() - 12, 50, 20), Qt.AlignmentFlag.AlignRight, f"{ymin:+.1f}"
        )
        painter.drawText(3, 12, "Score (pp)")
        painter.drawText(QRectF(area.left(), area.bottom() + 3, 100, 20), f"{xmin:.1f}")
        painter.drawText(
            QRectF(area.right() - 100, area.bottom() + 3, 100, 20),
            Qt.AlignmentFlag.AlignRight,
            f"{xmax:.1f}",
        )
        painter.drawText(
            QRectF(area.left(), area.bottom() + 20, area.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            self.x_label,
        )
        dot = QColor("#1683b5")
        dot.setAlpha(110)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        stride = max(1, len(self.pairs) // 800)
        for x, y in self.pairs[::stride][:800]:
            painter.drawEllipse(point(x, y), 2.5, 2.5)


class InsightsDialog(QDialog):
    def __init__(self, main, addon_name, sharing, provider=None):
        super().__init__(main)
        self.main, self.addon_name, self.sharing = main, addon_name, sharing
        self.collection = main.col
        self.source = self.collection.path
        self.busy, self.disposed, self.close_requested = False, False, False
        self.cancelled = threading.Event()
        self.update_job = None
        self.result = None
        self.selected_key = None
        self.selected_phase = None
        self.rows = []
        self.auto_provider = provider is None
        self.pending_reload = False
        self.visit_states = {}
        self.visit_revision = 0
        self.visit_futures = []
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.destroyed.connect(lambda: setattr(self, "disposed", True))
        self.setWindowTitle("Quantified Cognition · Personal insights")
        from .ui_layout import scrollable_report

        layout, outer, self.content_scroll = scrollable_report(
            self, 1240, 650, "Personal insights content"
        )
        intro = QLabel(
            "Your sleep and memory across all saved history. Revisit whenever you have more data. "
            "Higher memory scores mean better performance than FSRS predicted."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        controls = QHBoxLayout()
        self.provider = QComboBox()
        self.provider.addItem("Oura", "oura")
        self.provider.addItem("Google Health", "google_health")
        if provider == "google_health":
            self.provider.setCurrentIndex(1)
        self.provider.setAccessibleName("Health data source")
        controls.addWidget(self.provider)
        self.period = QComboBox()
        for label, days in (
            ("All saved history", None),
            ("Past 3 years", 1096),
            ("Past 12 months", 365),
        ):
            self.period.addItem(label, days)
        self.period.setAccessibleName("History included in personal insights")
        controls.addWidget(self.period)
        self.adjust = QCheckBox("Compare within calendar months")
        self.adjust.setChecked(True)
        self.adjust.setToolTip(
            "Remove each calendar month's average from both measurements. "
            "This reduces associations caused only by long-term shifts."
        )
        controls.addWidget(self.adjust)
        controls.addStretch()
        self.all_button = QPushButton("All associations")
        self.all_button.setCheckable(True)
        self.all_button.toggled.connect(self.view_changed)
        controls.addWidget(self.all_button)
        self.refresh_button = QPushButton("Update history")
        self.refresh_button.clicked.connect(self.refresh_memory)
        controls.addWidget(self.refresh_button)
        self.export_button = QPushButton("Export insights…")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        self.section = QTabBar()
        self.section.addTab("Main sleep & steps")
        self.section.addTab("Naps")
        self.section.currentChanged.connect(self.section_changed)
        layout.addWidget(self.section)
        self.summary = QLabel("Loading your history…")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.changes = QLabel()
        self.changes.setWordWrap(True)
        self.changes.setTextFormat(Qt.TextFormat.PlainText)
        self.changes.setToolTip(
            "Data additions and formation follow-ups use all saved completed study history. "
            "Uncertainty compares the selected view with the last displayed visit "
            "for this provider."
        )
        layout.addWidget(self.changes)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Measurement", *(item[0] for item in COLUMNS)])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(270)
        self.table.currentCellChanged.connect(
            lambda row, column, _r, _c: self.select_cell(row, column)
        )
        layout.addWidget(self.table, 3)
        self.detail = QLabel(
            "Each cell shows correlation r, an approximate 95% range, and matched days."
        )
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.detail)
        self.chart = AssociationChart(self)
        layout.addWidget(self.chart, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.section_text()
        bottom = QHBoxLayout()
        self.health_button = QPushButton("Health data…")
        self.health_button.clicked.connect(self.open_health)
        bottom.addWidget(self.health_button)
        self.saved_label = QLabel()
        self.saved_label.setTextFormat(Qt.TextFormat.PlainText)
        bottom.addWidget(self.saved_label, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        outer.addLayout(bottom)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.period.currentIndexChanged.connect(lambda _index: self.load())
        self.adjust.toggled.connect(lambda _checked: self.load())

    def check_current(self):
        if self.cancelled.is_set() or self.main.col is not self.collection:
            raise UpdateCancelled("The profile closed or changed.")

    def section_changed(self, _index):
        self.section_text()
        if self.result is not None:
            self.show_result(self.result)

    def view_changed(self, show_all):
        returning = self.result and not self.result.visit_changes.get("first_visit", True)
        self.resize(1240, (960 if returning else 900) if show_all else 650)
        self.all_button.setText("Phase comparisons" if show_all else "All associations")
        self.selected_key = None
        self.section_changed(self.section.currentIndex())

    def section_text(self):
        naps = self.section.currentIndex() == 1
        self.section.setTabText(
            0, "Main sleep & steps" if self.all_button.isChecked() else "Main sleep"
        )
        labels = ["Measurement"]
        for label, _metric, timing in COLUMNS:
            labels.append(
                label.split("\n")[0]
                + ("\nBefore studying" if timing == "before" else "\nAfter studying")
                if naps
                else label
            )
        self.set_headers(labels)
        if not self.all_button.isChecked():
            self.note.setText(
                f"Each arrow moves {SHIFT_MINUTES} minutes from the first phase to the second, "
                "holding total sleep and the remaining phase constant. All three rows use the "
                "same days within each column. Formation uses ≥30-day-old study days with ≥80% "
                "evaluated follow-ups. Approximate 95% ranges use 14-day calendar blocks. "
                "These are associations, not causal effects."
            )
            return
        self.note.setText(
            (
                "Before-study naps: previous main sleep to first review. After-study naps: "
                "last review to next main sleep. Duration, phases and proximity use nap days only; "
                "missing stages stay unknown. Proximity is the gap to studying. No recorded nap "
                "does not prove no nap occurred. Naps between reviews are exported separately. "
                if naps
                else "Main sleep uses actual sessions before the first review and after the last "
                "review, within 36 hours; naps are separate. Steps use the previous day for "
                "recall and the study day for formation. "
            )
            + "Formation needs ≥30-day-old study days and ≥80% evaluated follow-ups. Approximate "
            "95% ranges use 14-day calendar blocks. Comparisons are exploratory, not causal."
            " Phases share total sleep time; r does not rank their independent importance."
        )

    def provider_changed(self, _index):
        self.auto_provider = False
        self.load()

    def set_busy(self, busy):
        self.busy = busy
        for control in (
            self.provider,
            self.period,
            self.adjust,
            self.refresh_button,
            self.health_button,
            self.section,
            self.all_button,
        ):
            control.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self.result is not None)
        self.table.setEnabled(not busy)

    def failed(self, error):
        self.update_job = None
        if self.disposed:
            return
        self.set_busy(False)
        if isinstance(error, InputsChanged) and not self.close_requested:
            self.load()
            return
        if self.close_requested or isinstance(error, UpdateCancelled):
            self.close()
        else:
            self.summary.setText(str(error))

    def run(self, task, done):
        self.set_busy(True)

        def success(value):
            if self.disposed:
                return
            self.set_busy(False)
            if self.close_requested or self.main.col is not self.collection:
                self.close()
            else:
                done(value)
                if self.pending_reload and not self.busy:
                    self.load()

        run_file_job(task, success, self.failed)

    def refresh_memory(self):
        if self.busy:
            return
        self.set_busy(True)
        self.summary.setText(
            "Checking your complete Anki history and current FSRS settings in the background…"
        )
        manager, name, source = self.main.addonManager, self.addon_name, self.source

        def prepare(rollover):
            config = manager.getConfig(name)
            return UpdateRequest(
                Settings.from_config(config),
                load_daily_settings(source),
                anki_day(time.time(), rollover),
                self.sharing,
            )

        def done(_result):
            self.update_job = None
            if self.disposed:
                return
            self.set_busy(False)
            if self.close_requested or self.main.col is not self.collection:
                self.close()
            else:
                self.load()

        self.update_job = UpdateJob(self.main, self, prepare, done, self.failed)
        self.update_job.start()

    def load(self):
        if self.busy:
            return
        self.pending_reload = False
        provider, days = self.provider.currentData(), self.period.currentData()
        if self.auto_provider:
            provider = None
        settings = InsightSettings(adjust_month=self.adjust.isChecked())
        start = date.today() - timedelta(days=days - 1) if days else None
        self.table.setRowCount(0)
        self.chart.pairs = []
        self.chart.update()
        self.summary.setText(
            "Matching your saved history and estimating uncertainty in the background…"
        )
        self.changes.setText("Comparing with your last visit…")
        self.detail.setText("")
        visit_states = dict(self.visit_states)

        def task():
            result = load_insights(
                self.source,
                provider,
                settings=settings,
                start=start,
                period_days=days,
                check_cancelled=self.check_current,
            )
            self.check_current()
            try:
                state = begin_visit(self.source, result.provider, visit_states.get(result.provider))
                result.visit_state = state
                result.visit_changes = compare_visits(
                    result.visit_snapshot, state["previous"], problem=state["problem"]
                )
            except (OSError, ValueError) as error:
                result.visit_state = {}
                result.visit_changes = {
                    "first_visit": True,
                    "problem": "Visit comparison unavailable: " + str(error),
                }
            self.check_current()
            if result.health_days or any(item.points for item in result.series.values()):
                sharing_state(
                    self.source, self.sharing, event_type="results_exposed", surface="report"
                )
            return result

        self.run(task, self.show_result)

    def show_result(self, result):
        if self.pending_reload:
            self.load()
            return
        newly_displayed = result is not self.result
        self.result = result
        if result.visit_state:
            self.visit_states[result.provider] = result.visit_state
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(self.provider.findData(result.provider))
        self.provider.blockSignals(False)
        self.auto_provider = False
        self.summary.setText(self.overview_text(result))
        self.changes.setText(
            overview_changes(
                result.visit_changes,
                scope="nap" if self.section.currentIndex() else "main",
                phases=not self.all_button.isChecked(),
            )
        )
        self.changes.setVisible(not self.all_button.isChecked())
        generated = result.generated_at.replace("T", " ")[:16]
        memory = result.memory_generated_at.replace("T", " ")[:16]
        self.saved_label.setText(
            f"Memory saved {memory or '—'} UTC · Insights {generated} UTC"
            + (" · reused unchanged analysis" if result.reused else "")
        )
        self.export_button.setEnabled(True)
        self.chart.setVisible(self.all_button.isChecked())
        if not self.all_button.isChecked():
            self.show_phase_results(result)
            if newly_displayed:
                self.checkpoint_visit(result)
            return
        self.table.blockSignals(True)
        self.rows = [
            factor
            for factor in (NAP_FACTORS if self.section.currentIndex() else MAIN_FACTORS)
            if factor != "restless" or result.provider == "google_health"
        ]
        self.table.clearSpans()
        self.table.setRowCount(len(self.rows))
        for row, factor in enumerate(self.rows):
            self.table.setRowHeight(row, max(54, 3 * self.table.fontMetrics().height() + 6))
            self.table.setItem(row, 0, QTableWidgetItem(FACTOR_LABELS[FACTORS.index(factor)]))
            for column, (_label, metric, timing) in enumerate(COLUMNS, 1):
                if factor == "steps" and column in (3, 5, 7):
                    continue
                key = self.cell_key(factor, metric, timing)
                value = result.associations[key]
                text = (
                    f"r {number(value.correlation)}\n"
                    f"{number(value.low)} to {number(value.high)}\n{value.days:,} days"
                    if value.status != "insufficient data"
                    else f"Insufficient data\n{value.days:,} matched days"
                )
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(
                    value.reason
                    or "Approximate 95% range; exploratory correlation, not a causal effect."
                )
                self.table.setItem(row, column, item)
                if factor == "steps" and column in (2, 4, 6):
                    self.table.setSpan(row, column, 1, 2)
        content_height = (
            self.table.horizontalHeader().sizeHint().height()
            + 4
            + sum(self.table.rowHeight(row) for row in range(len(self.rows)))
        )
        height = min(480, max(220 if self.section.currentIndex() else 270, content_height))
        self.table.setMinimumHeight(height)
        self.table.setMaximumHeight(height if self.section.currentIndex() else 16777215)
        self.table.blockSignals(False)
        row, column = 0, 1
        if self.selected_key:
            for r, factor in enumerate(self.rows):
                for c, (_, metric, timing) in enumerate(COLUMNS, 1):
                    if self.cell_key(factor, metric, timing) == self.selected_key:
                        row, column = r, c
                        break
        self.table.setCurrentCell(row, column)
        self.select_cell(row, column)
        if newly_displayed:
            self.checkpoint_visit(result)

    def checkpoint_visit(self, result):
        if (
            not self.isVisible()
            or not result.visit_state
            or not result.visit_snapshot
            or self.close_requested
            or self.disposed
            or not (result.memory_generated_at or result.health_days)
        ):
            return
        self.visit_revision += 1
        future = queue_displayed_visit(
            self.source, result.visit_snapshot, result.visit_state, self.visit_revision
        )
        self.visit_futures = [job for job in self.visit_futures if not job.done()]
        self.visit_futures.append(future)

        def finished(job):
            error = job.exception()
            if error is None:
                return

            def notify_failure():
                if not self.disposed and not self.close_requested:
                    self.saved_label.setText("Visit comparison could not be saved: " + str(error))

            if not self.disposed and not self.close_requested:
                try:
                    self.main.taskman.run_on_main(notify_failure)
                except RuntimeError:
                    pass

        future.add_done_callback(finished)

    def overview_text(self, result):
        if not result.memory_generated_at:
            return "No saved memory scores yet. Select Update history to analyse your Anki history."
        if not result.health_days:
            return (
                "No saved health measurements. Open Health data to connect and import your history."
            )
        coverage = result.coverage
        scope = "nap" if self.section.currentIndex() else "main"
        groups = [cohort for cohort in result.phase_cohorts.values() if cohort.scope == scope]
        ready = sum(
            all(
                result.phase_associations.get(f"{scope}/{contrast}/{cohort.metric}/{cohort.timing}")
                is not None
                and result.phase_associations[
                    f"{scope}/{contrast}/{cohort.metric}/{cohort.timing}"
                ].status
                == "exploratory estimate"
                for contrast in CONTRASTS
            )
            for cohort in groups
        )
        text = (
            f"{coverage.get('study_days', 0):,} completed study days · "
            f"{coverage.get('health_days', result.health_days):,} saved health days. "
        )
        if self.all_button.isChecked():
            individual = [
                value
                for key, value in result.associations.items()
                if key.startswith("nap_") == (scope == "nap")
                and (result.provider == "google_health" or not key.startswith("restless/"))
            ]
            available = sum(value.status == "exploratory estimate" for value in individual)
            text += f"{available} of {len(individual)} individual associations have enough data."
        else:
            text += f"Phase comparisons ready for {ready} of 7 memory/timing combinations."
        if coverage.get("first_study_day"):
            text += (
                f"\nStudy history {coverage['first_study_day']} to {coverage['last_study_day']}. "
                f"Health history {coverage.get('first_health_day') or '—'} to "
                f"{coverage.get('last_health_day') or '—'}."
            )
        pending = coverage.get("pending_formation_days", 0)
        recent = coverage.get("recent_formation_days", 0)
        if pending or recent:
            text += (
                f"\n{pending:,} study days still have pending formation outcomes; "
                f"{recent:,} are less than 30 days old."
            )
        return text

    def show_phase_results(self, result):
        self.rows = list(CONTRASTS)
        self.table.blockSignals(True)
        self.table.clearSpans()
        self.table.setRowCount(3)
        scope = "nap" if self.section.currentIndex() else "main"
        headers = [f"{SHIFT_MINUTES}-minute\nshift"]
        for label, metric, timing in COLUMNS:
            cohort = result.phase_cohorts.get(f"{scope}/{metric}/{timing}")
            headers.append(
                label.split("\n")[0].replace(" · ", "\n")
                + "\n"
                + ("Before" if timing == "before" else "After")
                + f" · {len(cohort.points) if cohort else 0:,} days"
            )
        self.set_headers(headers)
        for row, (contrast, label) in enumerate(zip(CONTRASTS, PHASE_LABELS, strict=True)):
            self.table.setRowHeight(row, max(54, 3 * self.table.fontMetrics().height() + 6))
            self.table.setItem(row, 0, QTableWidgetItem(label))
            for column, (_, metric, timing) in enumerate(COLUMNS, 1):
                value = result.phase_associations.get(f"{scope}/{contrast}/{metric}/{timing}")
                text = (
                    f"{number(value.effect_pp)} pp\n"
                    f"{number(value.low_pp)} to {number(value.high_pp)}"
                    if value and value.status == "exploratory estimate"
                    else "Insufficient data"
                )
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(
                    value.reason
                    if value and value.reason
                    else "Score difference in percentage points; approximate 95% range."
                )
                self.table.setItem(row, column, item)
        height = max(
            220,
            self.table.horizontalHeader().sizeHint().height()
            + 4
            + sum(self.table.rowHeight(row) for row in range(3)),
        )
        self.table.setMinimumHeight(height)
        self.table.setMaximumHeight(height)
        self.table.blockSignals(False)
        row, column = 0, 1
        if self.selected_phase:
            for r, contrast in enumerate(CONTRASTS):
                for c, (_, metric, timing) in enumerate(COLUMNS, 1):
                    if self.selected_phase == (contrast, metric, timing):
                        row, column = r, c
        self.table.setCurrentCell(row, column)
        self.select_phase(row, column)

    def set_headers(self, labels):
        self.table.setHorizontalHeaderLabels(labels)
        metrics = self.table.fontMetrics()
        widest = max(
            metrics.horizontalAdvance(line) for label in labels for line in label.splitlines()
        )
        minimum = max(widest + 20, metrics.horizontalAdvance("Insufficient data") + 16)
        # Stretch normally, but allow horizontal scrolling instead of clipping
        # metric names and uncertainty labels at enlarged font sizes.
        self.table.horizontalHeader().setMinimumSectionSize(minimum)

    def select_phase(self, row, column):
        scope = "nap" if self.section.currentIndex() else "main"
        contrast = self.rows[row]
        label, metric, timing = COLUMNS[column - 1]
        self.selected_phase = (contrast, metric, timing)
        value = self.result.phase_associations.get(f"{scope}/{contrast}/{metric}/{timing}")
        cohort = self.result.phase_cohorts.get(f"{scope}/{metric}/{timing}")
        donor, receiver = CONTRASTS[contrast]
        remaining = ({"light", "deep", "REM"} - {donor, receiver}).pop()
        text = (
            f"{SHIFT_MINUTES} minutes of {receiver} instead of {donor} · "
            f"{label.split(chr(10))[0]} · {timing} studying. "
        )
        if not value or value.status == "insufficient data":
            text += "Insufficient data. " + (
                value.reason if value else "Update history to match phases."
            )
        else:
            text += (
                f"Associated score difference {number(value.effect_pp)} percentage points "
                f"(approximate 95% range {number(value.low_pp)} to {number(value.high_pp)}). "
                f"Total sleep and {remaining} are held constant"
                + (
                    ", with calendar-month adjustment. "
                    if self.result.settings.adjust_month
                    else ". "
                )
                + ("The range includes zero. " if value.low_pp <= 0 <= value.high_pp else "")
            )
        if value:
            text += f" {value.days:,} common days; {value.reviews:,} evaluated reviews."
        if value and value.status == "insufficient data" and cohort:
            omitted = "; ".join(f"{n:,} {why}" for why, n in cohort.omitted.items() if n)
            if omitted:
                text += " Not included: " + omitted + "."
        text += comparison_history(
            self.result.visit_changes, f"phase/{scope}/{contrast}/{metric}/{timing}"
        )
        self.detail.setText(text)
        self.detail.setToolTip(
            "Fixed-total linear phase substitution. This is an observational association."
        )

    @staticmethod
    def cell_key(factor, metric, timing):
        if factor == "steps":
            timing = "previous_day" if metric == "recall" else "study_day"
        return f"{factor}/{metric}/{timing}"

    def select_cell(self, row, column):
        if not self.result or column < 1 or not 0 <= row < len(self.rows):
            return
        if not self.all_button.isChecked():
            self.select_phase(row, column)
            return
        factor = self.rows[row]
        label, metric, timing = COLUMNS[column - 1]
        key = self.cell_key(factor, metric, timing)
        self.selected_key = key
        series, value = self.result.series[key], self.result.associations[key]
        name = FACTOR_LABELS[FACTORS.index(factor)]
        context = label.replace("\n", " · ")
        if factor in NAP_FACTORS:
            context = label.split("\n")[0] + (
                " · before studying" if timing == "before" else " · after studying"
            )
        if factor == "steps":
            context = label.split("\n")[0] + (
                " · previous day’s steps" if metric == "recall" else " · study day’s steps"
            )
        omitted = "; ".join(
            f"{count:,} {reason}" for reason, count in series.omitted.items() if count
        )
        text = f"{name} → {context}: "
        if value.status == "insufficient data":
            text += "Insufficient data. " + value.reason
        else:
            text += (
                f"r {number(value.correlation)}; approximate 95% range "
                f"{number(value.low)} to {number(value.high)}. "
                + (
                    "The range includes zero. "
                    if value.low <= 0 <= value.high
                    else "The range excludes zero for this comparison. "
                )
            )
            if value.correlation > 0:
                text += "Higher measurements tended to accompany higher memory scores. "
            elif value.correlation < 0:
                text += "Higher measurements tended to accompany lower memory scores. "
        text += f"{value.days:,} matched days; {value.reviews:,} evaluated reviews."
        if value.nap_days is not None:
            text += (
                f" {value.nap_days:,} recorded-nap days; "
                f"{value.no_recorded_nap_days:,} with no recorded nap."
            )
        if value.days:
            text += f" Study dates {value.first_day} to {value.last_day}."
        if self.result.settings.adjust_month and value.raw_correlation is not None:
            text += f" Unadjusted r {number(value.raw_correlation)}."
        if value.status == "insufficient data" and omitted:
            text += " Not included: " + omitted + "."
        text += comparison_history(self.result.visit_changes, "association/" + key)
        self.detail.setText(text)
        self.detail.setToolTip(
            "Days omitted: "
            + (omitted or "none")
            + ". Daily scores must meet your configured review/outcome-day thresholds. "
            "Formation scores depend on available follow-ups and current FSRS parameters."
        )
        self.chart.pairs = plotted_points(series, self.result.settings.adjust_month)
        self.chart.setToolTip(
            "Both axes are deviations from their calendar-month averages."
            if self.result.settings.adjust_month
            else "Unadjusted daily measurements and scores."
        )
        units = (
            "steps"
            if factor == "steps"
            else "minutes on nap days"
            if factor in NAP_FACTORS and factor not in ("nap_recorded", "nap_gap")
            else "hours from studying"
            if factor == "nap_gap"
            else "0=no record, 1=recorded"
            if factor == "nap_recorded"
            else "hours"
        )
        self.chart.x_label = (
            name
            + f" ({units})"
            + (" relative to calendar-month average" if self.result.settings.adjust_month else "")
        )
        self.chart.update()

    def export(self):
        if self.busy or not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export historical associations", "personal-insights.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        result = self.result

        def write():
            self.check_current()
            token = None
            if result.health_days or any(series.points for series in result.series.values()):
                _snapshot, token, _metadata = prepare_csv_exposure(self.source, self.sharing)
            try:
                count = export_insights(result, path, before_replace=self.check_current)
            except Exception:
                finish_csv_exposure(self.source, self.sharing, token, success=False)
                raise
            finish_csv_exposure(self.source, self.sharing, token, success=True)
            return count

        self.run(
            write, lambda count: self.saved_label.setText(f"Exported {count} comparisons to {path}")
        )

    def open_health(self):
        from .ui import open_oura_data

        open_oura_data(self.addon_name)

    def inputs_changed(self):
        if self.close_requested or self.disposed:
            return
        if self.busy:
            self.pending_reload = True
        else:
            self.load()

    def closeEvent(self, event):
        self.cancelled.set()
        self.close_requested = True
        if self.update_job:
            self.update_job.cancel()
        if self.busy:
            self.hide()
            event.ignore()
        else:
            event.accept()
