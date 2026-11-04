"""A single review-and-send screen for the fixed research package."""

import html

from aqt.qt import QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QTextBrowser, QVBoxLayout
from aqt.utils import askUser

from .background import run_file_job
from .submissions import prepare_submission, queue_deletion, retry_deletion, upload_submission


class SubmissionDialog(QDialog):
    def __init__(self, parent, source, services, sharing):
        super().__init__(parent)
        self.source, self.services, self.sharing = source, services, sharing
        self.package = None
        self.busy = False
        self.closed = False
        self.setWindowTitle("Quantified Cognition · Submit research data")
        self.resize(670, 430)
        layout = QVBoxLayout(self)
        self.summary = QLabel("Preparing your package in the background…")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        details = QTextBrowser()
        details.setPlainText(
            "Your package contains daily recall and overall, short-term and long-term "
            "formation scores, counts, uncertainty, follow-up coverage and status; "
            "Oura and Google Health total, light, deep, REM and awake durations, Google "
            "restless time, sleep-period counts, availability and daily steps when available.\n\n"
            "Separate main-sleep durations before/after studying, recorded-nap indicators, "
            "nap counts, total/light/deep/REM durations, gaps to studying and matching status "
            "are included.\n\n"
            "It includes dates, Anki version, FSRS settings fingerprint, timezone, day boundary, "
            "calculation settings, provider day conventions, data versions, Oura algorithm "
            "versions, Google sleep types and sync times. A random "
            "participant ID links contributions. Your sharing choice, notice version and "
            "whether you opted in before first viewing results are included.\n\n"
            "Excluded: card questions and answers, card/deck identifiers and names, individual "
            "reviews or review pairs, exact sleep or review times, heart rate, location, "
            "name, email, "
            "raw health records, Oura/Google account or device IDs, "
            "access/refresh tokens and passwords.\n\n"
            "This sends the complete package once. Future uploads require another confirmation. "
            "You can withdraw sharing or request deletion through Research sharing."
        )
        layout.addWidget(details, 1)
        self.privacy = QLabel()
        self.privacy.setOpenExternalLinks(True)
        self.privacy.setWordWrap(True)
        layout.addWidget(self.privacy)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.enrollment = QPushButton("Research enrollment…")
        self.enrollment.clicked.connect(self.open_enrollment)
        buttons.addWidget(self.enrollment)
        self.refresh = QPushButton("Prepare latest data")
        self.refresh.clicked.connect(lambda: self.prepare(reuse=False))
        buttons.addWidget(self.refresh)
        buttons.addStretch()
        self.send = QPushButton("Send this package")
        self.send.setEnabled(False)
        self.send.setAutoDefault(False)
        self.send.clicked.connect(self.submit)
        buttons.addWidget(self.send)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def run(self, task, success):
        self.busy = True
        self.send.setEnabled(False)
        self.refresh.setEnabled(False)
        self.enrollment.setEnabled(False)

        def finished(value):
            self.busy = False
            if self.closed:
                return
            self.refresh.setEnabled(True)
            self.enrollment.setEnabled(True)
            success(value)

        def failed(error):
            self.busy = False
            if not self.closed:
                self.refresh.setEnabled(True)
                self.enrollment.setEnabled(True)
                self.send.setEnabled(self.package is not None)
                self.status.setText(str(error))

        run_file_job(task, finished, failed)

    def open_enrollment(self):
        from .enrollment_ui import EnrollmentDialog

        dialog = EnrollmentDialog(self, self.source, self.services, self.sharing)
        dialog.exec()
        if dialog.enrolled:
            self.prepare(reuse=False)

    def prepare(self, *, reuse=True):
        if self.busy:
            return
        self.package = None

        def prepared(result):
            saved, status = result
            self.package = saved["package"]
            rows = self.package["rows"]
            self.summary.setText(
                f"{len(rows):,} days · {rows[0][0]} to {rows[-1][0]}\n"
                f"Recipient: {status['operator']}\nService: {self.services.base_url}"
            )
            self.privacy.setText(
                '<a href="'
                + html.escape(status["privacy_url"], quote=True)
                + '">Study information and privacy policy</a>'
            )
            self.status.setText(
                "Review the package, then choose Send. No measurements have been uploaded."
            )
            self.send.setEnabled(True)

        self.run(
            lambda: prepare_submission(self.source, self.services, self.sharing, reuse=reuse),
            prepared,
        )

    def submit(self):
        if self.busy or not self.package:
            return
        identity = self.package["submission_id"]
        self.status.setText("Sending your confirmed package…")

        def sent(receipt):
            self.package = None
            self.status.setText("Submitted successfully. Receipt: " + receipt["submission_id"])
            self.send.setEnabled(False)

        self.run(
            lambda: upload_submission(self.source, self.services, self.sharing, identity), sent
        )

    def reject(self):
        self.closed = True
        super().reject()


def delete_uploaded_data(parent, source, callback):
    if not askUser(
        "Delete all research submissions sent from this Anki profile? "
        "If offline, deletion will retry automatically while Anki is open. "
        "Your local measurements and personal CSV files are kept.",
        parent=parent,
    ):
        return

    def task():
        if not queue_deletion(source):
            from .enrollment import latest_enrollment

            previous = latest_enrollment(source)
            if previous and previous["receipt"]:
                return (
                    "The deletion credential is unavailable. Contact "
                    "quantifiedcognition@jollyjoyjourney.com with your saved research receipt "
                    "to request deletion. Server deletion has not been confirmed."
                )
            return "This profile has no research upload connection."
        try:
            if retry_deletion(source):
                return "Research data deletion confirmed, including registered copies."
            return "Active storage deleted. Deletion of registered research copies is pending."
        except Exception:
            return "Deletion is queued and will retry while Anki is open. New uploads are blocked."

    run_file_job(task, callback, lambda _error: callback("Could not queue deletion. Please retry."))
