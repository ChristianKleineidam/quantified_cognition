# Personal insights across historical data

Calculation method v4 includes retained deleted-card histories only when all
current presets agree on active FSRS parameters, historical retention and the
ignored-history cutoff. Otherwise they remain excluded. This can revise source
cohorts, first/last review anchors, matched sleep sessions and formation outcomes.
The visit comparison uses the method recorded in the saved report; legacy
reports without it have an unknown method. A method change is flagged rather
than attributed to newly matured outcomes or improved uncertainty. Existing
association caches remain reusable when their actual matched inputs are unchanged.

Open **Tools → Quantified Cognition Personal Insights…**, or **Personal insights…**
from the memory report or health screen. The default is **all saved history**,
including data collected before installing this add-on. There is no installation
date cutoff, daily streak or seven-day default. Users can return after months
of new data and see the expanded history. Past three years and past twelve
months are optional views. These periods filter study dates, not installation
dates or the dates on which later formation outcomes occurred.

## A compact overview first

The opening view summarizes selected completed study days, saved health days,
their date ranges, and how many of the seven memory/timing combinations can
support all three adjusted phase comparisons. It also reports study days with
pending formation outcomes and days younger than 30 days. Pending outcomes can
remain on a day that already meets the 80% follow-up threshold.

The default table has three rows: **Light → deep**, **Light → REM**, and
**REM → deep**. Each means a ten-minute reallocation at fixed total sleep and
fixed remaining phase. Cells show the associated memory-score difference in
percentage points and an approximate 95% range, or **insufficient data**. The
headers show common eligible day counts. Recall and all six formation/timing
columns remain visible together, with separate Main sleep and Naps tabs.
Selecting a cell explains the substitution, adjustment, counts and exclusions.
No scatter plot or ranking is put in front of these comparisons.

**All associations** opens the original correlation tables and scatter plots;
**Phase comparisons** returns to the overview. Switching views or tabs reuses
the current results without calculation. Full saved history remains the default.

## What changed since the last visit

The overview compares with the last successfully saved, displayed visit for
that provider. It reports added completed study days and health days, identifies
historical backfills, and counts updated/removed history. These data counts use
all saved history, even when a narrower analysis period is selected.

For previously studied dates, it reports net reductions in pending formation
outcomes only when the source cohort and exclusion count are unchanged and
the evaluated count increases by the same amount. Short/long increments are
shown only when their sum agrees with that reduction. Cohort changes, removed
history and revisions are reported separately. These aggregate transitions
do not establish identities of individual cards or when a synced outcome first
occurred; no review-pair records are retained. Newly eligible formation-score
days are counted separately, including days passing the 30-day age threshold
without another follow-up. They still need matching health data for insights.

Uncertainty is compared for the same estimate under the same method, settings,
FSRS configuration, Anki version, day boundary and time-window selection.
The overview counts narrower/wider ranges, newly available estimates and
estimates that are now insufficient. Selecting a cell shows the prior range
and matched-day count, with percentage width change when the prior width is
nonzero. A changed method, threshold or window is explained instead of treated
as improved uncertainty. A rolling 12-month/three-year selection remains the
same selection, but dropping older dates is explicitly noted. Seasonal standard/
daylight abbreviations alone do not count as a timezone change.

Range changes also reflect revisions to measurements, which days qualify,
and sampling variability. They do not demonstrate a change in memory ability,
establish causation or provide a formal test that the estimates differ.

The first displayed visit after upgrading establishes a baseline; the older
calculation cache cannot establish what a user actually saw. Background model
calculations, daily updates and exports never advance visit history. Comparisons
remain relative to the previous visit throughout an open window, including
refreshes, tab changes and provider changes. The latest displayed data is saved
for the next visit. A display opportunity does not prove that it was read.

`quantified-self-memory/personal-insights-visits.json` keeps only the latest
snapshot for each provider: display time, date-level counts/readiness, hashes
of health measurements and session matching inputs, analysis context, and
summary estimates/ranges. It contains no exact sleep/review times, raw health
values, card text/IDs, review pairs or credentials. Atomic file writes run on an
independent owned worker and can finish after immediate window/app closing;
they do not take Anki's collection/update/export lock. Tickets and revisions
reject out-of-order writes; provider deletion/account replacement clears its
baseline and invalidates already queued saves. A failed save preserves the
previous baseline and is reported inline while the window remains open.
Reconnecting also starts a new baseline: the add-on does not identify provider
accounts to establish that the reconnect is the same account.

The personal summary CSV appends previous-visit time, comparability, prior
range/day count, uncertainty trend/width change and added-day/follow-up counts.
Exporting the file is not a visit. Visit history and these comparisons are
excluded from fixed daily CSVs and research uploads. This feature reuses method
version 4 model caches; it does not force FSRS replay or a provider backfill.

