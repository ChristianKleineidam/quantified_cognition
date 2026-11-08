"""Run the offline release checks locally or in Silentworker's isolated executor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def preflight(root: Path, node: str, pglite: Path) -> dict:
    """Missing native/UI/database dependencies must fail, rather than skip coverage."""
    versions = {name: importlib.metadata.version(name) for name in (
        "anki", "aqt", "pytest", "ruff", "tzdata"
    )}
    if versions["anki"] != "26.9.3" or versions["aqt"] != "26.9.3":
        raise RuntimeError("This verification target requires Anki and aqt 26.9.3")
    if not pglite.is_file():
        raise RuntimeError("PGLITE_TEST_MODULE must point to PGlite's dist/index.js")
    deployment = root / "supabase/deployment.json"
    if deployment.is_file():
        state = json.loads(deployment.read_text(encoding="utf-8"))
        flags = state["environment"]
        if any(flags.get(key) != "false" for key in ("GOOGLE_HEALTH_ENABLED", "RESEARCH_ENABLED")):
            raise RuntimeError("Google Health and research uploads must remain disabled")
        if state.get("production_access_enabled") is not False or flags.get("OURA_ENABLED") not in (
            "true", "false"
        ):
            raise RuntimeError("Unexpected release state or Oura feature configuration")
    workflows = root / ".github" / "workflows"
    if workflows.exists() and any(p.suffix in (".yml", ".yaml") for p in workflows.iterdir()):
        raise RuntimeError("GitHub Actions are not used for this project")
    # Importing aqt alone would not prove Qt's native libraries can load.
    subprocess.run(
        [sys.executable, "-c", "from aqt.qt import QApplication; app=QApplication([])"],
        cwd=root, check=True, timeout=45,
    )
    versions["node"] = subprocess.check_output([node, "--version"], text=True).strip()
    if int(versions["node"].lstrip("v").split(".")[0]) < 22:
        raise RuntimeError("Backend tests require Node 22 or later")
    subprocess.run(
        [node, "--input-type=module", "-e",
         "import {pathToFileURL} from 'node:url'; "
         "const {PGlite}=await import(pathToFileURL(process.argv[1]).href); "
         "if(typeof PGlite!=='function') process.exit(1)", str(pglite.resolve())],
        check=True, timeout=45,
    )
    return versions


def inspect_package(root: Path) -> dict:
    packages = list((root / "dist").glob("*.ankiaddon"))
    if len(packages) != 1:
        raise RuntimeError("Expected exactly one freshly built add-on")
    with ZipFile(packages[0]) as archive:
        names = archive.namelist()
        crypto_files = set()
        crypto = {"platforms": {}}
        if "private_storage.py" in names or "crypto_runtime.py" in names:
            crypto = json.loads((root / "addon/crypto_wheels/manifest.json").read_text())
            crypto_files = {"crypto_wheels/manifest.json", "crypto_wheels/README.md"}
        for spec in crypto["platforms"].values():
            name = "crypto_wheels/" + spec["filename"]
            crypto_files.add(name)
            if hashlib.sha256(archive.read(name)).hexdigest() != spec["sha256"]:
                raise RuntimeError("Packaged crypto wheel digest mismatch")
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate archive entries")
        for name in names:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise RuntimeError(f"Unsafe archive path: {name}")
            if not (name in {"manifest.json", "README.md", "LICENSE"} or name in crypto_files
                    or ("/" not in name and path.suffix in {".py", ".json", ".md"})
                    or (name.startswith("docs/") and path.suffix in {".md", ".json", ".txt"})):
                raise RuntimeError(f"Unexpected packaged file: {name}")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["name"] != "Quantified Cognition":
            raise RuntimeError("Wrong product name")
        if manifest["package"] != "quantified_self_memory":
            raise RuntimeError("The stable Anki package identity changed")
        project_version = re.search(
            r'^version\s*=\s*"([^"]+)"\s*$',
            (root / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE,
        )
        if not project_version or Version(project_version[1]) != Version(manifest["human_version"]):
            raise RuntimeError("Project and add-on release versions differ")
        if packages[0].name != f"quantified-cognition-{manifest['human_version']}.ankiaddon":
            raise RuntimeError("Package filename does not identify its release version")
        if any(info.date_time != (1980, 1, 1, 0, 0, 0) for info in archive.infolist()):
            raise RuntimeError("Package contains variable ZIP timestamps")
        if archive.read("docs/ANKIWEB_DESCRIPTION.txt") != (
            root / "docs" / "ANKIWEB_DESCRIPTION.txt"
        ).read_bytes():
            raise RuntimeError("Packaged AnkiWeb description differs from source")
        for name in names:
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")
    return {"file": packages[0].name, "entries": len(names), "manifest": manifest,
            "sha256": hashlib.sha256(packages[0].read_bytes()).hexdigest()}


def run_checks(root: Path, node: str, pglite: Path, report: Path,
               postgres_bin: Path | None = None) -> int:
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "platform": sys.platform, "checks": []}
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ["PGLITE_TEST_MODULE"] = str(pglite.resolve())
    try:
        result["runtime"] = preflight(root, node, pglite)
        commands = [
            ("lint", [sys.executable, "-m", "ruff", "check", "."]),
            # Includes the seeded formation/phase simulation gates and real Qt tests.
            ("python", [sys.executable, "-m", "pytest", "-q", "-ra",
                        f"--junitxml={report.with_suffix('.xml')}"]),
            ("backend", [node, "--test", "--test-concurrency=1", *[
                str(p) for p in sorted((root / "supabase" / "tests").glob("*.test.mjs"))
            ]]),
        ]
        if postgres_bin is not None:
            commands.append(("native_backend", [
                sys.executable, "tools/native_postgres.py", "--postgres-bin", str(postgres_bin),
                "--report", str(report.with_name("native-backend.xml")),
            ]))
        commands.append(("package", [sys.executable, "tools/build_addon.py"]))
        for name, command in commands:
            print(f"\n=== {name} ===", flush=True)
            start = time.monotonic()
            completed = subprocess.run(command, cwd=root, check=False, timeout=2100)
            result["checks"].append({"name": name, "exit_code": completed.returncode,
                                     "seconds": round(time.monotonic() - start, 2)})
            if completed.returncode:
                raise RuntimeError(f"{name} exited with {completed.returncode}")
        result["package"] = inspect_package(root)
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        print(f"Verification failed: {exc}", file=sys.stderr, flush=True)
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        report.parent.mkdir(parents=True, exist_ok=True)
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report)
    return 0 if result["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--pglite", type=Path, default=os.environ.get("PGLITE_TEST_MODULE"))
    parser.add_argument("--report", type=Path, default=ROOT / ".reference/verification/report.json")
    parser.add_argument("--postgres-bin", type=Path)
    args = parser.parse_args()
    if args.pglite is None:
        parser.error("Set PGLITE_TEST_MODULE or pass --pglite")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(run_checks(ROOT, args.node, args.pglite, args.report, args.postgres_bin))


if __name__ == "__main__":
    main()
