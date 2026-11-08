"""Research snapshots preserve predictions without disclosing source identifiers."""

import base64
import copy
import json
import sqlite3
from dataclasses import replace

import pytest
from test_storage import report_with_followup

from addon import review_data, storage, vault


@pytest.fixture
def pseudonym_store(monkeypatch):
    values = {}
    monkeypatch.setattr(vault, "read_secret", lambda path, slot: values.get((str(path), slot)))
    monkeypatch.setattr(
        vault, "write_secret", lambda path, slot, value: values.update({(str(path), slot): value})
    )
    return values


def test_pseudonyms_are_stable_and_isolated_and_keep_missing_predictions(tmp_path, pseudonym_store):
    source = tmp_path / "first" / "collection.anki2"
    report = replace(report_with_followup(), parameters_hash="a" * 64)
    original = copy.deepcopy(report.review_records)
    one = review_data.prepare_reviews(source, "participant-one", report, original)
    two = review_data.prepare_reviews(source, "participant-one", report, original)
    records = [dict(zip(one["columns"], row, strict=True)) for row in one["rows"]]
    assert one == two and len(records) == 2
    assert records[0]["card_id"] == records[1]["card_id"]
    assert records[0]["card_id"] != str(original[0]["card_id"])
    assert records[0]["prediction"] is None and records[1]["prediction"] == 0.7
    assert records[1]["success"] == 0 and records[1]["elapsed_seconds"] == 4 * 86400
    assert original == report.review_records  # Pseudonymization never mutates the cache.
    other = review_data.prepare_reviews(source, "participant-two", report, original)
    profile = review_data.prepare_reviews(
        tmp_path / "second" / "collection.anki2", "participant-one", report, original
    )
    assert other["rows"][0][0] != one["rows"][0][0] != profile["rows"][0][0]
    assert not {"review_id", "timestamp", "deck_id", "question", "answer"} & set(one["columns"])
    assert base64.b64decode(pseudonym_store[(str(source), "review_pseudonyms")])


def test_lost_key_does_not_silently_reidentify_a_profile(tmp_path, pseudonym_store):
    source = tmp_path / "collection.anki2"
    review_data._pseudonym_key(source)
    pseudonym_store.clear()
    with pytest.raises(ValueError, match="unavailable"):
        review_data._pseudonym_key(source)


def test_review_cache_matches_daily_snapshot_and_rolls_back_together(tmp_path):
    path = storage.database_path(tmp_path / "collection.anki2")
    original = report_with_followup()
    storage.save_report(original, path)
    assert storage.load_review_records(path) == original.review_records
    assert storage.load_report(path).review_records is None  # Cheap report opening.
    with sqlite3.connect(path) as connection:
        assert "review_records" not in json.loads(connection.execute(
            "select value from metadata where key='report'"
        ).fetchone()[0])
        connection.execute("create trigger fail_daily before insert on daily_results "
                           "begin select raise(abort, 'synthetic failure'); end")
    revised = report_with_followup(False)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        storage.save_report(revised, path)
    assert storage.load_review_records(path) == original.review_records
    assert storage.load_report(path) == original
    with sqlite3.connect(path) as connection:
        connection.execute("drop trigger fail_daily")
    storage.save_report(revised, path)
    assert storage.load_review_records(path) == revised.review_records


def test_legacy_daily_cache_cannot_be_mistaken_for_a_complete_review_snapshot(tmp_path):
    path = storage.database_path(tmp_path / "collection.anki2")
    storage.save_report(report_with_followup(), path)
    with sqlite3.connect(path) as connection:
        connection.execute("drop table review_predictions")
        connection.execute("pragma user_version=3")
    assert storage.load_report(path)
    with pytest.raises(ValueError):
        storage.load_review_records(path)
