# Enrollment and research-data controls

Implemented in the **0.19.0 source**, tested with synthetic data. These migrations
have **not been deployed** to the inactive hosted project. Oura, Google Health and
research uploads remain disabled. The prepared protocol is inserted with
`approved=false`, `enrollment_open=false` and both provider permissions false.
An environment flag alone cannot approve enrollment.

## Participant flow

The first Yes / Not now / No remains a sharing **preference**. The original
choice, event order, first-results opportunity and unknown legacy exposure are
preserved. Updating to notice v6 asks for a current preference without changing
earlier timing. Existing CSV fields named `research_consent_*` remain the legacy
preference audit for compatibility; they are not proof of signed enrollment.

Research sharing → **Research enrollment…** opens eligibility first (18 or over,
own data), followed by the complete, saveable participant information, policy,
explicit health-data consent and typed pseudonymous signature. Signing runs in
the background and records enrollment only. **Review and submit data…** still
requires a package preview and a separate Send for every contribution. No
measurements are sent merely by signing. Personal use is independent of enrollment.

The exact documents and statement are frozen in `addon/research_enrollment.json`.
Their version/hash must match the approved server protocol. A typed pseudonym
is the implemented signature method; its acceptance must be confirmed in the
review/provider process. No legal name or email is requested. A different
required signature process needs an updated implementation and protocol before
activation. A signature field is not a claim of verified identity.

The append-only local sharing audit also records the actual signature event,
separately from the first preference. Enrollment records contain their own
before-first-results classification, including unknown. Server receipt time
is separate from the client's claimed signing time. Neither is proof that a
person read the documents. Signed records and original document copies are saved
in the separate profile audit database; **Save consent copy…** exports them with
the receipt. They do not change FSRS scores or the ordinary daily CSV workflow.

Package schema v5 includes an enrollment ID. Signatures, eligibility answers and
full consent documents are excluded from the measurement package and ordinary
research exports. Tracked analysis exports include the enrollment ID, protocol
version and actual consent timing separately from the original sharing-preference
timing, without including the signature or eligibility answers. Server-side
enrollment evidence is in `qs_governance`, separate
from `qs_private.submissions`. Anonymous/authenticated clients cannot access
either. Operator-only governance functions are unavailable to the Edge role.

The database rejects missing/withdrawn enrollment, wrong document/notice versions,
another participant's enrollment, mismatched public profile IDs, unapproved
protocols, overdue reviews and passed deletion deadlines. Provider fields require
the corresponding recorded protocol permission, independently of OAuth flags.
Legacy clients cannot bypass enrollment by sending `research_consent_active=true`.
Retries use the same signed evidence and package ID; neither resets a review date.

## Retention reviews

There is **no fixed research measurement expiry**. Initial enrollment records the
programme purpose and a review due within one year. Subsequent uploads do not
renew it. Due reviews hold new uploads and tracked exports until an operator
records a continuing research/validation/reproducibility need and another review.
A shorter mandatory provider/legal deadline always applies. Choosing not to
retain deletes active measurements and creates copy-deletion tasks.

Run `supabase/research-operations-report.sql` as an authorized database operator.
It lists upcoming reviews/deadlines, pending copy deletions, recipients and private
alerts without printing measurements or signature evidence. Example decisions:

```sql
select qs_governance.review_retention(
  '<participant UUID>', true, 'Continuing method-validation analysis',
  'review record reference', now()+interval '1 year', null);

select qs_governance.review_retention(
  '<participant UUID>', false, null, 'No continuing research need', null);
```

Review references should locate a real decision, not repeat a generic justification.
The function cannot extend an already-recorded mandatory deadline. Deleting on a
mandatory deadline is automated; overdue annual reviews hold use rather than
silently deleting measurements. Minimal consent evidence is retained separately
and normally purged three years after completed deletion, while unresolved copy
deletions prevent premature evidence removal. The random anti-resurrection
tombstone contains no capability hash or measurements and remains after that purge.

## Extracts and controlled collaborators

Every analysis extract, workspace copy, collaborator copy or recovery copy must
have a separate manifest **before release**. Record its purpose, location reference,
responsible holder, explicit participant inventory, access limit and review date.
External recipients must first have an approved record with organization,
responsible person, country, agreement, provider permission, consent coverage,
transfer safeguard and access end. Independent-controller transfers are unsupported
by this flow; they need the separately disclosed/consented process first.

