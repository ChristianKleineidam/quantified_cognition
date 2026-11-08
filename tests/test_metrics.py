from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from addon.metrics import (
    SHORT_TERM_MAX_MILLISECONDS,
    Attempt,
    Sample,
    Settings,
    anki_day,
    daily_results,
    summarize,
)

MONDAY = date(2026, 9, 7)
FRIDAY = date(2026, 9, 11)


def test_comparison_and_formation_completeness_are_independent():
    samples = [Sample(0.8, 1, MONDAY + timedelta(days=i)) for i in range(30)]
    metric = summarize(samples, cohort_count=1000, pending_count=970, formation=True)
    assert metric.status == "better"
    assert metric.coverage_percent == 3
    assert metric.completeness == "provisional"
    assert summarize(samples, cohort_count=30, formation=True).completeness == (
        "no pending follow-ups"
    )
    assert summarize([], cohort_count=0, formation=True).completeness == "no data"
    assert summarize([], cohort_count=2, excluded_count=2, formation=True).completeness == (
        "no evaluable follow-ups"
    )


def attempt(rid, day, rating, p, *, card=1, state=True, episode=0):
    timestamp = (
        int(
            datetime.combine(day, datetime.min.time(), timezone.utc).replace(hour=12).timestamp()
            * 1000
        )
        + rid
    )
    return Attempt(timestamp, card, day, rating, p, state, episode)


def test_first_attempt_recall_last_attempt_formation_and_pending():
    results = daily_results(
        [
            attempt(2, MONDAY, 3, 0.99),
            attempt(3, FRIDAY, 3, 0.6),
            attempt(1, MONDAY, 1, 0.8),
            attempt(4, FRIDAY, 3, 0.99),
        ]
    )
    monday, friday = results
    assert monday.recall.score_pp == pytest.approx(-80)
    assert monday.recall.count == 1
    assert monday.formation.score_pp == pytest.approx(40)
    assert monday.formation.count == 1
    assert friday.recall.score_pp == pytest.approx(40)
    assert friday.formation.pending_count == 1
    assert friday.formation.score_pp is None


def test_new_card_excluded_from_recall_but_contributes_to_formation():
    monday, _ = daily_results([attempt(1, MONDAY, 3, None), attempt(2, FRIDAY, 1, 0.7)])
    assert monday.recall.excluded_count == 1
    assert monday.formation.score_pp == pytest.approx(-70)


def test_reset_or_preview_cannot_bridge_a_formation_pair():
    monday, _ = daily_results(
        [
            attempt(1, MONDAY, 3, 0.8),
            attempt(2, FRIDAY, 3, 0.5, episode=1),
        ]
    )
    assert monday.formation.count == 0
    assert monday.formation.excluded_count == 1
    assert monday.formation.pending_count == 0


def test_source_cohort_can_have_100_outcomes_on_different_days():
    attempts = []
    for card in range(100):
        attempts.extend(
            [
                attempt(card * 2 + 1, MONDAY, 3, 0.8, card=card),
                attempt(
                    card * 2 + 2,
                    FRIDAY + timedelta(days=card),
                    3 if card < 90 else 1,
                    0.85,
                    card=card,
                ),
            ]
        )
    monday = daily_results(attempts)[0]
    assert monday.formation.count == 100
    assert monday.formation.outcome_days == 100
    assert monday.formation.score_pp == pytest.approx(5)
    assert monday.formation.coverage_percent == 100


def test_model_floor_and_low_count_label_for_all_pass():
    metric = summarize([Sample(0.9, 1, MONDAY)] * 10, cohort_count=10)
    assert metric.score_pp == pytest.approx(10)
    assert metric.uncertainty_high_pp > metric.score_pp
    assert metric.status == "insufficient data"


@pytest.mark.parametrize(
    ("p", "passed", "expected"), [(0.2, 100, "better"), (0.9, 0, "worse"), (0.9, 90, "on par")]
)
def test_comparison_labels(p, passed, expected):
    samples = [Sample(p, int(i < passed), MONDAY) for i in range(100)]
    assert summarize(samples, cohort_count=100).status == expected


def test_formation_uncertainty_accounts_for_shared_outcome_days():
    samples = [
        Sample(0.5, success, MONDAY + timedelta(days=day))
        for day, success in enumerate([0, 1, 0, 1])
        for _ in range(25)
    ]
    recall = summarize(samples, cohort_count=100)
    formation = summarize(samples, cohort_count=100, formation=True)
    assert formation.uncertainty_high_pp > recall.uncertainty_high_pp
    assert formation.outcome_days == 4


def test_small_number_of_formation_outcome_days_is_labelled():
    metric = summarize([Sample(0.5, 1, MONDAY)] * 100, cohort_count=100, formation=True)
    assert metric.status == "insufficient data"


def test_missing_scores_are_not_zero_and_counts_partition_cohort():
    metric = summarize([], cohort_count=5, pending_count=3, excluded_count=2)
    assert metric.score_pp is None
    with pytest.raises(ValueError):
        summarize([], cohort_count=2)


def test_day_boundary_with_local_wall_clock_offsets():
    cet = timezone(timedelta(hours=1))
    before = datetime(2026, 10, 7, 3, 59, tzinfo=cet).timestamp()
    after = datetime(2026, 10, 7, 4, 0, tzinfo=cet).timestamp()
    assert anki_day(before, 4, cet) == date(2026, 10, 6)
    assert anki_day(after, 4, cet) == date(2026, 10, 7)


