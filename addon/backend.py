"""Use Anki's native historical memory states and native retrievability function.

Anki's card-info API can populate its last-review-time cache. Run the report on an
online SQLite backup so that even those incidental writes cannot touch live cards.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from anki.collection import Collection

from .deleted_history import deleted_history_policy, restore_deleted_cards
from .metrics import DEFAULT_SETTINGS, Attempt, DailyResult, Settings, anki_day, daily_results


class CompatibilityError(Exception):
    """A useful message for unsupported Anki versions or settings."""


class UpdateCancelled(Exception):
    """The profile closed/switched, or an automatic snapshot was deferred."""


def capture_snapshot(collection: Collection, destination: str | Path) -> None:
    """Only this copy step requires Anki's serialized live-collection worker."""
    collection.db.execute("vacuum main into ?", str(destination))


# Bump when reconstruction or metric semantics change, even without a DB migration.
CALCULATION_VERSION = 5


def _deleted_review_count(collection: Collection) -> int:
    return (
        collection.db.scalar(
            "select count(*) from revlog r left join cards c on r.cid = c.id "
            "where c.id is null and r.ease between 1 and 4"
        )
        or 0
    )


def input_fingerprint(
    collection: Collection,
    settings: Settings = DEFAULT_SETTINGS,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> str:
    """Read-only input scan; no snapshot or native history reconstruction.

    Scan the contents, rather than the latest review ID/modification time: sync
    can insert, edit or remove older history. Ignore note text, sync counters,
    deck names and daily deck counters, which do not affect this calculation.
    """
    from anki.buildinfo import version

    rollover = collection.get_preferences().scheduling.rollover
    cutoff = collection.sched.day_cutoff
    deck_configs = collection.decks.get_deck_configs_for_update(1)
    configs = [
        {key: value for key, value in config.items() if key not in {"name", "mod", "usn"}}
        for config in collection.decks.all_config()
    ]
    decks = [
        {key: deck.get(key) for key in ("id", "conf", "dyn")} for deck in collection.decks.all()
    ]
    policy = deleted_history_policy(collection, deck_configs)
    thresholds = asdict(settings)
    del thresholds["default_display_days"]
    digest = hashlib.sha256()

    def add(value):
        digest.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")

    add(
        {
            "calculation": CALCULATION_VERSION,
            "anki": version,
            "fsrs": deck_configs.fsrs,
            "configs": sorted(configs, key=lambda item: item["id"]),
            "decks": sorted(decks, key=lambda item: item["id"]),
            "rollover": rollover,
            # Native history groups reviews relative to today's cutoff in UTC.
            # Its phase can change at DST even when historical local dates do not.
            "native_cutoff_phase": cutoff % 86_400,
            "created": collection.crt,
            "thresholds": thresholds,
            "timezone": (time.tzname, time.timezone, time.daylight),
            "deleted_history_policy": policy.reason,
            # If presets disagree, only this excluded count affects output.
            "deleted_card_reviews": _deleted_review_count(collection),
        }
    )
    for index, row in enumerate(
        collection.db.all(
            "select id, did, odid, type, queue, due, ivl, factor, reps, lapses, left, odue, data "
            "from cards order by id"
        )
    ):
        if check_cancelled is not None and index % 1000 == 0:
            check_cancelled()
        add(row)
    # Under a common policy orphaned logs also affect scores, so scan them in
    # full, including resets, historical edits and future-date day clamping.
    join = "join cards c on r.cid = c.id" if policy.deck_id is None else ""
    minimum_id, maximum_id = collection.db.first(
        f"select min(r.id), max(r.id) from revlog r {join}"
    )
    if minimum_id is None:
        return digest.hexdigest()
    if maximum_id >= cutoff * 1000:
        # Future-dated reviews are clamped by Anki's native history API; advancing
        # the day can therefore change their reconstruction without a log edit.
        add({"future_review_cutoff": cutoff})
    last_id = minimum_id - 1
    while rows := collection.db.all(
        "select r.id, r.cid, r.ease, r.ivl, r.lastIvl, r.factor, r.type from revlog r "
        f"{join} where r.id > ? order by r.id limit 10000",
        last_id,
    ):
        if check_cancelled is not None:
            check_cancelled()
        for row in rows:
            # Include historical local-day mapping so time zone/DST rule changes
            # invalidate the cache even if the zone's present offset is unchanged.
            add([*row, anki_day(row[0] / 1000, rollover).isoformat()])
        last_id = rows[-1][0]
    return digest.hexdigest()


@dataclass(frozen=True)
class Report:
    days: list[DailyResult]
    generated_at: str
    anki_version: str
    parameters_hash: str
    rollover_hour: int
    settings: Settings
    analyzed_cards: int
    excluded_preview_reviews: int
    deleted_card_reviews: int
    timezone_name: str
    # Only daily first/last answer anchors, local-only; no review-pair records.
    study_windows: dict[str, list[float]] = field(default_factory=dict)
    reconstructed_deleted_cards: int = 0
    reconstructed_deleted_reviews: int = 0
    deleted_history_status: str = "Legacy report: deleted histories excluded"
    calculation_version: int = CALCULATION_VERSION


@contextmanager
def collection_snapshot(collection: Collection):
    with tempfile.TemporaryDirectory(prefix="anki-quantified-self-") as directory:
        destination = Path(directory) / "snapshot.anki2"
        # Anki can hold an exclusive SQLite lock; use its existing connection.
        # VACUUM INTO creates a consistent copy and leaves the source unchanged.
        capture_snapshot(collection, destination)
        snapshot = Collection(str(destination))
        try:
            yield snapshot
        finally:
            snapshot.close()


def _is_answer(row: tuple) -> bool:
    # Anki's non-rescheduling filtered reviews have ease factor 0. They are
    # unmodelled exposures, whereas rescheduling filtered reviews are legitimate.
    _, rating, kind, factor = row
    return rating in (1, 2, 3, 4) and kind in (0, 1, 2, 3) and not (kind == 3 and factor == 0)


def _segment_ends(rows: list[tuple]) -> list[int]:
    """Separate resets and legacy resets that were not logged explicitly."""
    ends = []
    seen_review = False
    for index, (rid, rating, kind, factor) in enumerate(rows):
        reset = kind == 4 and rating == 0 and factor == 0
        new_learning_episode = kind == 0 and rating in (1, 2, 3, 4) and seen_review
        if (reset or new_learning_episode) and index:
            ends.append(index - 1)
            seen_review = False
        if _is_answer((rid, rating, kind, factor)) and kind != 0:
            seen_review = True
    if rows:
        ends.append(len(rows) - 1)
    return sorted(set(ends))


def _post_states(collection: Collection, card_id: int, rows: list[tuple]) -> dict[int, tuple]:
    """Anki reconstructs the latest reset episode; truncating the disposable
    snapshot lets that same native API reconstruct earlier episodes as well.
    """
    states = {}
    ends = _segment_ends(rows)
    start_by_end = {end: (ends[i - 1] + 1 if i else 0) for i, end in enumerate(ends)}
    for end in reversed(ends):
        collection.db.execute("delete from revlog where cid = ? and id > ?", card_id, rows[end][0])
        stats = collection.card_stats_data(card_id)
        history = list(reversed(stats.revlog))
        prefix = rows[: end + 1]
        if len(history) != len(prefix):
            raise CompatibilityError(
                "Anki's historical review format has changed. Update the add-on."
            )
        for row, entry in zip(
            prefix[start_by_end[end] :], history[start_by_end[end] :], strict=True
        ):
            if row[0] // 1000 != entry.time or row[1] != entry.button_chosen:
                raise CompatibilityError("Anki's historical review ordering has changed.")
            if _is_answer(row) and entry.HasField("memory_state"):
                state = entry.memory_state
                states[row[0]] = (state.stability, state.difficulty)
    return states


def _probabilities(collection: Collection, requests: list[dict]) -> list[float | None]:
    if not requests:
        return []
    # due=last_time+1, interval=1 also supports releases predating the lrt cache.
    # JSON batches avoid a separate Python/Rust round trip per review.
    result = collection.db.all(
        """select extract_fsrs_retrievability(
            json_extract(value, '$.data'), json_extract(value, '$.last') + 1,
            1, 0, 0, json_extract(value, '$.now'))
        from json_each(?) order by cast(key as integer)""",
        json.dumps(requests, separators=(",", ":")),
    )
    return [row[0] for row in result]


def build_report(
    collection: Collection,
    settings: Settings = DEFAULT_SETTINGS,
    *,
    is_snapshot: bool = False,
    check_cancelled: Callable[[], None] | None = None,
) -> Report:
    try:
        enabled = collection.decks.get_deck_configs_for_update(1).fsrs
    except (AttributeError, TypeError) as exc:
        raise CompatibilityError("This add-on requires Anki 25.09 or later.") from exc
    if not enabled:
        raise CompatibilityError(
            "Enable FSRS in Anki's Deck Options before calculating this report."
        )
    if is_snapshot:
        return _build_snapshot_report(collection, settings, check_cancelled)
    with collection_snapshot(collection) as snapshot:
        return _build_snapshot_report(snapshot, settings, check_cancelled)


def _build_snapshot_report(
    collection: Collection, settings: Settings, check_cancelled: Callable[[], None] | None = None
) -> Report:
    # Native card-info calls may populate per-card caches, and episode replay
    # truncates revlogs. Autocommitting each write forces thousands of disk
    # flushes on a large history. Batch them on this disposable copy only.
    report: Report

    def reconstruct():
        nonlocal report
        report = _reconstruct_snapshot_report(collection, settings, check_cancelled)

    collection.db.transact(reconstruct)
    return report


def _reconstruct_snapshot_report(
    collection: Collection, settings: Settings, check_cancelled: Callable[[], None] | None = None
) -> Report:
    from anki.buildinfo import version

    rollover = collection.get_preferences().scheduling.rollover
    deleted = _deleted_review_count(collection)
    policy = deleted_history_policy(collection)
    attempts = []
    preview_count = 0
    # Current deck/preset assignments are used. Historical preset changes are
    # absent from the revlog. Include all settings in the reproducibility hash.
    parameters = {
        "configs": collection.decks.all_config(),
        "decks": collection.decks.all(),
        "rollover": rollover,
        "calculation": CALCULATION_VERSION,
        "deleted_history_policy": policy.reason,
    }
    parameter_hash = hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    restored_cards, restored_reviews = restore_deleted_cards(
        collection, policy, check_cancelled=check_cancelled or (lambda: None)
    )
    cards = collection.db.list(
        "select distinct c.id from cards c join revlog r on r.cid = c.id order by c.id"
    )
    shared_decay = None
    for card_id in cards:
        if check_cancelled is not None:
            check_cancelled()
        rows = collection.db.all(
            "select id, ease, type, factor from revlog where cid = ? order by id", card_id
        )
        # Get the decay from Anki: it knows both the active FSRS generation and
        # the default parameters when a preset hasn't been optimized.
        # Decay depends on today's active FSRS parameters. When every preset
        # agrees, ask Anki once rather than replaying each card merely for decay.
        decay = shared_decay
        if decay is None:
            decay = collection.compute_memory_state(card_id).decay
        if decay is None:
            raise CompatibilityError("This Anki version does not expose FSRS decay. Update Anki.")
        if policy.deck_id is not None:
            shared_decay = decay
        states = _post_states(collection, card_id, rows)
        requests, predictions_for, card_attempts = [], [], []
        previous = None
        episode = 0
        seen_review = False
        for row in rows:
            rid, rating, kind, factor = row
            if (kind == 4 and rating == 0 and factor == 0) or (
                kind == 0 and rating in (1, 2, 3, 4) and seen_review
            ):
                episode += 1
                previous = None
                seen_review = False
                if card_attempts:
                    card_attempts[-1] = replace(card_attempts[-1], formation_blocked=True)
            if kind == 3 and factor == 0 and rating in (1, 2, 3, 4):
                preview_count += 1
                episode += 1
                previous = None
                if card_attempts:
                    card_attempts[-1] = replace(card_attempts[-1], formation_blocked=True)
                continue
            if not _is_answer(row):
                continue
            if kind != 0:
                seen_review = True
            if previous is not None:
                last_rid, (stability, difficulty) = previous
                data = {"s": stability, "d": difficulty, "decay": decay, "lrt": last_rid // 1000}
                requests.append(
                    {"data": json.dumps(data), "last": last_rid // 1000, "now": rid // 1000}
                )
                predictions_for.append(len(card_attempts))
            card_attempts.append(
                Attempt(
                    rid,
                    card_id,
                    anki_day(rid / 1000, rollover),
                    rating,
                    None,
                    rid in states,
                    episode,
                )
            )
            previous = (rid, states[rid]) if rid in states else None
        probabilities = _probabilities(collection, requests)
        for index, probability in zip(predictions_for, probabilities, strict=True):
            old = card_attempts[index]
            card_attempts[index] = replace(old, prediction=probability)
        attempts.extend(card_attempts)
    if check_cancelled is not None:
        check_cancelled()
    study_windows = {}
    for attempt in attempts:
        key, stamp = attempt.day.isoformat(), attempt.review_id / 1000
        window = study_windows.setdefault(key, [stamp, stamp])
        window[0], window[1] = min(window[0], stamp), max(window[1], stamp)
    return Report(
        daily_results(attempts, settings),
        datetime.now(timezone.utc).isoformat(),
        version,
        parameter_hash,
        rollover,
        settings,
        len(cards),
        preview_count,
        deleted - restored_reviews,
        datetime.now().astimezone().tzname() or "local",
        study_windows,
        restored_cards,
        restored_reviews,
        policy.reason,
        CALCULATION_VERSION,
    )
