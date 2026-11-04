"""Create and verify a recovery identity protected by the current Windows account."""

from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path


def dpapi(data, *, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = Blob()
    crypt, kernel = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32")
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
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise OSError("Windows could not protect or unlock the recovery identity")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def verify(age, protected_identity, encrypted):
    key = dpapi(protected_identity.read_bytes(), decrypt=True)
    process = subprocess.Popen(
        [str(age), "--decrypt", "--identity", "-", str(encrypted)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    digest, counts, total = hashlib.sha256(), {}, 0
    try:
        process.stdin.write(key)
        process.stdin.close()
        with gzip.GzipFile(fileobj=process.stdout, mode="rb") as source:
            while True:
                raw = source.readline(32 * 1024 * 1024 + 1)
                total += len(raw)
                if not raw or len(raw) > 32 * 1024 * 1024 or total > 512 * 1024 * 1024:
                    raise ValueError("Invalid recovery snapshot")
                record = json.loads(raw)
                if record.get("type") == "header":
                    if counts or record.get("format") != "quantified-cognition-recovery-v1":
                        raise ValueError("Unexpected recovery format")
                    counts = dict.fromkeys(record["tables"], 0)
                elif record.get("type") == "row":
                    counts[record["table"]] += 1
                elif record.get("type") == "end":
                    if (
                        record["counts"] != counts
                        or record["sha256"] != digest.hexdigest()
                        or source.read(1)
                    ):
                        raise ValueError("Recovery integrity check failed")
                    break
                else:
                    raise ValueError("Invalid recovery record")
                digest.update(raw)
        if process.wait(timeout=30):
            raise ValueError("Recovery decryption failed")
        return {"verified": True, "rows": sum(counts.values()), "plaintext_bytes": total}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "verify", "portable"))
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--age", type=Path)
    parser.add_argument("--keygen", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--ssh-public-key", type=Path)
    args = parser.parse_args()
    if args.mode == "create":
        if args.identity.exists() or args.keygen is None:
            raise ValueError("Refusing to replace an identity; provide age-keygen")
        key = subprocess.run(
            [str(args.keygen)], capture_output=True, check=True, creationflags=0x08000000
        ).stdout
        recipient = re.search(rb"# public key: (age1\S+)", key)[1].decode()
        with args.identity.open("xb") as target:
            target.write(dpapi(key))
        assert dpapi(args.identity.read_bytes(), decrypt=True) == key
        args.identity.with_suffix(".public.txt").write_text(recipient + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "created": True,
                    "recipient": recipient,
                    "protection": "Windows DPAPI; current account",
                }
            )
        )
    elif args.mode == "portable":
        if args.age is None or args.ssh_public_key is None:
            parser.error("Portable protection requires --age and --ssh-public-key")
        target = args.identity.with_suffix(".age")
        if target.exists():
            raise ValueError("Refusing to replace the portable encrypted identity")
        key = dpapi(args.identity.read_bytes(), decrypt=True)
        recipient = args.ssh_public_key.read_text().strip()
        if not recipient.startswith("ssh-ed25519 "):
            raise ValueError("Use the operator's Ed25519 public key")
        subprocess.run(
            [str(args.age), "--encrypt", "--recipient", recipient, "--output", str(target)],
            input=key,
            capture_output=True,
            check=True,
            creationflags=0x08000000,
        )
        print(
            json.dumps(
                {
                    "portable_identity_created": True,
                    "protection": "operator SSH key; private SSH key remains local",
                }
            )
        )
    else:
        if args.age is None or args.snapshot is None:
            parser.error("Verification requires --age and --snapshot")
        print(json.dumps(verify(args.age, args.identity, args.snapshot)))


if __name__ == "__main__":
    main()
