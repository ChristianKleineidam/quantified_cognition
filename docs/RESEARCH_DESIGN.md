# Quantified Cognition research design

Programme version 1, **2 November 2026**. Operator and research lead:
**Christian Kleineidam, trading as JollyJoyJourney**. This is the initial
scientific design, not evidence of ethics approval, provider permission or open
enrollment. The [participant information](RESEARCH_INFORMATION.md),
[privacy policy](PRIVACY.md) and [operating plan](RESEARCH_OPERATIONS.md) define
its participant commitments and activation requirements.

## Purpose and research flexibility

The programme studies how learning and recall vary over time, their relationships
with sleep, physical activity and study patterns, and the validity of measures
derived from repeated learning activity. Questions include persistence of
learning, daily recall, sleep stages and naps, activity, differences across
histories, missing follow-ups, measurement reliability and participation bias.
These examples are not an exhaustive list of hypotheses.

This first design is longitudinal and observational: retrospective histories
plus later voluntary contributions. It does not assign treatments or ask users
to change behavior. A future intervention study in this research area needs its
own protocol, appropriate approval and participant information before it begins.
An existing observational consent must not be treated as permission to assign an
intervention or collect undisclosed exposure data.

Study-specific questions, models, thresholds and sensitivity analyses are
recorded in dated analysis plans. A confirmatory analysis distinguishes decisions
made before the relevant results were examined from exploratory changes.
Exploration and method improvements within the programme are allowed and
documented; the public policy does not freeze one model or promise a fixed set
of papers. New data categories, unrelated purposes, materially different access
or changed participant burdens require a scope/consent review first.

## Participants and contributions

Enroll adults aged 18 or over who choose to contribute their own history and can
give informed consent. Use an age/eligibility attestation, not a birth date or
identity-document upload by default. Requirements imposed by the review board
or provider must be explained and implemented before enrollment. Anki-only
contributors are eligible; missing health history is handled per analysis.

There is no fixed sample size, required contribution frequency or daily visit.
Existing history supplies the starting data and new contributions can extend
follow-up. Recruitment can use the project's normal distribution channels after
approval. No individual invitations, account enrollment or provider applications
have been performed as part of preparing these documents.

A random profile participant ID links a contribution sequence. An upload
capability is a separate credential. IDs do not verify distinct human beings:
one person can have multiple profiles and a copied profile can reuse an ID.
Ask participants to use one contribution profile, contribute only their own
data, report sample sizes as profiles when unique people cannot be established,
and describe this limitation. Do not identify people by linking external data.

## Data and outcome definitions

The research dataset consists of the disclosed daily package, currently package
schema 4, plus a separate minimal enrollment/consent record. It contains no raw
cards, individual reviews or review-pair records. Data categories and exclusions
are specified in the policy; the package allowlist in the software remains
authoritative for what can actually be transmitted.

Daily recall is observed success minus FSRS-predicted success for each card's
first attempt on an Anki day, averaged and expressed in percentage points.
Again counts as failure; Hard, Good and Easy count as success. Formation assigns
the outcome at the first attempt on the card's next reviewed Anki day to the
earlier study day's last attempt, against the probability reconstructed after
that study day and the actual elapsed delay. Report overall formation and the
subgroups with delays up to 30 days and over 30 days separately. Pending later
reviews are not failures or zero scores.

Current FSRS settings are applied to older history, including eligible SM-2-era
logs. Retained deleted-card histories are included only when reconstruction
settings agree across current presets; otherwise they are excluded with a
reason. Dates follow the configured Anki boundary. Method/version, settings
fingerprint and data-quality metadata accompany the scores. A fingerprint is
not the parameter vector and does not permit central FSRS refitting.

These are model-relative learning and recall estimates, not a direct measure of
general intelligence or biological encoding. Current FSRS parameters can have
been fitted using later outcomes, so historical estimates are retrospective,
not untouched out-of-sample predictions. Later testing conditions, scheduling
and which cards get reviewed can also affect formation. Review-pair identities
and complete later-outcome conditions cannot be reconstructed from the shared
daily package; analyses must acknowledge that limitation.

## Initial analysis framework

Use study/review days nested within contribution profiles. Avoid treating daily
rows, card counts or repeated uploads as independent participants. Primary
pooled associations estimate changes within profiles, with profile intercepts
and appropriate allowance for differences in slopes and precision. Evaluate
between-profile differences separately and do not interpret them as within-
profile effects. Use participant-balanced estimates as a reference and report
the effect of alternative weighting by days or measurement precision.

Account for calendar patterns, long-term changes, study volume, outcome-method
versions and provider differences where the available data support it. Additional
unmeasured factors such as illness, motivation, medication and changes in card
content remain possible confounders. The current package does not collect
intervention or supplement exposure, so it cannot establish their effects.

