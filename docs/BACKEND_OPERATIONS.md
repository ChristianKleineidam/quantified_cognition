# Backend quotas and monitoring

Deployed on 26 October 2026. Oura, Google Health and research uploads remain
**disabled**. These controls do not enable a provider or submit any research data.
The local add-on and automatic CSV publication work independently of this backend.

## Limits

Every enabled protected request must first receive admission from PostgreSQL.
One lock per route family makes checking and charging all counters atomic across
Edge Function instances. If admission is unavailable, the request returns 503
before provider or research work. Exhaustion returns 429 with `Retry-After`.

| Route family | Global/minute | Global/day | Actor/minute | Actor/day |
| --- | ---: | ---: | ---: | ---: |
| Oura and Google OAuth combined | 60 | 2,000 | 10 | 100 |
| Research registration | 10 | 100 | 2 | 4 |
| Research submissions | 20 | 200 | 3 | 12 |
| Research deletion | 120 | 6,000 | 10 | 100 |

These are fixed UTC minute/day windows, so a boundary can allow two adjacent
windows' allowances in a short interval. Retries consume request allowances.
Deletion has its own budget and remains available with research disabled or
storage full. It can still be delayed by its own quota or an infrastructure outage.
Closed OAuth/upload routes return 503 without quota, provider or storage calls.
Public status and unknown routes do not access the quota database.

An actor is a server HMAC of the OAuth token/flow/state, or the research identity
plus its capability hash, scoped to the family and UTC day. Raw credentials and
identities are not stored in quota buckets. A changed secret cannot consume the
legitimate secret's actor allowance. OAuth start states and research identities
can be changed by a caller, so actor limits alone are not abuse protection; the
shared global limits still apply. Client IP headers are not trusted for identity.

| Stored research data | Beta limit |
| --- | ---: |
| Packages per participant | 12 |
| Package bytes per participant | 64 MiB |
| Packages across the project | 5,000 |
| Package bytes across the project | 256 MiB |
| New packages per UTC day | 100 |
| New package bytes per UTC day | 128 MiB |
| Registered identities, including deletion tombstones | 10,000 |
| One upload body / stored JSON text | 8 MiB |
| OAuth JSON body | 16 KiB |

Stored bytes use PostgreSQL's UTF-8 JSON text size, including its formatting,
rather than compressed database size. Byte and count limits are both enforced.
An accepted submission ID retried with the same owner and payload hash returns
its original receipt without charging storage or daily storage usage again.
Changed payloads or owners cannot reuse an ID. Deletion frees active package
counts/bytes; daily work and identity tombstones are not refunded. Failed writes
roll back all storage accounting. Storage rejection returns 429 with a one-day
retry hint; persistent caps require deletion or an operator decision before a
new package can be accepted. No research package is automatically deleted to
make room, and failed research uploads never retry automatically.

The private `quota_policy` and `storage_budget` tables contain the limits. Only
an authorized operator should change them, after reviewing capacity. No key,
secret or administrative setting belongs in Anki's configuration. Keep all three
enable flags false during ordinary deployments.

These are application work/storage limits, **not a billing ceiling**. Requests
rejected at the function can still consume invocations, logs and database work.
Indexes, row overhead, backups and platform logs are outside the payload budget.
Review ingress controls, project usage and plan alerts before an online pilot.

## What is monitored

Application request logs contain only a random request ID, fixed route label,
HTTP status and duration. They exclude URLs, query strings, IPs, headers, bodies,
tokens, participant identities and error messages. Hosting/gateway logs are a
separate system; their access, retention and callback-URL handling still need an
operator review before provider approval. See the [privacy draft](PRIVACY.md).

Admitted protected requests also update private minute aggregates of counts,
status and elapsed time. Quota denials are counted inside the admission RPC.
Completion reporting uses `EdgeRuntime.waitUntil`; a telemetry failure emits a
sanitized `qs_monitoring_failure` event and does not fail an otherwise successful
request. A process termination can lose completion telemetry. Closed routes,
status requests and quota-store failures appear in function logs, without an
additional metrics write. Do not treat the aggregate table as a complete audit
or an exact record of all incoming traffic.

The active `qs-operations-monitor` cron job runs every five minutes. It removes
quota windows older than 48 hours, metrics older than seven days and its own cron
run history older than seven days. It records a maintenance heartbeat and keeps
a small set of current alert states for:

- Storage, identity or current global request budgets at 80%.
- At least five 5xx responses and a 10% failure rate over 15 minutes.
- A protected request taking at least 20 seconds within 15 minutes.
- A failed public status check or an unexpectedly enabled feature.

The job uses `pg_net` to request public status without credentials. The next run
collects the response, so a change normally appears within about ten minutes.
No OAuth token, health record or research package is requested by the monitor.
It expects all three flags to be false and operations version 1. Update that
expectation only as part of a separately authorized online launch.