def test_settings_reject_invalid_types_and_ranges():
    settings = Settings.from_config({"minimum_reviews": True, "minimum_formation_outcome_days": -1})
    assert settings == Settings()


@pytest.mark.parametrize(("month", "day"), [(3, 29), (10, 25)])
def test_dst_transition_uses_rollover_wall_clock(month, day):
    berlin = ZoneInfo("Europe/Berlin")
    for hour in (3, 4):
        stamp = datetime(2026, month, day, hour, 30, tzinfo=berlin).timestamp()
        expected = date(2026, month, day) - timedelta(days=int(hour < 4))
        assert anki_day(stamp, 4, berlin) == expected


def formation_pair(card, elapsed_ms, probability=0.8, rating=3):
    source = int(datetime(2026, 8, 1, 12, tzinfo=timezone.utc).timestamp() * 1000) + card
    outcome = source + elapsed_ms
    source_day = datetime.fromtimestamp(source / 1000, timezone.utc).date()
    outcome_day = datetime.fromtimestamp(outcome / 1000, timezone.utc).date()
    return [
        Attempt(source, card, source_day, 3, None, True),
        Attempt(outcome, card, outcome_day, rating, probability, True),
    ]


@pytest.mark.parametrize(("offset", "short"), [(-1, True), (0, True), (1, False)])
def test_30_day_boundary_uses_actual_elapsed_time(offset, short):
    result, outcome = daily_results(formation_pair(1, SHORT_TERM_MAX_MILLISECONDS + offset))
    assert result.formation.count == 1
    assert result.formation_short_term.count == int(short)
    assert result.formation_long_term.count == int(not short)
    selected = result.formation_short_term if short else result.formation_long_term
    empty = result.formation_long_term if short else result.formation_short_term
    assert selected.score_pp == result.formation.score_pp
    assert empty.score_pp is None
    assert empty.status == "insufficient data"
    assert result.recall_short_term.count == result.recall_long_term.count == 0
    assert outcome.recall.count == 1
    assert outcome.recall_short_term.count == int(short)
    assert outcome.recall_long_term.count == int(not short)
    recall = outcome.recall_short_term if short else outcome.recall_long_term
    assert recall == outcome.recall


def test_overall_is_weighted_by_pairs_not_average_of_subgroup_scores():
    attempts = (
        formation_pair(1, 10 * 86_400_000, 0.7, 3)
        + formation_pair(2, 30 * 86_400_000, 0.8, 1)
        + formation_pair(3, 45 * 86_400_000, 0.6, 3)
    )
    result = daily_results(attempts)[0]
    assert result.formation_short_term.count == 2
    assert result.formation_long_term.count == 1
    assert result.formation_short_term.score_pp == pytest.approx(-25)
    assert result.formation_long_term.score_pp == pytest.approx(40)
    assert result.formation.score_pp == pytest.approx(-10 / 3)
    assert result.formation.count == (
        result.formation_short_term.count + result.formation_long_term.count
    )


def test_short_and_long_term_uncertainty_and_labels_use_their_own_counts():
    attempts = []
    for card in range(100):
        attempts.extend(formation_pair(card, (1 + card % 10) * 86_400_000, 0.5))
    attempts.extend(formation_pair(101, 60 * 86_400_000, 0.9))
    result = daily_results(attempts)[0]
    short, long = result.formation_short_term, result.formation_long_term
    assert short.count == 100 and long.count == 1
    assert short.status == "better"
    assert long.status == "insufficient data"
    assert short.outcome_days == 10 and long.outcome_days == 1
    assert long.uncertainty_low_pp is None and long.uncertainty_high_pp is None
    assert short.uncertainty_high_pp > short.uncertainty_low_pp


def test_split_uses_source_days_last_and_next_review_days_first_attempt():
    first, following = formation_pair(1, SHORT_TERM_MAX_MILLISECONDS)
    earlier = Attempt(first.review_id - 6 * 3_600_000, 1, first.day, 1, 0.9, True)
    later = Attempt(following.review_id + 6 * 3_600_000, 1, following.day, 1, 0.99, True)
    result, outcome = daily_results([earlier, first, following, later])
    assert result.formation_short_term.count == 1
    assert result.formation_short_term.score_pp == pytest.approx(20)
    assert result.formation_long_term.count == 0
    assert outcome.recall_short_term.score_pp == pytest.approx(20)
    assert outcome.recall_long_term.count == 0


def test_pending_and_excluded_pairs_are_not_guessed_into_delay_groups():
    pair = formation_pair(1, 60 * 86_400_000)
    first = pair[0]
    attempts = [
        first,
        Attempt(first.review_id + 1, 2, first.day, 3, None, False),
        Attempt(first.review_id + 2, 3, first.day, 3, None, True),
        Attempt(pair[1].review_id, 3, pair[1].day, 3, 0.7, True, episode=1),
    ]
    result, outcome = daily_results(attempts)
    assert result.formation.pending_count == 1
    assert result.formation.excluded_count == 2
    assert result.formation_short_term.count == result.formation_long_term.count == 0
    assert result.formation_short_term.score_pp is None
    assert result.formation_long_term.score_pp is None
    assert outcome.recall_short_term.count == outcome.recall_long_term.count == 0
