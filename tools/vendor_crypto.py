"""Fetch pinned, hash-verified PyCryptodomex wheels for supported Anki platforms."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

VERSION = "3.24.1"
TARGETS = {
    "win32-x86_64": "win_amd64",
    "win32-aarch64": "win_arm64",
    "darwin-universal2": "macosx_10_9_universal2",
    "linux-x86_64": "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "linux-aarch64": "manylinux2014_aarch64.manylinux_2_17_aarch64",
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / "addon/crypto_wheels"
    folder.mkdir(exist_ok=True)
    with urlopen(f"https://pypi.org/pypi/pycryptodomex/{VERSION}/json", timeout=30) as response:
        release = json.load(response)
    manifest = {"package": "pycryptodomex", "version": VERSION, "platforms": {}}
    for target, suffix in TARGETS.items():
        filename = f"pycryptodomex-{VERSION}-cp37-abi3-{suffix}.whl"
        row = next(item for item in release["urls"] if item["filename"] == filename)
        expected = row["digests"]["sha256"]
        destination = folder / filename
        if not destination.exists():
            with urlopen(row["url"], timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Crypto wheel digest mismatch")
            destination.write_bytes(data)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            raise ValueError("Existing crypto wheel digest mismatch")
        manifest["platforms"][target] = {"filename": filename, "sha256": expected}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified {len(TARGETS)} PyCryptodomex {VERSION} wheels")


if __name__ == "__main__":
    main()