## Individual associations

The fixed-order matrix has health measurements as rows and seven comparisons
as columns: recall with sleep before studying, then both sleep before and sleep
after studying for overall, ≤30-day and >30-day formation. All six formation
columns are visible together. There is no switch between the two sleep timings.
The steps row has one recall comparison and one per formation metric, spanning
the sleep-timing columns so identical step comparisons are not presented twice.

The Main sleep & steps rows are total sleep, light, deep, REM and awake time, plus daily steps.
Google also has restless time, which is distinct from light sleep. A missing
classic-sleep stage stays missing. Oura and Google are analyzed separately;
the provider with most saved days is initially selected, or the provider from
the health screen when opened there. They are never pooled or summed.

The **Naps** tab uses the same seven comparison columns, with six rows:
recorded nap (yes/no), total duration, light, deep and REM duration, and proximity to studying.
Both timings remain visible together for all three formation metrics. Selecting
a cell shows the coefficient, approximate 95% range, matched days and reviews,
and any insufficient-data reason, just as for main sleep.

Each populated cell shows Pearson correlation `r`, an approximate 95% bootstrap
range and matched eligible day count. Selecting a cell shows its date range,
evaluated review count, interpretation and scatter plot. An insufficient-data
cell explains why it cannot support an estimate; its tooltip/details also
describe omitted dates. Positive correlation means higher measured values
tended to accompany higher memory scores. It is not a percentage improvement,
and it does not show that changing a sleep stage would cause better memory.
Memory scores themselves are percentage-point deviations from FSRS prediction.

## Actual sleep-session alignment

For a source Anki study date D:

| Comparison | Matched measurement |
| --- | --- |
| Recall or formation, main sleep before | Last main sleep ending before D's first review |
| Formation, main sleep after | First main sleep starting after D's last review |
| Recall or formation, naps before | After preceding main sleep ends, before first review |
| Formation, naps after | After last review, before following main sleep starts |
| Recall and daily steps | D − 1 |
| Formation and daily steps | D |

There is no recall/next-night comparison. Formation is still credited to its
source study day, even when outcomes arrive months later. Short/long groups
retain the existing actual-delay definition; this analysis does not regroup
cards by how old their source day is today.

Main sleep must be within 36 hours of its review anchor. Matching uses actual
UTC instants from provider sessions and Anki review history, respecting offsets
and daylight-saving changes. It also works for daytime main sleep. Oura
`long_sleep` identifies candidate main sleep; `sleep` and `late_nap` identify
naps. Multiple long sleeps with the same local wake date are flagged as ambiguous.
Google supplies `mainSleep` and `nap` flags; a nap is kept separate even when
both flags are true. Unknown classifications, missing timestamps, processing
and overlapping/conflicting sessions are flagged rather than guessed. A main
sleep overlapping the day's review span prevents attribution for that day.

Nap duration sums the recorded asleep time in the window. Light, deep and REM
are summed separately when every nap in that window reports the relevant
stage. An unreported stage remains unknown, including for unstaged/classic
sleep; an explicitly reported zero remains zero. No-nap days have blank stage
durations. Available phases can be used even if another phase is missing.
Nap proximity is the
gap from the latest nap end to the first review (before), or from the last
review to the earliest nap start (after), in hours. A smaller value means closer
to studying. Duration, stage and proximity correlations include **nap days only**;
days with no recorded nap are not assigned a fictitious zero duration or time.
The recorded-nap comparison uses 0/1, and additionally requires at least ten
days in each group. A zero means no nap was recorded in a successfully queried
window bounded by observed main sleep, not proof that no nap occurred or that
the device was worn. Missing session coverage stays unknown.

Naps between the first and last reviews are counted separately in the daily
export and omitted from before/after correlations: a daily score combines
reviews on both sides of such a nap. These comparisons describe whole study
days, not precise per-card sleep effects. They do not establish an optimal nap
length or time, and tiredness or other reasons for napping can explain associations.

Original provider-dated totals are retained in the daily CSV for compatibility;
those totals may include naps. New `*_main_before_*`, `*_main_after_*`,
`*_nap_before_*` and `*_nap_after_*` columns are distinct, source-study-day
measurements used by insights. Exact session/review timestamps stay local and
are excluded from CSVs and research uploads. Existing installations refresh
review anchors once and backfill session history on the next successful
provider sync. Older aggregates alone cannot supply these comparisons.

## Which days are eligible

Only completed Anki days before the current Anki day are considered. Daily
scores need at least the configured `minimum_reviews` evaluated observations
(default 30). Formation also needs an available uncertainty range and the
configured effective outcome-day minimum (default 3), a source day at least
**30 calendar days old**, and **80% of the
overall studied cohort evaluated**, including when displaying a short/long
band. Coverage uses evaluated/full-cohort counts; excluded and pending cards
do not become successes or failures. This is an eligibility filter, not proof
that the remaining formation outcomes are unbiased or complete. Uneven numbers
of follow-ups per outcome day reduce effective support; raw day count alone is
insufficient. [Statistical validation](STATISTICAL_VALIDATION.md) gives the
formula and simulation evidence.

