"""Exercise a packaged add-on in a new, offline, disposable full Anki application.

Run with the project's aqt Python environment. The required root must not exist.
No normal Anki profile is opened. Screenshots and a JSON result stay in root.
"""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import importlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


def prepare(root, package, scale, dark, history, upgrade_from=None):
    import anki.lang
    from anki.collection import Collection
    from aqt.addons import AddonManager, InstallOk
    from aqt.profiles import ProfileManager
    from aqt.theme import Theme

    root.mkdir(parents=True, exist_ok=False)
    print("Preparing disposable profile", flush=True)
    (root / "DISPOSABLE_BETA_PROFILE").write_text("Synthetic data only\n")
    base = root / "Anki"
    base.mkdir()
    anki.lang.set_lang("en_US")
    pm = ProfileManager(base)
    pm.setupMeta()
    pm.meta.update(firstRun=False, defaultLang="en_US", updates=False, check_for_updates=False)
    pm.setUiScale(scale)
    pm.set_theme(Theme.DARK if dark else Theme.LIGHT)
    pm.create("Desktop Beta")
    pm.load("Desktop Beta")
    pm.profile.update(autoSync=False, syncKey=None)
    pm.save()
    manager = AddonManager.__new__(AddonManager)
    manager.mw = SimpleNamespace(pm=pm)
    installed = manager.install(str(upgrade_from or package), force_enable=True)
    print("Installed package", installed, flush=True)
    assert isinstance(installed, InstallOk), installed
    assert installed.compatible, installed
    source = Path(pm.collectionPath())
    print("Creating synthetic collection", flush=True)
    col = Collection(str(source))
    col.set_config("fsrs", True)
    if history:
        today = date.today()
        note = col.new_note(col.models.by_name("Basic"))
        note["Front"], note["Back"] = "Synthetic beta card 0", "Synthetic answer"
        col.add_note(note, 1)
        card = note.cards()[0]
        template_note = col.db.first("select * from notes where id=?", note.id)
        template_card = col.db.first("select * from cards where id=?", card.id)
        note_columns = [r[1] for r in col.db.all("pragma table_info(notes)")]
        card_columns = [r[1] for r in col.db.all("pragma table_info(cards)")]
        logs, notes, cards, deleted = [], [], [], []
        for n in range(880):
            nr, cr = (
                dict(zip(note_columns, template_note, strict=True)),
                dict(zip(card_columns, template_card, strict=True)),
            )
            front = f"Synthetic beta card {n}"
            nr.update(
                id=note.id + n,
                guid=hashlib.sha1(front.encode()).hexdigest()[:10],
                flds=front + "\x1fSynthetic answer",
                sfld=front,
                csum=int(hashlib.sha1(front.encode()).hexdigest()[:8], 16),
            )
            cr.update(
                id=card.id + n,
                nid=nr["id"],
                type=2,
                queue=2,
                ivl=10,
                factor=2500,
                due=col.sched.today if n == 0 else col.sched.today + 90,
            )
            if n:
                notes.append(tuple(nr[c] for c in note_columns))
            cards.append(tuple(cr[c] for c in card_columns))
            for j in range(11):
                day = today - timedelta(days=340 - j * (20 if n % 2 else 31) - n % 11)
                if day >= today:
                    continue
                stamp = int(
                    datetime.combine(day, datetime.min.time()).replace(hour=12).timestamp() * 1000
                )
                logs.append(
                    (
                        stamp + n,
                        cr["id"],
                        -1,
                        1 if (n + j) % 9 == 0 else 3,
                        10,
                        10,
                        2500,
                        1000,
                        0 if j == 0 else 1,
                    )
                )
            if n >= 870:
                deleted.append((cr["id"],))
        col.db.execute("begin")
        col.db.executemany(
            "insert into notes values (" + ",".join("?" for _ in note_columns) + ")", notes
        )
        col.db.executemany(
            "insert or replace into cards values (" + ",".join("?" for _ in card_columns) + ")",
            cards,
        )
        col.db.executemany("insert into revlog values (?,?,?,?,?,?,?,?,?)", logs)
        col.db.executemany("delete from cards where id=?", deleted)
        col.db.execute("commit")
    if upgrade_from:
        # Create the old version's real results cache, without inventing consent
        # timing for a version/profile whose results already existed.
        sys.path.insert(0, pm.addonFolder())
        old_backend = importlib.import_module("quantified_self_memory.backend")
        old_storage = importlib.import_module("quantified_self_memory.storage")
        old_report = old_backend.build_report(col)
        old_storage.save_report(old_report, old_storage.database_path(source))
        print("Prepared legacy calculation", old_report.calculation_version, flush=True)
        for name in list(sys.modules):
            if name == "quantified_self_memory" or name.startswith("quantified_self_memory."):
                del sys.modules[name]
        installed = manager.install(str(package), force_enable=True)
        assert isinstance(installed, InstallOk) and installed.compatible
    col.close()
    print("Synthetic collection ready", flush=True)
    shutil.copy2(source, root / "before-testing.anki2")
    pm.db.close()
    return base, source


