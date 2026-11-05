# Quantified Cognition for Anki

Source repository: [ChristianKleineidam/quantified_cognition](https://github.com/ChristianKleineidam/quantified_cognition).

A desktop add-on that compares recall with Anki's own FSRS predictions. It reconstructs past reviews with **today's FSRS parameters**, including pre-FSRS (SM-2) reviews and reviews synced from AnkiDroid, AnkiMobile, and AnkiWeb.

The release listing text is saved in [ANKIWEB_DESCRIPTION.txt](docs/ANKIWEB_DESCRIPTION.txt).
The public information website is hosted on Cloudflare Pages at
[quantifiedcognition.jollyjoyjourney.com](https://quantifiedcognition.jollyjoyjourney.com/);
see [website publishing](docs/WEBSITE.md).
It describes the intended public release and has not been uploaded to AnkiWeb.
**Oura, Google Health and research uploads remain disabled in this beta.**

Version 0.18.0 renames the add-on from Quantified Self Memory to Quantified Cognition.
The installer package ID (`quantified_self_memory`), profile directory
(`quantified-self-memory`) and credential-store identifiers stay unchanged,
so existing installations retain their settings, saved results and sharing history.

## Install and use

Requires Anki **25.09 or later** with FSRS enabled in Deck Options. No extra Python packages are required in Anki. The add-on runs on desktop; synced mobile/web reviews are analyzed on desktop.

1. From the repository root, run `python tools/build_addon.py` with Python 3.10 or later. This creates `dist/quantified-cognition-0.20.0.ankiaddon`; generated packages are not committed to the repository.
2. In Anki, select **Tools → Add-ons → Install from file**, select the file, and restart Anki.
3. Open **Tools → Quantified Cognition…**.
4. Choose **Daily recall** or an overall/short-term/long-term **Memory formation** metric. Inspect a day with the date selector or by clicking the chart. **Inspect daily data** switches to a detailed table.
5. Recall starts with the recent 30 days. Formation starts with 90 earlier study days, ending 30 days ago. **Suggested range** restores these defaults; custom dates and **All history** stay available. A day's age does not guarantee complete follow-up.
6. **Export CSV…** exports all metrics for the selected source-date range. **Daily settings…** enables automatic updates and a fixed full-history CSV. **Update** checks for changes immediately, including after syncing or optimizing FSRS.
7. **Health data…** opens one sleep/steps screen with an **Oura / Google Health** selector, also available through **Tools → Quantified Cognition Health Data…**. The backend is deployed with both provider connections and research uploads disabled until the policy and approvals are ready.
8. **Personal insights…** shows adjusted sleep-phase comparisons with all four memory metrics across **all saved history**, including data from before installation. Both sleep-before and sleep-after formation comparisons are visible together, with uncertainty and **insufficient data** labels. **Since your last visit** summarizes new history, formation follow-ups and uncertainty changes. **All associations** opens the full stage/steps correlation tables. It is designed for occasional visits after accumulating more history.

The report shows one metric at a time, with a score, evaluated count, approximate uncertainty range and comparison label for the selected day. Formation follow-up availability is shown separately, including pending and excluded cards. The optional table is populated only when opened. All Anki calculation stays on the computer and scheduling is unchanged. Oura sync and explicitly confirmed research uploads use separate online services. It uses a consistent copy of the collection because Anki's native card-info API may populate internal caches. Disposable working copies are deleted after calculation. Closing exports also keep a frozen local snapshot until publication succeeds. Collection-sized free space is needed for these copies.

## Daily updates and saved results

Open **Tools → Quantified Cognition Daily Settings…** or **Daily settings…** in the report. Enable automatic updates, choose an absolute CSV file path, and save. Enabling starts an initial update. Preferences are separate for each profile and are off by default; choose different CSV destinations for different profiles.

The add-on checks every five minutes while a profile is open. It requests a daily update using the configured **Anki day** boundary, and also requests a fresh update immediately when the current review queue is finished. Queue completion refreshes results even if the CSV was already generated earlier that day. Anki normally returns to its overview automatically after the last available card; no manual navigation is needed. A pending learning card that becomes available later can lead to another completed session. If Anki is closed at the boundary, the next profile opening catches up. New analysis waits while Anki is performing other collection operations or is on the review screen, and checks again after leaving review. A calculation already running on a copy continues independently when reviewing starts. It does not launch Anki independently; a final export worker can finish after its window closes. Catch-up checks the latest inputs once. If nothing affecting the analysis changed, saved scores are reused without replaying FSRS. Changed inputs trigger one full-history replay, rather than separate runs for missed days.

Version 0.18.1 also checks for changed history after Anki sync, even when today's
CSV is already current. It cancels older automatic captures before sync and
refreshes in the background afterward. Closing sync preserves imported reviews
for export even when no cards were answered locally. Unchanged history reuses
saved calculations; failed CSV publication retries from saved results.

Calculations, daily eligibility checks, results database reads/writes, CSV exports, and profile-preference file reads/writes run in **background threads**. Only reading the rollover setting and creating a consistent collection copy use Anki's serialized `QueryOp` worker. Temporary-folder creation, file/settings checks, opening the copied collection, input fingerprinting, FSRS replay, aggregation, saving results and CSV writing all use the independent worker pool. Each copied collection has its own native Anki backend; heavy work never holds Anki's collection queue or its background-operation guard. CSV retries use only saved results and can run while reviewing or syncing. Qt widgets and success/error messages are updated only on the main thread. Recalculation has an inline status message instead of a modal progress window. The copy itself still needs exclusive coordination with live collection operations and takes time proportional to collection size; automatic copying is deferred on the review screen. Finishing a review queue starts a fresh update that includes the final answer. If an older calculation is still running, one follow-up update is queued so its older copy does not omit the completed session. Leaving review early keeps the ordinary daily eligibility check. Disabled automatic updates stay disabled.

Every new manual or daily calculation saves the latest full report to a **separate SQLite database**, `<Anki profile>/quantified-self-memory/results.sqlite3`. It contains a `daily_results` table with numeric recall/formation scores, counts, uncertainty and coverage columns, plus a `metadata` table with report details and daily update state. It does not write metrics to the main Anki database. Current reports replace the previous results, so changed FSRS parameters and new follow-up reviews can revise earlier scores. This database stores the latest analysis, not an archive of every calculation. Saved results are loaded when the report opens, followed by a background freshness check. Their generation time stays unchanged when the analysis is reused.

When automatic updates are enabled, both daily and manual updates replace the chosen CSV with the latest **full-history** report, regardless of the report's display date range. The manual Export CSV button still exports the selected date range to a separately chosen destination. Files are replaced only after a complete new CSV is written. If the destination is unavailable or open in another application, the previous CSV remains intact; the newer database results remain available in the report, with a separate export error and **Retry CSV export** button. That button only reads saved results and writes the CSV, without collection access or FSRS replay. A retry uses the analysis's last checked Anki day, so exporting yesterday's analysis cannot mark today's analysis complete.

**Closing immediately after the last card still updates the fixed CSV.** If reviews have not been exported, the add-on preserves a consistent snapshot at the actual collection-close step, after closing sync. Only that capture needs brief coordination with Anki's collection queue; FSRS replay, saving scores and CSV writing use an owned background thread that outlives the Qt window and normal interpreter shutdown. It needs no Qt callbacks, live collection or extra user action. An older in-progress update is cancelled and cannot publish over the final snapshot. This also works when closing partway through a review session or switching profiles. Disabled automatic updates remain disabled.

The frozen input and request are stored locally in `<Anki profile>/quantified-self-memory/pending-exports/`. Each attempt opens a disposable working copy; the frozen input remains intact for recovery. Successful publication removes the request and frozen snapshot. The snapshot contains the original collection contents, stays on this computer and is never included in CSV output. A failed export preserves the previous complete CSV and the newer saved scores. The closing worker retries transient failures twice, then leaves the job for automatic recovery at the next profile opening. A destination that remains unwritable, a forced process termination or loss of power can postpone publication; normal closing does not cancel the final export.

Analysis freshness and export success are tracked independently. An export records the saved report revision it contains; a successful earlier CSV does not suppress retries for revised scores later the same day. Fixed exports and analysis updates use a per-profile file lock across threads and overlapping Anki processes. Durable snapshot sequence numbers prevent a delayed older copy from replacing newer results. A retry reads saved analysis without another collection operation, input scan or FSRS replay, including during reviewing. Automatic failures are logged and shown inline in an open report, without a modal warning, and retry after one hour while the profile is open. Exporting an older report does not mark a newer Anki day's analysis complete. A missing CSV, changed path, changed export format or new report revision also makes export due. Changing the CSV path or enabling updates starts a new check.

Daily preferences are stored beside the results database in `daily-settings.json`. These files are local to the profile, survive add-on upgrades, and are not synced by AnkiWeb or included in Anki's collection-package backups. Back them up separately if needed. The configured CSV is an independent file at your chosen location.

## Personal insights

Open **Tools → Quantified Cognition Personal Insights…** or the button in the memory/health screen. Existing history is used immediately; there is no installation cutoff or daily check-in requirement. The compact overview shows history coverage, formation follow-ups and three phase comparisons: light → deep, light → REM and REM → deep. Each estimates a ten-minute reallocation at fixed total sleep and remaining phase, using the same eligible days within each memory metric/timing, with uncertainty and insufficient-data labels. Main sleep and naps stay separate. Both sleep timings remain visible for overall, short-term and long-term formation; recall uses preceding sleep only. **All associations** opens the full correlation tables and scatter plots, including daily steps. **Export insights…** saves both analyses to a separate summary CSV.

The default compares daily deviations from their calendar-month averages to reduce associations caused only by slow historical shifts. Unadjusted correlations remain available. Approximate 95% ranges use 999 paired 14-day calendar-block bootstrap replicates, keeping missing-date gaps. Phase ranges also use a finite-sample standard-error floor and require six effectively represented blocks. An estimate needs 60 matched days spanning 90 calendar days, with variable measurements/scores. Daily scores must meet the review-count/outcome-day support thresholds. Formation additionally requires study days at least 30 days old and at least 80% of the overall cohort evaluated. Pending outcomes never become failures. Insufficient-data cells explain what is missing.

Main sleep and naps are matched separately using actual sleep and review timestamps. Both views show light, deep and REM sleep separately from total sleep, where reported. The **Naps** tab also shows recorded nap days and proximity to studying, with before/after comparisons together for all three formation metrics and before-study comparisons for recall. Missing or ambiguous sessions stay unknown. Nap durations, stages and proximity use nap days only. Correlations and their uncertainty describe associations; their sizes alone do not rank the independent importance of phases, which share total sleep time. See [INSIGHTS.md](docs/INSIGHTS.md) for alignment, readiness rules and statistical assumptions.

Memory freshness checks, correlations and exports run in the background. The actual analysis reads saved Anki/health data and never replays FSRS. The small local summary cache is reused for identical matched inputs, and invalidated by new/corrected data or newly eligible follow-ups. An open screen refreshes after background imports/score updates. Provider disconnect/account replacement also removes the derived cache. The ordinary full-history daily CSV and research upload contract remain unchanged.

## Oura API connection

Open **Health data… → Oura → Connect Oura…**, sign in on Oura's website and allow the `daily` permission. The add-on uses the Oura API, not uploaded ZIP/CSV/JSON files. The desktop receives access/refresh tokens through a hosted OAuth service; the Oura client secret stays on that server. OS-protected storage keeps credentials out of settings, CSV and research packages. Windows uses DPAPI, macOS Keychain and Linux Secret Service (`secret-tool` required). There is no plaintext fallback.

Initial sync fetches the history; subsequent checks normally sync the recent 30 days hourly while Anki is open, with a weekly full reconciliation for older changes. **Sync now** checks immediately. Automatic checks and deletion retries use file/network workers without occupying Anki's collection queue. The screen shows up to 100 recent saved days; CSV includes full saved history. OAuth cancellation, provider errors and retries never open a modal prompt during ordinary reviewing.

The separate profile-local `quantified-self-memory/health.sqlite3` stores Oura total, light, deep, REM and awake durations in seconds, daily steps, assigned dates, available sleep start/end timestamps and offsets, sleep types, algorithm versions, sync times and hashed sleep-record identifiers. It excludes heart rate samples and personal/account details. The health cache, saved insights and pending research packages are encrypted with an OS-protected profile key. Legacy caches migrate on their next successful access. Sleep/step API responses go directly from Oura to the desktop. OAuth tokens pass through the Supabase service but are not stored in its database. Research measurements are encrypted to the operator's public key before a separately confirmed upload; its private key stays off the server. Local health data is not synced by AnkiWeb. Keep the original OS credential store with cache backups. User-selected CSV exports remain readable for other programs. See [encryption and recovery](docs/ENCRYPTION.md).

All pages of sleep and activity are validated before a sync commits. Complete successful windows are authoritative: corrected values replace earlier ones and absent/deleted periods are removed within the queried window. Failed or partial requests leave saved measurements intact. Unchanged records preserve the health revision and avoid unnecessary CSV writes. Missing values remain unknown; observed zero steps stays zero. Confirmed sleep and naps use Oura's assigned day. Duplicate versions of an identical interval prefer Oura's V2 algorithm. Conflicting/overlapping intervals are flagged with blank totals. Older v0.8 daily-summary data stays readable but is replaced by authoritative API windows. `durations available` describes supplied measurements, not proof of full-day coverage.

Oura's assigned day is preserved, including for late naps, and is not shifted to Anki's rollover. Original daily CSV columns join ISO date labels and mark `oura_day_basis=provider_assigned_day`. Additional main-sleep/nap columns use actual session timing relative to that Anki day's first/last review. Exact sleep and review timestamps remain local and are excluded from exports and research uploads.

The fixed daily CSV and manual memory export append Oura measurements, availability flags, algorithm versions, sync time and data version. Health-only dates have blank Anki metrics; dates without health data retain blank Oura fields. Existing CSV columns keep their names and order. **Export CSV…** on the Oura screen exports combined full history, or health alone if no saved Anki report exists. A changed health sync refreshes an enabled fixed CSV from saved Anki scores without scanning/replaying FSRS. Failed publication preserves the previous complete CSV; normal export retries pick up the health revision. Raw sleep records, IDs and exact sleep times are excluded from exports.

**Disconnect and delete…** stops syncing, removes local Oura measurements and derived insights, and attempts token revocation. Research uploads and existing CSV copies have separate deletion controls. Provider-side scope/membership restrictions and approval still apply. See the [approval checklist](docs/OURA_APPROVAL.md), [privacy-policy draft](docs/PRIVACY.md) and [Supabase deployment guide](docs/DEPLOYMENT.md). The dedicated backend is deployed in Frankfurt with provider connections and research uploads explicitly disabled; credentials, a completed published policy, live-account pilots and required approvals remain outstanding. Tests use synthetic API responses.

## Google Health API connection

In **Health data…**, select **Google Health** and choose **Import since** (the past year by default). First link your Google account in the Google Health mobile app, then select **Connect Google Health…**. This integration uses Google Health API v4 and reads only sleep and daily steps available through that account, including supported connected sources. It does not directly read every Android Health Connect record. Google's sleep and activity read permissions are requested; activity permission is broader than steps, but only steps are fetched. Granting just one permission imports that dataset. Google Cloud credentials, policy/verification and research approval steps are documented in [GOOGLE_HEALTH.md](docs/GOOGLE_HEALTH.md); the Google service remains disabled pending those steps.

Sleep totals, light/deep/REM/awake/restless durations, period counts, availability, sleep type and steps are stored in the same separate health database. Selected sleep-session timestamps, main-sleep/nap flags, durations and hashed record identifiers are retained locally for matching; account/device IDs and individual step samples are excluded. Google supplies sleep summaries to whole minutes, exported as seconds. Classic sleep has no invented light/deep/REM values. Missing observations stay blank; measured zero steps stays zero. Processing/overlapping sleep is flagged. Daily totals use the local wake date; steps use Google's civil day. Current days may be incomplete and later corrected. Google and Oura columns are separate and never added together, because their underlying measurements can overlap.

Hourly background sync rechecks recent 30 days; weekly sync rechecks the full chosen history in 90-day chunks. Every page is validated before a window replaces saved values. Failed downloads preserve the previous cache. Changed data refreshes the enabled fixed CSV from saved Anki results without replaying FSRS. All export paths include both providers alongside the Anki metrics, with health-only dates included. Identical syncs avoid rewriting the file; CSV publication retries separately. Provider disconnection removes only its local measurements and attempts token revocation. Research sharing remains a separately confirmed complete package. Its v3 notice names the Google fields and requests a new choice while preserving the original pre-view timing. No uploads occur automatically.

## Sharing choice and consent timing

Version 0.19.1 uses the existing participant ID for explicit research consent.
The original sharing preference/exposure history is preserved. Enrollment checks
eligibility, presents saveable information and obtains explicit consent through
**Agree and join research**; no measurements upload until a separate preview
and Send. The server requires valid enrollment and a current retention review.
Tracked research extracts and collaborator copies have deletion tasks and
confirmation records. There is no fixed research measurement expiry.
The backend migrations are deployed and their closed-state checks passed;
enrollment and all three online features remain closed. Consent-process
acceptance, provider/research approvals and a permitted live-account pilot
remain necessary. See
[research controls](docs/RESEARCH_CONTROLS.md) for the flow and operator procedures.

Before opening the first personal report (including cached results), the add-on asks **Yes, I’m interested / Not now / No, keep it private**. It also asks before a new user enables automatic CSV output. Not now is the default; every choice allows personal use. Change the choice later through **Tools → Quantified Cognition Research Sharing…** or the report's Research sharing button. This first question records a preference; it does not enroll the user.

Choices, prompt presentations and the first results exposure opportunity are logged in the separate, profile-local `study-sharing.sqlite3` database. The append-only event log includes UTC timestamps, event order, notice version/hash and a random participant ID. The first consent and first exposure are preserved when the user changes their mind. Current permission is separate from historical timing: withdrawing permission does not turn a previously early consent into a late consent, and a later opt-in does not retroactively become early consent. A changed notice/version requires a new choice.

CSV exports include sharing metadata, including `research_consent_active`, `research_consent_before_first_view`, first-consent and first-exposure timestamps, and `results_exposure_history`. `true` means the consent event preceded the first recorded opportunity to see results; `false` means it followed. Empty means no consent or unknown prior exposure. The original consent timing survives notice updates; `current_notice_consent_before_first_view` separately describes consent to the current notice. Event order is used for classification even if the system clock moves backward. Opening a personal results report or making a nonempty CSV available counts as exposure; simply calculating results in the background does not. Failed CSV writes cancel the exposure reservation and do not create a first-view event. An unresolved earlier export reservation (for example after a crash) is treated as unknown when classifying later consent.

On upgrade from versions that did not track viewing, existing results files cause `unknown_legacy` classification. The app cannot assume that those users have never seen their results. Future consent timing remains logged without manufacturing a known pre-view status. Exposure describes when results became available, not proof that a person read them. These records allow stratification by consent timing; they do not establish that all sources of participant self-selection are absent.

With Yes selected, reopen Research sharing and choose **Review and submit data…**. The preview shows date range/day count, recipient, service, policy link and visible included/excluded data. **Send this package** submits the entire fixed package once. Packages contain daily aggregates and consent-timing metadata; no review pairs, card text, raw sleep records, exact sleep times or credentials. Supabase stores immutable packages in private PostgreSQL tables. A separate secret authorizes upload/deletion; the public participant ID is never used as a password. Receipts and package IDs prevent duplicate submissions after uncertain failures. **Prepare latest data** prepares a new snapshot; upload retries always require an explicit user action.

Changing to Not now/No queues deletion of previous uploads and discards unsent packages. **Delete uploaded data…** requests deletion separately. Offline requests remain encrypted, block uploads and retry while Anki is open. Acknowledged deletion removes active research packages; an unlinked random-ID tombstone rejects stale requests. Backup/research-extract retention must be configured and disclosed by the operator. A later contribution creates a new upload identity while keeping original local consent timing. Personal Oura sync is independent. Opening a populated Oura table or exporting health measurements counts as a results exposure opportunity. Neither opting in nor background calculations automatically submits research data.

## What the scores mean

Again = failure (`0`); Hard, Good, and Easy = successful recall (`1`). For a card with predicted recall probability `p`, its contribution is `actual − p`. Each daily score is the mean contribution multiplied by 100, expressed in **percentage points (pp)**. Predicted average 85% and observed recall 90% yields **+5 pp**. All evaluated cards have equal weight.

**Recall:** For each card studied on an Anki day, score only its first attempt against the probability immediately before that attempt. A new card's first presentation has no pre-review memory prediction and is excluded from recall. Later same-day attempts still update its reconstructed memory state.

**Formation:** The day's final attempt on each card starts a pair with that card's first attempt on its next reviewed Anki day. The prediction uses the reconstructed memory state after the source day's final attempt and the actual elapsed time to the later attempt. The later recall outcome is credited to the **source day**. Learning/new cards can contribute once a post-review state exists. If 100 cards were studied on Monday, Monday's formation score can eventually contain 100 observations from up to 100 later outcome days. The same later outcome contributes to recall on its own day and formation on the previous studied day; do not treat the two time series as independent.

Formation is recorded in three groups: **overall**, **short term (≤30 days)**, and **long term (>30 days)**. The split uses the actual elapsed time between the source day's final attempt and the next reviewed day's first attempt, not the scheduled interval, card age, or the age of the source day today. Exactly 30 × 24 hours is short term; longer delays are long term. Anki's configured day boundary still determines the source/outcome dates and which attempts are paired. Each evaluated pair belongs to exactly one subgroup and contributes to overall formation too. The overall score is weighted by pair count, so it is not the simple average of the two subgroup scores.

Select the overall, up-to-30-days, or over-30-days formation metric to see its score and graph. Each subgroup has its own score, evaluated-pair count, distinct outcome-day count, uncertainty, and comparison label. A group with no outcomes has an empty score, not zero. Pending pairs and pairs excluded from scoring remain in the overall counts; they are not assigned a guessed delay group. Subgroup cohort counts cover evaluated/classified pairs only, so their pending/excluded counts are zero and coverage is 100% when nonempty. Follow-up coverage should be assessed using the overall counts.

Cards with no later review are **pending**, not failures. Source/outcome pairs separated by a reset or unmodelled preview/cram exposure are **excluded**. The formation inspector always shows the overall evaluated/cohort count, coverage, pending count and excluded count, including while a short/long band is selected. Pending cards cannot yet be assigned a delay band.

Formation availability is independent of the statistical comparison. A day with any pending follow-ups is **provisional**, even if its evaluated subset is labeled better or worse. With none pending it shows **no pending follow-ups**; an empty cohort shows **no data**, and an entirely excluded cohort shows **no evaluable follow-ups**. These describe availability, not absence of bias or certainty. For example, 30 successful evaluations out of 1,000 studied cards can be statistically better while still showing **Provisional · 30/1,000 evaluated (3%) · 970 pending**. The uncertainty range describes observed follow-ups and does not include unknown pending outcomes. A source day's date range does not limit which later outcomes are used. Formation scores continue changing as subsequent reviews arrive; unequal follow-up can bias partially evaluated cohorts.

Anki days follow the configured local rollover hour, including daylight-saving transitions. Historical timezone travel information is not stored in review logs; past dates use the desktop computer's local timezone rules. Each Anki card is a question; sibling cards are separate observations. Deleted-card histories are included when **all current presets agree** on active FSRS parameters, historical retention and the ignored-history cutoff. Native defaults and float precision are normalized; differences in scheduling preferences or inactive legacy parameter fields do not prevent inclusion. All presets, including unused ones, are checked because the deleted cards' deck assignments are unknown. When those settings differ, deleted histories stay excluded and the report explains which settings differ. Included/skipped counts are shown in the report's metadata, with the policy and detailed counts in its tooltip.

Deleted histories are replayed by Anki using temporary blank-note/card placeholders on the disposable copy, retaining the original review IDs and card grouping. Question/answer text and the lost deck assignment are not recovered. The common current policy is an explicit reconstruction rule, not proof of the deleted card's former settings. All existing reset/preview rules, short/long delays, pending outcomes and uncertainty calculations still apply. Deletion does not turn an unobserved later review into a failure. This behavior needs retained or imported orphan review logs; it cannot recover records that were themselves erased.

## Uncertainty and labels

The numeric score is always shown when at least one card can be evaluated. Defaults:

* **Insufficient data:** fewer than 30 evaluated cards, or formation outcomes from fewer than 3 distinct Anki days. These thresholds can be changed through Anki's add-on configuration.
* **Better:** approximate 95% uncertainty range is entirely above zero.
* **Worse:** approximate 95% range is entirely below zero.
* **On par:** range includes zero, meaning no clear difference rather than proof of equality.

Recall uses the larger of model-based and empirical variance with an approximate normal range. Formation additionally uses outcome-day clustering and a delete-one-day jackknife correction, then a bounded, asymmetric modified Wilson range with a small-sample t correction. Outcome days are weighted by how many follow-ups they contain: a few heavily represented days provide less independent information than the raw day count suggests. With insufficient effective outcome-day support, the numeric score/counts stay visible but the range is withheld and labeled **insufficient data**. The ranges describe evaluated follow-ups conditional on the fitted FSRS model; they do not quantify unknown pending outcomes, all sibling-card or across-day dependence, or parameter-fitting uncertainty. See [statistical validation](docs/STATISTICAL_VALIDATION.md) for formulas, simulation coverage, reproducible commands and limits.

Current parameters may have been fitted to the same historical outcomes. These scores are **retrospective comparisons**, not held-out tests of prediction quality. Formation also reflects retrieval conditions on later days and is not a causal estimate of learning ability. A prediction is not displayed before answering a card.

## Historical reconstruction

The add-on calls Anki's `Collection.card_stats_data()` for native historical post-review stability/difficulty and `Collection.compute_memory_state()` for the active decay. It evaluates probabilities with Anki's native SQLite `extract_fsrs_retrievability()` function, using the preceding state and the actual review timestamp. It does not reimplement FSRS or use a present-day card state to predict its past.

The disposable snapshot is truncated at reset/legacy-learning episode boundaries to let Anki reconstruct earlier episodes using the same native history API. Current deck/preset assignments, historical retention settings, and Ignore reviews before settings apply. Incomplete histories use Anki's own initialization rules; reviews without a reconstructable state are excluded explicitly. Non-rescheduling filtered/preview reviews are excluded because Anki does not update the model for those exposures. Rescheduling filtered reviews are included. Manual reschedules are not answers.

The report uses the whole history even when displaying a short date range. A read-only SHA-256 input scan checks the contents of every included review, relevant card state, current preset/configuration and deck assignments, FSRS enablement, Anki engine version, calculation version, scoring thresholds and day/timezone inputs. This detects older synced inserts, edits and deletions without relying on the latest review ID. Deleted-card logs are scanned fully when the common policy permits inclusion; otherwise only their reported exclusion count affects the report. Anki's native UTC day-cutoff phase is included because DST can affect historical reconstruction. Future-dated histories are also rechecked as Anki's cutoff advances. Note text, deck names and sync counters do not invalidate the analysis. A display-window-only preference change avoids FSRS replay.

The cache stores the latest complete analysis and its fingerprint. Any changed calculation input rebuilds the full history on a fresh snapshot; this version does not incrementally replay only changed cards. Five-minute checks that find the day's export complete create no snapshot. A due analysis or manual Update takes one copy for an isolated freshness scan, then reuses unchanged scores without another copy, FSRS replay or rewriting daily score rows. Current-parameter changes always invalidate the cached analysis.

Native reconstruction batches incidental cache writes, placeholder insertion and episode truncation into one transaction on the disposable collection copy. This avoids a separate durable disk flush for every card. Cancellation or failure rolls back the transaction. When all presets agree, native decay is obtained once and reused within that immutable snapshot. Method v4 introduced deleted histories; method v5 corrects formation uncertainty and effective-day support. Database/CSV schemas are unchanged. Upgrading triggers a freshness recalculation even if today's old CSV was already complete, while CSV publication retries remain independent. See [performance testing](docs/PERFORMANCE.md) for the private-history benchmark and reproduction steps.

## CSV

On return to Personal insights, **Since your last visit** shows added/backfilled history, formation follow-ups on previously studied days, newly eligible formation scores and changes in uncertainty. The first displayed visit establishes a baseline. Refreshes in the same window keep comparing with the previous visit; background calculations and exports do not consume it. Changed analysis/FSRS settings or time-window selections are flagged before comparing ranges. A small provider-specific snapshot stays in the profile-local `personal-insights-visits.json`; it is excluded from research uploads. Personal summary exports include the visit comparisons. See [INSIGHTS.md](docs/INSIGHTS.md) for the exact lifecycle and interpretation.

Version 0.13 additionally appends `{provider}_nap_{before|after}_{light_sleep|deep_sleep|rem_sleep}_seconds` for Oura and Google Health. Each stage is summed only when every recorded nap in that window supplies it; missing stages remain blank and reported zeros remain zero. Existing sessions are reused without another provider backfill or FSRS replay. Personal insights includes each phase with uncertainty. Sharing notice v5 and research package v4 disclose the new phase measurements while preserving original consent timing.

One row per studied Anki day or saved Oura/Google Health day, chronological order, ISO `date`, UTF-8 with BOM for Excel. Numeric values use a decimal point. Missing scores are empty, never zero. `score_pp` and uncertainty bounds use percentage points; predicted/recalled values and coverage use percent. Counts distinguish evaluated (`count`), total studied (`cohort_count`), pending, excluded, and distinct outcome days. Existing recall and overall `formation_*` columns keep their names and order. New `formation_short_term_*` and `formation_long_term_*` columns, plus `formation_short_term_max_days` (30), are appended. The appended `formation_completeness` column describes the overall cohort's follow-up availability. Oura columns follow: total/light/deep/REM/awake seconds, sleep-period count and status, sleep algorithm versions, steps, assigned-day basis, import time and data version. Google fields are appended after all previous fields: total/light/deep/REM/awake/restless seconds, period count/status, sleep types, steps, day basis, sync time and data version. Metadata records analysis generation time in UTC, Anki version, rollover, timezone, thresholds, and a SHA-256 fingerprint of the current deck/configuration settings. Save the exported file alongside other QS data when you need to preserve a particular analysis; recalculation can revise history.

Version 0.12 appends separate main-sleep and nap columns after the existing fields. Each provider has main-sleep durations/status before and after studying; recorded-nap indicators, counts, durations, proximity gaps and status; between-review nap count; and a matching version. Sleep durations and gaps use seconds. These fields use the Anki study day and actual session timing, while the original provider-date totals remain unchanged. Exact sleep/review instants and session identifiers stay local. Google health storage upgrades to schema 3, provider session history backfills once, and native Anki calculations refresh once to save daily first/last review anchors. Sharing notice v4 and research package v3 disclose these additional derived measurements and require a new choice without changing original consent timing.

Version 0.3 upgrades the separate results database from schema 1 to schema 2 in a transaction, preserving preferences and daily update state. Older saved reports remain readable; their subgroup values show as **not calculated** until the next background recalculation. The daily updater requests a fresh report after the upgrade even if the previous version already ran that day. A failed CSV write does not mark the new schema's daily run successful.

## Back up, disable or uninstall

Before testing an upgrade, close Anki and copy the whole Anki profile folder to
a backup location, including `quantified-self-memory/`, and back up CSVs saved
elsewhere. Anki's collection backups do not back up every add-on data file.
That profile-local folder includes results, health measurements, sharing history
and settings; keep it private. Restoring OS-protected credentials may require
the original OS account or reconnecting later.

To pause automatic CSV publication, open **Quantified Cognition Daily Settings…** and
clear the automatic-update checkbox. To disable the add-on, open **Tools →
Add-ons**, select **Quantified Cognition**, choose **Toggle Enabled**, and
restart Anki. To uninstall it, use **Delete** in the same dialog and restart.
Disabling or uninstalling does not delete your profile-local data, exported CSVs
or any previously submitted research data. Keep the backup before removing
local files. Online features remain disabled in the current beta.

Report reproducible problems using the desktop beta issue template. Include
versions and sanitized errors, rather than your collection, CSV or health data.

## Development

Automated verification runs directly on [Silentworker](docs/SILENTWORKER.md).
This project does not use GitHub Actions. New commits on `main` run the test
suites, statistical regression gates, lint and package checks in an isolated
executor; unchanged commits reuse their recorded result.

For release preparation, use the [beta checklist](docs/BETA_CHECKLIST.md).
The [research design](docs/RESEARCH_DESIGN.md),
[participant information](docs/RESEARCH_INFORMATION.md) and
[privacy policy](docs/PRIVACY.md) define the intended research programme.
The [research operating plan](docs/RESEARCH_OPERATIONS.md) separates those
commitments from the enrollment, retention and provider controls still needed
before activation. Version 0.18.2 packages these documents; it does not open
research enrollment or change the current sharing notice.
The hosted service has [request/storage quotas and private monitoring](docs/BACKEND_OPERATIONS.md);
Oura, Google Health and research uploads remain disabled.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check addon tests tools
.\.venv\Scripts\python.exe tools/build_addon.py
```

Concurrency tests answer a real card while fingerprinting, FSRS calculation, result saving and CSV writing are each paused, and verify file-only automatic retries during review, cancellation of ordinary jobs and failed-copy cleanup. Subprocess tests immediately close after the final native card answer, stop Qt and return from the main thread while export is still pending, including with an older calculation running. They also exercise interrupted-handoff recovery at the next profile opening, persistent capture ordering and file-only export retries. Other tests use synthetic collections, including SM-2-era history, same-day relearning, next-day pairing, resets, previews, current-parameter changes, CSV output, DST boundaries, and preservation of the live collection. Runtime integration tests run against the real Anki Rust backend. The project is licensed under AGPL-3.0-or-later.

API references: [Anki statistics](https://docs.ankiweb.net/manual/stats), [native historical memory states](https://github.com/ankitects/anki/blob/main/rslib/src/stats/card.rs), [native retrievability](https://github.com/ankitects/anki/blob/main/rslib/src/storage/sqlite.rs), [add-on background operations](https://docs.ankiweb.net/addons/background-ops).
