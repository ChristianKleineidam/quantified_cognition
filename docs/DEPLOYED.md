# Disabled backend deployment

Verified **26 October 2026, 23:15 UTC**. This is a deployed prelaunch backend;
Oura, Google Health and research uploads remain disabled.

**4 November website publication:** the public information site, privacy policy
and research information are now hosted on Cloudflare Pages at
[quantifiedcognition.jollyjoyjourney.com](https://quantifiedcognition.jollyjoyjourney.com/).
See [WEBSITE.md](WEBSITE.md). Publication did not resume Supabase, change hosted
settings or enable provider access, enrollment or uploads.

**30 October desktop-beta recheck:** the management API reports this project
`INACTIVE`. Public DNS and database checks are unavailable. It was not resumed
and no server settings were changed. The successful live verification below is
historical; monitoring cannot run while the project is inactive. Restore only
through a separately authorized operation, then repeat the closed-state checks.

| Setting | Value |
| --- | --- |
| Project | `anki-quantified-self` |
| Organization | Tempo |
| Project ref | `wfjlzecckihsgcvauqwt` |
| Region | Frankfurt, `eu-central-1` |
| Database | PostgreSQL 17.11 |
| Function | `qs-service`, deployed version 9 |
| Operational controls | Version 1; shared request/storage quotas |
| Monitor | Active, every five minutes; private alerts |
| Oura access | **Disabled** (`OURA_ENABLED=false`) |
| Google Health access | **Disabled** (`GOOGLE_HEALTH_ENABLED=false`) |
| Research registration/uploads | **Disabled** (`RESEARCH_ENABLED=false`) |
| Real participant/research data | **None** |

[Project dashboard](https://supabase.com/dashboard/project/wfjlzecckihsgcvauqwt),
[deployed status](https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/status).
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

Complete and publish the [JollyJoyJourney privacy draft](PRIVACY.md), supply its
study/contact and policy details, finish the [Oura approval checklist](OURA_APPROVAL.md)
and its storage/revocation/deletion requirements, install Oura client credentials,
and complete [Google setup/research requirements](GOOGLE_HEALTH.md) with its own
credentials and permitted live-account pilot. Review retention, logs, backup
deletion and abuse controls as described in [DEPLOYMENT.md](DEPLOYMENT.md).

Do not change the three flags as part of ordinary deployment, add-on installation,
or testing. Enabling is a separate operator action after the required policy and
approvals are ready. Supabase quoted $0/month for project creation on the current
Free plan; ongoing plan limits and paused-project behavior are managed in the
operator's dashboard.