Alerts stay in private PostgreSQL tables; **no email, paging or external uptime
service is configured**. A paused/unreachable project cannot alert through its
own database. The operator report marks maintenance and probes stale after
12 minutes; arrange independent delivery/uptime checks before an online pilot.

## Operator checks

From this linked project, using an authorized Supabase CLI session:

```powershell
supabase db query --linked --file supabase/operations-report.sql
python tools/check_deployment.py https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service
```

Investigate active alerts, false `probe_ok`, or either stale indicator. Inspect
the Supabase function logs for `qs_monitoring_failure`, status 503/500 and quota
429s; check the dashboard's database/function usage as well. No public API lists
operational metrics or research submissions. Never paste credentials or payloads
into incident reports.

The hosted verification script is administrative and rollback-only:

```powershell
supabase db query --linked --file supabase/operations-smoke.sql
```

Run it when the monitor has a completed status response. It temporarily lowers
limits and edits a synthetic probe response inside one transaction, checks
quota/deduplication/deletion and alert firing/recovery, then rolls everything
back. It never enables a real feature. Run during maintenance if online access
is later permitted: its policy locks can briefly delay concurrent requests.

Add `--check-quota` to the Python closed-state verifier to exercise a real HTTP
429 and `Retry-After` using up to 21 deletion requests for one nonexistent
identity. This creates only operational counters, not a participant or package.
Avoid repeating it during an incident that is already consuming deletion capacity.

Local verification:

```powershell
# Set PGLITE_TEST_MODULE to your installed @electric-sql/pglite/dist/index.js.
node --test supabase/tests/*.test.mjs
```

PGlite runs PostgreSQL quota/permission/accounting tests. Its concurrent client
test shares one database runtime. The separate native suite below tests actual
lock contention across independent PostgreSQL connections. Neither establishes
hosted Edge Function throughput under representative pilot traffic. PGlite lacks
pg_cron/pg_net, so scheduler checks still require the deployed project.

### Native connection races

On 4 November, **11 native PostgreSQL 17.11 tests passed**, alongside the existing
42 backend tests. The suite waits for actual server lock waits before releasing
competing connections. It covers actor/global quotas, shared storage caps,
identical/conflicting upload retries, deletion retries, upload versus deletion,
extract release versus withdrawal, and deadline maintenance versus withdrawal.

The last case reproduced a deadlock in a batch of 64 deadline deletions. Migration
`20261104203715_maintenance_lock_order.sql` fixes it by acquiring participant locks
in UUID order before changing shared accounting or copy state. The complete lock
pass includes tombstones because copy confirmations also lock their participant.
This briefly serializes participant writes during maintenance. In a disposable
PostgreSQL 17.11 instance with 10,000 synthetic identities and no due deletions,
five passes took 6.2–9.5 ms (median 6.7 ms). That is a local idle measurement,
not a hosted-load or mass-deletion timing guarantee.

Run the native suite explicitly against a **disposable local cluster**, never a
linked Supabase project. For example, with Docker (Podman accepts equivalent
arguments):

```powershell
docker run --rm -d --name qc-postgres-tests --publish 127.0.0.1:55439:5432 --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=qc-disposable-test-only postgres:17.11-bookworm -c quantified_cognition.test_cluster=on
python -m pip install -r requirements-backend-tests.txt
$env:QC_PG_TEST_DSN='host=127.0.0.1 port=55439 user=postgres password=qc-disposable-test-only dbname=postgres sslmode=disable'
python -m pytest supabase/tests/test_concurrency.py -q --junitxml=.reference/backend-concurrency.xml
docker stop qc-postgres-tests
```

Wait for the server to accept connections before running pytest. The password is
only for this synthetic local example. Tests require an explicit loopback host
and the server-side `quantified_cognition.test_cluster=on` marker. They create
randomly named databases, apply the real migrations, approve only an isolated
synthetic protocol and remove their databases afterward. Missing configuration
or dependencies fail the explicit run. They do not read cloud credentials or
change hosted feature flags. This optional native suite is separate from the
ordinary PGlite/Silentworker pass; its result is not implied by that pass.

## Recovery

Keep the disable flags false during an incident; do not disable quotas as a
workaround. Research deletion stays available within its independent budget.
For a lost monitor, inspect `cron.job_run_details` for this job, then run
`select qs_private.monitor_tick()` as an administrator, wait for its HTTP
response and run it again. Recheck heartbeats, alerts and public status. Keep
`qs_private` unexposed and retain RLS and server-only function permissions.

Deploy fixes forward from checked-in migrations. A service rollback must retain
the admission calls; rolling back to the old unmetered handler removes the
protection. Recover research data only under the published retention/backup
procedure and replay deletion tombstones. Review counters against stored rows
after any administrative data repair. No destructive rollback is automated.

Platform references: [Cron](https://supabase.com/docs/guides/cron),
[pg_net](https://supabase.com/docs/guides/database/extensions/pg_net),
[background tasks](https://supabase.com/docs/guides/functions/background-tasks),
[function logging](https://supabase.com/docs/guides/functions/logging).
