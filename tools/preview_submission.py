"""Render the combined research dialog with a synthetic package."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.research import SharingConfig, SharingSnapshot  # noqa: E402
from addon.sharing_ui import SharingDialog  # noqa: E402

app = QApplication([])
if os.name == "nt":
    font_id = QFontDatabase.addApplicationFont(
        str(Path(os.environ["WINDIR"]) / "Fonts/segoeui.ttf")
    )
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
parent = QWidget()
sharing = SharingConfig()
dialog = SharingDialog(
    parent,
    str(root / ".reference/preview.anki2"),
    SharingSnapshot(
        "synthetic", "new",
        [{"scope": sharing.scope, "event_type": "sharing_choice", "choice": "opt_in"}],
        sharing,
    ),
)
dialog.enrolled = True
dialog.package = {"submission_id": "synthetic"}
dialog.details_button.setText("Full sharing notice and study information")
dialog.preview.setText("180 days · 2026-04-27 to 2026-10-23\nRecipient · Example memory study")
dialog.preview.show()
dialog.privacy.setText(
    '<a href="https://example.org/privacy">Study information and privacy policy</a>'
)
dialog.status.setText("Review the package, then choose Send. Nothing has been uploaded.")
dialog.update_actions()
dialog.show()
for _ in range(4):
    app.processEvents()
destination = root / ".reference/submission-preview.png"
dialog.grab().save(str(destination))
print(destination)
dialog.hide()