For an internal copy, as the authorized operator:

```sql
select qs_governance.register_extract(
  '<new extract UUID>', 'Validation dataset', 'operator', null,
  'Measurement validation', 'controlled workspace/inventory reference',
  now()+interval '90 days', array['<participant UUID>']::uuid[]);
select set_config('qs.extract_id', '<new extract UUID>', false);
-- Run supabase/research-export.sql in this same session.
```

The export checks current enrollment, retention and recipient access under the
same participant locks as withdrawal. It marks the manifest released and outputs
the latest submitted snapshot per public profile/day with provenance. Each new
download/copy requires a new manifest; an existing release cannot be repeated.
Use holder `collaborator` with an approved recipient UUID for controlled external
access, or `workspace`/`recovery` for those copies. Recovery access is capped at
30 days and may need a shorter provider deadline. Direct administrator queries,
unregistered backups and files copied outside this workflow cannot be discovered
or physically controlled by PostgreSQL. Operator procedures and access controls
must prevent those bypasses; this inventory does not attest to every platform backup.

Overdue copy reviews hold use until a documented need is recorded; they do not
impose an age expiry. Use `qs_governance.review_extract(extract_uuid, purpose,
review_reference, access_until)` to record a review and permitted access period.
Expired/revoked recipient access or an ended copy access period creates deletion
tasks. A task identifies the affected profile and copy and records a
deadline. A withdrawal marks the whole affected extract unavailable for further
release. Existing copies must stop being used immediately, including queued
analysis. Remove the affected records or the entire copy, then record evidence:

```sql
select qs_governance.confirm_copy_deletion(
  '<extract UUID>', '<participant UUID>', 'verified deletion/recipient confirmation reference');
```

A reference is required. Recording it is an operator attestation, not a remote
filesystem eraser or an automated message to collaborators. No collaborator is
authorized by default and no messages are sent. Unreleased manifests can be
cancelled automatically because no copy was released.

## Withdrawal, deletion and restoration

Withdrawal durably queues deletion before changing the local preference and
invalidates local enrollment and pending previews. The server removes active
measurements, withdraws enrollment, blocks old credentials and queues every
registered copy with a 72-hour target or earlier mandatory deadline. The client
separately reports **active storage deleted** and **registered copies pending**.
Only after all confirmations does it report completed deletion. It polls status
using a separate random receipt capability, discarding the former upload secret.
Lost responses reuse that receipt; status and deletion work while uploads are off.
Receipt capability hashes are purged with completed case records after three
years; a client absent longer may need operator help confirming its old request.

On a restore, keep the database isolated, import the **current external deletion
ledger**, then run `select qs_governance.replay_deletions()` before any research
access. It removes resurrected measurements, suppresses their use and reconciles
active storage counters. A restored backup's own old ledger is insufficient.
This function does not configure encrypted backups, arrange an external ledger
or prove platform deletion deadlines; those remain activation checks.

After deployment on Supabase, `qs-research-maintenance` is registered every five
minutes. It processes mandatory deadlines, overdue reviews, expired access,
evidence housekeeping and private overdue alerts. Local PostgreSQL tests execute
the maintenance function directly; the actual hosted scheduler still needs a live
verification. Operator alert delivery and independent uptime checks remain separate
backend work. A paused project cannot run maintenance or accept deletion requests.

## Updating and deploying

The two new migrations are `20261102141401_research_enrollment_lifecycle.sql` and
`20261102142008_research_protocol_v1.sql`. Apply them in order only as part of the
planned closed-backend deployment. Existing rows are **not** backfilled as enrolled.
Configure v6 notice/hash from `python tools/service_schema.py`; the source default
and the last hosted v5 deployment are intentionally different until deployment.

When participant documents change, choose a new enrollment protocol version,
regenerate the frozen bundle with `tools/build_research_bundle.py`, and generate
a new seed using `--migration` pointing at an empty file created with
`supabase migration new`. Do not rewrite an applied seed or reinterpret old signed
copies. The script never approves a protocol. Publish the documents, record actual
permissions and accepted signature method, and test the complete flow before any
separately authorized activation. Keep all three feature flags false throughout
this implementation and ordinary deployment.
