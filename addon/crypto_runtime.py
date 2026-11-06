"""Load the bundled, pinned crypto library without installing into Anki's Python."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from zipfile import ZipFile

_lock = threading.Lock()
_loaded = None


def primitives():
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded
        folder = Path(__file__).resolve().parent / "crypto_wheels"
        manifest = json.loads((folder / "manifest.json").read_text())
        machine = platform.machine().lower()
        machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
        target = "darwin-universal2" if sys.platform == "darwin" else f"{sys.platform}-{machine}"
        spec = manifest["platforms"].get(target)
        if spec is None:
            raise OSError("Health encryption is unavailable on this platform.")
        wheel = folder / spec["filename"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != spec["sha256"]:
            raise OSError("The bundled encryption library is damaged. Reinstall the add-on.")
        base = folder.parent / ".crypto-runtime"
        base.mkdir(mode=0o700, exist_ok=True)
        # Keep DLL paths within the legacy Windows loader limit on older Anki builds.
        cache_key = spec["sha256"][:20] if sys.platform == "win32" else spec["sha256"]
        destination = base / cache_key
        if not (destination / ".complete").exists():
            temporary = Path(tempfile.mkdtemp(dir=base))
            try:
                with ZipFile(wheel) as archive:
                    for name in archive.namelist():
                        if not name.startswith("Cryptodome/"):
                            continue
                        output = (temporary / name).resolve()
                        if not output.is_relative_to(temporary.resolve()):
                            raise ValueError("Invalid crypto wheel path")
                        archive.extract(name, temporary)
                (temporary / ".complete").write_text(spec["sha256"])
                try:
                    os.rename(temporary, destination)
                except OSError:
                    if not (destination / ".complete").exists():
                        raise
            finally:
                if temporary.exists():
                    if temporary.resolve().parent != base.resolve():
                        raise ValueError("Unexpected crypto extraction directory")
                    shutil.rmtree(temporary)
        sys.path.insert(0, str(destination))
        try:
            import Cryptodome
            from Cryptodome.Cipher import AES
            from Cryptodome.Protocol import HPKE
            from Cryptodome.PublicKey import ECC

            if Cryptodome.__version__ != manifest["version"]:
                raise OSError("Another add-on loaded a different crypto library. Restart Anki.")
            _loaded = AES, HPKE, ECC
        finally:
            sys.path.remove(str(destination))
        return _loaded
