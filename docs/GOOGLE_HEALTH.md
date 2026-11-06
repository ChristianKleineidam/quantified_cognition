# Google Health integration and launch preparation

Checked against Google's official documentation on **23 October 2026**. The
implementation targets **Google Health API v4**, not Google Fit or the retired
Fitbit Web API. It has been tested with synthetic responses. No Google account
has been connected, and no approval application has been submitted.

## Personal use

Open **Health data…** from the Quantified Cognition memory report, select
**Google Health**, choose **Import since**, then **Connect Google Health…**. First sign in to the Google
Health mobile app and link the Google account there, as Google requires. Only
data available to that account through the cloud API can be imported. This is
not a direct desktop reader for every Android Health Connect record.

The initial range defaults to the past 365 days; choose an earlier date to fetch
older history. **Sync now** can extend the saved range backwards. Changing the
date forwards does not delete previously imported days or shorten their weekly
reconciliation range. After connection, hourly sync checks recent 30 days and a
weekly sync reconciles the chosen full history while Anki is open. Historical
requests are split into at most 90-day chunks. Downloads, OS credential access,
parsing, SQLite updates and CSV publication run on file workers, independent of
Anki's collection queue. Normal reviewing remains available. Closing Anki can
cancel an import; the next run retries it without publishing a partial window.

The application asks for only these read permissions:

```
https://www.googleapis.com/auth/googlehealth.sleep.readonly
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
```

The activity permission is broader than daily steps; only steps are requested.
There are no profile, email, location, heart-rate, write or other health scopes.
If Google grants only one requested permission, only that dataset is fetched;
the other values remain unknown. Reconnect to change accounts or permissions.

## Exact measurement contract

Sleep uses `users/me/dataTypes/sleep/dataPoints:reconcile`, all sources, with
minimal response fields. The API supports filtering on interval **start**;
queries include a buffer for overnight sleep and travel, then validated sessions
are selected by their **local end/wake date**, using the session's end UTC
offset. Google supplies summary durations in whole minutes; exports multiply
them by 60 but do not imply second-level precision. Provider summary totals
already account for short awakenings. Raw stage intervals and short-awakening
intervals are never added together. Classic sleep can supply asleep, awake and
restless time but cannot supply light/deep/REM measurements. Processing and
overlapping periods are marked and their daily durations left blank. Missing
stages remain blank. Non-overlapping sessions/naps on the same wake date are
aggregated. `durations available` describes supplied values, not complete wear.

Daily steps use `users/me/dataTypes/steps/dataPoints:dailyRollUp`, one-day civil
windows, all sources. Google reconciles multiple devices and excludes periods
identified as not worn. Full-day endpoints accept next midnight and the
documented same-day 23:59:59 representation. A missing steps payload is unknown;
an explicit zero steps aggregate remains zero. Recent days may contain partial
activity and provider values may change. Provider corrections and deletions
replace the successful queried window; failures leave the previous cache intact.

Daily aggregates and selected sleep sessions are stored in the separate
`quantified-self-memory/health.sqlite3`. The database and derived insight caches
are encrypted on disk using an OS-protected profile key; SQLite runs in memory.
Legacy caches migrate on access. User-selected CSV exports remain readable.
See [encryption and recovery](ENCRYPTION.md). Sessions retain UTC start/end instants,
main-sleep/nap flags, processing status, durations and hashed record identifiers.
Raw record/account/device IDs and individual step samples are not retained.
Exact session timestamps remain local and are excluded from CSVs and research
uploads. Sleep permission triggers one full history backfill after upgrading;
steps-only grants keep their normal sync interval. Original Google daily CSV fields:

```
google_health_total_sleep_seconds
google_health_light_sleep_seconds
google_health_deep_sleep_seconds
google_health_rem_sleep_seconds
google_health_awake_seconds
google_health_restless_seconds
google_health_sleep_period_count
google_health_sleep_status
google_health_sleep_types
google_health_steps
google_health_day_basis
google_health_synced_at_utc
google_health_data_version
```

The day-basis value is `sleep_end_local_date_steps_civil_date`. The combined CSV
joins ISO date labels with Anki's configured study day. Those day conventions
are not identical; do not infer that a row's sleep occurred during the Anki day
or that a same-date association measures a particular causal lag. Oura and
Google columns stay separate because their underlying data can overlap. They
are never summed. Health-only dates have blank Anki scores. Changed health data
refreshes an enabled fixed CSV using cached Anki results, without FSRS replay.
Identical syncs leave the health revision and CSV unchanged. Export failures
retry separately. [Personal insights](INSIGHTS.md) separates main sleep and naps
using actual session timestamps and daily first/last review anchors. It shows
nap occurrence, duration and proximity to studying alongside all four memory
metrics, with uncertainty and insufficient-data labels. Additional derived
columns are appended to daily CSVs; the original provider-date totals remain.

