"""The official-build checker must observe the final review, not an older CSV."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from tools.official_desktop_beta import check_final_csv


@pytest.fixture
def final_review(tmp_path):
    source = tmp_path / "collection.anki2"
    stamp = datetime(2026, 11, 2, 2, 0)
    with sqlite3.connect(source) as db:
        db.execute("create table revlog(id integer, ease integer)")
        db.execute("insert into revlog values(?,3)", [int(stamp.timestamp() * 1000)])
    # A review before 04:00 belongs to the preceding Anki day in this fixture.
    return source, tmp_path / "scores.csv", (stamp - timedelta(hours=4)).date().isoformat()


def test_official_exit_check_uses_the_anki_day_and_newly_published_answer(final_review):
    source, csv, day = final_review
    csv.write_text(f"date,recall_count\n{day},1\n")
    assert check_final_csv(source, csv) == day


@pytest.mark.parametrize("row", ["2026-10-31,1", "2026-11-01,0"])
def test_old_or_incomplete_csv_cannot_pass_final_answer_check(final_review, row):
    source, csv, _day = final_review
    csv.write_text("date,recall_count\n" + row + "\n")
    with pytest.raises(AssertionError, match="Final answer missing"):
        check_final_csv(source, csv, timeout=0.001)


def test_temporarily_unavailable_csv_has_a_bounded_failure(final_review):
    source, csv, _day = final_review
    with pytest.raises(AssertionError, match="Final answer missing"):
        check_final_csv(source, csv, timeout=0.001)