Scores and health measurements must both be finite and present. Zero steps is
an observation, not missing data. Processing, conflicting or overlapping sleep
is excluded. A provided total can be used when some individual stages are
unavailable; missing stages are never imputed. Each eligible study day gets
equal weight, rather than allowing a high-review day to dominate the relation.
Matched review totals are shown as context, not as the correlation sample size.

The default adjusts each variable for its own calendar-month average. Months
with fewer than four matched days are omitted. The plotted axes use the same
centering. Unadjusted r remains visible in the detail, and users can uncheck
**Compare within calendar months** to inspect the unadjusted relation. This
reduces shared slow shifts but does not control for weekday, illness, stress,
study habits or other influences, or eliminate all trends and seasonality.
Monthly groups use the source Anki study date for both measurements. Different
cells can have different matched dates; their coefficients are not a formal
test of a difference between the two sleep timings.

An estimate is labeled **insufficient data** unless there are at least **60
matched days**, **90 calendar days of span**, and **six occupied 14-day blocks**.
Both variables must vary after the chosen adjustment. These are conservative
readiness rules, not a formal power calculation. If fewer than 95% of bootstrap
histories have usable variation, uncertainty is also withheld. Low-data point
estimates may be present in the exported summary but never receive a ready label.

## Fair phase comparisons at fixed total sleep

For each provider, main-sleep/nap scope, memory metric and before/after timing,
the phase model intersects the eligible dates with reported total, light, deep
and REM durations. All three contrasts use exactly that common cohort. Different
metrics or timings can still have different eligible days; before/after values
are not a formal test against each other. Recall never uses following sleep.
Nap models include recorded nap days only, with complete stages for every nap
in the matched window. A reported zero phase duration is allowed.

Stages must sum to total asleep time within three minutes. Larger discrepancies
and missing phases are excluded. The three reported stages are proportionally
rescaled to the reported total only within that tolerance, closing small summary
rounding differences. Awake and restless time are not substituted for a phase.
After intersecting dates, the four-common-days-per-month rule is checked again.

The fitted model is ordinary least squares with an intercept (or source-calendar-
month intercepts), total sleep minutes, deep minutes and REM minutes. Light is
the omitted reference. Deep's coefficient estimates deep replacing light;
REM's coefficient estimates REM replacing light; their coefficient difference
estimates deep replacing REM. Effects are multiplied by ten to express score
percentage points per ten-minute reallocation. These are linear isotemporal
substitutions, not correlations or log-ratio compositional regression. If total
sleep is constant within the fitted periods, its redundant column is dropped;
total is already held fixed in that case.

The same 60-day, 90-calendar-day and six-block readiness rules apply. Models
also need variation in memory scores and identifiable phase variation after
adjustment. Rank deficiency, a variance inflation factor above 100, or less than
one minute residual standard deviation in deep or REM makes the model
insufficient. Each contrast additionally needs ten observed days with at least
ten minutes in its donor phase. These are explicit conservative application
choices, not established power calculations or recommendations for changing sleep.

All three contrasts are fitted together. Uncertainty uses 999 deterministic
paired moving-block bootstrap draws over the actual source-date calendar,
preserving gaps. Every draw refits total-sleep/phase coefficients and month
intercepts; it does not reuse the original residuals or hold nuisance coefficients
fixed. Method v5 adds a finite-block t/standard-error floor to the percentile
range, with regression degrees-of-freedom correction. The effective count of
calendar blocks must also meet the six-block default, so isolated sparse dates
cannot hide concentrated data. More than 5% unstable fits withholds the ranges.
The ranges are individual,
not simultaneous across contrasts. This accounts for short-range temporal
dependence, not all dependencies from shared later formation outcomes.

The result is a conditional observational association, not an estimate of the
effect of deliberately changing a sleep stage. Linear substitution need not
describe nonlinear relationships or reallocations beyond the observed range.
It does not control for prior/following sleep simultaneously, nap count/timing,
illness or other unmeasured influences. The daily score, current FSRS model,
available follow-ups, device classifications and chosen eligibility remain
conditions of the estimate; their measurement/model uncertainty is not added
to these bootstrap ranges. There is no single independent "importance" ranking.