For recall, preceding main sleep is temporally relevant; subsequent sleep is
not a recall exposure. For formation, examine preceding and following main
sleep separately. Match using the software's actual-session rules, with naps
kept separate. Consider nap occurrence, duration, phases and proximity to
studying. Activity uses the preceding day for recall and the study day for
formation in the initial framework; alternative lags must be identified as such.

Compare light, deep and REM phases on the same eligible days within an
outcome/timing comparison. Account for total sleep and express phase comparisons
as substitutions, such as ten additional minutes of deep sleep replacing light
sleep at fixed total sleep and remaining phase. A positive substitution estimate
does not prove one stage is biologically more important. Other suitable
compositional or nonlinear methods can be explored within the same purpose.

Keep Oura and Google measurements separate, preserve missing versus zero, and
account for sleep-type, stage availability, algorithm changes, overlaps and
matching status. Google classic sleep does not supply staged sleep and
whole-minute stage summaries are not second-precision measurements. Compare
results across providers only with explicit harmonization and sensitivity checks.

Report formation completeness and distinguish pending from excluded outcomes.
The initial ready-cohort reference is study days at least 30 days old with at
least 80% evaluated follow-ups, matching the current personal-insights default.
It is not a promise that all such days are mature or unbiased. Examine other
coverage thresholds, observation windows and missingness patterns, including
long-term outcomes that take longer to arrive. Never select days by a favorable
score or replace pending outcomes with failure.

Daily uncertainty is conditional on the observed history and fitted FSRS model.
Where enough independent profiles exist, use profile-level resampling or robust
inference to allow dependence across their days. Assess common calendar effects
and sensitivity to longer temporal dependence. Within-profile estimates need
appropriate time dependence and sufficient support. The package lacks review-
pair/outcome-day membership, so exact later-outcome clustering cannot be rebuilt
centrally. An outcome-day count alone does not repair that limitation.

Declare primary outcomes and contrasts for each confirmatory analysis, address
multiple comparisons, report effect sizes and intervals, and label exploratory
results. Use simulation-based feasibility/power checks with the intended
dependence and missingness rather than a universal day-count cutoff. An
insufficient-data result remains valid; no publication or recommendation should
depend on finding a statistically favorable association.

## Selection, versions and reproducibility

Keep three concepts separate: the first sharing preference, first opportunity
to see results, and completed approved study consent. Existing CSV fields named
research_consent refer to the app's versioned sharing-choice audit. They are
not independent proof that the new enrollment requirements have been completed.
Record the actual study-information/consent version, time and required signature
in the separate enrollment record before permitting research access.

Analyze early preference, later preference and unknown exposure separately.
For any analysis of consent before viewing, use the actual approved consent
record as well as the exposure audit. Preserve original timing when obtaining
new consent; never rewrite a later consent as an earlier one. The audit cannot
prove that a person read results or had never seen related information elsewhere.
Contributors alone provide no denominator for all installations or noncontributors;
do not claim to have ruled out selection effects or upload data about decliners.

Keep immutable received-package versions while retention permits. For a given
analysis, select one documented compatible snapshot per profile/day, normally
the latest received before the analysis freeze. Deduplicate retries and repeated
overlapping histories. Record source versions, receipt cutoff and code revision;
never count successive snapshots as extra participants or merge incompatible
scoring methods without justification. Receipt time, not a client-supplied clock,
governs version provenance and analysis inclusion cutoffs, and any applicable
provider/legal deadlines. There is no fixed programme retention expiry;
continuing need, review and deletion rules are specified in the operating plan.

Publish methods, limitations, relevant funding and conflicts, and suitably
anonymous results. Share source code and synthetic reproductions openly.
Controlled replication with pseudonymous data follows the same collaborator
agreement and provider permissions as other research access. Removing IDs alone
does not make longitudinal daily data suitable for public release.

## Why the consent scope has this shape

The European Commission describes consent to a research area without requiring
every future question to be fixed, while retaining informed and specific choice.
The [Commission's research-consent guidance](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/legal-grounds-processing-data_en)
and [EDPB consent guidance, section 7.2](https://www.edpb.europa.eu/system/files/documents/files/file1/edpb_guidelines_202005_consent_en.pdf)
support defining a meaningful area with safeguards. They do not authorize
unrelated use merely by calling it research. On this review date, the EDPB's
2026 scientific-research guidance is listed as public consultation closed;
do not describe that draft as a final adopted legal rule.

Google's separate [health research policy](https://developers.google.com/health/policies/health-api-user-data-and-research-policy)
requires its own intake, review-board approval/waiver, eligibility and consent
arrangements. [Oura's agreement](https://cloud.ouraring.com/legal/api-agreement)
also governs storage, onward access and deletion. The operating plan tracks
these permissions separately from the scientific design and GDPR consent.
