"""Build an installable .ankiaddon with source and configuration only."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

root = Path(__file__).resolve().parents[1]
version = "0.23.0"
destination = root / "dist" / f"quantified-cognition-{version}.ankiaddon"
destination.parent.mkdir(exist_ok=True)
manifest = {
    "package": "quantified_self_memory",
    "name": "Quantified Cognition",
    "min_point_version": 250900,
    "max_point_version": 260903,
    "human_version": version,
}


def write_entry(archive, name, content):
    # Fixed ZIP metadata makes the same Git source reproducible across checkouts.
    entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = ZIP_DEFLATED
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, content)


with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    write_entry(archive, "manifest.json", json.dumps(manifest, indent=2))
    for path in sorted((root / "addon").iterdir(), key=lambda path: path.name):
        if path.suffix in (".py", ".json", ".md"):
            write_entry(archive, path.name, path.read_bytes())
    write_entry(archive, "README.md", (root / "README.md").read_bytes())
    for path in sorted((root / "addon/crypto_wheels").iterdir(), key=lambda path: path.name):
        if path.suffix in (".whl", ".json", ".md"):
            write_entry(archive, "crypto_wheels/" + path.name, path.read_bytes())
    write_entry(archive, "LICENSE", (root / "LICENSE").read_bytes())
    for name in (
        "ANKIWEB_DESCRIPTION.txt",
        "RELEASE_CANDIDATE.md",
        "PRIVACY.md",
        "RESEARCH_INFORMATION.md",
        "RESEARCH_DESIGN.md",
        "RESEARCH_OPERATIONS.md",
        "DATA_PROTECTION_ASSESSMENT.md",
        "LAUNCH.md",
        "RESEARCH_CONTROLS.md",
        "ENCRYPTION.md",
        "HOSTED_SMOKE_RESULT.json",
        "HOSTED_ENCRYPTION_RESULT.json",
        "WORKOUT_VALIDATION.json",
        "OURA_APPROVAL.md",
        "OURA_APPLICATION_APPROVAL.json",
        "OURA_RESEARCH_REVOCATION.md",
        "OURA_RESEARCH_REVOCATION_RESULT.json",
        "RESEARCH_WORKSPACE.md",
        "research-e2e-2026-11-08.json",
        "GOOGLE_HEALTH.md",
        "INSIGHTS.md",
        "PERFORMANCE.md",
        "STATISTICAL_VALIDATION.md",
        "STATISTICAL_RESULTS.json",
        "DEPLOYMENT.md",
        "DEPLOYED.md",
        "BACKEND_OPERATIONS.md",
        "BACKEND_RECOVERY.md",
        "BETA_CHECKLIST.md",
        "DESKTOP_BETA.md",
        "SILENTWORKER.md",
        "WEBSITE.md",
    ):
        write_entry(archive, "docs/" + name, (root / "docs" / name).read_bytes())
print(destination)
