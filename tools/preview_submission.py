"""Render the actual upload screen with synthetic summary text, without sending."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.research import SharingConfig  # noqa: E402
from addon.service import ServiceConfig  # noqa: E402
from addon.submission_ui import SubmissionDialog  # noqa: E402

app = QApplication([])
if os.name == "nt":
    font_id = QFontDatabase.addApplicationFont(
        str(Path(os.environ["WINDIR"]) / "Fonts/segoeui.ttf")
    )
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
parent = QWidget()
dialog = SubmissionDialog(
    parent,
    str(root / ".reference/preview.anki2"),
    ServiceConfig("https://example.supabase.co/functions/v1/qs-service"),
    SharingConfig(),
)
dialog.summary.setText(
    "180 days · 2026-04-27 to 2026-10-23\nRecipient: Example memory study\n"
    "Service: https://example.supabase.co/functions/v1/qs-service"
)
dialog.privacy.setText(
    '<a href="https://example.org/privacy">Study information and privacy policy</a>'
)
dialog.status.setText("Review the package, then choose Send. Nothing has been uploaded.")
dialog.send.setEnabled(True)
dialog.show()
app.processEvents()
destination = root / ".reference/submission-preview.png"
dialog.grab().save(str(destination))
print(destination)
dialog.hide()