## OAuth and deployment

The existing Supabase function brokers OAuth tokens only; sleep and steps travel
directly from Google to the desktop. The broker uses signed, ten-minute,
provider-specific state and S256 PKCE, then returns to a random loopback path
on 127.0.0.1. It requests offline access and consent for a refresh token. Tokens
are stored in the OS vault, never settings, CSV or the research database. Google
refresh responses can omit the refresh token/scope: previous values are kept.
Any replacement is saved before cancellation or further API requests. No token
or health payload is deliberately logged. Infrastructure logs still need review.

`GOOGLE_HEALTH_ENABLED=false` is independent of `OURA_ENABLED=false` and
`RESEARCH_ENABLED=false`. All remain disabled in this deployment. To prepare
a permitted pilot, create a separate development Google Cloud project, enable
Google Health API and create the **Web Server** OAuth client with this exact
authorized redirect (not the documentation's example google.com redirect):

```
https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/google/oauth/callback
```

Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` only in server secrets. Add the
two scopes to Google's consent configuration and add permitted test users.
Google currently documents an initial 100-user cap and a third-party security
review to exceed it. There is no separate Health API sandbox; use separate
development and production Cloud projects. Complete the required OAuth
verification, branding, public app/privacy information and provider review.
Testing-mode token expiry and revocation can require renewed authorization.

Before enabling, pilot actual sign-in/PKCE/browser return, granted scopes,
refresh, representative classic/staged sleep and step rollups, travel/DST,
missing/zero days, corrections/deletions, rate-limit backoff, offline retry,
disconnection and CSV. Synthetic tests cannot establish live API eligibility,
permission or accuracy. **Disconnect and delete…** removes only this provider's
local data, refreshes the fixed CSV and attempts Google revocation. If offline,
remove the app's access in the Google account too. Existing exported files and
research submissions have separate deletion controls.

## Research-specific requirements before activation

The prepared [research design](RESEARCH_DESIGN.md),
[participant information](RESEARCH_INFORMATION.md) and
[operating plan](RESEARCH_OPERATIONS.md) supply the programme scope and intended
enrollment statement. The source now includes a separate signed-consent flow;
neither the materials nor that implementation establish study approval.

Google's Health Research Policy requires review-board approval or waiver,
research intake/attestation and an exact data-use rationale. Eligibility must be
confirmed before study consent and data-access permission. Required disclosures
include purpose/duration, risks/benefits, contact, safeguards, retention,
withdrawal/deletion and board-required documents; participants must be able to
save the information and sign/submit the required consent. Restrictions apply
to derived/aggregated data and onward transfers too. Resolve these with the
provider/review board before enabling the research service; the current local
sharing preference and explicit package confirmation are not a completed
approved study enrollment process. Confirm acceptance of the implemented
eligibility, information-copy and participant-ID consent flow and incorporate
any required changes once the requirements are known.

The v7 sharing notice and package schema v5 precisely include the daily Google
fields above and derived main-sleep/nap measurements, including reported nap
light/deep/REM durations, alongside Anki/Oura fields.
Exact session and review times remain excluded. Separate signed enrollment is
implemented in the source but remains closed; its signature method and information
must be accepted before activation. See [RESEARCH_CONTROLS.md](RESEARCH_CONTROLS.md).
Updated consent is required without
rewriting original pre-view timing. No measurements upload just by connecting,
syncing or opting in. Each upload still requires package review and Send.
Publish the completed [privacy policy](PRIVACY.md), determine research
retention/access/deletion and confirm permitted data uses first. Keep all flags
false until those steps and the live pilot are complete.

## Official references

[Cloud/OAuth setup](https://developers.google.com/health/setup),
[scopes](https://developers.google.com/health/scopes),
[reconcile](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/reconcile),
[supported filters](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/list),
[sleep resource](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints),
[daily rollup](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/dailyRollUp),
[endpoint examples](https://developers.google.com/health/endpoints),
[research policy](https://developers.google.com/health/policies/health-api-user-data-and-research-policy),
[developer checklist](https://developers.google.com/health/developer-checklist).