Method background: [Mekary et al. (2009), isotemporal substitution](https://pmc.ncbi.nlm.nih.gov/articles/PMC2733862/).
That paper concerns time-use exposures, not validation of this add-on's sleep
model. [Dumuid et al. (2019)](https://doi.org/10.1177/0962280217737805)
describes the distinct compositional/log-ratio alternative and its interpretation.

## Individual-correlation uncertainty and limits

The coefficient is ordinary Pearson correlation, either raw or after removing
calendar-month means from both daily series. There are **999 deterministic
paired moving-block bootstrap replicates**. The source-date calendar is filled
with empty slots for missing days, then contiguous 14-day calendar blocks are
sampled with replacement to the original calendar length. The last block is
truncated to that length. Every draw keeps health/score pairs together. Missing
days remain empty; observations separated by months are not turned into adjacent
days. Each replicate refits the calendar-month means to its sampled observations.
The 2.5th and 97.5th percentiles of usable bootstrap correlations form the range.
The final short block samples all starts eligible for its own width, including
the final calendar day. This sampler is shared with phase comparisons.
Hash-derived seeds make unchanged inputs reproducible.

Blocking is intended to accommodate short-range temporal dependence; it is
not an assurance that all dependencies are captured. Formation cohorts can
share later outcome days farther apart than a block. The ranges are conditional
on the observed daily scores, eligibility and the current fitted FSRS model.
They do not incorporate unknown pending outcomes, FSRS parameter-fitting
uncertainty, selective review timing or all effects of historical changes.
The 30-day age and 80% coverage rules reduce incomplete follow-up problems;
they do not remove them. There is no independent causal estimate of memory
formation or learning ability.

Ranges are **per comparison**, not simultaneous intervals across the table.
No p-value, best-factor ranking or confirmatory significance badge is shown.
Checking many sleep stages, outcomes, periods and adjustments can expose chance
patterns; adjacent sleep stages and total sleep are also related. The matrix is
an exploratory description of the user's available history, not a recommendation
to optimize a particular stage based on a single apparent association.
In particular, a larger deep-sleep correlation than REM correlation is not a
test that deep sleep is independently more important. Stage durations share
total sleep time, other influences can affect both, and missing stages can give
different rows different matched dates. These individual correlations are separate associations, not
a model that holds total sleep and every other phase constant.

Relevant statistical background: [NIST on temporal dependence and uncertainty](https://www.itl.nist.gov/div898/handbook/eda/section2/eda251.htm),
[Hyndman and Athanasopoulos on blocked time-series bootstrap](https://otexts.com/fpp3/advanced.html#bootstrapping-and-bagging).
The particular day/coverage thresholds and 14-day block length are application
choices, not requirements claimed from these references. There is no automatic
selection of the most favorable lag, time window or block length.

## Updating, storage and export

On opening, the existing Anki background update checks historical reviews and
current FSRS inputs and reuses unchanged scores. **Update history** repeats that
check. The insights step reads only the separate results/health databases; it
never replays FSRS. Correlations and bootstrap work run on an independent file
worker, so normal reviews and Qt remain available. Closing cancels the work and
profile changes cannot display another profile's results.

The local `quantified-self-memory/personal-insights.json` cache stores only
summary coefficients, ranges, counts and method information, not individual
reviews or raw health records. A fingerprint of actual matched points and
analysis settings reuses unchanged estimates even on a later date. Corrected
older health values, revised scores or newly eligible formation follow-ups
change the fingerprint. Publication checks source revisions so an older worker
cannot publish results calculated from superseded inputs. Up to eight historical
setting/input variants are retained. Provider disconnect/account replacement
removes the derived cache too. Previously exported files remain user-managed.

An open window refreshes after background health imports or daily score updates.
Users do not have to check each day to accumulate useful observations. Historical
availability still depends on the provider import range and saved Anki history;
the add-on cannot manufacture absent measurements. API connections remain
disabled until their separate setup/approval steps are complete.

**Export insights…** writes a separate summary CSV with all measurements and
timings, including insufficient-data rows, the uncertainty method/version and
thresholds, date ranges, omitted-day counts, review totals and memory metadata.
It includes 88 individual correlations and 42 phase contrasts. Existing columns
stay in order; appended fields distinguish `analysis_kind=correlation` from
`phase_substitution`, main/nap scope, donor/receiver, ten-minute shift, effect/range
in percentage points, donor support, VIF and phase-model thresholds. Correlation
fields are blank on substitution rows. Method version 4 invalidates older
personal-insights caches once; unchanged inputs reuse both kinds of estimates.
Atomic replacement preserves a previous file on export failure. The existing
fixed daily CSV continues to contain the underlying Anki/health measurements.
Research package v4 includes the disclosed derived main-sleep/nap measurements,
including nap light/deep/REM durations, not personal correlation summaries.
Notice v5 requires a fresh sharing choice
while preserving the original first-consent/exposure audit. Each submission
still needs an explicit package preview and Send. The existing sharing gate
and first-exposure audit cover this screen and nonempty personal exports.
