# Research data-protection assessment

Prepared 8 November 2026 for the public desktop beta. Controller and accountable
operator: JollyJoyJourney, Christian Kleineidam. This is the programme's internal
assessment and processing record, including the matters in GDPR Article 35(7).
It is not an ethics-committee decision or a claim of provider endorsement.

## Processing, purpose and necessity

Adults voluntarily submit their own existing Anki history and available Oura
summaries for research on learning, recall, sleep, activity and measurement
validation. There is no intervention, required behaviour change, clinical
diagnosis, recruitment of children or decision about access to a service,
employment, insurance or credit. Personal analysis remains available without
research participation. Eligibility is self-declared; civil identity is not
verified or collected by default.

Daily aggregates alone cannot support prediction calibration, review-level Brier
scores or analyses of repeated cards and changing intervals. Pseudonymous review
records therefore accompany daily summaries. Current-FSRS reconstructed
probabilities are labelled as such. Health summaries permit stage, timing, nap
and activity comparisons; raw health records, original card/deck IDs, card text,
exact event times, account IDs and direct identifiers are excluded. A stable
random participant ID and keyed card pseudonyms support longitudinal analysis,
deduplication and withdrawal. They are personal data, not anonymous data.

The local cache and pending package are encrypted with an OS-protected key.
Measurements are encrypted on-device to an off-server research key. Supabase
stores ciphertext, minimal linkage/receipt metadata and separate consent
evidence. Its Frankfurt database region does not imply all Edge processing is
in Germany. Research contributions containing Oura also bind an encrypted,
expiring access token for independent permission checks; refresh tokens remain
on the desktop. Monitoring credentials are omitted from extracts and backups.

Only the operator has analysis access at launch. No external recipient is
authorized. Any collaborator requires the recorded purpose, agreement, access
period, transfer basis, consent coverage and applicable Oura permission before
release. No individual-level dataset is publicly released or sent to AI services.

## Lawful basis, choice and proportionality

The programme relies on consent under Article 6(1)(a) and explicit consent under
Article 9(2)(a) for health-related processing. The first sharing preference is
separate from enrollment. Its original before-results timing is preserved;
unknown legacy exposure stays unknown. The final information and consent
statement are frozen with a version/hash, linked to the participant ID, and
enforced on the server. Every package requires preview and an explicit Send.
Provider OAuth permission is separate from research consent.

The research area permits new questions within the disclosed programme. New data
types, unrelated purposes, broader access or weaker safeguards require a new
assessment and any necessary updated information, permission and consent.
Full consent evidence and eligibility answers are excluded from analysis exports.

Research measurements are retained only for a documented continuing programme,
validation or reproducibility need. Annual reviews hold further use when overdue;
uploads do not reset them. Withdrawal removes live packages, blocks old
capabilities and creates tracked deletion tasks for every released copy. Consent
evidence and minimal anti-resurrection markers are handled separately. Encrypted
recovery copies expire within seven days and are purged earlier when withdrawals
affect them. Restore replays the independent deletion ledger and holds research.

## Risks and safeguards

| Risk to participants | Implemented safeguards and remaining exposure |
| --- | --- |
| Identification from dates and distinctive histories | Excluded direct identifiers, keyed card pseudonyms, controlled access and anonymous-summary publication. A recipient with auxiliary information could still identify patterns; encryption does not make data anonymous. |
| Unauthorized research, coercion or mistaken consent | Personal use independent of joining; concise disclosure, explicit adult/own-data/health-data consent, versioned server enrollment and per-package Send. Self-declaration cannot guarantee understanding or age. |
| Hosted compromise or accidental public disclosure | Client encryption, off-server private key, private schemas/RLS, restricted operator roles, MFA, verified TLS and source/package secret checks. Consent/linkage metadata and temporary authorization credentials remain service-accessible. |
| Operator-device or key compromise | OS-protected keys, restricted workspace access, registered copies and guarded jobs. A privileged or unlocked-device compromise can expose plaintext during analysis; device access and credential/key rotation remain operator responsibilities. |
| Continued use after withdrawal or Oura revocation | Independent scheduled checks, short fail-closed use leases, guarded process shutdown, separate deletion receipts, deadlines and backup ledger. Detection is periodic; the server cannot remotely erase an offline or uncontrolled computer. |
| Incomplete deletion or resurrection | Inventory before release; acknowledgment waits for actual copy-removal confirmations; encrypted backup purge, independent tombstones and restore replay. External holders require verified evidence and escalation. |
| Misleading findings or individual harm | Experimental-method notices, uncertainty/coverage labels, missing-data handling and observational interpretation. No diagnosis, treatment or consequential individual decisions. Publication requires a disclosure/identifiability review. |
| Unavailable rights requests or service abuse | Independent status/heartbeat/backup alerts, upload kill switch, separate deletion budget, bounded ingress/storage and project contact route. Infrastructure outages and shared-host failures can delay response; operator escalation remains necessary. |

The scope combines health-related, longitudinal and behavioural data with new
measurements. This assessment therefore addresses those risks directly rather
than relying on a small initial audience as an exemption from assessment.
After the controls above, the assessed residual risks are meaningful but not
judged likely to remain high for the described voluntary, operator-only launch.
That is a project assessment, not a supervisory-authority ruling. Do not open a
materially changed activity with unresolved high residual risk; apply Article 36
where its conditions are met.

## Services and operating responsibilities

Supabase's published DPA forms part of its service agreement, identifies its
processor role and includes international-transfer provisions. Record subsequent
term/subprocessor changes with this assessment. Cloudflare Pages hosts public
information only. Project contact email is routed through Cloudflare to Gmail;
correspondence is separate from measurements and no Google Workspace processor
agreement is claimed. Silentworker holds encrypted recovery archives and the
minimal deletion ledger; its private decryption key remains off-host.

The operator owns rights-request verification, annual retention decisions,
copy-deletion confirmations, incident assessment/response and review of public
outputs. Monitor failures reach the existing Silentworker health channel. A
suspected incident requires stopping affected use, preserving minimal evidence,
containing access and assessing the notification duties and deadlines under
Articles 33–34. Do not put measurements or credentials in support logs.

The initial limits remain 256 MiB of live package JSON, 128 MiB of new packages
per UTC day, 100 new packages/day and 8 MiB/request, with 80% alerts and independent
deletion capacity. They bound an initial public beta, not a promised participant
capacity or a billing cap. Oversized histories fail explicitly without truncation.
Review usage and access before raising limits or expanding recruitment; the
operator can close enrollment/uploads while existing deletion controls continue.

Review this assessment at least annually and before expanded scale, new
recipients/countries, new fields, interventions, identity collection, changed
provider terms, security incidents or material method/purpose changes. Existing
participant information and the project contact route provide a way to raise
concerns; no participant consultation study is claimed.

## Evidence and sources

[Hosted research rehearsal](research-e2e-2026-11-08.json),
[encryption](ENCRYPTION.md), [recovery](BACKEND_RECOVERY.md),
[research controls](RESEARCH_CONTROLS.md), [workspace](RESEARCH_WORKSPACE.md),
[Oura approval](OURA_APPLICATION_APPROVAL.json).

Legal sources reviewed 8 November 2026:
[GDPR Articles 5, 6, 9, 25, 30, 32–36 and 89](https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng),
[German supervisory authorities' DPIA list](https://www.datenschutzkonferenz-online.de/media/ah/20181017_ah_DSK_DSFA_Muss-Liste_Version_1.1_Deutsch.pdf),
[Supabase DPA](https://supabase.com/legal/customer-resources/data-processing-addendum).
