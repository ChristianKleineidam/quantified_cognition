"""Local, append-only audit of sharing choices and first results exposure.

The record describes opportunities to view this add-on's results. It cannot
establish that a person read them or never saw health data in another app.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RESEARCH_METADATA_FIELDS = (
    "research_participant_id",
    "sharing_current_choice",
    "sharing_first_choice",
    "sharing_first_choice_at_utc",
    "sharing_first_opt_in_at_utc",
    "sharing_opt_in_before_first_view",
    "research_consent_active",
    "research_consent_first_given_at_utc",
    "research_consent_before_first_view",
    "current_notice_consent_before_first_view",
    "first_consent_notice_version",
    "first_results_exposed_at_utc",
    "first_results_exposure_surface",
    "results_exposure_history",
    "sharing_notice_version",
    "sharing_notice_hash",
    "sharing_initial_prompt_at_utc",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SharingConfig:
    notice_version: str = "research-sharing-v7"
    custom_notice_text: str = ""

    @classmethod
    def from_config(cls, config: dict | None) -> SharingConfig:
        data = (config or {}).get("research_sharing", {})
        if not isinstance(data, dict):
            return cls()
        version = data.get("notice_version", "")
        text = data.get("notice_text", "")
        return cls(
            notice_version=version.strip()
            if isinstance(version, str) and version.strip()
            else "research-sharing-v7",
            custom_notice_text=text.strip() if isinstance(text, str) else "",
        )

    @property
    def mode(self) -> str:
        return "consent"

    @property
    def scope(self) -> str:
        # Changed sharing text requires a new choice rather than silently
        # extending permission. No registration details are required.
        return hashlib.sha256((self.notice_version + self.notice_text).encode()).hexdigest()

    @property
    def notice_text(self) -> str:
        if self.custom_notice_text:
            return self.custom_notice_text
        data = (
            "Daily recall and overall, short-term and long-term memory formation scores, "
            "counts, uncertainty and coverage; Oura and Google Health daily total sleep, "
            "light/deep/REM/awake durations, Google restless time, sleep-period counts, "
            "data availability and steps when available; dates, sources and calculation versions. "
            "Separate main-sleep durations before and after studying, recorded-nap indicators, "
            "nap counts and total/light/deep/REM durations, gaps to studying, and matching status "
            "are included when available. "
            "Anki version, FSRS settings fingerprint, timezone, day boundary, calculation "
            "settings, Oura algorithm versions, Google sleep types, provider day conventions "
            "and sync times are included. "
            "A random participant ID links contributions, together with your sharing "
            "choice and whether it preceded your first results view. "
            "Card questions and answers, card/deck names and IDs, individual reviews, "
            "exact sleep or review times, raw health records, heart rate, location, name, email, "
            "Oura/Google account and device IDs and "
            "credentials are excluded. "
        )
        return (
            "Would you be interested in contributing to scientific research into learning, recall, "
            "sleep and activity?\n\n" + data + "\n\n"
            "Sharing is optional. Your personal results are available whichever choice "
            "you make. You can change your choice later through Research sharing. "
            "The app records whether you expressed interest before your first results "
            "were available. "
            "Data will only be sent after you separately choose and confirm an upload. "
            "JollyJoyJourney operates this research programme. Approved research collaborators "
            "may receive controlled access as described in the research information. "
            "Research data is kept while needed for the programme. We regularly review "
            "that need and delete data that is no longer needed. "
            "Joining requires separate eligibility and explicit study consent. This question "
            "records your sharing preference and does not enroll you. "
            "Withdrawing sharing queues deletion of your previous uploads. "
            "No data is uploaded by answering this question."
        )

    @property
    def notice_hash(self) -> str:
        return hashlib.sha256(self.notice_text.encode()).hexdigest()


@dataclass(frozen=True)
class SharingSnapshot:
    participant_id: str
    history: str
    events: list[dict]
    study: SharingConfig

    @property
    def choices(self) -> list[dict]:
        return [
            event
            for event in self.events
            if event["scope"] == self.study.scope
            and event["event_type"] in ("sharing_choice", "consent_withdrawn")
        ]

    @property
    def current_choice(self) -> str | None:
        return self.choices[-1]["choice"] if self.choices else None

    @property
    def first_exposure(self) -> dict | None:
        exposed = [event for event in self.events if event["event_type"] == "results_exposed"]
        return min(
            exposed, key=lambda event: event["related_event_id"] or event["id"], default=None
        )

    def before_first_view(self, event: dict | None) -> bool | None:
        if event is None or self.history != "tracked":
            return None
        exposure = self.first_exposure
        # SQLite event order, rather than wall-clock comparisons, protects
        # classification when the computer's clock changes between events.
        if exposure is not None and event["id"] >= (exposure["related_event_id"] or exposure["id"]):
            return False
        completed = {
            item["related_event_id"]
            for item in self.events
            if item["event_type"] in ("results_exposed", "exposure_cancelled")
        }
        if any(
            item["event_type"] == "exposure_prepared"
            and item["id"] not in completed
            and item["id"] < event["id"]
            for item in self.events
        ):
            # A crash could have occurred after publishing a CSV but before its
            # confirmation. Do not turn unverifiable prior exposure into early consent.
            return None
        return True

    def metadata(self) -> dict:
        choices = [
            event
            for event in self.events
            if event["event_type"] in ("sharing_choice", "consent_withdrawn")
        ]
        first = choices[0] if choices else None
        first_opt_in = next((event for event in choices if event["choice"] == "opt_in"), None)
        first_consent = next(
            (
                event
                for event in choices
                if event["choice"] == "opt_in" and event["mode"] == "consent"
            ),
            None,
        )
        current_consent = next(
            (event for event in self.choices if event["choice"] == "opt_in"), None
        )
        exposure = self.first_exposure
        prompt = next(
            (event for event in self.events if event["event_type"] == "prompt_presented"),
            None,
        )
        return {
            "research_participant_id": self.participant_id,
            "sharing_current_choice": self.current_choice or "not asked",
            "sharing_first_choice": first["choice"] if first else "",
            "sharing_first_choice_at_utc": first["occurred_at"] if first else "",
            "sharing_first_opt_in_at_utc": first_opt_in["occurred_at"] if first_opt_in else "",
            "sharing_opt_in_before_first_view": self.before_first_view(first_opt_in),
            "research_consent_active": self.current_choice == "opt_in",
            "research_consent_first_given_at_utc": first_consent["occurred_at"]
            if first_consent
            else "",
            "research_consent_before_first_view": self.before_first_view(first_consent),
            "current_notice_consent_before_first_view": self.before_first_view(current_consent),
            "first_consent_notice_version": first_consent["notice_version"]
            if first_consent
            else "",
            "first_results_exposed_at_utc": (
                next(
                    event["occurred_at"]
                    for event in self.events
                    if event["id"] == exposure["related_event_id"]
                )
                if exposure and exposure["related_event_id"]
                else exposure["occurred_at"]
                if exposure
                else ""
            ),
            "first_results_exposure_surface": exposure["surface"] if exposure else "",
            "results_exposure_history": self.history,
            "sharing_notice_version": self.study.notice_version,
            "sharing_notice_hash": self.study.notice_hash,
            "sharing_initial_prompt_at_utc": prompt["occurred_at"] if prompt else "",
        }


def audit_path(collection_path: str | Path) -> Path:
    from .storage import results_directory

    return results_directory(collection_path) / "study-sharing.sqlite3"


def sharing_revision_at(path: str | Path) -> str:
    """Choice changes invalidate CSV metadata without invalidating Anki scores."""
    path = Path(path).resolve()
    if not path.exists():
        return ""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "select max(id) from events where event_type in ('sharing_choice','consent_withdrawn')"
        ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _legacy_exposure_possible(collection_path: str | Path) -> bool:
    from .storage import database_path, load_daily_settings

    if database_path(collection_path).exists():
        return True
    try:
        daily = load_daily_settings(collection_path)
        return bool(daily.csv_path and Path(daily.csv_path).expanduser().exists())
    except (OSError, ValueError):
        return True  # Unverifiable history is never classified as known pre-exposure.


def _snapshot(connection: sqlite3.Connection, study: SharingConfig) -> SharingSnapshot:
    connection.row_factory = sqlite3.Row
    metadata = dict(connection.execute("select key, value from metadata"))
    events = [dict(row) for row in connection.execute("select * from events order by id")]
    return SharingSnapshot(metadata["participant_id"], metadata["exposure_history"], events, study)


def _event(
    connection,
    study: SharingConfig,
    event_type: str,
    *,
    choice=None,
    surface=None,
    related_event_id=None,
) -> None:
    connection.execute(
        "insert into events (occurred_at, event_type, scope, mode, choice, surface, "
        "notice_version, notice_hash, related_event_id) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _now(),
            event_type,
            study.scope,
            study.mode,
            choice,
            surface,
            study.notice_version,
            study.notice_hash,
            related_event_id,
        ),
    )


def sharing_state(
    collection_path: str | Path,
    study: SharingConfig | None = None,
    *,
    event_type: str | None = None,
    choice: str | None = None,
    surface: str | None = None,
    related_event_id: int | None = None,
) -> SharingSnapshot:
    study = study or SharingConfig()
    if choice is not None and choice not in ("opt_in", "declined", "deferred"):
        raise ValueError("Invalid sharing choice")
    path = audit_path(collection_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("begin immediate")
        connection.execute("create table if not exists metadata (key text primary key, value text)")
        connection.execute("""create table if not exists notices (
            scope text primary key, notice_version text not null,
            notice_hash text not null, notice_text text not null)""")
        connection.execute(
            "insert or ignore into notices values (?, ?, ?, ?)",
            (study.scope, study.notice_version, study.notice_hash, study.notice_text),
        )
        connection.execute("""create table if not exists events (
            id integer primary key autoincrement, occurred_at text not null,
            event_type text not null, scope text not null, mode text not null,
            choice text, surface text, notice_version text not null, notice_hash text not null,
            related_event_id integer)""")
        if (
            connection.execute("select 1 from metadata where key='participant_id'").fetchone()
            is None
        ):
            history = "unknown_legacy" if _legacy_exposure_possible(collection_path) else "tracked"
            connection.executemany(
                "insert into metadata values (?, ?)",
                [
                    ("participant_id", str(uuid.uuid4())),
                    ("exposure_history", history),
                ],
            )
            _event(connection, study, "tracking_initialized")
        snapshot = _snapshot(connection, study)
        if event_type in ("results_exposed", "exposure_prepared", "exposure_cancelled"):
            if surface not in ("report", "csv"):
                raise ValueError("Invalid results exposure surface")
            if snapshot.first_exposure is None or related_event_id is not None:
                _event(
                    connection,
                    study,
                    event_type,
                    surface=surface,
                    related_event_id=related_event_id,
                )
        elif event_type in ("prompt_presented", "enrollment_signed"):
            _event(connection, study, event_type)
        elif event_type == "sharing_choice":
            if choice is None:
                raise ValueError("A sharing choice is required")
            changed_type = (
                "consent_withdrawn"
                if snapshot.current_choice == "opt_in" and choice != "opt_in"
                else event_type
            )
            _event(connection, study, changed_type, choice=choice)
        elif event_type is not None:
            raise ValueError("Invalid research audit event")
        return _snapshot(connection, study)


def prepare_csv_exposure(collection_path, config=None) -> tuple[SharingSnapshot, int | None, dict]:
    snapshot = sharing_state(collection_path, config, event_type="exposure_prepared", surface="csv")
    metadata = snapshot.metadata()
    token = None
    if snapshot.first_exposure is None:
        prepared = snapshot.events[-1]
        token = prepared["id"]
        metadata["first_results_exposed_at_utc"] = prepared["occurred_at"]
        metadata["first_results_exposure_surface"] = "csv"
    return snapshot, token, metadata


def finish_csv_exposure(collection_path, config, token: int | None, *, success: bool) -> None:
    if token is not None:
        sharing_state(
            collection_path,
            config,
            event_type="results_exposed" if success else "exposure_cancelled",
            surface="csv",
            related_event_id=token,
        )
