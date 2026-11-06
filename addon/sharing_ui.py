"""Sharing preferences, research enrollment and package submission."""

from __future__ import annotations

import html
from pathlib import Path

from aqt.qt import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, showWarning

from .background import run_file_job
from .enrollment import (
    enroll,
    enrollment_information,
    latest_enrollment,
    load_bundle,
    require_enrollment,
    save_consent_copy,
)
from .research import SharingConfig, SharingSnapshot, sharing_state
from .service import ServiceConfig, ServiceError
from .submissions import (
    prepare_submission,
    queue_deletion,
    retry_deletion,
    submission_summary,
    upload_submission,
    withdraw_sharing,
)
from .ui_layout import scrollable_report

_pending: set[str] = set()


def _label(text=""):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class SharingDialog(QDialog):
    def __init__(
        self,
        parent,
        collection_path: str,
        snapshot: SharingSnapshot,
        services=None,
        *,
        manage=False,
    ):
        super().__init__(parent)
        self.collection_path = collection_path
        self.snapshot = snapshot
        self.services = services or ServiceConfig()
        self.manage = manage
        self.busy = False
        self.saving_choice = False
        self.closed = False
        self.destroyed.connect(lambda: setattr(self, "closed", True))
        self.frozen = None
        self.has_consent_copy = False
        self.enrolled = False
        self.package = None
        self.setWindowTitle("Quantified Cognition · Research sharing")
        layout, outer, _scroll = scrollable_report(self, 720, 460, "Research sharing")
        heading = _label("Help study how sleep and activity relate to learning")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        layout.addWidget(
            _label(
                "The package includes daily recall and memory formation scores, plus any "
                "saved Oura sleep stages and durations, naps, steps and workout summaries. "
                "It also includes dates, review counts, uncertainty, calculation settings, "
                "a random participant ID and whether you expressed interest before seeing results."
            )
        )
        layout.addWidget(
            _label(
                "An upload excludes card text, card and deck names and IDs, individual reviews, "
                "raw health records, exact event times, workout labels, heart rate, location, "
                "name, email, account and device IDs, and credentials."
            )
        )
        layout.addWidget(
            _label(
                "Personal results are available either way. Joining requires explicit consent, "
                "and you decide whether to send each package."
            )
        )
        self.details = QTextBrowser()
        self.details.setPlainText(snapshot.study.notice_text)
        self.details.setMinimumHeight(160)
        self.details_button = QPushButton("Full sharing notice")
        self.details_button.setAutoDefault(False)
        self.details_button.setCheckable(True)
        self.details_button.setChecked(bool(snapshot.study.custom_notice_text))
        self.details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.details)
        self.details.setVisible(bool(snapshot.study.custom_notice_text))

        self.choice_status = _label()
        layout.addWidget(self.choice_status)
        self.status = _label()
        layout.addWidget(self.status)
        self.preview = _label()
        self.preview.hide()
        layout.addWidget(self.preview)
        self.privacy = _label()
        self.privacy.setTextFormat(Qt.TextFormat.RichText)
        self.privacy.setOpenExternalLinks(True)
        layout.addWidget(self.privacy)

        self.enrollment_form = QWidget()
        form = QVBoxLayout(self.enrollment_form)
        form.setContentsMargins(0, 0, 0, 0)
        self.adult = QCheckBox("I am at least 18 years old.")
        self.own = QCheckBox("I am contributing my own data.")
        form.addWidget(self.adult)
        form.addWidget(self.own)
        self.consent = QWidget()
        consent = QVBoxLayout(self.consent)
        consent.setContentsMargins(0, 0, 0, 0)
        self.statement = _label()
        consent.addWidget(self.statement)
        self.explicit = QCheckBox("I explicitly consent, including to the use of my health data.")
        consent.addWidget(self.explicit)
        self.join = QPushButton("Agree and join research")
        self.join.setAutoDefault(False)
        self.join.clicked.connect(self.agree)
        consent.addWidget(self.join, alignment=Qt.AlignmentFlag.AlignLeft)
        form.addWidget(self.consent)
        layout.addWidget(self.enrollment_form)
        self.enrollment_form.hide()
        for widget in (self.adult, self.own, self.explicit):
            widget.toggled.connect(self.update_actions)

        actions = QHBoxLayout()
        self.save_info = QPushButton("Save study information…")
        self.save_info.clicked.connect(self.save_information)
        self.save_record = QPushButton("Save consent copy…")
        self.save_record.clicked.connect(self.save_record_copy)
        self.delete = QPushButton("Delete uploaded data…")
        self.delete.clicked.connect(self.delete_data)
        self.refresh = QPushButton("Prepare latest data")
        self.refresh.clicked.connect(lambda: self.prepare_package(reuse=False))
        self.send = QPushButton("Send this package")
        self.send.clicked.connect(self.submit)
        for button in (self.save_info, self.save_record, self.delete, self.refresh, self.send):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.details_button.toggled.connect(self.update_actions)

        buttons = QHBoxLayout()
        self.buttons = []
        for label, choice in (
            ("Yes, I’m interested", "opt_in"),
            ("Not now", "deferred"),
            ("No, keep it private", "declined"),
        ):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, selected=choice: self.choose(selected))
            buttons.addWidget(button)
            self.buttons.append(button)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        self.change_choice = QPushButton("Change sharing choice")
        self.change_choice.setCheckable(True)
        self.change_choice.setAutoDefault(False)
        self.change_choice.toggled.connect(self.update_actions)
        buttons.addWidget(self.change_choice)
        buttons.addWidget(self.close_button)
        self.withdrawal_hint = _label("Choosing Not now or No requests deletion of past uploads.")
        outer.addWidget(self.withdrawal_hint)
        outer.addLayout(buttons)
        self.show_choice()
        self.update_actions()
        if self.manage and snapshot.current_choice is not None:
            self.load_participation()

    def show_choice(self):
        choice = self.snapshot.current_choice
        self.close_button.setVisible(choice is not None)
        if choice is not None:
            label = {"opt_in": "Interested", "declined": "Keep it private", "deferred": "Not now"}
            self.choice_status.setText(f"Saved choice · {label[choice]}")
            self.close_button.setDefault(True)
            self.close_button.setFocus()
        else:
            self.buttons[1].setDefault(True)
            if self.snapshot.history == "unknown_legacy":
                self.choice_status.setText("Earlier viewing history is unknown for this profile.")

    def update_actions(self, *_args):
        eligible = self.adult.isChecked() and self.own.isChecked()
        self.consent.setVisible(eligible)
        if not eligible:
            self.explicit.setChecked(False)
        self.join.setEnabled(
            not self.enrollment_form.isHidden()
            and eligible
            and self.explicit.isChecked()
            and not self.busy
        )
        for widget in (self.adult, self.own, self.explicit, *self.buttons):
            widget.setEnabled(not self.busy)
        opted_in = self.snapshot.current_choice == "opt_in"
        self.change_choice.setVisible(opted_in)
        self.change_choice.setEnabled(not self.busy)
        changing = opted_in and self.change_choice.isChecked()
        self.buttons[0].setVisible(not opted_in)
        for button in self.buttons[1:]:
            button.setVisible(not opted_in or changing)
        self.withdrawal_hint.setVisible(changing)
        self.close_button.setEnabled(not self.saving_choice)
        self.save_info.setVisible(self.frozen is not None and not self.details.isHidden())
        self.save_record.setVisible(self.has_consent_copy)
        self.delete.setVisible(self.manage and self.snapshot.current_choice is not None)
        self.refresh.setVisible(self.enrolled)
        self.send.setVisible(self.enrolled)
        for button in (self.save_info, self.save_record, self.delete, self.refresh):
            button.setEnabled(not self.busy)
        self.send.setEnabled(self.package is not None and not self.busy)

    def run(self, task, success, failure=None):
        self.busy = True
        self.update_actions()

        def finish():
            self.busy = self.saving_choice = False
            if self.closed:
                return False
            self.update_actions()
            return True

        def done(value):
            if finish():
                success(value)

        def failed(error):
            if finish():
                if failure:
                    failure(error)
                else:
                    self.status.setText(str(error))

        run_file_job(task, done, failed)

    def load_participation(self):
        self.status.setText("Checking research availability…")
        choice, study = self.snapshot.current_choice, self.snapshot.study

        def load():
            frozen = load_bundle()
            previous = latest_enrollment(self.collection_path)
            message = submission_summary(self.collection_path)
            if choice != "opt_in":
                return frozen, previous, message, False, False
            try:
                enrollment_information(self.collection_path, self.services, study)
            except ServiceError as error:
                return frozen, previous, f"{error}\n{message}", False, False
            try:
                require_enrollment(self.collection_path, self.services)
            except ServiceError:
                return frozen, previous, "Confirm eligibility to join the study.", True, False
            return frozen, previous, message, False, True

        def loaded(result):
            self.frozen, previous, message, needs_enrollment, self.enrolled = result
            self.has_consent_copy = previous is not None
            bundle = self.frozen["bundle"]
            self.statement.setText(bundle["statement"])
            self.details_button.setText("Full sharing notice and study information")
            self.details.setPlainText(study.notice_text + "\n\n" + self.study_information())
            self.enrollment_form.setVisible(needs_enrollment)
            self.status.setText(message)
            self.update_actions()
            if self.enrolled:
                self.prepare_package()

        self.run(load, loaded)

    def study_information(self):
        bundle = self.frozen["bundle"]
        return "\n\n".join((*bundle["documents"].values(), bundle["statement"]))

    def agree(self):
        if self.busy or self.enrollment_form.isHidden() or not self.join.isEnabled():
            return
        adult = self.adult.isChecked()
        own = self.own.isChecked()
        explicit = self.explicit.isChecked()
        self.status.setText("Saving your consent…")

        def signed(_record):
            self.enrolled = self.has_consent_copy = True
            self.enrollment_form.hide()
            self.prepare_package()

        self.run(
            lambda: enroll(
                self.collection_path, self.services, self.snapshot.study,
                adult=adult, own_data=own, explicit_consent=explicit,
            ),
            signed,
        )

    def prepare_package(self, *, reuse=True):
        if self.busy or not self.enrolled:
            return
        self.package = None
        self.preview.hide()
        self.privacy.clear()
        self.status.setText("Preparing your package…")

        def prepared(result):
            saved, status = result
            self.package = saved["package"]
            rows = self.package["rows"]
            self.preview.setText(
                f"{len(rows):,} days · {rows[0][0]} to {rows[-1][0]}\n"
                f"Recipient · {status['operator']}"
            )
            self.preview.show()
            self.privacy.setText(
                f'<a href="{html.escape(status["privacy_url"], quote=True)}">Privacy policy</a>'
            )
            self.status.setText("Choose Send this package to contribute these measurements once.")
            self.update_actions()

        self.run(
            lambda: prepare_submission(
                self.collection_path, self.services, self.snapshot.study, reuse=reuse
            ),
            prepared,
        )

    def submit(self):
        if self.busy or self.package is None:
            return
        identity = self.package["submission_id"]
        self.status.setText("Sending your package…")

        def sent(receipt):
            self.package = None
            self.status.setText(f"Package sent. Receipt · {receipt['submission_id']}")
            self.update_actions()

        self.run(
            lambda: upload_submission(
                self.collection_path, self.services, self.snapshot.study, identity
            ),
            sent,
        )

    def save_information(self):
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save study information", "research-information.txt", "Text (*.txt)"
        )
        if destination:
            text = self.study_information()
            self.run(
                lambda: Path(destination).write_text(text, encoding="utf-8"),
                lambda _: self.status.setText("Study information saved."),
            )

    def save_record_copy(self):
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save consent copy", "research-consent.json", "JSON (*.json)"
        )
        if destination:
            self.run(
                lambda: save_consent_copy(self.collection_path, destination),
                lambda _: self.status.setText("Consent copy saved."),
            )

    def delete_data(self):
        if self.busy or not askUser(
            "Delete all research submissions from this profile? "
            "If you are offline, deletion will retry while Anki is open.",
            parent=self,
        ):
            return
        self.package = None
        self.enrolled = False
        self.enrollment_form.hide()
        self.preview.hide()
        self.privacy.clear()

        def delete():
            if not queue_deletion(self.collection_path):
                previous = latest_enrollment(self.collection_path)
                if previous and previous["receipt"]:
                    return (
                        "Contact quantifiedcognition@jollyjoyjourney.com with your receipt "
                        "to request deletion. The local deletion credential is unavailable."
                    )
                return "This profile has no research upload connection."
            try:
                if retry_deletion(self.collection_path):
                    return "Research data deletion confirmed, including registered copies."
                return "Active storage deleted. Deletion of registered copies is pending."
            except Exception:
                return "Deletion is queued and will retry while Anki is open."

        self.run(delete, self.status.setText)

    def choose(self, choice: str) -> None:
        if self.busy:
            return
        if choice == self.snapshot.current_choice:
            self.reject()
            return
        self.saving_choice = True
        self.status.setText("Saving your choice…")

        def saved(snapshot: SharingSnapshot) -> None:
            self.snapshot = snapshot
            if self.manage and choice == "opt_in":
                self.change_choice.setChecked(False)
                self.show_choice()
                self.load_participation()
            else:
                self.closed = True
                super(SharingDialog, self).accept()

        def failed(error: Exception) -> None:
            self.status.setText("Your choice could not be saved. Please try again.")
            showWarning(str(error), parent=self)

        self.run(
            lambda: withdraw_sharing(self.collection_path, self.snapshot.study, choice),
            saved,
            failed,
        )

    def reject(self) -> None:
        if self.saving_choice:
            return
        if self.snapshot.current_choice is not None:
            self.closed = True
            super().reject()
        else:
            self.choose("deferred")

    def closeEvent(self, event) -> None:
        if self.saving_choice or self.snapshot.current_choice is None:
            event.ignore()
            self.choose("deferred")
        else:
            super().closeEvent(event)


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
        dialog = SharingDialog(main_window, collection_path, snapshot, services, manage=force)
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
