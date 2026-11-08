"""Render the real sharing prompt with a fresh synthetic profile."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.research import sharing_state  # noqa: E402
from addon.sharing_ui import SharingDialog  # noqa: E402

app = QApplication([])
if os.name == "nt":
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font))
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
with tempfile.TemporaryDirectory(dir=root / ".reference", prefix="sharing-preview-") as directory:
    parent = QWidget()
    profile = Path(directory) / "collection.anki2"
    dialog = SharingDialog(parent, str(profile), sharing_state(profile))
    dialog.show()
    for _ in range(4):
        app.processEvents()
    destination = root / ".reference" / "sharing-preview-compact.png"
    dialog.grab().save(str(destination))
    print(destination)
    dialog.hide()
