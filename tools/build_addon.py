"""Build an installable .ankiaddon with source and configuration only."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "quantified-cognition-0.19.0.ankiaddon"
destination.parent.mkdir(exist_ok=True)
manifest = {
    "package": "quantified_self_memory",
    "name": "Quantified Cognition",
    "min_point_version": 250900,
    "max_point_version": 260903,
    "human_version": "0.19.0",
}
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    for path in sorted((root / "addon").iterdir()):
        if path.suffix in (".py", ".json", ".md"):
            archive.write(path, path.name)
    archive.write(root / "README.md", "README.md")
    archive.write(root / "LICENSE", "LICENSE")
    for name in (
        "ANKIWEB_DESCRIPTION.txt",
        "PRIVACY.md",
        "RESEARCH_INFORMATION.md",
        "RESEARCH_DESIGN.md",
        "RESEARCH_OPERATIONS.md",
        "RESEARCH_CONTROLS.md",
        "OURA_APPROVAL.md",
        "GOOGLE_HEALTH.md",
        "INSIGHTS.md",
        "PERFORMANCE.md",
        "STATISTICAL_VALIDATION.md",
        "STATISTICAL_RESULTS.json",
        "DEPLOYMENT.md",
        "DEPLOYED.md",
        "BACKEND_OPERATIONS.md",
        "BETA_CHECKLIST.md",
        "DESKTOP_BETA.md",
        "SILENTWORKER.md",
    ):
        archive.write(root / "docs" / name, "docs/" + name)
print(destination)
