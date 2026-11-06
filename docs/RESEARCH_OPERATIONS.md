# Research data and activation plan

Version 1, **2 November 2026**. Operator: **JollyJoyJourney**. This document
contains operator procedures and
implementation requirements; it is not a claim that unpublished controls are
already deployed. The participant commitments are in [PRIVACY.md](PRIVACY.md)
and [RESEARCH_INFORMATION.md](RESEARCH_INFORMATION.md). Scientific scope and
analysis rules are in [RESEARCH_DESIGN.md](RESEARCH_DESIGN.md).

## Operator decisions

The programme covers variation in learning/recall, relationships with sleep,
activity and study patterns, and measurement validation. Individual hypotheses
and analysis methods can evolve within that area. The initial population is
adults contributing their own data. Personal use is independent of research,
submissions remain a complete package of available disclosed fields, and every
submission requires an explicit Send.

Operator decisions confirmed on 2 November 2026 allow controlled access for
approved academic/nonprofit/commercial research collaborators and impose no
fixed expiry date on research measurements. Continued retention depends on the
programme's research, validation or reproducibility needs, periodic review,
withdrawal and applicable legal/provider requirements. An old contribution is
not automatically expired, and a possible unspecified future use alone does not
justify keeping personal data.

There are no authorized external data recipients at preparation. Christian
Kleineidam is the initial accountable operator/research lead. Identify
new people and organizations in an access register before access. Separate
researcher, service-administrator and collaborator roles; an administrative
account is not routine permission to analyze every dataset.

## Verified state and remaining work

On 4 November the Frankfurt Supabase project wfjlzecckihsgcvauqwt was restored
and the prepared backend updates were deployed. It reports **ACTIVE_HEALTHY**.
The live configuration and checked-in examples keep Oura, Google Health and
research uploads false. Verification used synthetic identities only; no real
participant measurements were uploaded. See [DEPLOYED.md](DEPLOYED.md) for the
current live checks and their limits.

The code implements a strict daily-package allowlist, explicit preview/send,
private storage, separate upload/deletion capabilities, idempotency, atomic live
deletion, anti-resurrection tombstones, and local pre-view preference/exposure
history. Version 0.19.1 source implements separate eligibility/explicit-consent
enrollment linked to the existing participant ID, server-side enrollment enforcement,
retention reviews and tracked extract/collaborator deletion. See
[RESEARCH_CONTROLS.md](RESEARCH_CONTROLS.md). The migrations are deployed, all
prepared protocols remain closed/unapproved, and acceptance of the consent
process must be confirmed. Provider revocation/backup handling and the permitted
live-account pilot remain outstanding. Implementation is not study/provider
approval or feature activation.

The independent monitor, encrypted recovery copies, external minimal deletion
ledger and operator withdrawal procedure are now implemented. The operator
approved Silentworker as the encrypted-copy destination and confirmed MFA.
See [BACKEND_RECOVERY.md](BACKEND_RECOVERY.md) for actual schedules, limits,
role boundaries, key handling and the synthetic restore/operator rehearsals.

## Data handling and retention

| Records | Operating limit and action |
| --- | --- |
| Research measurements and identifiable/pseudonymous derived datasets | No fixed expiry. Document the continuing programme research, validation or reproducibility need and review it at least annually. Delete on withdrawal, loss of need or an applicable legal/provider deadline; irreversible anonymization requires permission and a genuine anonymity assessment. |
| Consent, withdrawal and rights evidence | Keep the minimal record needed while measurements remain and normally three years after their deletion; record any specific legal reason for an exception. No retained measurement values. |
| Anti-resurrection deletion markers | Minimal random UUID/timestamp only; retain as needed to reject old client/restored-copy requests. Do not use as a research record or retain the former capability hash. |
| Routine correspondence | Delete normally within two years of resolution; preserve only records required for a specific legal obligation or claim. |
| Quota windows | Existing housekeeping removes windows older than 48 hours. |
| Operational metrics and resolved alert states | Existing limits are seven days and 30 days respectively. |
| Routine application/platform request logs | Verify a maximum of 30 days and shortest useful setting; redact credentials, payloads and OAuth callback secrets wherever controllable. Document unavoidable provider metadata/access. |
| Recovery copies | Maximum 30 days, isolated from research use, with deletion replay before access. A shorter applicable provider/legal deadline overrides this cap. |
| Public outputs | Only genuinely anonymous summaries and synthetic data; no individual-level daily-history public releases. |

