"""Freeze the exact participant documents and consent statement used for enrollment.

Changing this bundle never approves enrollment. The database protocol remains
closed until an operator records the actual approvals and accepted signature method.
"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATEMENT = (
    "I confirm that I am at least 18 and am contributing my own data. I have read "
    "the research information and privacy policy. I explicitly consent to "
    "JollyJoyJourney processing my submitted daily Anki measurements and any Oura "
    "sleep and activity summaries, including health-related data, for this research "
    "programme, with controlled access by approved research collaborators as "
    "described. I understand the retention criteria, that each upload requires my "
    "confirmation, and that I can withdraw without losing personal use."
)


def bundle():
    return {
        "version": "research-enrollment-v10",
        "operator": "JollyJoyJourney",
        "signature_method": "explicit_consent",
        "statement": STATEMENT,
        "documents": {
            name: (ROOT / "docs" / name).read_text(encoding="utf-8")
            for name in ("RESEARCH_INFORMATION.md", "PRIVACY.md")
        },
    }


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migration", type=Path, help="An empty file created by supabase migration new"
    )
    args = parser.parse_args()
    if args.migration and args.migration.read_text(encoding="utf-8").strip():
        raise SystemExit("Refusing to rewrite a nonempty migration")
    value = bundle()
    frozen = {"bundle": value, "sha256": hashlib.sha256(canonical(value).encode()).hexdigest()}
    destination = ROOT / "addon" / "research_enrollment.json"
    destination.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.migration:
        # The seed is immutable migration history. A later information change
        # requires a new protocol version and new migration, never an UPDATE.
        import sys

        sys.path.insert(0, str(ROOT))
        from addon.research import SharingConfig

        notice = SharingConfig()

        def literal(text):
            return "'" + text.replace("'", "''") + "'"

        sql = (
            "-- Prepared documents only: approval and provider permissions remain false.\n"
            "insert into qs_governance.protocols\n"
            "(version,bundle_hash,bundle,notice_version,notice_hash,signature_method) values (\n"
            + ",\n".join(
                literal(item)
                for item in (
                    value["version"],
                    frozen["sha256"],
                    json.dumps(value, ensure_ascii=False),
                    notice.notice_version,
                    notice.notice_hash,
                    value["signature_method"],
                )
            )
            + ");\n"
        )
        args.migration.write_text(sql, encoding="utf-8")
    print(frozen["sha256"])
