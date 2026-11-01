# JollyJoyJourney — Quantified Cognition privacy policy draft

**Deployment draft, 30 October 2026.** Replace every bracketed field and publish
this policy before enabling online research uploads or submitting the provider apps
for approval. This file describes the implemented flow; it is not a claim that
research arrangements, legal basis or provider approval have already been established.

## Who operates the service

JollyJoyJourney operates Quantified Cognition and the associated research service.
Its registered business address is:

JollyJoyJourney  
Schweidnitzer Straße 6  
10709 Berlin  
Germany

Contact [contact@jollyjoyjourney.com](mailto:contact@jollyjoyjourney.com) about
participation, privacy, access or deletion.
The public study information is at [study URL]. The published policy is at
[policy URL], effective [date]. The study concerns associations between sleep,
activity and memory; an association does not establish causation.

The contact address forwards messages through Cloudflare Email Routing to a
Gmail inbox used by JollyJoyJourney. Cloudflare handles email delivery; Google
stores the messages in that inbox. Your sender address, message, attachments
and delivery metadata are processed to deliver and answer your request. Contact
correspondence is separate from the research dataset. See the
[Cloudflare privacy policy](https://www.cloudflare.com/privacypolicy/) and
[Google privacy policy](https://policies.google.com/privacy). [Specify
contact-message retention and the applicable arrangements for these providers.]

## Personal use and health connections

Your Anki calculations run on your computer. The add-on keeps a separate local
results database, health database and sharing-choice audit in your Anki profile.
It does not change scheduling. Temporary collection snapshots used for native
FSRS calculations can contain card text; they remain local and are never part
of a research upload. CSV files are written only to locations you choose.
If review logs remain for deleted cards, they can contribute to the same daily
aggregates when all current presets share the relevant FSRS reconstruction
settings. Blank placeholder cards/notes exist only in the disposable local copy;
lost question/answer content and deck assignments are not recovered. Conflicting
presets leave those histories excluded. Included/skipped counts and the reason
are saved locally; no new individual review or card fields enter research uploads.
The results database also keeps each study day's first and last review instant
locally to match preceding/following sleep. These exact instants are excluded
from CSVs and research uploads; individual review-pair records are not stored.

Connecting Oura and submitting research data are separate actions. You can use
Oura sync without submitting research data. The add-on requests Oura's `daily`
permission and fetches sleep stages/durations and daily steps directly from
Oura. It requests only the fields needed for these measurements. It retains
sleep dates, period times, type and algorithm version locally to aggregate naps,
detect overlaps and apply corrections; raw sleep-period identifiers are hashed
locally. It does not request name, email, heart rate, location or workouts.

Our OAuth service, hosted on Supabase in Frankfurt (`eu-central-1`), exchanges authorization
codes and renews Oura tokens. Codes and tokens pass through this service but
the application does not store them in its cloud database or log them. The Oura
application secret remains on the server. Access and refresh tokens are saved
in your OS-protected credential store (Windows DPAPI, macOS Keychain or Linux
Secret Service). No token is included in your CSV or research package. Your Oura
password is entered on Oura's website, not in Anki or our service.

Sync runs in the background while Anki is open: an initial history fetch,
normally hourly checks of the recent 30 days, and a weekly history refresh for
older corrections/deletions. Provider rate limits and failed connections delay
retries. Oura's assigned date is preserved rather than shifted to Anki's day
boundary. A failed or incomplete fetch does not replace saved measurements.

Google Health can also be connected separately for personal sleep/steps use. The
add-on asks for Google Health sleep read access and activity/fitness read access;
the latter covers more than steps, but only steps are fetched. If one permission
is granted, only that dataset is imported. Users must first link Google Health
to their account in its mobile app. Only cloud-available data can be read.
Google sleep/step measurements travel directly to the desktop. Google tokens
pass through the Supabase OAuth service without application database storage
and are stored in the OS vault. The Google client secret stays server-side.
Google sign-in uses Google's external website, not an embedded Anki browser.

Google daily aggregates are stored locally: total/light/deep/REM/awake/restless
sleep seconds, period count, sleep type/status, steps, day convention, sync time
and data version. Selected Google sleep sessions retain start/end UTC instants,
main-sleep/nap flags, processing status, durations and hashed record identifiers
locally to match sleep to studying. Raw record/account/device IDs and individual
step samples are not saved. Sleep summaries have whole-minute precision; classic
sleep does not supply light/deep/REM values. Google uses the local sleep-end
date and civil step date, which can differ from Anki's day boundary. The initial
range defaults to a year and can be extended backwards. Background refresh and
atomic cache/CSV retry behavior match Oura. Recent days may be incomplete.
Google and Oura data stay in separate columns to avoid duplicate counting.

Before enabling the Google research use, complete Google's research intake,
review-board approval/waiver and approved eligibility/signed-consent process.
The implemented local sharing choice and package confirmation alone are not that
study enrollment process; see [GOOGLE_HEALTH.md](GOOGLE_HEALTH.md).

## Choosing to contribute

Personal insights computes historical correlations locally from saved daily
measurements. The local `personal-insights.json` cache holds coefficients,
uncertainty ranges, counts and method information, not individual reviews or
raw health records. Up to eight input/settings variants are retained and
provider disconnect/account replacement removes the cache. A separate personal
summary CSV is saved only when requested; exported copies are user-managed.
Personal insights also computes matched, fixed-total phase substitution models
locally. Its cache stores their summary effects, ranges and readiness information,
and its overview reports history coverage and formation follow-up counts. No
new health fields are collected for these models. Phase summaries are included
in the personal summary export, not in research uploads.
The separate profile-local `personal-insights-visits.json` retains the latest
displayed-visit snapshot for each provider: viewing time, date-level study and
formation counts/readiness, health-day hashes, analysis settings and summary
estimates/uncertainty. It does not copy raw health measurements, exact sleep or
review times, card identifiers, review pairs or credentials. It supports added/
backfilled history, formation follow-up and uncertainty comparisons on return.
Each provider's snapshot is removed on disconnect/account replacement, including
protection against queued writes restoring it. Personal summary exports include
the selected visit comparisons; fixed daily CSVs and research uploads do not.
The insights screen and nonempty personal exports are covered by the same
first-results-exposure audit. These summaries are not added to research uploads
automatically; the research package remains the disclosed daily measurements.

Before the first opportunity to see results, the add-on asks Yes, Not now or No.
All choices allow personal use. Answering Yes does not send measurements.
Whenever you choose **Review and submit data**, it prepares a fixed package and
shows its date range, day count, recipient and this policy. Nothing is submitted
until you choose **Send this package**. There are no automatic research uploads.
Retrying a failed submission sends the same package ID and is deduplicated.

The complete research package contains daily recall and overall, short-term
(up to 30 days) and long-term memory formation scores; predicted and observed
recall, evaluated/cohort/pending/excluded counts, outcome-day counts, uncertainty,
coverage, comparison labels and formation completeness. When available, it also
contains Oura total/light/deep/REM/awake seconds, sleep-period counts and data
status, daily steps, algorithm versions, source date basis, sync times and data
version. The package also includes the Google daily aggregates described above,
including restless time, classic/stages type and availability. It includes dates,
separate main-sleep durations before/after studying, recorded-nap indicators,
nap counts and total/light/deep/REM durations, gaps to studying, between-review nap counts,
matching status and matching-method version when available. It includes
Anki version, FSRS parameter/settings fingerprint,
timezone, Anki day boundary, calculation settings/method and generation time.

A random participant ID links contributions. The package includes current and
initial sharing choices, notice version/hash, prompt/consent/exposure times and
whether consent preceded the first recorded results exposure. These records
help researchers examine selection effects. Unknown prior exposure stays
unknown; the audit cannot prove that someone read a result or never saw their
sleep information elsewhere. We do not collect the full local event log.

**Excluded from the package:** card questions and answers; card/deck names and
identifiers; individual reviews and review pairs; raw Oura sleep records or IDs;
exact sleep or review times; raw Google records; heart rate; location; name; email;
Oura/Google account or device IDs;
passwords; access/refresh tokens. A separate random secret authorizes uploads
and deletion; the public participant ID is never used as a password.

These are pseudonymous health-related measurements, not guaranteed anonymous
data. Dates, behavior patterns and timezone can still be identifying. We use
the data for [specific research purposes, recipients and publication plan].
There are no leaderboards. [State whether any onward sharing will occur,
its recipients and safeguards; obtain the necessary Oura permission first.]

## Cloud storage, access and retention

Confirmed packages are stored as JSON in private PostgreSQL tables on Supabase
in Frankfurt (`eu-central-1`). Neither public clients nor other participants can read them. Access
is restricted to [named research roles/authorized recipients]. HTTPS protects
transit; OS storage protects local credentials. [Describe deployed cloud access
controls, encryption/backup configuration and administrator access.] Supabase
and Oura process requests under their own policies/contracts. [Describe any
international transfers and safeguards applicable to this deployment.]

The application does not deliberately log tokens, package bodies or measurement
values. Hosting/gateway infrastructure can retain IP addresses, request times
and technical request metadata; OAuth callback URLs can contain short-lived
codes. [Specify actual log retention and URL redaction/access settings.] No user
health data is sent to AI services by this application.

For service quotas, the application stores daily rotating, server-keyed hashes
of OAuth credentials/flows or the research capability identity, with request
counts and UTC window boundaries. Raw credentials and participant IDs are not
stored in those quota records. Windows older than 48 hours are removed by a
five-minute maintenance job. Private operational metrics retain minute-level
route/status counts and response durations for seven days. Application function
logs contain random request IDs, fixed route labels, status and duration; their
platform retention must be set by the operator. A small set of current alert
states and health-check/maintenance timestamps supports service operation.
These operational records are separate from research packages and are not
included in personal CSVs or the research dataset. They do not contain sleep,
steps, Anki scores or individual reviews. This automatic housekeeping does not
set or replace the research-package retention period below.

Local measurements remain until you delete them or the Anki profile. Research
packages are retained for [specific justified duration or study end date].
[Set a database retention job consistent with the published duration.] Earlier
submissions are kept as immutable snapshots so corrected/recalculated results
can be distinguished; analysis must select the appropriate snapshot per day
rather than count repeated uploads as independent observations.

## Withdrawal, disconnecting and deletion

Changing Research sharing to Not now or No stops new submissions, removes any
unsent package and queues deletion of all previous uploads from this profile.
**Delete uploaded data** requests the same cloud deletion while leaving local
measurements intact. When the service acknowledges deletion, all active research
packages for that profile's upload identity are removed. A random UUID tombstone
with a deletion timestamp remains solely to reject stale requests; it contains
no measurements or credential hash. A later contribution uses a new upload
identity. The local historical consent timing remains accurate.

If offline, the add-on retains an encrypted pending deletion request, blocks new
uploads and retries every five minutes while Anki is open. It cannot deliver an
offline request after the computer or Anki is closed. You can contact
[contact@jollyjoyjourney.com](mailto:contact@jollyjoyjourney.com) if you cannot
reopen the profile. Keep your local profile until deletion is acknowledged; losing its credential may prevent automated deletion. [Provide
an operator process for securely verifying and handling exceptional requests.]

Disconnect and delete on the Oura screen removes local Oura measurements, stops
sync and attempts to revoke the Oura token. If unreachable, revoke the app in
your Oura account too. It refreshes an enabled fixed CSV from saved Anki results
when available. Previously saved/exported copies and previously submitted
research packages are separate: delete the files or use Delete uploaded data.
Research withdrawal does not disconnect a personal Oura connection.

The Google Health screen has the same provider-specific disconnect/delete
control: it deletes Google's local aggregates, stops sync, refreshes the fixed
CSV and attempts Google token revocation. If offline, remove the app's access
in the Google account too. It preserves Oura measurements. Research deletion
and copies of previously exported CSV files are managed separately. Provider
revocation/deletion notifications, offline clients and backups must be resolved
with Google before launch; the current implementation polls while Anki is open.

Supabase backups and independently saved research copies require a separate
retention/deletion process. [State the configured maximum backup lifetime,
restore-time deletion replay procedure, and how research extracts are deleted.]
Do not promise that deleting live rows immediately removes every backup or a
previously published aggregate. [Describe any justified limits and user rights
applicable to the actual study.] The operator must meet the applicable Oura
deletion obligations and resolve backup/revocation handling before launch.

## Questions and changes

Contact [contact@jollyjoyjourney.com](mailto:contact@jollyjoyjourney.com) for
access, correction, withdrawal, deletion or complaints.
[Explain applicable rights, supervisory authority, legal basis for health data,
age/eligibility requirements, and ethics/participant information for this study.]
A changed sharing notice prompts a new choice before further submissions while
preserving earlier consent timing. Policy and software versions are published
at [project URL].
