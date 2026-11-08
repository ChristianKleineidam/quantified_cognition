"""Generate an off-server research key or decrypt an inventoried research download."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from addon.crypto_runtime import primitives  # noqa: E402
from addon.research_crypto import ALGORITHM, open_package  # noqa: E402


def generate(destination, age, recipient):
    from addon.vault import _dpapi

    if sys.platform != "win32":
        raise ValueError("This key-generation command uses the operator's Windows DPAPI store.")
    public_path = ROOT / "addon/research_encryption.json"
    portable = destination.with_suffix(".age")
    if any(path.exists() for path in (destination, portable, public_path)):
        raise ValueError("Refusing to replace an existing research key")
    _, _, ECC = primitives()
    key = ECC.generate(curve="Curve25519")
    private = key.export_key(format="DER", use_pkcs8=True)
    protected = _dpapi(private)
    # A portable recovery copy is encrypted to an existing operator SSH public
    # key; the SSH private key and the research key never leave this computer.
    completed = subprocess.run(
        [str(age), "-R", str(recipient)], input=private, capture_output=True, check=True, timeout=30
    )
    public = key.public_key()
    config = {
        "algorithm": ALGORITHM,
        "key_id": hashlib.sha256(public.export_key(format="DER")).hexdigest(),
        "public_key": public.export_key(format="PEM"),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(protected)
    portable.write_bytes(completed.stdout)
    public_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "key_id": config["key_id"],
                "private_key_protected": True,
                "portable_recovery_copy_encrypted": True,
            }
        )
    )


def load_private(path, *, age=None, identity=None):
    if path.suffix == ".dpapi":
        from addon.vault import _dpapi

        return _dpapi(path.read_bytes(), True)
    if path.suffix == ".age" and age and identity:
        return subprocess.run(
            [str(age), "-d", "-i", str(identity), str(path)],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    raise ValueError("Use a protected .dpapi key or .age with --age and --identity")


def decrypt_download(
    source, destination, key_path, *, age=None, identity=None, reviews_destination=None
):
    from addon.research_crypto import validate_package

    private = load_private(key_path, age=age, identity=identity)
    raw = json.loads(source.read_text(encoding="utf-8"))
    records = raw.get("rows", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError("Download must be a JSON array or a Supabase query result")
    daily = {}
    review_snapshots = {}
    for item in records:
        envelope = item.get("package", item.get("qs_release_extract", item))
        # Support CLI SELECT ... AS record JSON results without accepting
        # arbitrary unknown layers that could silently hide dropped records.
        if "record" in item:
            envelope = item["record"]["package"]
            item = item["record"]
        package = open_package(envelope, private)
        validate_package(package)
        if item.get("submission_id", package["submission_id"]) != package["submission_id"]:
            raise ValueError("Download receipt identity mismatch")
        received = item.get("received_at", package["created_at"])
        order = (
            datetime.fromisoformat(received.replace("Z", "+00:00")),
            package["submission_id"],
        )
        participant = package["metadata"]["research_participant_id"]
        previous_snapshot = review_snapshots.get(participant)
        if previous_snapshot is None or order > previous_snapshot[0]:
            review_snapshots[participant] = (order, package, item)
        for row in package["rows"]:
            identifier = participant, row[0]
            previous = daily.get(identifier)
            if previous is None or order > previous[0]:
                daily[identifier] = (
                    order,
                    {
                        **package["metadata"],
                        **dict(zip(package["columns"], row, strict=True)),
                        **item.get("consent_timing", {}),
                        "submission_id": package["submission_id"],
                        "received_at": item.get("received_at", ""),
                    },
                )
    keys = list(dict.fromkeys(key for _, values in daily.values() for key in values))
    outputs = [(destination, keys, (daily[key][1] for key in sorted(daily)))]
    review_count = 0
    review_records = []
    has_reviews = any("reviews" in entry[1] for entry in review_snapshots.values())
    for participant in sorted(review_snapshots):
        _, package, item = review_snapshots[participant]
        if "reviews" not in package:
            continue
        snapshot = package["reviews"]
        for row in snapshot["rows"]:
            review_records.append({
                **package["metadata"], **snapshot["context"],
                **dict(zip(snapshot["columns"], row, strict=True)),
                **item.get("consent_timing", {}),
                "submission_id": package["submission_id"],
                "received_at": item.get("received_at", ""),
            })
    if has_reviews:
        reviews_destination = reviews_destination or destination.with_name(
            destination.stem + "-reviews.csv"
        )
        review_count = len(review_records)
        review_keys = list(dict.fromkeys(
            key for values in review_records for key in values
        ))
        if not review_keys:
            from addon.review_data import REVIEW_COLUMNS

            review_keys = ["research_participant_id", *REVIEW_COLUMNS]
        outputs.append((reviews_destination, review_keys, review_records))
    paths = [path.resolve() for path, _, _ in outputs]
    if len(set(paths)) != len(paths) or any(path.exists() for path in paths):
        raise ValueError("Refusing to overwrite an existing analysis export")
    # Every package validates before either file is created. Roll back files
    # created by this invocation if either exclusive write fails.
    created = []
    try:
        for path, columns, rows in outputs:
            with path.open("x", encoding="utf-8-sig", newline="") as handle:
                created.append(path)
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    print(json.dumps({
        "decrypted_packages": len(records), "daily_records": len(daily),
        "review_records": review_count,
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--age", type=Path, required=True)
    keygen.add_argument("--recipient", type=Path, required=True)
    decrypt = commands.add_parser("decrypt")
    decrypt.add_argument("--input", type=Path, required=True)
    decrypt.add_argument("--output", type=Path, required=True)
    decrypt.add_argument("--reviews-output", type=Path)
    decrypt.add_argument("--private-key", type=Path, required=True)
    decrypt.add_argument("--age", type=Path)
    decrypt.add_argument("--identity", type=Path)
    args = parser.parse_args()
    if args.command == "keygen":
        generate(args.private_key, args.age, args.recipient)
    else:
        decrypt_download(
            args.input, args.output, args.private_key, age=args.age, identity=args.identity,
            reviews_destination=args.reviews_output,
        )


if __name__ == "__main__":
    main()
