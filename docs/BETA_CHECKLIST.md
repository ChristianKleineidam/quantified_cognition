# Beta checklist

Current target: a desktop beta with local Anki analysis and CSV export.
**Oura, Google Health and research uploads stay disabled.** Installation, testing
and ordinary deployment must not enable them. Checked items have evidence;
unchecked items remain work, not implied release approval.

## Completed

- [x] Recall and three formation metrics, completeness, uncertainty and
  insufficient-data labels; phase comparisons and return-visit summaries.
- [x] Statistical simulations and private-history performance checks documented
  in [validation](STATISTICAL_VALIDATION.md) and [performance](PERFORMANCE.md).
- [x] Background calculations and automatic CSV retry/shutdown tests, including
  closing immediately after the final review.
- [x] Hosted request/storage quotas, private monitoring and five-minute closed
  feature checks. See [backend operations](BACKEND_OPERATIONS.md).
- [x] Exercise independent native PostgreSQL connections with synthetic data:
  11 deterministic race tests passed on 4 November. Fix and deploy the batch
  deadline-maintenance/withdrawal deadlock; 42 existing backend tests also pass.
- [x] Require native concurrency and encrypted-recovery/operator rehearsals in
  Silentworker's isolated executor. The staged native run passed 15 tests;
  missing native tools fail rather than silently omitting coverage.
- [x] Backend tests for quotas, failure behaviour, RLS, ownership, duplicate
  receipts, deletion and stale writes. Hosted quota/alert tests roll back all
  synthetic changes; live probes confirm all three features are disabled.
- [x] Research sends require a preview and explicit submission; choosing Yes
  alone never uploads. Consent/exposure timing is preserved locally.
- [x] Implement separate enrollment, frozen saveable information, explicit
  consent linked to the participant ID, server-side enrollment checks, retention reviews,
  tracked extract/collaborator-copy deletion and differentiated deletion receipts.
  Synthetic Qt/HTTP/PostgreSQL tests verify the source implementation; see
  [RESEARCH_CONTROLS.md](RESEARCH_CONTROLS.md). The closed backend is deployed and
  verified; provider and study approvals remain outstanding.

## Before distributing the desktop beta

- [x] Install the packaged add-on in backed-up, disposable full AnkiQt profiles
  on 25.09.2 and 26.09.3. Check fresh install and v0.16.0 cache upgrade.
- [x] Walk through Yes / Not now / No before first results; later opt-in,
  unknown prior exposure, personal insight viewing and CSV exposure timing.
- [x] Review and close Anki immediately after the native final answer. Confirm a
  second program can read the newly replaced CSV after the detached worker
  finishes. Repeat with a locked CSV, unavailable folder and interrupted export.
- [x] Inspect synthetic long-history results for pending and matured short/long formation,
  deleted cards with agreeing/conflicting presets, missing stages and naps.
  Reopen visits and test months-later changes through snapshot simulations.
- [x] Review Windows light/dark appearance, enlarged text, scrolling and accessible
  control names; fix clipped dates and insight headings.
- [x] Provide an issue template for versions and sanitized errors, plus install,
  disable, uninstall and backup instructions.
- [x] Inspect the v0.18.1 archive for source/config/docs only, no credentials,
  profiles, private benchmark files or personal health data. Its SHA-256 sidecar
  is saved alongside the archive in `dist/`.
- [x] Run the signed official Windows 26.09.3 MSI payload and checksum-verified
  official Linux 26.09.3 package in disposable profiles. Fresh v0.18.0 and dark
  v0.18.0-to-v0.18.1 upgrades each passed 14 checks on both platforms, including
  final-answer CSV publication after the official executable exits. The Windows
  package was extracted on M:, leaving the normal installation untouched.
- [x] Exercise Linux UI under Xvfb, fixed CSV, background shutdown and a private
  synthetic Secret Service keyring (save/read/delete and profile isolation).
- [x] Fix sync-completion refresh after an earlier daily export; check unchanged
  history reuse, cancellation of older captures, closing-sync durability and
  saved-report-only CSV retries using native Anki collections and real Qt hooks.
- [ ] Perform actual AnkiWeb synchronization using an empty test account. No
  test account is available yet; the disposable Windows sync profile is prepared.
  Hook-level synced-history tests do not establish cloud authentication/transport.
- [ ] Test macOS UI, Keychain storage, background shutdown and export locations
  on a Mac. No macOS test environment is available in this session.
- [ ] Give a few Anki users the concise instructions; record confusing labels
  and revisit with their real accumulated follow-ups. No invitations were sent.

An accessibility audit is excluded from this pass at the user's request.

Evidence, reproducible commands and limits: [DESKTOP_BETA.md](DESKTOP_BETA.md).

## Before any online pilot — separate launch decision

- [x] Prepare the programme design, participant information, privacy text and
  operational implementation plan together. See [RESEARCH_DESIGN.md](RESEARCH_DESIGN.md)
  and [RESEARCH_OPERATIONS.md](RESEARCH_OPERATIONS.md). Publication and activation
  controls below remain outstanding; prepared text is not evidence of deployment.
- [x] Include the prepared research documents in the documentation-only v0.18.2
  package. Inspect its 52 entries, internal research-document links, unchanged
  application code and unchanged beta sharing notice/disabled deployment state.
- [x] Publish the current [policy](PRIVACY.md), recipient/contact and study
  information for JollyJoyJourney; configure the displayed notice and matching version/hash.
- [x] Deploy/verify the closed enrollment/lifecycle backend and notice v7.
  Function 11, all 13 migrations, disabled HTTP routes, anonymous denial,
  quota responses and both scheduled maintenance jobs were verified on 4 November.
- [ ] Complete [Oura approval](OURA_APPROVAL.md) and
  [Google Health requirements](GOOGLE_HEALTH.md), separately. Install only the
  relevant server credentials after approval. No real-account import was tested
  during this task.
- [ ] Verify approved-account authorization, refresh/revocation, corrections,
  pagination, naps/stages and steps without logging credentials or raw responses.
- [ ] Test deployed function instances under representative pilot load. The local
  connection races above do not establish cloud throughput. Review ceilings for
  the intended pilot size, 429 retry
  UX, provider limits, request ingress controls and infrastructure usage alerts.
- [x] Install independent status, heartbeat, backup and deletion-ledger checks
  through the existing Silentworker/Home Assistant health channel. Preserve the
  notification target and suppress repeated unchanged failures.
- [x] Trigger named public-status outage drills and confirm the existing health
  notification automation ran. Keep unrelated host failures visible and restore
  the normal Quantified Cognition monitor after the drills.
- [x] Rehearse encrypted recovery and deletion replay, retention holds, extract
  deletion and verified operator withdrawal. Verify the initial encrypted copy
  with the off-host key; confirm administrator MFA with the operator and review
  log settings/retention. See [BACKEND_RECOVERY.md](BACKEND_RECOVERY.md).
- [ ] Confirm acceptance of the implemented participant-ID consent and eligibility
  flow and incorporate any required review/provider changes before research opens.
- [ ] Test explicit package preview/send, uncertain receipt retries, withdrawal
  and deletion with uploads disabled, including lost-credential recovery.
- [ ] Run the closed-state verifier and operations report immediately before
  review. Enable a feature only through a separately authorized launch; update
  the closed-state monitor's expectation then. Keep unrelated flags false.

Deployment evidence: [DEPLOYED.md](DEPLOYED.md). Desktop statistical readiness
does not complete provider approval, permitted research enrollment or an online
pilot. The current service remains closed while those checks are pending.
