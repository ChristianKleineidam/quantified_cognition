# Enrollment and research-data controls

Implemented in the **0.19.4 source** and deployed on **4 November 2026** with
synthetic-data and live closed-state verification. Oura, Google Health and
research uploads remain disabled. Every prepared protocol remains
`approved=false`, `enrollment_open=false` and both provider permissions false.
An environment flag alone cannot approve enrollment.

## Participant flow

The first Yes / Not now / No remains a sharing **preference**. The original
choice, event order, first-results opportunity and unknown legacy exposure are
preserved. Updating to the Oura-only notice asks for a current preference without
changing earlier timing. Existing CSV fields named `research_consent_*` remain the legacy
preference audit for compatibility; they are not proof of signed enrollment.

When participation is open, **Research sharing…** shows eligibility (18 or over,
own data), the saveable study information and policy, and the explicit health-data
consent statement. **Agree and join research** records enrollment in the background
and prepares a package for review in the same window. Each contribution requires
**Send this package**. Personal use is available without enrolling.

The exact documents and statement are frozen in `addon/research_enrollment.json`.
Their version/hash must match the approved server protocol. Protocol v5 records
an explicit consent action linked to the existing random profile participant ID
(`signature_method=explicit_consent`), without a separate signature-name field.
No legal name or email is requested. Confirm acceptance of this consent process
in the review/provider process; any required change needs an updated protocol
before activation. It does not verify a person's civil identity.

The append-only local sharing audit also records the actual consent event
(`enrollment_signed`, retained for audit compatibility),
separately from the first preference. Enrollment records contain their own
before-first-results classification, including unknown. Server receipt time
is separate from the client's claimed signing time. Neither is proof that a
person read the documents. Signed records and original document copies are saved
in the separate profile audit database; **Save consent copy…** exports them with
the receipt. They do not change FSRS scores or the ordinary daily CSV workflow.

Package schema v5 includes an enrollment ID. Eligibility answers and
full consent documents are excluded from the measurement package and ordinary
research exports. Tracked analysis exports include the enrollment ID, protocol
version and actual consent timing separately from the original sharing-preference
timing, without including full consent evidence or eligibility answers. Server-side
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
encrypted packages with consent provenance. The operator decrypts them locally
and selects the latest received snapshot per public profile/day using
`tools/research_crypto.py`; the private key stays off the server. See
[ENCRYPTION.md](ENCRYPTION.md). Each new
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
The independent encrypted recovery service and external minimal ledger are now
implemented on Silentworker; see [BACKEND_RECOVERY.md](BACKEND_RECOVERY.md).
Provider-specific deletion/revocation acceptance and real-account checks remain
activation requirements.

After deployment on Supabase, `qs-research-maintenance` is registered every five
minutes. It processes mandatory deadlines, overdue reviews, expired access,
evidence housekeeping and private overdue alerts. Local PostgreSQL tests execute
the maintenance function directly; both hosted schedulers have also run
successfully. Independent uptime/heartbeat checks and the existing Home Assistant
notification integration are installed. A paused project cannot run maintenance
or accept deletion requests, so the external monitor reports it.

## Updating and deploying

The migrations are `20261102141401_research_enrollment_lifecycle.sql`,
`20261102142008_research_protocol_v1.sql`,
`20261104151505_research_protocol_v2.sql`,
`20261104155542_research_protocol_v3.sql`,
`20261104164851_research_protocol_v4.sql`,
`20261104180329_participant_id_consent.sql`,
`20261104180330_research_protocol_v5.sql`,
`20261104184538_research_protocol_v6.sql` and
`20261104191205_research_protocol_v7.sql`. Versions 2 through 4 revise public
wording without expanding the data scope. Version 4 also states Google's
Limited Use commitment explicitly. Version 5 removes the extra pseudonym field
and links the explicit consent action to the existing participant ID. New
enrollments reject extra fields and retired consent methods. Renewals cannot
change the public participant ID bound to their authenticated connection.
Existing evidence and frozen document copies remain unchanged and saveable;
upgrading does not convert old consent to the new protocol. Earlier copies
remain in migration history. Version 6 updates the project contact address to
quantifiedcognition@jollyjoyjourney.com without changing research purposes, data
scope or the consent action. The previous company contact continues to forward
to the same operator inbox. Version 7 binds the revised sharing notice: the
initial Yes expresses interest, and retention uses purpose-and-review wording.
It changes no research scope or enrollment consent. Legacy audit modes, field
names, original choices and exposure timing remain intact.
Saved settings for older built-in notices advance to the current built-in
version when their text override is empty. Explicit custom notices retain their
configured versions. Neither operation rewrites the audit history.
All migrations through protocol v7 were applied in order on 4 November. Existing
rows are **not** backfilled as enrolled. Hosted notice v7 and its hash match
`python tools/service_schema.py`. The live Edge role cannot approve a protocol
or release research extracts; both scheduled maintenance jobs ran successfully.

When participant documents change, choose a new enrollment protocol version,
regenerate the frozen bundle with `tools/build_research_bundle.py`, and generate
a new seed using `--migration` pointing at an empty file created with
`supabase migration new`. Do not rewrite an applied seed or reinterpret old signed
copies. The script never approves a protocol. Publish the documents, record actual
permissions and accepted signature method, and test the complete flow before any
separately authorized activation. Keep all three feature flags false throughout
this implementation and ordinary deployment.
