# Oura approval and release preparation

Checked against Oura's official documentation on **5 November 2026**, including
the V2 OpenAPI 1.43 workout contract. The operator has registered the developer
application with Daily and Workout selected and saved both credential names in
server secrets. A consented developer-account pilot has now verified token
exchange, refresh and revocation without exposing the client secret in logs.
Oura is the sole health integration for the first release. The developer-limit
beta is active. The request for wider access and optional research uploads was
resubmitted on 6 November 2026. Research uploads are closed.

The **5 November developer pilot** imported paginated sleep, daily activity and
workouts directly into a disposable local Anki profile, checked its encrypted
cache and aggregate CSV, rotated and persisted the refresh token, and completed
an incremental sync. Disconnect removed the local Oura measurements; the revoked
token was rejected. Oura was enabled only for the bounded test and restored to
disabled afterward. Google Health and research uploads stayed disabled throughout;
no enrollment or research submission was created. Saved evidence contains no
health records, measurements, dates, identifiers or record counts. See
[OURA_PILOT_RESULT.json](OURA_PILOT_RESULT.json).

## What is already implemented

The desktop add-on uses authorization code OAuth with PKCE S256 and a temporary
127.0.0.1 callback protected by a random path and state. A Supabase Edge Function
provides one fixed HTTPS redirect, code exchange, refresh and revocation. The
Oura client secret exists only in server environment variables. The desktop
stores rotating tokens in the OS credential store. Sleep and activity requests
go directly to Oura, using `daily` and `workout` scopes and minimal field projections.
Workout requests retain only hashed ID, assigned day, start/end, intensity and
categorical recording source; no labels, activity names, calories, distance or
heart rate. Background sync validates every page before committing and
reconciles successful windows. Private research storage has a separate explicit
confirmation, precise aggregate allowlist, consent-timing metadata, receipts,
idempotency, withdrawal and deletion. Personal use requires no research account.

The independent controls `OURA_ENABLED=false` and `RESEARCH_ENABLED=false` keep
online features closed until their operator enables them. An empty desktop
service URL leaves local Anki calculations/CSV available. These switches are
deployment controls; they do not imply Oura approval by themselves.

## Prepare before asking Oura

- Use the prepared [research design](RESEARCH_DESIGN.md),
  [participant information](RESEARCH_INFORMATION.md) and
  [operating plan](RESEARCH_OPERATIONS.md). Their research scope allows new
  questions within learning/recall, sleep/activity and measurement validation;
  their controlled-collaboration and retention commitments still need accepted
  permissions and implemented controls before activation.
- Keep the published [privacy policy](PRIVACY.md), Terms and participant
  information consistent with the registered app and actual fields.
- Choose the app's public name, project/site URL, icon and support address.
  Describe the sleep/activity–memory research purpose accurately; do not imply
  endorsement or put Oura's marks in the app name without permission.
- Registered callback:
  `https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/oauth/callback`.
  Keep the client secret on the backend, never in an add-on package or repository.
- Deploy a dedicated Supabase project using [DEPLOYMENT.md](DEPLOYMENT.md).
  Verify its region, private-table permissions, logs, abuse controls, backups
  and monitoring. Publish the same recipient/policy information the app shows.
- Pilot using the developer app's permitted users and real consenting test
  accounts. Verify browser return, renewal, sleep/step/workout accuracy, corrections,
  revocation, membership errors, disconnection, CSV, upload and deletion. The
  automated tests use synthetic records and cannot replace this provider test.
- Prepare a short screen recording and data-flow description, including the
  full-package upload preview and visible exclusions. Provide the source URL,
  scope justification (`daily` for sleep/steps; `workout` for exercise timing,
  duration and intensity), policy URL, callback and support contact. Check partial
  grants: Workout denial must leave sleep/steps usable and workout values unknown.

## Ask Oura to confirm the intended data uses explicitly

New developer applications have a **10-user limit** until approved for wider
use. A hosted service does not remove that limit. The current agreement also
places conditions on caching/storage and on disclosure of Oura-derived data.
Request written confirmation for local historical storage, fixed CSV exports
used by other local QS tools, optional research packages stored on Supabase,
research retention, who may access/extract them and any intended publication or
onward sharing. Ask whether the planned research flow is treated as an
Aggregator/End Customer arrangement and, if so, which approvals are needed.
User consent and open-source licensing do not replace these permissions.

The agreement requires stopping access/processing and deleting relevant data
within its specified revocation/deletion timeframe (including the stated
72-hour obligation). Before production, resolve how account-side revocation,
offline desktops, stored research copies and backups will meet that obligation.
The add-on detects API authorization failures, stops automatic retries and
provides disconnect/delete and research-deletion controls. It currently polls
while Anki is open; it has **no Oura webhook receiver** and cannot observe a
revocation while every desktop is offline. Ask Oura which notification and
retention arrangements are accepted for this architecture. Document and
implement any required webhook/operator process after their requirements are
known; do not describe polling as a guaranteed continuous revocation monitor.

Do not feed participant Oura data to AI/LLM tools or train models on it without
the permissions required by Oura's agreement. Keep development/demo fixtures
synthetic. Confirm any intended statistical/modeling uses in the application.

## Provider constraints to describe accurately

The documented client-only implicit flow has no refresh token and currently
expires after about 30 days. The implemented hosted code flow supports renewal,
but refresh tokens are single-use and replacements must be saved immediately.
Expired/revoked tokens, denied scopes and Gen3-or-later membership restrictions
still apply. Handle 429 and provider headers rather than assuming an old fixed
application-wide rate allowance. Initial history plus incremental refresh is
used; a weekly reconciliation catches older edits. For larger usage, discuss
webhooks with Oura to reduce polling. Oura reserves the ability to change API
terms and charges; neither hosting choice bypasses the agreement.

## Official references

[OAuth and PKCE](https://cloud.ouraring.com/docs/authentication),
[V2 API and application limits](https://cloud.ouraring.com/v2/docs),
[Oura API support and membership](https://support.ouraring.com/hc/en-us/articles/4415266939155-The-Oura-API),
[API agreement, effective 8 June 2026](https://cloud.ouraring.com/legal/api-agreement).

## Remaining external steps

The developer app, credentials, public policy and initial account pilot are
prepared. The operator must obtain Oura's written
permissions and user-limit approval; then enable the appropriate production
flags. The project and service URL are recorded in `supabase/deployment.json`.
Provider-side revocation, membership/permission failures and later scheduled
sync behavior still need representative permitted-account checks. Wider-use and
research approvals are still required.
The repository is intended to make that pilot and application concrete,
without requiring approval before the software can be developed and tested.