The source retention register and scheduled review process record the
continuing purpose, applicable consent/provider restrictions, review decision,
next review and any mandatory deadline. Use server receipt timestamps for
provenance and applicable deadlines, not package-generated timestamps. Neither
technical retries nor a fresh duplicate snapshot establish continuing research
need. Do not impose an automatic age-based expiry on contributions. Apply
required deletion to original submissions,
analysis extracts, researcher workspaces and controlled collaborator copies.
Test retries and expired identities without allowing stale requests to restore
removed records. Keep a separate minimal deletion ledger sufficient to suppress
restored data for the complete lifetime of every recoverable copy. A ledger
must not contain the deleted measurements.

Supabase's Free plan does not provide the automatic daily backup entitlement
documented for paid plans. Verify the actual recovery configuration; do not
claim a backup schedule just because the project exists. Any operator-managed
backups require encryption, restricted key access, an inventory, expiry and
restore tests. Do not create unmanaged long-lived database dumps on a desktop,
Drive or GitHub. The private database schema and TLS do not constitute per-person
cryptographic erasure of a stored JSONB package.

## Withdrawal and exceptional requests

The normal client flow queues deletion before recording withdrawal, blocks new
submissions and retries while Anki is open. The current server deletion receipt
confirms removal from active application storage. Before research activation,
retain the distinction between active-storage removal and pending registered
copies; do not label an active-storage receipt proof of full downstream deletion.

On receiving a verified withdrawal, stop affected research use immediately,
including queued analyses, and start deletion across the inventory. Use 72 hours
as the operator's target for active storage and extracts, subject to any shorter
requirement. Oura's agreement specifies immediate cessation on opt-out and
deletion within 72 hours of a request. Confirm its treatment of backups and
offline personal copies in writing before activation. A normal 30-day recovery
window cannot simply override that obligation.

Contact requests go to quantifiedcognition@jollyjoyjourney.com. First try the existing local
capability or secure in-app recovery. A public participant ID locates data but
does not authorize disclosure or deletion. If the credential is lost, request
only proportionate additional evidence, such as a private receipt and an
operator-reviewed account of the contribution. Do not assume an email address
or identity document proves ownership of a pseudonymous record. Avoid collecting
identity documents by default; where identification is impossible, explain the
limitation rather than attempting to identify the person through outside data.
Log the verified request, action, scope and response without copying measurements
into the support inbox. Respond within the applicable rights-request period.

## Research access and publication

Before an external recipient gets data, record the organization, named
responsible person, role as processor/joint or independent controller, country,
question/purpose, minimum dataset, access period, provider permission, consent
coverage and approved transfer safeguard. Prefer access in a controlled
workspace to unrestricted downloads. Use an agreement requiring confidentiality,
no reidentification, no unrelated use, no onward sharing, incident reporting,
expiry, withdrawal propagation and deletion confirmation. Keep an extract
inventory and access/export log. Revoke accounts and collect deletion evidence
when access ends. Contractors administering the service need appropriate
processor arrangements rather than a research-collaborator label.

An independent controller must be named and receive any necessary fresh consent
before access; the general collaborator category does not silently appoint one.
Do not issue a public daily-data download, even with names/IDs removed. Review
public tables, figures and examples for rare patterns, small groups, exact dates,
linkability and repeated-release disclosure. Coarsen or suppress where needed.
No fixed minimum cell count guarantees anonymity. Publish funding/conflicts and
avoid implying provider endorsement.

