"""One modeless health screen; authorization, sync and export stay off Qt."""

from __future__ import annotations

import threading

from aqt.qt import (
    QComboBox,
    QDate,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .backend import UpdateCancelled
from .background import run_file_job
from .export import export_csv_atomic
from .google_health_api import (
    connect_google_health,
    default_history_start,
    disconnect_google_health,
    saved_google_health,
    sync_google_health,
)
from .health import HealthSnapshot, load_health
from .oura_api import connect_oura, disconnect_oura, saved_oura, sync_oura
from .research import finish_csv_exposure, prepare_csv_exposure, sharing_state
from .service import ServiceConfig
from .storage import database_path, load_report


def _duration(seconds):
    if seconds is None:
        return "—"
    minutes = int(seconds) // 60
    return f"{minutes // 60}h {minutes % 60:02}m"


class OuraDialog(QDialog):
    def __init__(self, main, sharing, services=None):
        super().__init__(main)
        self.main = main
        self.collection = main.col
        self.collection_path = self.collection.path
        self.sharing = sharing
        self.services = services or ServiceConfig()
        self.cancelled = threading.Event()
        self.busy = False
        self.disposed = False
        self.destroyed.connect(lambda: setattr(self, "disposed", True))
        self.close_requested = False
        self.snapshot = HealthSnapshot("", {})
        self.prefix = "oura"
        self.provider = "Oura"
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Quantified Cognition · Health data")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)
        controls = QHBoxLayout()
        self.provider_selector = QComboBox()
        self.provider_selector.setAccessibleName("Health data source")
        self.provider_selector.addItems(["Oura", "Google Health"])
        self.provider_selector.currentIndexChanged.connect(self.change_provider)
        controls.addWidget(self.provider_selector)
        self.choose_button = QPushButton("Connect Oura…")
        self.choose_button.clicked.connect(self.connect_oura)
        controls.addWidget(self.choose_button)
        self.import_button = QPushButton("Sync now")
        self.import_button.clicked.connect(self.sync)
        controls.addWidget(self.import_button)
        self.disconnect_button = QPushButton("Disconnect and delete…")
        self.disconnect_button.clicked.connect(self.disconnect)
        controls.addWidget(self.disconnect_button)
        controls.addStretch()
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        history_row = QHBoxLayout()
        self.history_label = QLabel("Import since")
        self.history_start = QDateEdit()
        self.history_start.setAccessibleName("Import history start date")
        self.history_start.setCalendarPopup(True)
        self.history_start.setDisplayFormat("yyyy-MM-dd")
        self.history_start.setMinimumDate(QDate(1900, 1, 1))
        self.history_start.setMaximumDate(QDate.currentDate())
        self.history_start.setDate(QDate.fromString(default_history_start(), "yyyy-MM-dd"))
        history_row.addWidget(self.history_label)
        history_row.addWidget(self.history_start)
        history_row.addStretch()
        layout.addLayout(history_row)
        self.status = QLabel("Loading saved Oura data…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 11)
        self.table.setAccessibleName("Saved daily health measurements")
        self.table.setHorizontalHeaderLabels(
            [
                "Date",
                "Sleep",
                "Light",
                "Deep",
                "REM",
                "Awake",
                "Restless",
                "Steps",
                "Sleep data",
                "Workouts",
                "Exercise",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        close_row = QHBoxLayout()
        insights_button = QPushButton("Personal insights…")
        insights_button.clicked.connect(self.open_insights)
        close_row.addWidget(insights_button)
        close_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        self.provider_text()

    def open_insights(self):
        from .ui import open_personal_insights

        open_personal_insights(getattr(self, "addon_name", __package__), self.prefix)

    def provider_text(self):
        google = self.prefix == "google_health"
        self.choose_button.setText(f"Connect {self.provider}…")
        self.history_label.setVisible(google)
        self.history_start.setVisible(google)
        self.table.setColumnHidden(6, not google)
        self.table.setColumnHidden(9, google)
        self.table.setColumnHidden(10, google)
        self.explanation.setText(
            "Connect Google Health for sleep and daily steps in your Google Health account. "
            "First link your account in the Google Health mobile app. "
            "Google’s activity permission covers more than steps; this add-on reads only steps. "
            "Sleep sessions and daily step totals are saved in this Anki profile. "
            "Research uploads require separate confirmation."
            if google
            else "Connect Oura to sync sleep stages, daily steps and workouts automatically. "
            "Daily and Workout permissions are used. Workout labels, heart rate, calories and "
            "distance are not saved. "
            "Measurements stay in this Anki profile. Uploads require separate confirmation."
        )
        self.note.setText(
            "Showing up to 100 recent days; CSV combines all saved health and Anki days. "
            + (
                "Google sleep uses the local wake date; steps use Google’s civil day. "
                "Sleep uses whole minutes. Classic sleep has no light/deep/REM values. "
                "Recent days may change. "
                if google
                else "Oura’s assigned day can differ from Anki’s day boundary. "
                "No recorded workout does not prove no exercise occurred. "
            )
            + "Missing values stay blank. Overlapping sleep periods are flagged. "
            "Oura and Google remain separate; their measurements are never added together."
        )

    def change_provider(self, index):
        if self.busy:
            return
        self.prefix = "google_health" if index else "oura"
        self.provider = "Google Health" if index else "Oura"
        self.provider_text()
        self.load()

    def check_current(self):
        if self.cancelled.is_set() or self.main.col is not self.collection:
            raise UpdateCancelled("The profile closed or changed.")

    def set_busy(self, busy):
        self.busy = busy
        self.provider_selector.setEnabled(not busy)
        self.history_start.setEnabled(not busy)
        self.choose_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.disconnect_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and bool(self.snapshot.days))

    def run(self, task, success):
        self.set_busy(True)

        def done(result):
            if self.disposed:
                return
            self.set_busy(False)
            if self.close_requested or self.main.col is not self.collection:
                self.close()
                return
            success(result)

        def failed(error):
            if self.disposed:
                return
            self.set_busy(False)
            if self.close_requested or isinstance(error, UpdateCancelled):
                self.close()
            else:
                self.status.setText(str(error))

        run_file_job(task, done, failed)

    def expose(self, snapshot):
        self.check_current()
        if any(health.values.get(f"{self.prefix}_day_basis") for health in snapshot.days.values()):
            sharing_state(
                self.collection_path, self.sharing, event_type="results_exposed", surface="report"
            )
        return snapshot

    def load(self):
        def read():
            loader = saved_google_health if self.prefix == "google_health" else saved_oura
            snapshot, message = loader(self.collection_path)
            return self.expose(snapshot), message

        def loaded(result):
            self.show_snapshot(result[0])
            self.status.setText(result[1])

        self.run(read, loaded)

    def show_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.table.setRowCount(0)
        days = {
            day: health
            for day, health in snapshot.days.items()
            if health.values.get(f"{self.prefix}_day_basis")
        }
        for day, health in sorted(days.items(), reverse=True)[:100]:
            values = health.values
            cells = (
                [day.isoformat()]
                + [
                    _duration(values.get(f"{self.prefix}_{field}_seconds"))
                    for field in (
                        "total_sleep",
                        "light_sleep",
                        "deep_sleep",
                        "rem_sleep",
                        "awake",
                        "restless",
                    )
                ]
                + [
                    "—"
                    if values.get(f"{self.prefix}_steps") is None
                    else f"{values[f'{self.prefix}_steps']:,}",
                    values.get(f"{self.prefix}_sleep_status") or "—",
                    "—"
                    if values.get("oura_workout_count") is None
                    else str(values["oura_workout_count"]),
                    (
                        _duration(values.get("oura_workout_duration_seconds"))
                        if values.get("oura_workout_status") == "available"
                        else values.get("oura_workout_status") or "—"
                    ),
                ]
            )
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if days:
            self.status.setText(f"Saved {self.provider} measurements for {len(days):,} days.")
        else:
            self.status.setText(f"No {self.provider} data synced yet.")
        self.set_busy(False)

    def connect_oura(self):
        if self.busy:
            return
        self.status.setText(
            f"Opening {self.provider} sign-in in your browser… Return here after allowing access."
        )
        connect = connect_google_health if self.prefix == "google_health" else connect_oura
        options = (
            {"history_start": self.history_start.date().toString("yyyy-MM-dd")}
            if self.prefix == "google_health"
            else {}
        )
        self.run(
            lambda: self.expose_result(
                connect(
                    self.collection_path,
                    self.services,
                    self.sharing,
                    check_cancelled=self.check_current,
                    **options,
                )
            ),
            self.synced,
        )

    def sync(self):
        if self.busy:
            return
        self.status.setText(f"Syncing {self.provider} in the background… You can keep using Anki.")
        sync = sync_google_health if self.prefix == "google_health" else sync_oura
        options = (
            {"history_start": self.history_start.date().toString("yyyy-MM-dd")}
            if self.prefix == "google_health"
            else {}
        )
        self.run(
            lambda: self.expose_result(
                sync(
                    self.collection_path,
                    self.services,
                    self.sharing,
                    force=True,
                    check_cancelled=self.check_current,
                    **options,
                )
            ),
            self.synced,
        )

    def synced(self, result):
        if result is None:
            self.status.setText(f"Connect {self.provider} first, then sync your measurements.")
            return
        self.show_snapshot(result.snapshot)
        if result.changed_records or result.metadata_changed:
            from .ui import notify_insights_updated

            notify_insights_updated(self.collection_path)
        self.status.setText(
            f"{self.provider} synced. {result.changed_records:,} new, changed or deleted records."
            + (f" CSV retry pending: {result.csv_error}" if result.csv_error else "")
        )

    def expose_result(self, result):
        if result is not None:
            self.expose(result.snapshot)
        return result

    def disconnect(self):
        from aqt.utils import askUser

        if self.busy or not askUser(
            f"Disconnect {self.provider} and delete its measurements from this Anki profile? "
            "The fixed CSV will be refreshed. Previously exported files and research "
            "submissions are managed separately.",
            parent=self,
        ):
            return
        self.status.setText(f"Disconnecting and deleting local {self.provider} data…")

        def done(result):
            self.show_snapshot(result[0])
            self.status.setText(result[1])
            from .ui import notify_insights_updated

            notify_insights_updated(self.collection_path)

        disconnect = disconnect_google_health if self.prefix == "google_health" else disconnect_oura
        self.run(lambda: disconnect(self.collection_path, self.services, self.sharing), done)

    def export(self):
        if self.busy or not self.snapshot.days:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export memory and health data", "memory-and-health.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self.status.setText("Exporting CSV in the background…")

        def write():
            self.check_current()
            snapshot = load_health(self.collection_path)
            report = load_report(database_path(self.collection_path))
            _state, token, metadata = prepare_csv_exposure(self.collection_path, self.sharing)
            try:
                count = export_csv_atomic(
                    report,
                    path,
                    research_metadata=metadata,
                    health_snapshot=snapshot,
                    before_replace=self.check_current,
                )
            except Exception:
                finish_csv_exposure(self.collection_path, self.sharing, token, success=False)
                raise
            finish_csv_exposure(self.collection_path, self.sharing, token, success=True)
            return count

        self.run(write, lambda count: self.status.setText(f"Exported {count:,} days to {path}"))

    def closeEvent(self, event):
        self.cancelled.set()
        self.close_requested = True
        if self.busy:
            self.hide()
            event.ignore()
        else:
            event.accept()
