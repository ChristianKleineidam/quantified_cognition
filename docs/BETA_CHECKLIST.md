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
- [x] Backend tests for quotas, failure behaviour, RLS, ownership, duplicate
  receipts, deletion and stale writes. Hosted quota/alert tests roll back all
  synthetic changes; live probes confirm all three features are disabled.
- [x] Research sends require a preview and explicit submission; choosing Yes
  alone never uploads. Consent/exposure timing is preserved locally.

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
- [x] Inspect the v0.18.0 archive for source/config/docs only, no credentials,
  profiles, private benchmark files or personal health data. Its SHA-256 sidecar
  is saved alongside the archive in `dist/`.
- [ ] Repeat the walkthrough in official installer builds, with actual AnkiWeb
  sync on a permitted test account. Offline synced-history/lifecycle tests passed.
- [ ] Test macOS/Linux UI, OS credential storage, background shutdown and chosen
  export locations. Only Windows has been exercised on this development host.
- [ ] Test a screen reader and give a few Anki users the concise instructions;
  record confusing labels and revisit with their real accumulated follow-ups.

Evidence, reproducible commands and limits: [DESKTOP_BETA.md](DESKTOP_BETA.md).

## Before any online pilot — separate launch decision

- [ ] Publish the final [policy](PRIVACY.md), recipient/contact and study
  information for JollyJoyJourney; configure the displayed notice and matching version/hash.
- [ ] Complete [Oura approval](OURA_APPROVAL.md) and
  [Google Health requirements](GOOGLE_HEALTH.md), separately. Install only the
  relevant server credentials after approval. No real-account import was tested
  during this task.
- [ ] Verify approved-account authorization, refresh/revocation, corrections,
  pagination, naps/stages and steps without logging credentials or raw responses.
- [ ] Test quotas from separate database connections/function instances under
  representative load. Review ceilings for the intended pilot size, 429 retry
  UX, provider limits, request ingress controls and infrastructure usage alerts.
- [ ] Arrange operator alert delivery and independent uptime checks; prove an
  operator notices a stale cron heartbeat and a simulated outage. Private alert
  rows and function logs currently require inspection; no paging is configured.
- [ ] Confirm platform log retention/redaction/access, administrator MFA, backup
  restoration and deletion replay, research retention and extract deletion.
- [ ] Test explicit package preview/send, uncertain receipt retries, withdrawal
  and deletion with uploads disabled, including lost-credential recovery.
- [ ] Run the closed-state verifier and operations report immediately before
  review. Enable a feature only through a separately authorized launch; update
  the closed-state monitor's expectation then. Keep unrelated flags false.

Deployment evidence: [DEPLOYED.md](DEPLOYED.md). Desktop statistical readiness
does not complete provider approval, permitted research enrollment or an online
pilot. The current service remains closed while those checks are pending.
