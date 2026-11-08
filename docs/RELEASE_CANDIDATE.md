# Quantified Cognition 0.23.0 release

Prepared 8 November 2026 for the public desktop beta with Oura and voluntary
research participation. Google Health stays disabled. The original sharing
preference and before-results audit are preserved; explicit enrollment and each
package's Send remain separate choices.

The final consent bundle is protocol v14, notice v12. The launch adds configurable
research-monitor expectations, preserves the upload kill switch, and retains
independent Oura authorization, encrypted measurements, tracked withdrawals and
actual copy-deletion acknowledgment. Source migrations prepare documents closed;
operator activation is a separate recorded operation.

Run the exact final commit through [Silentworker](SILENTWORKER.md), then tag it
`v0.23.0` and build from the tag's Git archive. Store the tested commit, run,
source hashes, package checksum and clean-profile installation result in the
release provenance artifact. Do not move an existing tag or replace a published
asset with a different build under the same checksum.

Before publication, scan committed source, reachable history and the package for
credentials and private benchmark/profile data. The operator has accepted that
his email address is already public; it is not a publication blocker. Private
profiles, health data, benchmarks, generated packages and operator keys remain
ignored. The reviewed historical secret-scan false positives are restricted to
exact fingerprints in `.gitleaksignore`.

The earlier `v0.23.0-rc.1` tag remains an immutable historical candidate. Its
checks do not establish that a newer untagged package was tested. macOS testing
is deferred by the operator; report issues on GitHub after launch.
