# Backend deployment state

Latest hosted verification: **5 November 2026**. This is a deployed prelaunch backend;
Oura, Google Health and research uploads remain disabled.

**5 November live Oura developer pilot:** authorization/browser return,
paginated sleep/activity/workout imports, encrypted caching, persisted refresh
rotation, incremental sync, aggregate CSV and disconnect/revocation passed in a
disposable local Anki profile. The temporary export and local Oura measurements
were removed; the ordinary collection was not opened or modified. No research
enrollment or upload occurred. All online features were restored to disabled.
The same backend bundle is now function version **23** after the temporary
configuration changes; its code and research protocol were unchanged. This was
an integration-level live-account test, separate from the earlier synthetic
desktop UI walkthrough. See [OURA_PILOT_RESULT.json](OURA_PILOT_RESULT.json).

**5 November workout update:** version 0.21.0 adds Oura workout imports,
24-hour study-window summaries, historical associations and appended CSV fields.
The deployed OAuth request uses `daily workout`. Function version **21** and
migration `20261105134053_research_protocol_v9.sql` are verified (18 migrations
total). Notice v9 and the frozen v9 documents match the published pages; all
nine protocols remain unapproved, closed and without provider permissions.
The original before-first-results sharing audit is preserved.

All 17 public website files match the reviewed build. Four hosted code files
match exactly and the hosted JSON schema is structurally identical. The security
advisor has no findings; Oura, Google Health and research registration/uploads
return their disabled responses. Those pre-pilot checks used synthetic data only.
The Oura credential values were not exposed or changed.
Tests cover synthetic workout corrections/deletions, partial grants, encrypted
exports, unknown history before device use and background UI operation.
Ten years/7,300 synthetic workouts took 0.031 seconds to parse and 0.16 seconds
to match to studying; this excludes network and statistical estimation time.
See [WORKOUT_VALIDATION.json](WORKOUT_VALIDATION.json) for the test/deployment record.

**Earlier 5 November encryption deployment:** migrations
`20261105112324_client_encrypted_research.sql` and
`20261105113158_research_protocol_v8.sql` are applied (17 total). The live function
was version 17, with notice v8 and the matching frozen document bundle. All eight
protocols at that point were unapproved, closed and without provider permissions.

Version 0.20.0 encrypts local health/insight caches and pending previews using
OS-protected profile keys. Research measurements use client-side HPKE encryption;
the private recipient key stays on the operator's computer. Consent and minimal
operational metadata remain available for server enforcement. The new server
rejects plaintext uploads; scientific allowlist checks run before encryption
and after local decryption. The registered export releases encrypted packages
with consent provenance for local analysis. See [ENCRYPTION.md](ENCRYPTION.md).

Validation passed 413 Windows desktop tests, 42 backend tests and 15 native
PostgreSQL/recovery tests on Silentworker. A native Windows DPAPI cache reopened
in another process; an unchanged import preserved the ciphertext. Both protected
copies of the research private key were verified locally. The v0.20.0 add-on
archive passed identity, source, dependency-digest and licence-presence checks.

A separate protected test endpoint and temporary schemas verified a real hosted
encrypted upload, identical-receipt retry, local decryption of the stored
ciphertext, deletion and rejection of the old credential. The test used one
synthetic package. All temporary components were removed; the live data and
settings matched their pre-test snapshot. The security advisor reports no
findings. Evidence: [HOSTED_ENCRYPTION_RESULT.json](HOSTED_ENCRYPTION_RESULT.json).

