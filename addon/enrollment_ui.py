"""Eligibility first, then the exact saveable information and explicit signature."""

from aqt.qt import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    Qt,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .background import run_file_job
from .enrollment import (
    enroll,
    enrollment_information,
    latest_enrollment,
    load_bundle,
    save_consent_copy,
)


class EnrollmentDialog(QDialog):
    def __init__(self, parent, source, services, sharing):
        super().__init__(parent)
        self.source, self.services, self.sharing = source, services, sharing
        self.closed = False
        self.busy = False
        self.available = False
        self.enrolled = False
        self.setWindowTitle("Quantified Cognition · Research enrollment")
        self.resize(710, 640)
        layout = QVBoxLayout(self)
        self.status = QLabel("Checking whether research enrollment is open…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.eligibility = QWidget()
        eligibility_layout = QVBoxLayout(self.eligibility)
        self.adult = QCheckBox("I am at least 18 years old.")
        self.own = QCheckBox("I am contributing my own data.")
        eligibility_layout.addWidget(self.adult)
        eligibility_layout.addWidget(self.own)
        layout.addWidget(self.eligibility)
        self.documents = QTextBrowser()
        self.documents.setOpenExternalLinks(False)
        self.documents.setPlainText("Loading research information…")
        layout.addWidget(self.documents, 1)
        self.consent = QWidget()
        consent_layout = QVBoxLayout(self.consent)
        self.statement = QLabel()
        self.statement.setWordWrap(True)
        self.statement.setTextFormat(Qt.TextFormat.PlainText)
        consent_layout.addWidget(self.statement)
        self.explicit = QCheckBox(
            "I explicitly consent to the statement above, including health data."
        )
        consent_layout.addWidget(self.explicit)
        consent_layout.addWidget(
            QLabel("Research signature — choose a pseudonym; a legal name is not required.")
        )
        self.signature = QLineEdit()
        self.signature.setMaxLength(80)
        self.signature.setPlaceholderText("Type your chosen research signature")
        consent_layout.addWidget(self.signature)
        layout.addWidget(self.consent)
        self.consent.hide()
        buttons = QHBoxLayout()
        self.save_info = QPushButton("Save research information…")
        self.save_info.clicked.connect(self.save_information)
        buttons.addWidget(self.save_info)
        self.save_record = QPushButton("Save consent copy…")
        self.save_record.clicked.connect(self.save_record_copy)
        self.save_record.setEnabled(False)
        buttons.addWidget(self.save_record)
        buttons.addStretch()
        self.next = QPushButton("Continue to consent")
        self.next.setEnabled(False)
        self.next.setAutoDefault(False)
        self.next.clicked.connect(self.show_consent)
        buttons.addWidget(self.next)
        self.join = QPushButton("Sign and join research")
        self.join.setAutoDefault(False)
        self.join.setEnabled(False)
        self.join.clicked.connect(self.sign)
        self.join.hide()
        buttons.addWidget(self.join)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        for widget in (self.adult, self.own, self.explicit):
            widget.toggled.connect(self.update_actions)
        self.signature.textChanged.connect(self.update_actions)
        self.run(self.prepare, self.prepared)

    def run(self, task, success):
        self.busy = True
        self.update_actions()

        def done(value):
            self.busy = False
            if not self.closed:
                success(value)
                self.update_actions()

        def failed(error):
            self.busy = False
            if not self.closed:
                self.status.setText(str(error))
                self.update_actions()

        run_file_job(task, done, failed)

    def prepare(self):
        frozen = load_bundle()
        previous = latest_enrollment(self.source)
        try:
            info = enrollment_information(self.source, self.services, self.sharing)
            return frozen, previous, info, ""
        except Exception as error:
            return frozen, previous, None, str(error)

    def prepared(self, result):
        self.frozen, previous, info, error = result
        bundle = self.frozen["bundle"]
        self.statement.setText(bundle["statement"])
        self.information_text = "\n\n".join(bundle["documents"].values())
        self.documents.setPlainText(self.information_text)
        self.available = info is not None
        self.save_record.setEnabled(previous is not None)
        self.status.setText(
            error
            or (
                "First confirm eligibility. You can save the information before deciding. "
                "Joining records consent; measurements are sent only after a separate "
                "package preview and Send."
            )
        )

    def update_actions(self):
        eligible = self.adult.isChecked() and self.own.isChecked()
        self.next.setEnabled(self.available and eligible and not self.busy)
        self.join.setEnabled(
            self.available
            and eligible
            and self.explicit.isChecked()
            and 2 <= len(self.signature.text().strip()) <= 80
            and not self.busy
        )
        self.adult.setEnabled(not self.busy)
        self.own.setEnabled(not self.busy)
        self.explicit.setEnabled(not self.busy)
        self.signature.setEnabled(not self.busy)

    def show_consent(self):
        if not self.next.isEnabled():
            return
        self.eligibility.hide()
        self.next.hide()
        self.consent.show()
        self.join.show()
        self.status.setText(
            "Read the information and statement. Signing is voluntary; "
            "personal use stays available."
        )

    def sign(self):
        if self.busy or not self.join.isEnabled():
            return
        adult, own = self.adult.isChecked(), self.own.isChecked()
        explicit, signature = self.explicit.isChecked(), self.signature.text()
        self.status.setText("Saving your signed consent and requesting an enrollment receipt…")

        def signed(_record):
            self.enrolled = True
            self.available = False
            self.save_record.setEnabled(True)
            self.status.setText(
                "Enrollment confirmed. Save your consent copy. No measurements were uploaded. "
                "You can now review and explicitly send a research package."
            )

        self.run(
            lambda: enroll(
                self.source,
                self.services,
                self.sharing,
                adult=adult,
                own_data=own,
                explicit_consent=explicit,
                signature=signature,
            ),
            signed,
        )

    def save_information(self):
        if not hasattr(self, "frozen"):
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save research information", "research-information.txt", "Text (*.txt)"
        )
        if destination:
            text = (
                self.information_text
                + "\n\nCONSENT STATEMENT\n\n"
                + self.frozen["bundle"]["statement"]
            )

            def save():
                from pathlib import Path

                Path(destination).write_text(text, encoding="utf-8")

            self.run(save, lambda _: self.status.setText("Research information saved."))

    def save_record_copy(self):
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save consent copy", "research-consent.json", "JSON (*.json)"
        )
        if destination:
            self.run(
                lambda: save_consent_copy(self.source, destination),
                lambda _: self.status.setText(
                    "Consent copy saved with the signed information and receipt."
                ),
            )

    def reject(self):
        self.closed = True
        super().reject()
