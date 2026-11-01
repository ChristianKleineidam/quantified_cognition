"""OS-protected, profile-specific secrets. No plaintext fallback or dependencies."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from .storage import profile_job_lock, results_directory


def _identity(source):
    return hashlib.sha256(os.path.normcase(str(Path(source).resolve())).encode()).hexdigest()


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError(
            "Windows could not unlock saved connections. Restore the original Windows "
            "account or remove connections.dpapi to reconnect."
        )
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def _command(args, *, input=None, missing_ok=False):
    try:
        result = subprocess.run(
            args,
            input=input,
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise OSError("The OS credential store is unavailable. Linux needs secret-tool.") from None
    if result.returncode:
        if missing_ok and result.returncode in (1, 44):
            return b""
        raise OSError("The OS credential store could not save or unlock this connection.")
    return result.stdout.strip()


def _read(source):
    directory = results_directory(source)
    if (
        not (directory / ".connections-present").exists()
        and not (directory / "connections.dpapi").exists()
    ):
        return {}
    identity = _identity(source)
    if sys.platform == "win32":
        path = results_directory(source) / "connections.dpapi"
        raw = _dpapi(path.read_bytes(), True) if path.exists() else b""
    elif sys.platform == "darwin":
        raw = _command(
            [
                "security",
                "find-generic-password",
                "-s",
                "QuantifiedSelfMemory",
                "-a",
                identity,
                "-w",
            ],
            missing_ok=True,
        )
        raw = base64.b64decode(raw) if raw else b""
    else:
        raw = _command(
            ["secret-tool", "lookup", "application", "QuantifiedSelfMemory", "profile", identity],
            missing_ok=True,
        )
        raw = base64.b64decode(raw) if raw else b""
    return json.loads(raw) if raw else {}


def _write(source, values):
    raw = json.dumps(values, allow_nan=False).encode()
    identity = _identity(source)
    if sys.platform == "win32":
        directory = results_directory(source)
        directory.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                name = Path(handle.name)
                handle.write(_dpapi(raw))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, directory / "connections.dpapi")
        finally:
            if name is not None:
                name.unlink(missing_ok=True)
    elif sys.platform == "darwin":
        # Interactive stdin keeps the password out of the process argument list.
        command = "add-generic-password -U -s QuantifiedSelfMemory -a " + shlex.quote(identity)
        command += " -w " + shlex.quote(base64.b64encode(raw).decode()) + "\n"
        _command(["security", "-i"], input=command.encode())
    else:
        _command(
            [
                "secret-tool",
                "store",
                "--label=Quantified Cognition",
                "application",
                "QuantifiedSelfMemory",
                "profile",
                identity,
            ],
            input=base64.b64encode(raw),
        )
    directory = results_directory(source)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".connections-present").touch()


def read_secret(source, slot):
    with profile_job_lock(source, "credentials"):
        return _read(source).get(slot)


def write_secret(source, slot, value):
    with profile_job_lock(source, "credentials"):
        values = _read(source)
        if value is None:
            values.pop(slot, None)
        else:
            values[slot] = value
        _write(source, values)
