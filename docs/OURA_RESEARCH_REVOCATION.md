# Oura research authorization and deletion

The research service stays disabled. This control is required before opening it.
Oura OpenAPI 1.43 lists create/update/delete webhooks for health records, not an
OAuth revocation event. The implementation therefore uses authorization checks
independent of the Anki desktop.

## What runs on the server

Only an explicit **Send this package** containing Oura data starts monitoring.
Enrollment must cover `access-token-v1` monitoring. The service authenticates a
server-signed token-expiry receipt and stores only an AES-GCM-encrypted access
token, bound to the participant and binding generation. No refresh token or
Oura account ID is retained. The encryption key is an Edge secret, distinct from
the off-server research decryption key. Monitoring credentials are excluded
from research extracts and recovery archives.

`qs-oura-research-authorization` runs each minute and checks due contributors
normally every five minutes, up to 100 per run with eight concurrent requests.
It requests an empty, pre-Oura date window and discards successful response bodies
without reading them. Error classification reads at most 4 KiB and retains nothing.
Oura's 401 scope-denial responses are distinguished from invalid tokens; Daily
and Workout grants are handled separately. Ambiguous errors pause use. A successful check grants at most a
ten-minute research-use lease, bounded by token expiry.

An expired token, missing scope/membership, timeout, rate limit or provider
outage pauses research use. It does not manufacture a withdrawal or delete
measurements. Expired credentials are removed; normal desktop sync can renew
authorization. A stopped scheduler also expires use leases without relying on
the scheduler to perform that expiration. Monitor lag and pauses raise private
alerts through the existing operations channel. At capacity, data becomes
unavailable rather than receiving an extended authorization lease.

An explicit invalid-token response for an unexpired, server-attested token
withdraws the complete encrypted contribution through the existing lifecycle.
Expiry has a two-minute margin and is handled as a pause. Generation checks
prevent a late failure for an older token from deleting a renewed binding.
The participant lock serializes withdrawal against uploads and extract release.

## Submissions, copies and backups

Revocation deletes active submissions, withdraws enrollment, invalidates upload
credentials and queues every registered research copy. Copy deadlines are no
later than 72 hours from the last successful authorization check when available,
or from detection otherwise. The credential copy itself is erased. Previously
saved receipt credentials allow the desktop to retrieve the deletion status
after an automatic withdrawal; lost upload responses cannot resurrect records.

The existing Silentworker deletion ledger detects the participant tombstone,
purges affected encrypted recovery archives and records confirmations. Restores
replay the current external ledger before any use. Monitoring tokens are never
restored: Oura-derived research remains unavailable until newly verified.

Every operator/workspace/collaborator copy must remain inventoried. Run
decryption and analyses under the use guard, with an operator-controlled libpq
service configured through `PGSERVICE` and verified TLS:

```powershell
python tools/research_guard.py --extract <registered-extract-UUID> -- python analysis.py
```

The guard checks current authorization before starting and every five seconds.
Unavailable authorization or database connectivity stops the managed process
tree. It never claims to erase files or confirm someone else's deletion.
Do not use an already-open notebook, an offline job or an unregistered copy to
bypass the guard. Paused copies require a fresh use decision before resuming.

Use `supabase/research-operations-report.sql` to locate deletion tasks. Stop use
immediately, remove the affected records or the complete copy, then call
`qs_governance.confirm_copy_deletion` with actual evidence. Completion stays
false until every registered holder confirms. The server cannot physically
erase an uncontrolled or disconnected collaborator computer; agreements,
restricted workspaces, current-use checks, deadlines and escalation cover that
boundary. No collaborator receives access by default.

## Commissioning

Commissioned **8 November 2026**: Edge version 34, both migrations, the one-minute
cron job and matching Silentworker modules are deployed. Empty job authentication,
scheduled HTTP responses, heartbeat and closed research routes passed. The 17
native PostgreSQL tests include actual encrypted backup omission and restore
holds; 59 backend tests cover revocation and partial grants. No real participant
was enrolled or contributed a research package. See the sanitized
[verification record](OURA_RESEARCH_REVOCATION_RESULT.json). Actual research
workspaces still require the guard and copy inventory before use.

The 0.23.0 package passed source/crypto-wheel checks and installation in a clean
Anki 26.9.3 profile. The sharing prompt was rendered with synthetic local state.
Monitoring secrets were checked for exclusion from every archive entry.

Create independent random Edge secrets `OURA_RESEARCH_TOKEN_KEY` (base64 of 32
bytes) and `OURA_RESEARCH_JOB_SECRET` (at least 43 URL-safe characters), outside
source control. Store only the job secret in Supabase Vault under
`qs_oura_research_job`. Apply `supabase/oura-research-schedule.sql` as the database
operator, and deploy the matching six-file Edge bundle. Keep
`RESEARCH_ENABLED=false` and all prepared research protocols closed.

Verify an empty authenticated monitor run, its cron completion/heartbeat and
the existing private alerts without enrolling a real participant. Invalid job
credentials must produce 401 without provider requests. Token keys must not be
rotated over live bindings without a migration or deliberate authorization hold.

Normal checks are periodic, not instant notification. They cannot promise zero
delay from a provider-side revocation. Explicit deletion requests through the
add-on or project contact work independently of that detection mechanism.