Statistical methods and validation remain within the programme's purpose.
Oura's AI/model restrictions are separate: obtain written permission for any
intended use that its agreement restricts. Do not send real participant data to
general-purpose AI assistants or treat ordinary consent as that permission.
Use synthetic fixtures for development and agent-assisted work.

## Enrollment, notices and approvals before activation

The existing research-sharing-v5 audit is a local preference mechanism. Keep
original preference/exposure times and unknown legacy status intact. The new
scope, collaborators and retention are reflected in notice v7, whose matching
server hash is deployed. Do not edit v5's
meaning retrospectively or interpret its research_consent_active field alone
as proof of completed legal/provider-approved enrollment.

The source flow checks eligibility before consent, provides saveable information
and explicit health-data consent, and stores the signed version/timestamp separately
from measurements. The server requires that enrollment. Confirm that its
explicit consent action linked to the participant ID is accepted;
if a name/contact is required, disclose it first and keep it out of the analysis
package. Required changes need a new versioned protocol before activation.
Review the personal health-connection consent separately too; a provider's
OAuth authorization screen is not automatically proof of valid explicit consent
for every operation described by the app.

The first release's research scope covers Anki and Oura. Confirm Oura's
permission for local historical storage, user CSV export, hosted research
retention, intended analysis and controlled collaborators. Resolve whether its Aggregator/End
Customer requirements apply. Implement the accepted revocation notification and
offline-data process; current desktop polling is not continuous notification.
No provider app, ethics application or account is submitted by these documents.

Before publication, review actual Supabase processing terms, administrator MFA,
access, logging and recovery configuration. The database region is not a global
OAuth-processing guarantee: Edge requests normally execute near the requester.
Verify supported regional routing if the operator chooses to restrict execution;
the present policy instead discloses possible international processing. Record
the applicable processor/subprocessor and transfer arrangements, including the
roles and terms for Cloudflare-to-consumer-Gmail contact handling. Do not claim
a Google Workspace agreement or an executed contract that has not been checked.
Record the data-protection risk assessment and whether a DPIA is required for
the intended scale and processing before enrollment; do not claim an assessment
or exemption that has not been made.

Publish the policy and participant information at stable public URLs, verify
them without an account, and configure matching operator/notice/version/hash
values. Suggested paths are /quantified-cognition/privacy and
/quantified-cognition/research on the operator's site; these are proposed paths,
not published pages. Publish the current partner/role register alongside them.
Keep all three online feature flags false through preparation. Only enable a
specific feature after its permissions, implementation and live pilot checks
pass; source publication alone is not activation.

## Change decisions

New hypotheses, suitable analysis methods and quality thresholds within the
existing purpose normally belong in a versioned analysis plan. Keep new data
types, unrelated purposes, broader recipients, new identity collection or a
broader retention purposes or weaker deletion/review safeguards out of that
shortcut: update participant information,
permissions and consent as required before proceeding. A move to an intervention
also requires an appropriate protocol and participant information. Shorter
retention or reduced collection can be implemented and communicated without
using a policy update to expand existing permission.

## Sources checked on 2 November 2026

[European Commission: consent and research scope](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/legal-grounds-processing-data_en),
[European Commission: storage limitation](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/principles-gdpr_en),
[EDPB consent guidance](https://www.edpb.europa.eu/system/files/documents/files/file1/edpb_guidelines_202005_consent_en.pdf),
[Google research policy](https://developers.google.com/health/policies/health-api-user-data-and-research-policy),
[Oura agreement](https://cloud.ouraring.com/legal/api-agreement),
[Supabase regional invocation](https://supabase.com/docs/guides/functions/regional-invocation),
[Supabase backups](https://supabase.com/docs/guides/platform/backups),
[Supabase DPA](https://supabase.com/legal/dpa),
[Berlin data-protection authority](https://www.datenschutz-berlin.de/ueber-uns/kontakt).
