# Oura approval and release preparation

Checked against Oura's official documentation on **23 October 2026**. This is a
preparation checklist, not an approval application. No application has been
submitted and no real health data used in tests. The backend is deployed with
Oura access and research uploads disabled; see [DEPLOYED.md](DEPLOYED.md).

## What is already implemented

The desktop add-on uses authorization code OAuth with PKCE S256 and a temporary
127.0.0.1 callback protected by a random path and state. A Supabase Edge Function
provides one fixed HTTPS redirect, code exchange, refresh and revocation. The
Oura client secret exists only in server environment variables. The desktop
stores rotating tokens in the OS credential store. Sleep and activity requests
go directly to Oura, using only `daily` scope and minimal field projections.
Background sync validates every page of both datasets before committing and
reconciles successful windows. Private research storage has a separate explicit
confirmation, precise aggregate allowlist, consent-timing metadata, receipts,
idempotency, withdrawal and deletion. Personal use requires no research account.

The independent controls `OURA_ENABLED=false` and `RESEARCH_ENABLED=false` keep
online features closed until their operator enables them. An empty desktop
service URL leaves local Anki calculations/CSV available. These switches are
deployment controls; they do not imply Oura approval by themselves.

## Prepare before asking Oura

- Fill in and publish [PRIVACY.md](PRIVACY.md): operator/contact, study purpose,
  region, retention, permitted recipients, international transfers, backup/log
  retention, deletion process and study/ethics information as applicable. Do not
  publish placeholders as an operational policy.
- Choose the app's public name, project/site URL, icon and support address.
  Describe the sleep/activity–memory research purpose accurately; do not imply
  endorsement or put Oura's marks in the app name without permission.
- Register the Oura developer app and exact HTTPS callback
  `https://PROJECT.supabase.co/functions/v1/qs-service/oauth/callback` (or the
  equivalent fixed domain actually deployed). Store the client secret on the
  backend; never in an add-on package or repository.
- Deploy a dedicated Supabase project using [DEPLOYMENT.md](DEPLOYMENT.md).
  Verify its region, private-table permissions, logs, abuse controls, backups
  and monitoring. Publish the same recipient/policy information the app shows.
- Pilot using the developer app's permitted users and real consenting test
  accounts. Verify browser return, renewal, sleep/step accuracy, corrections,
  revocation, membership errors, disconnection, CSV, upload and deletion. The
  automated tests use synthetic records and cannot replace this provider test.
- Prepare a short screen recording and data-flow description, including the
  full-package upload preview and visible exclusions. Provide the source URL,
  scope justification (`daily` only), policy URL, callback and support contact.

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

The operator must supply Oura credentials and
published policy details; perform the live pilot; obtain Oura's written
permissions and user-limit approval; then enable the appropriate production
flags. The project and service URL are recorded in `supabase/deployment.json`;
Oura credentials, final policy details and approvals are still required.
The repository is intended to make that pilot and application concrete,
without requiring approval before the software can be developed and tested.
