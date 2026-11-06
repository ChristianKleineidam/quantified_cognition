"""Render the actual Qt report with synthetic examples, without opening a profile."""

from __future__ import annotations

import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aqt.qt import QApplication, QFont, QFontDatabase, QWidget  # noqa: E402

from addon.backend import Report  # noqa: E402
from addon.metrics import Attempt, Settings, daily_results  # noqa: E402
from addon.ui import MemoryReportDialog  # noqa: E402

app = QApplication([])
if os.name == "nt":
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id >= 0:
        app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
parent = QWidget()
parent.addonManager = type("Manager", (), {"getConfig": lambda self, name: None})()
dialog = MemoryReportDialog(parent, "addon")
rng = random.Random(42)
start = date(2026, 8, 15)
attempts = []
for card in range(600):
    day = start + timedelta(days=rng.randrange(10))
    while day <= date(2026, 10, 8):
        rid = int(datetime.combine(day, datetime.min.time()).replace(hour=12).timestamp() * 1000)
        rid += card
        p = rng.uniform(0.7, 0.95)
        rating = 3 if rng.random() < p else 1
        attempts.append(Attempt(rid, card, day, rating, p, True))
        day += timedelta(days=rng.randrange(2, 8) if card < 200 else rng.randrange(31, 46))
report = Report(
    daily_results(attempts),
    "2026-10-08T12:00:00+00:00",
    "26.9.3",
    "a13074672c22",
    4,
    Settings(),
    600,
    0,
    0,
    "CEST",
)
dialog.accept_report(report)
dialog.show()
app.processEvents()
destination = Path(__file__).resolve().parents[1] / ".reference" / "report-preview.png"
dialog.grab().save(str(destination))
print(destination)
dialog.start_date.setDate(date(2026, 8, 15))
dialog.end_date.setDate(date(2026, 8, 24))
dialog.filter_dates.setChecked(True)
for index, prefix in ((1, "formation"), (2, "short"), (3, "long")):
    dialog.metric_selector.setCurrentIndex(index)
    dialog.select_day(date(2026, 8, 20))
    app.processEvents()
    band_destination = destination.with_name(f"report-{prefix}-preview.png")
    dialog.grab().save(str(band_destination))
    print(band_destination)

dialog.inspect_button.setChecked(True)
app.processEvents()
table_destination = destination.with_name("report-inspect-preview.png")
dialog.grab().save(str(table_destination))
print(table_destination)

dialog.inspect_button.setChecked(False)
dialog.filter_dates.setChecked(False)
dialog.metric_selector.setCurrentIndex(1)
dialog.select_day(date(2026, 9, 25))
app.processEvents()
provisional_destination = destination.with_name("report-provisional-preview.png")
dialog.grab().save(str(provisional_destination))
print(provisional_destination)
dialog.close()
