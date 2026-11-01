"""Reconstruct orphaned histories only under a shared present-day FSRS policy.

Policy inspection is read-only. Placeholder creation is called exclusively on
the disposable snapshot, inside its reconstruction transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class DeletedHistoryPolicy:
    deck_id: int | None
    reason: str


def deleted_history_policy(collection, response=None) -> DeletedHistoryPolicy:
    if response is None:
        response = collection.decks.get_deck_configs_for_update(1)
    presets = [entry.config for entry in response.all_config]
    defaults = response.defaults.config
    default_params = tuple(
        defaults.fsrs_params_6 or defaults.fsrs_params_5 or defaults.fsrs_params_4
    )

    def signature(preset):
        config = preset.config
        # Match Anki's active-generation precedence, resolve native defaults,
        # and compare protobuf f32 values rather than legacy JSON precision.
        params = tuple(
            config.fsrs_params_6 or config.fsrs_params_5 or config.fsrs_params_4 or default_params
        )
        return params, config.historical_retention, config.ignore_revlogs_before_date

    if not presets or not default_params:
        return DeletedHistoryPolicy(None, "FSRS preset settings unavailable")
    signatures = [signature(preset) for preset in presets]
    differing = [
        label
        for index, label in enumerate(("FSRS parameters", "historical retention", "history cutoff"))
        if len({value[index] for value in signatures}) > 1
    ]
    if differing:
        return DeletedHistoryPolicy(None, "Presets differ: " + ", ".join(differing))
    preset_ids = {preset.id for preset in presets}
    decks = sorted(collection.decks.all(), key=lambda deck: deck["id"])
    for deck in decks:
        if not deck.get("dyn") and int(deck.get("conf", 0)) in preset_ids:
            return DeletedHistoryPolicy(deck["id"], "Common FSRS settings across all presets")
    return DeletedHistoryPolicy(None, "No normal deck with the common FSRS settings")


def restore_deleted_cards(collection, policy, *, check_cancelled=lambda: None):
    """Create one blank note and orphan-ID placeholders only on the snapshot.

    The original IDs preserve grouping. Native historical reconstruction uses
    the unchanged logs and common preset; placeholder scheduling fields do not
    stand in for a lost memory state. No question/answer content is recovered.
    """
    if policy.deck_id is None:
        return 0, 0
    count, reviews = collection.db.first(
        "select count(*), coalesce(sum(answers), 0) from ("
        "select r.cid, sum(r.ease between 1 and 4) as answers from revlog r "
        "left join cards c on c.id = r.cid where c.id is null group by r.cid "
        "having answers > 0)"
    )
    if not count:
        return 0, 0
    check_cancelled()
    model_id = min(model.id for model in collection.models.all_names_and_ids())
    model = collection.models.get(model_id)
    note_id = collection.db.scalar("select coalesce(max(id), 0) + 1 from notes")
    collection.db.execute(
        "insert into notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "values (?, ?, ?, 0, -1, '', ?, '', 0, 0, '')",
        note_id,
        uuid4().hex[:10],
        model_id,
        "\x1f" * (len(model["flds"]) - 1),
    )
    collection.db.execute(
        "insert into cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, "
        "reps, lapses, left, odue, odid, flags, data) "
        "select r.cid, ?, ?, 0, 0, -1, 2, -1, 0, 1, 2500, count(*), 0, 0, 0, 0, 0, '{}' "
        "from revlog r left join cards c on c.id = r.cid where c.id is null "
        "group by r.cid having sum(r.ease between 1 and 4) > 0",
        note_id,
        policy.deck_id,
    )
    check_cancelled()
    return count, reviews
