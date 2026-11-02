"""Run the synthetic walkthrough inside an official Anki executable.

The disposable driver is a separate test add-on, never included in the product.
AnkiWeb authentication and synchronization are deliberately left to a separate
interactive test profile; this runner contains no account credentials.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from .desktop_beta import prepare
except ImportError:  # Direct invocation as python tools/official_desktop_beta.py.
    from desktop_beta import prepare

DRIVER = '''
import importlib.metadata
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import aqt
from aqt import gui_hooks
from aqt.qt import QApplication, QTimer
from PyQt6.QtCore import QT_VERSION_STR

root = Path(__file__).resolve().parents[3]
settings = json.loads((root / "driver/options.json").read_text())
for field in ("package", "upgrade_from"):
    if settings[field] is not None:
        settings[field] = Path(settings[field])
args = SimpleNamespace(**settings)
blocked = []

def network_guard(event, values):
    if event in ("socket.connect", "socket.getaddrinfo"):
        address = values[1] if event == "socket.connect" else values[0]
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            blocked.append(event)
            raise OSError("Official disposable beta blocks external Python networking")

sys.addaudithook(network_guard)

def start():
    global monitor
    try:
        source = root / "Anki/Desktop Beta/collection.anki2"
        assert Path(aqt.mw.col.path).resolve() == source.resolve()
        assert (root / "DISPOSABLE_BETA_PROFILE").exists()
        (root / "driver/runtime.json").write_text(json.dumps({
            "executable": sys.executable, "python": sys.version,
            "anki": importlib.metadata.version("anki"),
            "aqt": importlib.metadata.version("aqt"), "qt": QT_VERSION_STR,
        }, indent=2))
        spec = importlib.util.spec_from_file_location(
            "qc_desktop_beta", root / "driver/desktop_beta.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monitor = module.start_walkthrough(QApplication.instance(), root, source, args, blocked)
    except Exception:
        (root / "driver/error.txt").write_text(traceback.format_exc())
        QTimer.singleShot(0, aqt.mw.close)

gui_hooks.profile_did_open.append(start)
'''


def check_final_csv(source: Path, destination: Path, *, timeout: float = 40) -> str:
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
        stamp = db.execute("select max(id) from revlog where ease between 1 and 4").fetchone()[0]
    if stamp is None:
        raise AssertionError("The synthetic final review is missing")
    # The synthetic fixture uses Anki's default four-hour rollover.
    day = (datetime.fromtimestamp(stamp / 1000) - timedelta(hours=4)).date().isoformat()
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            with destination.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            if next((row for row in rows if row["date"] == day), {}).get("recall_count") == "1":
                return day
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError("Final answer missing from fixed CSV after official Anki exited")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--anki-exe", type=Path, required=True)
    parser.add_argument("--upgrade-from", type=Path)
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args()
    root, package, executable = args.root.resolve(), args.package.resolve(), args.anki_exe.resolve()
    if root.exists() or not package.is_file() or not executable.is_file():
        parser.error("Provide a new disposable root, a packaged add-on and an official executable")
    base, source = prepare(root, package, 1, args.dark, True, args.upgrade_from)
    driver = root / "driver"
    driver.mkdir()
    shutil.copyfile(Path(__file__).with_name("desktop_beta.py"), driver / "desktop_beta.py")
    (driver / "options.json").write_text(json.dumps({
        "package": str(package), "choice": "deferred", "scale": 1, "dark": args.dark,
        "empty": False, "health_fixture": True, "review_close": True, "timeout": 240,
        "upgrade_from": str(args.upgrade_from.resolve()) if args.upgrade_from else None,
    }))
    test_addon = base / "addons21/qc_disposable_beta_driver"
    test_addon.mkdir()
    (test_addon / "__init__.py").write_text(DRIVER)
    environment = dict(os.environ, ANKI_SINGLE_INSTANCE_KEY="qc-official-" + root.name,
                       ANKI_SOFTWAREOPENGL="1")
    with (root / "application.stdout.log").open("w") as stdout:
        with (root / "application.stderr.log").open("w") as stderr:
            process = subprocess.Popen(
                [str(executable), "-b", str(base), "-p", "Desktop Beta", "-l", "en_US"],
                env=environment, stdout=stdout, stderr=stderr, cwd=executable.parent,
            )
            try:
                exit_code = process.wait(timeout=330)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only the process created for this disposable fixture.
                process.wait(timeout=20)
                raise
    error = driver / "error.txt"
    if error.exists():
        raise RuntimeError(error.read_text())
    result_path = root / "result.json"
    result = json.loads(result_path.read_text())
    result["official_runtime"] = json.loads((driver / "runtime.json").read_text())
    result["official_executable_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    result["official_exit_code"] = exit_code
    if exit_code != 0:
        result["errors"].append(f"Official executable exited with {exit_code}")
    if not result["errors"]:
        try:
            day = check_final_csv(source, root / "scores.csv")
            result["checks"].append({
                "name": "external interpreter reads final answer after official executable exit",
                "detail": day,
            })
        except Exception as exc:
            result["errors"].append(str(exc))
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"root": str(root), "checks": len(result["checks"]),
                      "errors": result["errors"], "runtime": result["official_runtime"]}, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
