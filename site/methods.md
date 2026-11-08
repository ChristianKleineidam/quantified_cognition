# How the measurements work

Quantified Cognition uses your Anki review history to estimate daily recall and
memory formation. It compares your answers with Anki's FSRS predictions, so the
scores account for how likely you were to remember each card and how long it
had been since you studied it.

These measurements use new, experimental methods. Their reliability for
detecting changes in everyday cognitive performance has not yet been
established. Use the results to spot patterns and inform personal experiments
or adjustments to your routines, alongside other evidence and your own
experience. They should not be the sole basis for major decisions. Quantified
Cognition does not diagnose medical conditions or provide treatment advice.

## Recall: how you performed that day

For each card, the add-on uses your first answer on an Anki day. Again counts
as a failed recall; Hard, Good and Easy count as successful recalls. It compares
each outcome with the predicted chance of remembering and averages the
differences across the day.

Recall is shown overall and separately for reviews at most 30 days after the
previous review and those more than 30 days later.

A score above zero means you recalled more than predicted; below zero means
less than predicted. Scores are expressed in percentage points. For example,
if 80 successful answers were predicted across 100 eligible cards and you
answered 85 successfully, the score is +5 percentage points.

## Formation: what later reviews tell us about a study day

For each card studied on a day, the add-on looks at your first answer on the
next Anki day when you review that card. It compares that answer with the FSRS
prediction after your last review on the earlier day, allowing for the actual
time between reviews. The difference contributes to the earlier day's
formation score.

If you study a card on Monday and review it again on Friday, Friday's answer
helps estimate Monday's memory formation. The daily score averages the
available follow-ups. It is shown overall and separately for follow-ups within
30 days and beyond 30 days.

Cards awaiting another review are shown as pending, outside the score until
a follow-up is available. Coverage shows how much of a study day's result is
available. Scores become more complete as cards are reviewed again, with
longer follow-ups taking more time to accumulate.

## Sleep stages, naps and activity

Personal insights compare your daily scores with sleep and activity from the
same periods of your history. Recall is compared with the preceding main
sleep. Formation is compared separately with the main sleep before and after
studying. Actual sleep-session times distinguish main sleep from naps, whose
timing, duration and stages are examined separately. Steps use the previous day
for recall and the study day for formation.

Oura workouts are compared in the 24 hours before the first review and after
the last review. Recall uses only the preceding window; formation uses both
separately. Comparisons cover recorded workouts, duration, easy/moderate/hard
exercise and proximity to studying. Workouts between reviews are counted
separately in the daily export. Duplicate recordings are not added twice;
overlapping workouts and incomplete history are flagged. No recorded workout
does not prove no exercise occurred. These comparisons account for calendar
month when selected, but do not adjust for sleep or other lifestyle factors.

Comparisons between light, deep and REM sleep use the same eligible days and
account for total sleep duration. For example, an estimate for ten more minutes
of deep sleep replacing light sleep compares a change in sleep composition
while holding total sleep and REM sleep constant.

## Uncertainty and interpreting changes

Optional research packages include pseudonymous individual review predictions,
ratings and outcomes. They support prediction checks such as the Brier score,
the average squared difference between predicted probability and observed
success, as well as further statistical analysis. These probabilities are
reconstructed with current FSRS settings. The research information and privacy
policy describe the records shared and the separate consent required.

Scores include review counts, follow-up coverage and uncertainty intervals.
Personal insights also show uncertainty and identify when there is
insufficient data. Estimates depend on both the number of usable reviews and
the number of days they represent.

Uncertainty ranges describe statistical variation under the methods'
assumptions; they do not cover every possible source of error. More observations
and narrower ranges do not eliminate systematic bias or establish that the
measurements are valid. For example, a consistent mismatch in FSRS predictions
or selective follow-up reviews can influence scores even in a large history,
without reliably reflecting a change in learning ability.

Existing history is recalculated with your current FSRS settings. This allows
older reviews to be analyzed, including eligible reviews from before FSRS and
reviews synced from other Anki apps. Reviews that cannot be reconstructed
reliably are excluded with a reason. Changing FSRS settings can change past
scores. Dates use your configured Anki day boundary.

These scores estimate learning and recall relative to FSRS predictions. Card
content, study routines, scheduling and conditions at the later review can also
affect them. The comparisons can help identify patterns worth exploring in
personal experiments. Establishing cause and effect also requires an
appropriate experimental design.

The [research information](RESEARCH_INFORMATION.md) explains voluntary
participation, and the [privacy policy](PRIVACY.md) describes the data used and
shared.