def seed_health(source):
    """Synthetic local cache only: this never connects or imports from a provider."""
    from dataclasses import replace

    from quantified_self_memory.google_health import GOOGLE_HEALTH_CSV_FIELDS, GoogleHealthImport
    from quantified_self_memory.health import (
        load_health,
        save_google_health_import,
        save_oura_import,
    )
    from quantified_self_memory.oura import Activity, OuraImport, Sleep

    rng = random.Random(684)
    periods, activity = [], []
    start = date.today() - timedelta(days=350)
    for n in range(352):
        day = start + timedelta(days=n)
        wake = datetime.combine(day, datetime.min.time()).replace(hour=7)
        total = rng.randrange(23000, 31000)
        deep, rem = rng.randrange(3500, 7000), rng.randrange(4200, 7600)
        light = total - deep - rem
        periods.append(
            Sleep(
                f"beta-main-{n}",
                day.isoformat(),
                "long_sleep",
                (wake - timedelta(seconds=total + 1200)).astimezone().isoformat(),
                wake.astimezone().isoformat(),
                total,
                light,
                None if n % 17 == 0 else deep,
                rem,
                1200,
                "v2",
            )
        )
        for hour in [10, 17] if n % 3 == 0 else []:
            nap = wake.replace(hour=hour)
            periods.append(
                Sleep(
                    f"beta-nap-{n}-{hour}",
                    day.isoformat(),
                    "late_nap",
                    nap.astimezone().isoformat(),
                    (nap + timedelta(minutes=30)).astimezone().isoformat(),
                    1800,
                    1000,
                    500,
                    300,
                    0,
                    "v2",
                )
            )
        activity.append(Activity(day.isoformat(), rng.randrange(2000, 14000)))
    save_oura_import(
        source,
        OuraImport(tuple(periods), tuple(activity)),
        authoritative_window=(start.isoformat(), (start + timedelta(days=352)).isoformat()),
    )
    snapshot = load_health(source)
    google_days = {
        day: {
            field: values.values.get(field.replace("google_health_", "oura_"), "")
            for field in GOOGLE_HEALTH_CSV_FIELDS
        }
        for day, values in snapshot.days.items()
    }
    for values in google_days.values():
        values["google_health_sleep_types"] = "STAGES"
    sessions = tuple(
        replace(s, provider="google_health", key="google-" + s.key) for s in snapshot.sessions
    )
    save_google_health_import(
        source,
        GoogleHealthImport(google_days, sessions),
        start=start.isoformat(),
        end=(start + timedelta(days=352)).isoformat(),
    )


