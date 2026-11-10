# Backend operations and recovery

Operator guide, updated 10 November 2026. The operator approved encrypted
recovery copies on Silentworker.

## Stop uploads

The existing **server** flag is `RESEARCH_ENABLED=false`. It rejects new
registration, enrollment and submissions even if a modified client asks to send.
Deletion requests and deletion-status checks remain available, within their
independent quota. Oura and Google have separate flags.

With an authorized Supabase CLI session:

```powershell
supabase secrets set RESEARCH_ENABLED=false --project-ref wfjlzecckihsgcvauqwt
```

This changes only the research switch. Reopening research is a separate operator
decision and still requires an approved protocol; the switch does not approve
consent terms or provider use. Test the closed routes using
`tools/check_deployment.py`. Synthetic HTTP tests exercise deletion while uploads
are disabled; do not enable real uploads just to test a pause.

## Independent checks and notification delivery

Silentworker checks public `/status`, private operations heartbeats, research
maintenance, private alerts, backup freshness and deletion-ledger freshness every
five minutes. `qs_monitor_reader` can read only operational state, alerts and
storage counters. It cannot read research submissions, participant identities or
consent. Requests verify TLS certificates and hostnames using the pinned
Supabase CA. The database also enforces TLS for external connections.

The monitor is registered in the existing Silentworker health inventory, whose
Home Assistant automation delivers notifications to the existing target. The
health publisher normally runs every 15 minutes. A stopped timer, a failed job
or a stale backend heartbeat becomes a host-health failure. The notification
rule additionally reports **new Quantified Cognition failures while another
host fault is already active**. Unchanged failures do not repeatedly notify.
The target and unrelated automation actions are unchanged.

Named outage drills triggered that automation at 21:57:56 and 22:02:56 UTC on
4 November, including while other host faults were active. Verification used
the recorded `automation_triggered` events; Home Assistant intentionally omits
the automation's `last_triggered` attribute from recorded state history. The
normal backend monitor was restored to healthy after each drill. These events
prove automation execution, not that a person read the phone notification.

The check runs outside Supabase, so a paused or unreachable database can still
be reported. Home Assistant and the monitor share Silentworker: loss of that
entire host can prevent delivery. This is not a third-host monitoring service.
The health channel's existing diagnostic uploads contain operational status and
bounded service logs, never recovery archives or research measurements.

The same mobile destination gets a separate alert when a participant sends
their first accepted research package. Silentworker reads only a running total.
Home Assistant sends the difference from its last notification and groups first
uploads that arrive between checks. Delivery normally takes a few minutes.
Retries, later packages from the same participant and administrator imports do
not increase the count. No participant IDs, measurements or credentials appear
in the alert. Withdrawal does not change the anonymous total.

After applying the matching migration and reinstalling the operations monitor,
run `tools/silentworker/research_notifications.py` on Silentworker as root to
install the Home Assistant package and load it. The installer uses the target
in the existing Silentworker health rule. During a backend rollback, preserve
the live upload counter along with the independent deletion ledger so the
notification checkpoint can continue from the correct value.

## Recovery copies and the separate deletion ledger

`quantified-cognition-backup.timer` runs daily at **02:15 Europe/Berlin**.
`quantified-cognition-ledger.timer` checks deletions every five minutes and makes
a replacement if no usable copy remains. Copies expire within **7 days** and
are bounded to **1 GiB combined**. This is shorter than the policy's maximum
recovery window. Backup reads consume database egress; review that usage with
pilot-size and quota checks before real online use.

An archive contains the private backend tables: research packages, separate
consent/governance records, credential hashes, deletion records and operational
state. It contains no provider tokens or server environment secrets. Each copy
is inventoried on the server before streaming. Its participant inventory bounds
the snapshot; participants added afterward belong to a later copy. A consistent
read transaction streams JSON records through gzip and age encryption. No
plaintext database dump is staged on disk.

The encrypted research-authorization access-token table is deliberately excluded.
Restores clear any existing monitoring credentials, keep research closed and
hold all restored copies. Fresh authorization is required before Oura research
use. See [OURA_RESEARCH_REVOCATION.md](OURA_RESEARCH_REVOCATION.md).

`qs_backup_agent` can read those private tables, lock a harmless participant
marker and manage only its own fixed recovery-copy inventory. It cannot change
measurements, consent, credentials, approval, retention decisions or collaborator
confirmations. Its inventory functions use `SECURITY INVOKER`, constrained RLS
policies and column grants. The monitor and test executor use separate users.

Silentworker stores:

```text
/var/lib/quantified-cognition-backup/snapshots/   encrypted copies and checksums
/var/lib/quantified-cognition-backup/deletions.json   independent minimal ledger
/var/lib/quantified-cognition-backup/status.json   non-sensitive job status
```

