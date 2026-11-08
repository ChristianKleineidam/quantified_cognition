# Public launch and emergency closure

The operator's 8 November 2026 instruction to continue the publication plan
authorizes publication with voluntary research available at the same launch.
This record is an operator release decision, not an external ethics approval.
The Oura application approval and ten-user-cap removal are already recorded in
[the provider evidence](OURA_APPLICATION_APPROVAL.json). No external research
recipient is authorized at launch. Google Health remains out of scope.

The current release is 0.23.0, protocol v14 and notice v12. The new protocol
removes obsolete closed-state wording from the information and policy. It changes
no uploaded fields, research purpose, collaborator categories, retention criteria,
consent action or original before-results sharing audit. Historical protocols
stay closed. Review the [data-protection assessment](DATA_PROTECTION_ASSESSMENT.md).

## Release acceptance

Use the exact committed revision that passes Silentworker, tag it `v0.23.0`, build
from the tag archive and verify a clean synthetic-profile installation. The
package includes the current information, policy, description and linked
workspace/rehearsal documents. Save source hashes, tested revision, package
checksum and install result in the release provenance outside the tested commit.
Source, reachable history and package must contain no credentials, private
benchmark files or participant measurements. Public email metadata is not an
operator publication blocker. macOS testing is deferred; Google and an
accessibility audit are not added to this release's scope.

Apply the launch-monitor migration and final document seed while the service
remains closed. Deploy the matching Silentworker monitor/recovery modules.
Publish the matching static Pages build using its reviewed public-file allowlist.
No new paid plan, Cloudflare analytics, GitHub Actions or research collaborator
is enabled by this release.

The existing capacity limits are retained for the initial beta: 256 MiB live
package JSON, 128 MiB new package data/day, 100 new packages/day and 8 MiB per
request. The 80% alerts, 1 GiB/seven-day encrypted recovery budget and independent
deletion allowance remain enabled. These caps do not promise a participant count
or bound all platform charges. Review measured usage before raising them;
oversized full histories fail explicitly rather than being truncated.

## Activate

As the authorized operator, apply `supabase/launch-research.sql`. It checks the
exact final bundle/notice and opens only v14 with Oura permission, Google false.
It also updates monitor expectations. Then set the server flag using the official
Supabase secret store:

```powershell
supabase secrets set RESEARCH_ENABLED=true --project-ref wfjlzecckihsgcvauqwt
```

Run the current verifier with `--expect-oura-enabled --expect-research-enabled`.
It checks live public information against the release bundle, anonymous write
denial and closed Google routes without enrolling or uploading measurements.
Confirm both monitor jobs are healthy. Publish the source repository and the
tagged package/checksum/provenance, and submit the same archive and saved
description to AnkiWeb using the operator's eligible publishing account.

## Emergency upload closure

```powershell
supabase secrets set RESEARCH_ENABLED=false --project-ref wfjlzecckihsgcvauqwt
```

Apply `supabase/close-research.sql` to close enrollment and update monitoring.
Run the verifier with only `--expect-oura-enabled`. This blocks new enrollment
and submissions while deletion receipts, independent Oura checks and existing
copy-deletion duties continue. It does not by itself withdraw existing consent
or erase research. If an incident requires stopping existing research use too,
hold affected protocols/copies or revoke protocol approval through the operator
governance procedure. Do not suppress alerts by changing expectations when the
actual service state is unknown.

Reopening requires checking the matching release documents and reason for the
closure before rerunning the launch script and enabling the flag. Restores
clear the research expectation and close/hold research by default.
