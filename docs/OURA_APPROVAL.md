# Oura approval and release preparation

Checked against Oura's official documentation on **5 November 2026**, including
the V2 OpenAPI 1.43 workout contract. The operator has registered the developer
application with Daily and Workout selected and saved both credential names in
server secrets. A consented developer-account pilot has now verified token
exchange, refresh and revocation without exposing the client secret in logs.
Oura is the sole health integration for the first release. On **6 November 2026
at 19:47 UTC**, Ken from Oura's Enterprise Support Team confirmed application
approval and removal of the ten-user limit in ticket **#8481236**. This followed
the combined wider-access/research request in **#8471927**.
[Approval record](OURA_APPLICATION_APPROVAL.json) ·
[Submitted request](OURA_REVIEW_SUBMISSION.md)

Status updated **8 November 2026**: personal Oura connections are enabled.
Research enrollment and uploads remain disabled. API use remains subject to
the API Agreement and branding conditions; this approval is not an endorsement
or a general trademark license.

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
confirmation, daily and pseudonymous-review allowlists, consent-timing metadata, receipts,
idempotency, withdrawal and deletion. Personal use requires no research account.

The independent controls currently use `OURA_ENABLED=true` and
`RESEARCH_ENABLED=false`: Oura connections are enabled while research stays
closed. An empty desktop
service URL leaves local Anki calculations/CSV available. These switches are
deployment controls; they do not imply Oura approval by themselves.

## Application setup and release maintenance

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

## Submitted scope and continuing conditions

The ten-user limit has been lifted for this application. The submitted request
described local historical analysis and CSV exports, optional on-device-encrypted
Oura/Anki research packages stored in Frankfurt, withdrawal and deletion, and
collaborator access subject to Oura permission. Ken's reply approves the developer
application; it does not activate our research service or waive the API Agreement.
Any later onward-access arrangement must satisfy the agreement's conditions.

The agreement requires stopping access/processing and deleting relevant data
within its specified revocation/deletion timeframe (including the stated
72-hour obligation). Account-side revocation, offline desktops, stored research
copies and backups are covered by the independent controls below.
The research service checks contributor authorization independently of Anki,
using encrypted access tokens and short research-use leases. Confirmed loss of
access invokes complete-contribution deletion and copy/backup tracking. Expiry
and outages pause use. Oura's published webhooks do not list an OAuth revocation
event; checks are periodic, not immediate notifications. See
[implementation and operating limits](OURA_RESEARCH_REVOCATION.md).

Do not feed participant Oura data to AI/LLM tools or train models on it without
the permissions required by Oura's agreement. Keep development/demo fixtures
synthetic. Keep statistical work within the submitted purpose and agreement.

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

## Remaining launch work

The API application is approved, credentials are configured, the policy is public
and the initial account pilot is complete. Record any subsequent provider
conditions with the approval evidence. The project and service URL are recorded
in `supabase/deployment.json`.
Provider-side revocation, membership/permission failures and later scheduled
sync behavior remain in the launch checklist. Research data-handling work and
an explicit operator launch decision remain separate from API approval. Keep
research enrollment and uploads disabled while those items are completed.
