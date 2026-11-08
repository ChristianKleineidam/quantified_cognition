"""Pseudonymous review rows for encrypted research snapshots."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

REVIEW_COLUMNS = (
    "card_id", "ordinal", "date", "rating", "success", "prediction", "elapsed_seconds",
    "review_type", "episode", "first_on_day", "last_on_day", "has_post_state",
    "formation_blocked",
)
REVIEW_CONTEXT_FIELDS = (
    "prediction_basis", "anki_version", "calculation_version", "parameters_hash",
    "generated_at", "deleted_history_status",
)
MAX_REVIEW_ROWS = 1_000_000


def _pseudonym_key(source):
    from .storage import profile_job_lock, results_directory
    from .vault import read_secret, write_secret

    with profile_job_lock(source, "credentials"):
        marker = results_directory(source) / ".review-pseudonyms-present"
        value = read_secret(source, "review_pseudonyms")
        if value is None:
            if marker.exists():
                raise ValueError(
                    "The review pseudonym key is unavailable in the OS credential store."
                )
            value = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
            write_secret(source, "review_pseudonyms", value)
        key = base64.b64decode(value, validate=True)
        if len(key) != 32:
            raise ValueError("Invalid review pseudonym key.")
        marker.touch(exist_ok=True)
        return key


def prepare_reviews(source, participant_id, report, records):
    """Pseudonymize original card IDs before encryption; retain no clock times."""
    key = _pseudonym_key(source)
    cards = {}
    rows = []
    for record in records:
        original = record["card_id"]
        if original not in cards:
            digest = hmac.digest(
                key, f"QC research card v1/{participant_id}/{original}".encode(), hashlib.sha256
            )
            cards[original] = base64.urlsafe_b64encode(digest[:16]).decode().rstrip("=")
        rows.append([
            cards[original] if name == "card_id" else record[name] for name in REVIEW_COLUMNS
        ])
    rows.sort(key=lambda row: (row[0], row[1]))
    context = {
        "prediction_basis": "reconstructed_current_fsrs",
        "anki_version": report.anki_version,
        "calculation_version": report.calculation_version,
        "parameters_hash": report.parameters_hash,
        "generated_at": report.generated_at,
        "deleted_history_status": report.deleted_history_status,
    }
    return {"columns": list(REVIEW_COLUMNS), "rows": rows, "context": context}
