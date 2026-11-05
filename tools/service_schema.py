"""Generate the server allowlist from the actual CSV contract (no user data)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from addon.export import CSV_FIELDS  # noqa: E402
from addon.research import RESEARCH_METADATA_FIELDS, SharingConfig  # noqa: E402
from addon.sleep_sessions import SESSION_CSV_FIELDS, STATUSES  # noqa: E402
from addon.workouts import STATUSES as WORKOUT_STATUSES  # noqa: E402
from addon.workouts import WORKOUT_DAILY_FIELDS, WORKOUT_STUDY_FIELDS  # noqa: E402

columns = [name for name in CSV_FIELDS if name not in RESEARCH_METADATA_FIELDS]
text = {
    "date",
    "generated_at_utc",
    "anki_version",
    "parameters_hash",
    "timezone",
    "uncertainty_method",
    "formation_completeness",
    "oura_sleep_status",
    "oura_sleep_algorithm_versions",
    "oura_day_basis",
    "oura_imported_at_utc",
    "google_health_sleep_types",
    "google_health_synced_at_utc",
}
enums = {
    name: ["", "insufficient data", "better", "worse", "on par", "not calculated"]
    for name in columns
    if name.endswith("_status") and not name.startswith(("oura_", "google_health_"))
}
enums.update(
    {
        "formation_completeness": [
            "",
            "no data",
            "provisional",
            "no evaluable follow-ups",
            "no pending follow-ups",
        ],
        "oura_sleep_status": [
            "",
            "durations available",
            "missing durations",
            "overlapping periods",
            "ambiguous periods",
            "ambiguous time offsets",
            "conflicting periods",
        ],
        "oura_day_basis": ["", "provider_assigned_day"],
        "google_health_day_basis": ["", "sleep_end_local_date_steps_civil_date"],
        "google_health_sleep_status": [
            "",
            "durations available",
            "stages unavailable",
            "missing durations",
            "processing",
            "overlapping periods",
        ],
        "google_health_sleep_types": [
            "",
            "CLASSIC",
            "STAGES",
            "SLEEP_TYPE_UNSPECIFIED",
            "CLASSIC;STAGES",
            "CLASSIC;SLEEP_TYPE_UNSPECIFIED",
            "SLEEP_TYPE_UNSPECIFIED;STAGES",
            "CLASSIC;SLEEP_TYPE_UNSPECIFIED;STAGES",
        ],
    }
)
schema = {
    "package_version": 6,
    "columns": columns,
    "metadata": list(RESEARCH_METADATA_FIELDS),
    "numeric": [name for name in columns if name not in text and name not in enums],
    "enums": enums,
}
for name in SESSION_CSV_FIELDS:
    if name.endswith("_status"):
        schema["enums"][name] = ["", *STATUSES]
        schema["numeric"].remove(name)
for name in (*WORKOUT_DAILY_FIELDS, *WORKOUT_STUDY_FIELDS):
    if name.endswith("_status"):
        schema["enums"][name] = ["", *WORKOUT_STATUSES]
        schema["numeric"].remove(name)
root = Path(__file__).resolve().parents[1]
schema["encryption"] = json.loads((root / "addon/research_encryption.json").read_text())
destination = root / "supabase/functions/qs-service/schema.json"
destination.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
(destination.parents[3] / "addon/research_schema.json").write_text(
    json.dumps(schema, indent=2) + "\n", encoding="utf-8"
)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--config", type=Path, help="Deployed add-on config, including customized notice"
)
arguments = parser.parse_args()
config = json.loads(arguments.config.read_text(encoding="utf-8")) if arguments.config else None
sharing = SharingConfig.from_config(config)
print("RESEARCH_NOTICE_VERSION=" + sharing.notice_version)
print("RESEARCH_NOTICE_HASH=" + sharing.notice_hash)
