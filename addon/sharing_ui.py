"""Neutral opt-in prompt with background persistence and a reusable display gate."""

from __future__ import annotations

from aqt.qt import QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QTextBrowser, QVBoxLayout
from aqt.utils import showWarning

from .background import run_file_job
from .research import SharingConfig, SharingSnapshot, sharing_state
from .service import ServiceConfig
from .submissions import withdraw_sharing

_pending: set[str] = set()


class SharingDialog(QDialog):
    def __init__(self, parent, collection_path: str, snapshot: SharingSnapshot, services=None):
        super().__init__(parent)
        self.collection_path = collection_path
        self.snapshot = snapshot
        self.services = services or ServiceConfig()
        self.busy = False
        self.disposed = False
        self.destroyed.connect(lambda: setattr(self, "disposed", True))
        self.setWindowTitle("Quantified Cognition · Research sharing")
        self.resize(660, 420)
        layout = QVBoxLayout(self)
        heading = QLabel("Would you like to share data for scientific analysis?")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        notice = QTextBrowser()
        notice.setPlainText(snapshot.study.notice_text)
        layout.addWidget(notice, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        if snapshot.current_choice is not None:
            choice = {"opt_in": "Yes", "declined": "No", "deferred": "Not now"}[
                snapshot.current_choice
            ]
            self.status.setText(f"Current choice: {choice}. You can change it below.")
        elif snapshot.history == "unknown_legacy":
            self.status.setText(
                "Results existed before timing tracking was added. Your earlier viewing "
                "history will be recorded as unknown."
            )
        layout.addWidget(self.status)
        if snapshot.current_choice is not None:
            actions = QHBoxLayout()
            enrollment = QPushButton("Research enrollment…")
            enrollment.setEnabled(snapshot.current_choice == "opt_in")
            enrollment.clicked.connect(self.open_enrollment)
            actions.addWidget(enrollment)
            submit = QPushButton("Review and submit data…")
            submit.setEnabled(snapshot.current_choice == "opt_in")
            submit.clicked.connect(self.open_submission)
            actions.addWidget(submit)
            delete = QPushButton("Delete uploaded data…")
            delete.clicked.connect(self.delete_data)
            actions.addWidget(delete)
            layout.addLayout(actions)
        buttons = QHBoxLayout()
        self.buttons = []
        for label, choice in (
            ("Yes, allow sharing", "opt_in"),
            ("Not now", "deferred"),
            ("No, keep it private", "declined"),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, selected=choice: self.choose(selected))
            button.setDefault(choice == "deferred")
            buttons.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(buttons)
        if snapshot.current_choice is not None:
            from .submissions import submission_summary

            def loaded(message):
                if not self.disposed and not self.busy:
                    self.status.setText(self.status.text() + "\n" + message)

            run_file_job(
                lambda: submission_summary(collection_path),
                loaded,
                lambda _error: loaded("Connection status is unavailable."),
            )

    def open_submission(self):
        from .submission_ui import SubmissionDialog

        dialog = SubmissionDialog(self, self.collection_path, self.services, self.snapshot.study)
        dialog.prepare()
        dialog.exec()

    def open_enrollment(self):
        from .enrollment_ui import EnrollmentDialog

        dialog = EnrollmentDialog(self, self.collection_path, self.services, self.snapshot.study)
        dialog.exec()

    def delete_data(self):
        from .submission_ui import delete_uploaded_data

        delete_uploaded_data(
            self,
            self.collection_path,
            lambda text: None if self.disposed else self.status.setText(text),
        )

    def choose(self, choice: str) -> None:
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.setEnabled(False)
        self.status.setText("Saving your choice…")

        def saved(snapshot: SharingSnapshot) -> None:
            if self.disposed:
                return
            self.snapshot = snapshot
            self.busy = False
            super(SharingDialog, self).accept()

        def failed(error: Exception) -> None:
            if self.disposed:
                return
            self.busy = False
            for button in self.buttons:
                button.setEnabled(True)
            self.status.setText("Your choice could not be saved. Please try again.")
            showWarning(str(error), parent=self)

        run_file_job(
            lambda: withdraw_sharing(self.collection_path, self.snapshot.study, choice),
            saved,
            failed,
        )

    def reject(self) -> None:
        if self.busy:
            return
        if self.snapshot.current_choice is not None:
            super().reject()
        else:
            self.choose("deferred")

    def closeEvent(self, event) -> None:
        if self.snapshot.current_choice is not None and not self.busy:
            event.accept()
        else:
            event.ignore()
            self.choose("deferred")


def sharing_gate(main_window, addon_name: str, on_ready, *, force: bool = False) -> None:
    if main_window.col is None:
        showWarning("Open an Anki profile before choosing research sharing.", parent=main_window)
        return
    collection_path = main_window.col.path
    if collection_path in _pending:
        return
    _pending.add(collection_path)
    config_manager = main_window.addonManager

    def prepare():
        config = SharingConfig.from_config(config_manager.getConfig(addon_name))
        return sharing_state(collection_path, config)

    def active() -> bool:
        return main_window.col is not None and main_window.col.path == collection_path

    def fail(error: Exception) -> None:
        _pending.discard(collection_path)
        if active():
            showWarning(f"Could not load the sharing choice:\n{error}", parent=main_window)

    def ask(snapshot: SharingSnapshot) -> None:
        if not active():
            _pending.discard(collection_path)
            return
        services = ServiceConfig.from_config(config_manager.getConfig(addon_name))
        dialog = SharingDialog(main_window, collection_path, snapshot, services)
        dialog.exec()
        _pending.discard(collection_path)
        if active():
            on_ready(dialog.snapshot.study)

    def prepared(snapshot: SharingSnapshot) -> None:
        if not active():
            _pending.discard(collection_path)
            return
        if snapshot.current_choice is not None and not force:
            _pending.discard(collection_path)
            on_ready(snapshot.study)
        else:
            run_file_job(
                lambda: sharing_state(
                    collection_path, snapshot.study, event_type="prompt_presented"
                ),
                ask,
                fail,
            )

    run_file_job(prepare, prepared, fail)
