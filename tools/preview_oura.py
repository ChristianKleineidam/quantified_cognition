"""Render the health screen with synthetic measurements; --google selects Google."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.health import HEALTH_CSV_FIELDS, DailyHealth, HealthSnapshot  # noqa: E402
from addon.oura_ui import OuraDialog  # noqa: E402
from addon.research import SharingConfig  # noqa: E402

app = QApplication([])
if os.name == "nt":
    font_id = QFontDatabase.addApplicationFont(
        str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf")
    )
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
parent = QWidget()
parent.col = SimpleNamespace(path=str(Path(__file__).resolve().parents[1] / "preview.anki2"))
dialog = OuraDialog(parent, SharingConfig())
google = "--google" in sys.argv
if google:
    dialog.provider_selector.blockSignals(True)
    dialog.provider_selector.setCurrentIndex(1)
    dialog.provider_selector.blockSignals(False)
    dialog.prefix, dialog.provider = "google_health", "Google Health"
    dialog.provider_text()
prefix = dialog.prefix
days = {}
for index in range(12):
    values = dict.fromkeys(HEALTH_CSV_FIELDS)
    values.update(
        {
            f"{prefix}_total_sleep_seconds": 25200 + 300 * (index % 4),
            f"{prefix}_light_sleep_seconds": 12600 + 300 * (index % 4),
            f"{prefix}_deep_sleep_seconds": 5400,
            f"{prefix}_rem_sleep_seconds": 7200,
            f"{prefix}_awake_seconds": 3600,
            f"{prefix}_sleep_status": "durations available",
            f"{prefix}_steps": 6789 + 300 * index,
            f"{prefix}_day_basis": "synthetic",
        }
    )
    days[date(2026, 10, 23) - timedelta(days=index)] = DailyHealth(values)
dialog.show_snapshot(HealthSnapshot("synthetic-preview", days))
dialog.show()
app.processEvents()
destination = (
    Path(__file__).resolve().parents[1]
    / ".reference"
    / ("google-health-preview.png" if google else "oura-preview.png")
)
destination.parent.mkdir(exist_ok=True)
dialog.grab().save(str(destination))
print(destination)