The [Terms of Use](https://quantifiedcognition.jollyjoyjourney.com/terms/) and
updated privacy policy are published on the existing Pages site. All 17 public
files matched the 19-file build over HTTPS, with canonical document equality,
security headers and retired-manual redirects verified. Publication did not
enable any online feature.

**5 November synthetic hosted test:** a separate, access-key-protected Edge
Function used temporary private schemas, prefixed RPCs and NOLOGIN roles inside
this project. The four application modules were copied byte-for-byte. The test
wrapper redirected every RPC to the temporary namespace and enabled research
only for that endpoint, using a synthetic protocol with both providers disabled.
The six migrations containing upload/lifecycle logic were copied with namespace
substitutions; production protocol seeds, cron/extension changes and access to
production migration history were excluded.

The hosted HTTP-to-PostgreSQL flow passed: enrollment and a synthetic upload
returned 200; retrying the identical package returned the same receipt and kept
exactly one row, 6,441 active-storage bytes and one daily storage charge. Deletion
returned a completed receipt and left zero submission rows or active-storage
bytes. A later upload with the deleted credential returned 401. The daily usage
counter correctly remained one after deletion.

The test endpoint, both schemas, all nine public test RPCs, both temporary roles
and the test secret were removed. The retired endpoint returns 404. Before/after
snapshots match for live participant/submission/enrollment counts, storage
counters, protocol approvals, RPC definitions, cron configuration and migration
count. All three live feature flags remain false; the security advisor reports
no findings. The live function is version 13 after installing/removing the
temporary secret; its application bundle hash is unchanged. This was one small
functional roundtrip through shared hosted infrastructure. Evidence and exact
source hashes: [HOSTED_SMOKE_RESULT.json](HOSTED_SMOKE_RESULT.json).

**4 November project restoration:** the project was restored at the operator's
request and now reports `ACTIVE_HEALTHY`. A read-only database query succeeded,
and the live `/status` endpoint confirmed all three online features remain
disabled. At restoration, the service was version 9 with sharing notice v5 and
the `qs_governance` schema was absent. The restoration itself changed no function
code, schema, provider credentials or feature flags. The separately authorized
deployment below subsequently applied the prepared updates.

**4 November closed backend deployment:** all nine pending migrations were
applied in order, through `20261104191205_research_protocol_v7.sql`. Function
version 11 matches all five prepared source files. Hosted notice v7, its hash,
JollyJoyJourney and the published policy URL match the add-on. All three enable
flags remain false, and all seven protocol versions remain unapproved, closed
and without provider permissions.

**4 November maintenance fix:** migration `20261104203715_maintenance_lock_order.sql`
was subsequently applied, bringing the total to 14 migrations. It fixes a
reproduced deadlock between batch deadline deletion and participant withdrawal.
All 11 native PostgreSQL race tests and 42 existing backend tests pass. The
hosted function remains version 11; this database-only fix changes no feature
flag, protocol approval or provider permission. The installed maintenance
function retains `SECURITY INVOKER`. See [backend operations](BACKEND_OPERATIONS.md)
for reproduction, local timings and the remaining hosted-load checks.

**4 November operations update:** migration
`20261104211750_backend_operations_access.sql` is applied (15 migrations total).
It adds restricted monitor/backup roles, a separate research-maintenance
heartbeat, tracked recovery copies and audited operator withdrawal. All remain
invoker functions. Independent Silentworker monitoring, five-minute deletion
checks and encrypted daily recovery copies are installed. The initial live
archive passed local key/integrity verification; the project had no submitted
research packages, consent records or active participants. Synthetic native
rehearsals passed 15 tests. Administrator MFA was confirmed by the operator,
external database TLS enforcement is on, and the security advisor has no
findings. See [BACKEND_RECOVERY.md](BACKEND_RECOVERY.md) for boundaries and keys.
Named alert drills triggered the existing Home Assistant automation at 21:57:56
and 22:02:56 UTC; the normal Quantified Cognition monitor recovered afterward.
Other Silentworker health failures remain outside this project's scope.

**4 November website publication:** the public information site, privacy policy
and research information are now hosted on Cloudflare Pages at
[quantifiedcognition.jollyjoyjourney.com](https://quantifiedcognition.jollyjoyjourney.com/).
See [WEBSITE.md](WEBSITE.md). Publication did not resume Supabase, change hosted
settings or enable provider access, enrollment or uploads.

**30 October desktop-beta recheck:** the management API reported this project
`INACTIVE`. Public DNS and database checks were unavailable. It was not resumed
at that check and no server settings were changed. The full deployment
verification from that date is historical and superseded by the 4 November
deployments above. Repeat current closed-state checks before a separately
authorized online pilot.

| Setting | Value |
| --- | --- |
| Project | Quantified Cognition |
| Organization | Tempo |
| Project ref | `wfjlzecckihsgcvauqwt` |
| Region | Frankfurt, `eu-central-1` |
| Database | PostgreSQL 17.11 |
| Function | `qs-service`, deployed version 17 |
| Latest migration | `20261105113158_research_protocol_v8.sql` |
| Enrollment/lifecycle controls | Version 1; deployed, research closed |
| Operational controls | Version 1; shared request/storage quotas |
| Monitor | Active, every five minutes; private alerts |
| Independent monitor | Silentworker; existing Home Assistant notification channel |
| Recovery | Encrypted daily copies; independent deletion ledger; 7 days / 1 GiB |
| Oura access | **Disabled** (`OURA_ENABLED=false`) |
| Google Health access | **Disabled** (`GOOGLE_HEALTH_ENABLED=false`) |
| Research registration/uploads | **Disabled** (`RESEARCH_ENABLED=false`) |
| Real participant/research data | **None** |

[Project dashboard](https://supabase.com/dashboard/project/wfjlzecckihsgcvauqwt),
[deployed status](https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/status).

## Closed deployment checks on 4 November

All **42 backend tests** passed before deployment. The live closed-state verifier
passed with an enabled public client key: all OAuth, enrollment and upload
routes returned 503; a synthetic deletion returned a completed receipt; repeated
requests reached 429 with `Retry-After`; anonymous register/admission/telemetry
RPCs were denied; and the private schema was unavailable through the Data API.
No real provider account was connected and no research measurements were uploaded.
The deletion probe can leave a synthetic tombstone and operational counters.

All 16 private tables have RLS enabled and deny direct access to anonymous and
authenticated client roles. Governance functions use `SECURITY INVOKER`; the
Edge role cannot approve a protocol or release research extracts. Both
`qs-operations-monitor` and `qs-research-maintenance` ran successfully at 20:10
UTC on their five-minute schedules. The operator report showed fresh successful
probes and no active alerts. The independent delivery integration was added in
the later operations update above.

The security advisor reported only informational `rls_enabled_no_policy`
reminders for the intentionally private tables. These tables grant no direct
client access; server RPCs enforce authorization. See
[Supabase's explanation](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).

The optional administrative transaction stress script was not executed: its
client confirmation was cancelled before any SQL ran. Its historical 26 October
results below are not a new execution. The direct HTTP, quota, permission and
scheduled-maintenance checks above passed on the deployed backend.

The public service root is
`https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service`.
It has been configured in the add-on since v0.13.0. Existing installations can set that URL
in their add-on configuration; no server keys belong in Anki settings.

## Contact email verified on 30 October

[contact@jollyjoyjourney.com](mailto:contact@jollyjoyjourney.com) is active
on Cloudflare Email Routing with the Free plan, forwarding to the operator's
verified Gmail inbox. A setup test was accepted and reported delivered/forwarded
by Cloudflare. A test sent from that same Gmail account may be hidden as a
duplicate in Gmail; the sent copy alone is not evidence of inbox delivery.
The main-site, www and Somatic Signals Substack DNS mappings were copied and
verified as DNS-only records before changing the nameservers at Hostinger.
At that check, only the contact address routed; catch-all remained disabled. No paid plan,
outbound sending service or custom Worker was enabled.

## Project contact configured on 4 November

quantifiedcognition@jollyjoyjourney.com is now an enabled Cloudflare forwarding
rule to the same verified operator Gmail inbox. The original company contact
rule remains enabled, and catch-all remains disabled. The routing configuration
and destination verification were checked through the Cloudflare API; no new
delivery test email was sent. The website and current document bundle use the
project address. Email Sending, a paid plan and a custom Worker were not enabled.

The privacy policy uses this contact address and discloses the
Cloudflare-to-Gmail email flow. It was published on 4 November; provider and
study approvals remain outstanding. The email-routing change did
not resume Supabase, alter backend settings, or enable Oura, Google Health
or research uploads.

## Quotas and monitoring verified on 26 October

Three CLI-generated migrations were dry-run reviewed and applied:
`20261026230405_backend_operations.sql`,
`20261026230643_scheduled_monitoring.sql` and
`20261026231031_isolate_pg_net_extension.sql`. The function now also deploys
`operations.mjs`. All three enable flags were explicitly reasserted false.
No provider credentials or recipient/policy values were added.

Request admission is shared across instances, fails closed on database errors,
and returns 429/`Retry-After` at quota. Research storage has per-participant,
project and daily limits; accepted retries are deduplicated before storage is
charged. Deletion frees active storage and has an independent request budget.
See [BACKEND_OPERATIONS.md](BACKEND_OPERATIONS.md) for defaults and operator commands.

The `qs-operations-monitor` job succeeded automatically at **23:15 UTC**.
Its maintenance heartbeat and collected public status probe were fresh,
`probe_ok=true`, with **no active alerts**. `pg_cron` is installed in `pg_catalog`
and `pg_net` in `extensions`; neither monitoring extension is in `public`.
The monitor expects all online features disabled and does not send notifications.
Operator alert delivery/independent uptime remain on the [beta checklist](BETA_CHECKLIST.md).

All **25 backend tests** and **53 desktop provider/research contract tests**
passed. Hosted rollback-only tests verified quotas, unchanged duplicate receipts,
deletion/tombstones, and unhealthy/feature-drift alert firing and recovery using
synthetic probe responses. No actual feature was enabled. Synthetic research rows
and policy/probe edits were rolled back; final counts remain **zero participants
and zero submissions**. Live public probes also denied anonymous admission and
metrics RPC calls (401). The private schema returned 406.
The optional HTTP quota probe returned **429 with `Retry-After`** after repeated
deletions of one nonexistent identity, without creating a participant or package.

Security advisories after extension isolation contained only the expected
[RLS enabled without ordinary-user policies](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
information for the eight private tables; no warning/error remained. The unused
research-participant index remains appropriate on an empty research database.

## Foundation verified on 23–25 October

The CLI was linked to this new, empty project. A dry run showed only
`20261023170248_research_storage.sql`, then that exact migration was applied.
Remote migration history matched the local filename. The function was originally deployed
from `index.ts`, `service.mjs`, `google.mjs` and `schema.json`. All three disable flags were explicitly
set in server configuration, along with the public service URL and notice
version/hash. A random server-only OAuth state-signing secret was generated
locally, installed through a temporary file on M:, then the file was removed. No Oura client ID or
client secret was installed. No Google client ID/secret was installed either.
The privacy and recipient fields remain empty. The Google extension adds only
OAuth routes and aggregate-column validation; it needs no cloud SQL migration.
Research notice v5 matches package schema v4, including the derived main-sleep
and nap measurements and nap light/deep/REM durations. Exact sleep/review instants and session identifiers are
not accepted. This validator update needs no cloud SQL migration. All three
disable flags were reasserted and their stored hashes checked without printing
secret values. The database still has zero participants and zero submissions.

Live HTTPS checks returned:

| Probe | Result |
| --- | --- |
| Status | 200; all three enabled flags false, matching v5 notice hash |
| Oura start, exchange, refresh and revoke | 503, disabled |
| Oura browser callback | 503, disabled |
| Google start, exchange, refresh, revoke and browser callback | 503, disabled |
| Research registration and upload, including syntactically valid test authentication | 503, disabled |
| Deletion of a random nonexistent identity | 200, idempotent no-op |
| Anonymous direct registration RPC with the public API key | 401, denied |
| Direct request for the private schema | 406, schema not exposed |

Deletion intentionally remains available while new uploads are disabled. Its
no-op probe verified the hosted function's server credential and database RPC
connection without registering a participant or sending measurements. A final
SQL query confirmed **zero participants and zero submissions**.

SQL verification confirmed RLS on both private tables, no private-schema usage
or RPC execution for `anon`/`authenticated`, and access only for `service_role`.
All three RPCs are `SECURITY INVOKER` with an empty search path. The function's
gateway JWT check is disabled because the application verifies its own
capabilities and OAuth state; the private database permissions remain enforced.
No Supabase Auth users were created by the deployment or checks.

The security advisor reported only informational
[RLS enabled with no policies](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
for the two private tables. This is intentional: ordinary clients get no table
access; the server role bypasses RLS. The performance advisor reported the
[unused participant index](https://supabase.com/docs/guides/database/database-linter?lint=0005_unused_index),
expected on an empty database. It is retained for deletion/ownership lookups.
No security warning/error was returned by this check.

The v0.17.1 desktop package includes the current beta checklist, operating guide
and privacy draft. This patch updates release documentation; desktop analysis
and its sharing notice/package schema remain unchanged.

## Recheck the closed state

```powershell
python tools/check_deployment.py https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service
```

Supply `--publishable-key` with the project's **public** key to also repeat the
anonymous REST probes. This verifier sends empty bodies and a random nonexistent
test identity; it never registers a participant or uploads research/health data.
The script requires all three feature flags to remain false and fails otherwise.

`supabase/deployment.json` records the nonsecret project configuration. Server
keys, access tokens and state-signing values are excluded from that file and
from the add-on archive. Current server-secret hashes confirmed all three explicit
disable values; no credential values were printed during verification.

## Before enabling online features

Review the published [JollyJoyJourney privacy policy](PRIVACY.md) and study
information for any required approval changes, finish the [Oura approval checklist](OURA_APPROVAL.md)
and its storage/revocation/deletion requirements and permitted-account pilot,
and complete [Google setup/research requirements](GOOGLE_HEALTH.md) with its own
credentials and permitted live-account pilot. Review retention, logs, backup
deletion and abuse controls as described in [DEPLOYMENT.md](DEPLOYMENT.md).

Do not change the three flags as part of ordinary deployment, add-on installation,
or testing. Enabling is a separate operator action after the required policy and
approvals are ready. Supabase quoted $0/month for project creation on the current
Free plan; ongoing plan limits and paused-project behavior are managed in the
operator's dashboard.
