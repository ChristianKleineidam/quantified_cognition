# Quantified Cognition 0.23.0 release candidate 1

Prepared **8 November 2026**, tag `v0.23.0-rc.1`. This candidate includes overall,
short-term and long-term recall, the simpler full-history report and automatic
CSV controls, encrypted pseudonymous review snapshots, and independent Oura
research authorization and withdrawal handling. Automatic withdrawal receipts
remain usable after the upload credential is invalidated.

Oura connections are enabled. Google Health and research enrollment/uploads
remain disabled. This candidate does not activate research or publish the add-on
on AnkiWeb. macOS remains unverified; users can report issues after launch.

The release procedure reviews all pending source and document changes, scans
the candidate and reachable Git history for credentials and private benchmark
data, commits the reviewed files, and runs that exact commit through
[Silentworker](SILENTWORKER.md). Tagging follows a successful run. The final
package is built from the tag's Git archive, with fixed ZIP metadata. A separate
release record contains the commit, test result, source-file hashes and package
checksum without changing the tested commit.

## Privacy publication gate

Reachable history was cleaned earlier. The latest check found that GitHub still
returns private author metadata from an old rewritten, unreferenced commit.
Keep the repository private until the retained GitHub objects/cache are removed
or a clean publication route is chosen. Passing a local source/history scan does
not clear GitHub's retained objects. The support-request draft and affected
commit references stay outside the repository.

The secret scan has three reviewed historical false positives: two public
migration checksums and one documentation paragraph. `.gitleaksignore` names
only their exact commit/file/line fingerprints; it does not exclude entire files
or disable credential rules. Private profiles, health data, benchmarks, generated
packages, operator keys and local reports remain ignored.