def start_walkthrough(app, root, source, args, blocked):
    """Drive only synthetic profiles; also callable by a disposable test add-on."""
    import aqt
    from aqt.qt import QApplication, QPushButton, QTimer

    results = {
        "package": args.package.name,
        "choice": args.choice,
        "scale": args.scale,
        "dark": args.dark,
        "history": not args.empty,
        "synthetic_health": args.health_fixture,
        "upgrade_from": args.upgrade_from.name if args.upgrade_from else None,
        "checks": [],
        "errors": [],
    }
    state = {"stage": "prompt", "start": time.monotonic()}

    def check(name, detail=None):
        results["checks"].append({"name": name, "detail": detail})
        print("PASS", name, flush=True)

    def capture(widget, name):
        assert widget.grab().save(str(root / (name + ".png")))
        results.setdefault("screens", []).append(
            {"name": name, "width": widget.width(), "height": widget.height()}
        )

    def open_memory():
        actions = [
            item
            for item in aqt.mw.form.menuTools.actions()
            if item.text().startswith("Quantified Cognition")
        ]
        assert [item.text() for item in actions] == ["Quantified Cognition…"]
        check("one Quantified Cognition Tools entry")
        QTimer.singleShot(0, actions[0].trigger)

    def report_button(label):
        dialog = state["ui"]._dialog
        assert dialog is not None and dialog.isVisible()
        buttons = [button for button in dialog.findChildren(QPushButton) if button.text() == label]
        assert len(buttons) == 1 and buttons[0].isVisible(), label
        QTimer.singleShot(0, buttons[0].click)

    def finish():
        timer.stop()
        results["blocked_external_requests"] = blocked
        (root / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        QTimer.singleShot(0, aqt.mw.close)

    def poll():
        try:
            if time.monotonic() - state["start"] > args.timeout:
                raise TimeoutError("Timed out at " + state["stage"])
            widgets = QApplication.topLevelWidgets()
            stage = state["stage"]
            if stage == "prompt":
                dialogs = [
                    w for w in widgets if type(w).__name__ == "SharingDialog" and w.isVisible()
                ]
                if not dialogs:
                    return
                dialog = dialogs[0]
                assert dialog.snapshot.first_exposure is None
                if args.upgrade_from:
                    assert dialog.snapshot.history == "unknown_legacy"
                capture(dialog, "01-first-sharing")
                check("sharing prompt precedes first results")
                index = {"opt_in": 0, "deferred": 1, "declined": 2}[args.choice]
                state["stage"] = "memory_open"
                dialog.buttons[index].click()
            elif stage == "memory_open":
                if any(type(w).__name__ == "SharingDialog" and w.isVisible() for w in widgets):
                    return
                state["ui"] = importlib.import_module("quantified_self_memory.ui")
                research = importlib.import_module("quantified_self_memory.research")
                snapshot = research.sharing_state(source)
                assert snapshot.current_choice == args.choice
                assert snapshot.first_exposure is None
                check("choice saved without exposing results or uploading")
                if args.health_fixture:
                    from quantified_self_memory.background import run_file_job

                    state["stage"] = "seed_health"

                    def seeded(_result):
                        check(
                            "synthetic health sessions saved locally without provider connections"
                        )
                        open_memory()
                        state["stage"] = "memory"

                    run_file_job(
                        lambda: seed_health(source),
                        seeded,
                        lambda error: results["errors"].append(str(error)),
                    )
                    return
                open_memory()
                state["stage"] = "memory"
            elif stage == "memory":
                dialog = state["ui"]._dialog
                if dialog is None or dialog.report is None or dialog.busy:
                    return
                capture(dialog, "02-memory")
                check(
                    "packaged memory screen loaded",
                    {"days": len(dialog.report.days), "summary": dialog.summary.text()},
                )
                assert dialog.report.calculation_version == 5
                if not args.empty:
                    assert dialog.report.reconstructed_deleted_cards == 10
                    details = []
                    for i in range(4):
                        dialog.metric_selector.setCurrentIndex(i)
                        metric_key = dialog.metric_key()
                        eligible = [d for d in dialog.report.days if getattr(d, metric_key).count]
                        assert eligible
                        dialog.select_day(
                            max(eligible, key=lambda d: getattr(d, metric_key).count).day
                        )
                        details.append(
                            {
                                "metric": dialog.metric_selector.currentText(),
                                "score": dialog.summary.text(),
                                "completeness": dialog.completeness.text(),
                            }
                        )
                    check("four metrics and agreed-preset deleted histories", details)
                state["stage"] = "settings_open"
            elif stage == "settings_open":
                report_button("Daily settings…")
                state["stage"] = "settings"
            elif stage == "settings":
                dialogs = [
                    w
                    for w in widgets
                    if type(w).__name__ == "DailySettingsDialog" and w.isVisible()
                ]
                if not dialogs or dialogs[0].file_busy:
                    return
                dialog = dialogs[0]
                capture(dialog, "03-daily-settings")
                dialog.enabled.setChecked(True)
                dialog.csv_input.setText(str(root / "scores.csv"))
                state["stage"] = "export_wait"
                dialog.save_button.click()
            elif stage == "export_wait":
                if any(
                    type(w).__name__ == "DailySettingsDialog" and w.isVisible() for w in widgets
                ):
                    return
                state["ui"]._daily_updater.check(force=True)
                state["stage"] = "export"
            elif stage == "export":
                if not (root / "scores.csv").exists():
                    return
                import csv

                try:
                    with (root / "scores.csv").open(encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                except OSError:
                    # Windows can briefly deny a reader during replacement or
                    # a file scan. The bounded poll retries without delaying Qt.
                    return
                check("daily fixed CSV readable by independent file reader", {"rows": len(rows)})
                report_button("Health data…")
                state["stage"] = "health"
            elif stage == "health":
                dialog = state["ui"]._oura_dialog
                if dialog is None or dialog.busy or "Loading" in dialog.status.text():
                    return
                capture(dialog, "04-health-disconnected")
                check("health screen usable without a provider connection", dialog.status.text())
                dialog.close()
                report_button("Personal insights…")
                state["stage"] = "insights"
            elif stage == "insights":
                dialog = state["ui"]._insights_dialog
                if dialog is None or dialog.busy:
                    return
                assert dialog.result is not None, dialog.summary.text()
                capture(dialog, "05-insights")
                check("insights overview", dialog.summary.text())
                state["stage"] = "insights_naps"
                dialog.section.setCurrentIndex(1)
            elif stage == "insights_naps":
                dialog = state["ui"]._insights_dialog
                if dialog.busy:
                    return
                capture(dialog, "06-insights-naps")
                check("nap comparisons separate from main sleep", dialog.summary.text())
                if args.health_fixture and dialog.provider.count() > 1:
                    state["stage"] = "insights_google"
                    dialog.provider.setCurrentIndex(1)
                else:
                    dialog.close()
                    state["stage"] = "sharing_later_open"
            elif stage == "insights_google":
                dialog = state["ui"]._insights_dialog
                if dialog.busy or dialog.result.provider != "google_health":
                    return
                capture(dialog, "07-insights-google")
                check("Google local historical insights", dialog.summary.text())
                dialog.close()
                state["stage"] = "sharing_later_open"
            elif stage == "sharing_later_open":
                report_button("Research sharing…")
                state["stage"] = "sharing_later"
            elif stage == "sharing_later":
                dialogs = [
                    w for w in widgets if type(w).__name__ == "SharingDialog" and w.isVisible()
                ]
                if not dialogs or dialogs[0].busy:
                    return
                state["stage"] = "sharing_saved"
                if dialogs[0].snapshot.current_choice != "opt_in":
                    dialogs[0].buttons[0].click()
            elif stage == "sharing_saved":
                dialogs = [
                    w for w in widgets if type(w).__name__ == "SharingDialog" and w.isVisible()
                ]
                if dialogs:
                    dialog = dialogs[0]
                    if dialog.busy or dialog.snapshot.current_choice != "opt_in":
                        return
                    dialog.close_button.click()
                state["stage"] = "audit"
            elif stage == "audit":
                if any(type(w).__name__ == "SharingDialog" and w.isVisible() for w in widgets):
                    return
                from quantified_self_memory.research import sharing_state

                snapshot = sharing_state(source)
                if not args.empty:
                    expected = None if args.upgrade_from else args.choice == "opt_in"
                    assert snapshot.metadata()["research_consent_before_first_view"] is expected
                check(
                    "first-consent timing preserved after later opt-in",
                    snapshot.metadata()["research_consent_before_first_view"],
                )
                report_button("Personal insights…")
                state["stage"] = "return_visit"
            elif stage == "return_visit":
                dialog = state["ui"]._insights_dialog
                if dialog is None or dialog.busy or dialog.result is None:
                    return
                assert not dialog.changes.text().startswith("First visit")
                capture(dialog, "08-return-visit")
                check(
                    "reopened visit compares with previous displayed baseline",
                    dialog.changes.text(),
                )
                dialog.close()
                state["stage"] = "review" if args.review_close else "done"
                if args.review_close:
                    aqt.mw.moveToState("review")
            elif stage == "review":
                if aqt.mw.reviewer.card is None:
                    return
                aqt.mw.reviewer._showAnswer()
                state["stage"] = "answer"
            elif stage == "answer":
                # A click starts an asynchronous native answer. Close at its
                # completion hook, on the next event, as an actual window-close
                # input would; never unload while the answer is still pending.
                def answered(*_args):
                    check("native final-card answer followed immediately by window close")
                    finish()

                aqt.gui_hooks.reviewer_did_answer_card.append(answered)
                state["stage"] = "closing"
                aqt.mw.reviewer._answerCard(3)
            elif stage == "done":
                finish()
        except Exception:
            results["errors"].append(traceback.format_exc())
            print(results["errors"][-1], flush=True)
            finish()

    timer = QTimer(app)
    timer.setInterval(100)
    timer.timeout.connect(poll)
    timer.start()
    return results, timer, check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--choice", choices=("opt_in", "deferred", "declined"), default="deferred")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--review-close", action="store_true")
    parser.add_argument("--health-fixture", action="store_true")
    parser.add_argument("--upgrade-from", type=Path)
    parser.add_argument("--platform", choices=("windows", "xcb", "offscreen", "cocoa"))
    parser.add_argument("--timeout", type=float, default=100)
    args = parser.parse_args()
    faulthandler.dump_traceback_later(30, repeat=True)
    root, package = args.root.resolve(), args.package.resolve()
    if root.exists() or not package.is_file() or args.timeout <= 0:
        parser.error("Use a new disposable root and an existing packaged add-on.")
    os.environ["ANKI_SINGLE_INSTANCE_KEY"] = (
        "qs-beta-" + hashlib.sha256(str(root).encode()).hexdigest()[:20]
    )
    os.environ.setdefault("ANKI_SOFTWAREOPENGL", "1")
    os.environ["QT_QPA_PLATFORM"] = args.platform or (
        "windows" if sys.platform == "win32" else "offscreen"
    )
    base, source = prepare(root, package, args.scale, args.dark, not args.empty, args.upgrade_from)
    blocked = []

    def network_guard(event, values):
        if event in ("socket.connect", "socket.getaddrinfo"):
            address = values[1] if event == "socket.connect" else values[0]
            host = address[0] if isinstance(address, tuple) else address
            if host not in ("127.0.0.1", "::1", "localhost"):
                blocked.append(event)
                raise OSError("Disposable beta test blocks external network access")

    sys.addaudithook(network_guard)
    import aqt

    app = aqt._run(["anki", "-b", str(base), "-p", "Desktop Beta", "-l", "en_US"], exec=False)
    assert app is not None
    faulthandler.cancel_dump_traceback_later()
    if args.scale != 1:
        font = app.font()
        font.setPointSizeF(font.pointSizeF() * args.scale)
        app.setFont(font)
    results, timer, check = start_walkthrough(app, root, source, args, blocked)
    app.exec()
    # Anki clears its exception handler during cleanup. This standalone test
    # still needs subprocess reader threads for independent post-exit checks.
    sys.excepthook = sys.__excepthook__
    faulthandler.cancel_dump_traceback_later()
    if args.review_close and not results["errors"]:
        # A separate interpreter reads the real CSV after Qt has stopped. It
        # waits only for the owned export thread, not a Qt completion callback.
        script = """
import csv,sqlite3,sys,time
from datetime import datetime,timedelta
from pathlib import Path
source,destination=map(Path,sys.argv[1:])
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
    stamp=db.execute('select max(id) from revlog where ease between 1 and 4').fetchone()[0]
day=(datetime.fromtimestamp(stamp/1000)-timedelta(hours=4)).date().isoformat()
until=time.monotonic()+40
while time.monotonic()<until:
    try:
        with destination.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        row=next((r for r in rows if r['date']==day),{})
        if row.get('recall_count')=='1':
            print('Final answer published on '+day)
            break
    except (OSError,ValueError): pass
    time.sleep(0.1)
else: raise AssertionError('Final answer missing from fixed CSV after Qt exit')
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(source), str(root / "scores.csv")],
            capture_output=True,
            text=True,
            timeout=50,
        )
        if completed.returncode:
            results["errors"].append(completed.stderr)
        else:
            check("external interpreter reads final answer after Qt exit", completed.stdout.strip())
        (root / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("RESULT", root / "result.json", flush=True)
    return 1 if results["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