The ledger contains only random participant IDs and deletion timestamps. It is
outside the recovery archives, accumulates deletions and never drops them when
the source is rolled back. A missing ledger with existing archives fails
visibly. New withdrawals purge the older encrypted archives, then confirm their
removal in the server inventory. The next research-maintenance run completes a
participant's deletion only when all registered copies are confirmed. Copy
expiry, storage limits and interrupted writes use the same inventory cleanup.

Backups are isolated recovery material, never an analysis dataset. If a source
rollback reintroduces an independently recorded deleted identity, the agent
stops and requires deletion replay rather than making another contaminated copy.
The backup host and this ledger must not both be restored to an old recovery
point without obtaining a current deletion ledger first.

## Keys, credentials and installation

The private age identity stays on the operator's computer:

```text
M:\CodexSecrets\quantified-cognition\recovery-key.dpapi
M:\CodexSecrets\quantified-cognition\recovery-key.age
```

The first is protected by the current Windows account. The second is a portable
copy encrypted to the operator's existing Ed25519 SSH public key. Preserve that
private SSH key independently; losing both recovery methods makes the encrypted
archives unusable. Silentworker receives only the public age recipient.
`tools/recovery_key.py verify` decrypts in memory and prints only integrity
counts. The initial live archive was verified using the protected local key;
there were no research submissions or consent records in the project.

Database passwords are random and dedicated to these roles. Local copies are
Windows-protected. Server configuration uses root-restricted encrypted systemd
credentials, delivered to each service through its private credential directory.
The host's systemd credential key is on unencrypted media: privileged or offline
host access can expose database access, so revoke/rotate those two logins after
host compromise. This does not provide the off-host archive decryption key.
The credential provisioning process passes SCRAM verifiers, never plaintext
passwords, to administrative SQL. Nothing secret belongs in Git, Anki settings,
command-line arguments, journal messages or the add-on archive.

Install the reviewed `tools/silentworker/ops_install.py` source as root on
Silentworker. It installs the three service/timer pairs and preserves existing
health-inventory entries. `--enable-monitor` commissions only monitoring;
`--enable` commissions all three after the backup destination is authorized and
the first backup passes. Prepare the encrypted credentials before enabling.
All units are unprivileged, have bounded resources and run outside normal Anki
use. The test executor is denied access to operations credentials and archives.

## Restore rehearsal and exceptional requests

Use the matching recorded migrations and the **current independent ledger**.
`tools/backend_recovery.py` accepts only an explicitly marked disposable
loopback database for rehearsal. It validates the project, schema, bounded
record stream, counts and checksum within a transaction. It imports current
deletion markers, runs deletion replay, reconciles storage accounting, closes
all protocols and holds research access before committing. Corruption rolls
back the restore. A stale backup's own deletion ledger is insufficient.

The native automated tests exercise encryption/decryption, restore after
withdrawal, removal of resurrected measurements, tracked recovery copies,
operator extract release, overdue review, downstream confirmation and rejected
lost-credential requests. They use synthetic data in credential-free isolated
PostgreSQL, not live participants. A real replacement of production storage is
an operator recovery action; this rehearsal tool never overwrites Supabase.

For a lost-credential request, first verify ownership using proportionate
evidence as described in [RESEARCH_OPERATIONS.md](RESEARCH_OPERATIONS.md).
An email address or public participant ID alone does not authorize deletion or
disclosure. Record verification in restricted support records, then call as the
authorized operator:

```sql
select qs_governance.operator_withdraw(
  '<verified participant UUID>', 'rights-request-2026-001');
```

The reference must locate the verification record, not contain its personal
details. This withdraws use, removes active storage and queues controlled copies;
the result distinguishes pending copies from completion. It is idempotent and
audited. Clients, the Edge role and both operations roles cannot call it.
If ownership cannot be established, do not bypass authentication with the public
ID or send measurements through support email. Normal capability-based deletion
continues independently of this exceptional procedure.

## Access and log review

On 4 November the operator confirmed administrator sign-in uses two-factor
authentication. The two machine roles have no administrative permissions.
Production read-only permission probes and native negative tests cover their
boundaries. Both cron jobs have separate freshness checks. Security-advisor
findings are recorded with deployment evidence.

The observed database settings were `log_statement=ddl`, connection/duration
logging off, and error bind-parameter logging disabled. Application and agent
logs use fixed field/code allowlists and omit payloads, credentials and exception
text. Supabase documents **one day** of API/database log retention on the current
[Free plan](https://supabase.com/pricing); recheck if the plan changes. No new log
drain was configured. OAuth callback URLs and provider-side logging still need
the permitted real-account checks before provider access opens; source tests
are not evidence of those live data flows.
