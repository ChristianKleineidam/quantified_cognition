# Desktop beta verification

Windows checks performed on 27 and 30 October 2026 with v0.17.2. The final
regression run passed **342 tests**, followed by **9 focused UI tests** after
the enlarged-header adjustment. Ruff passed. Oura, Google Health and research
uploads were never enabled; all health fixtures were synthetic local data.

## Environment and isolation

The runner used full AnkiQt applications and native Rust backends from
Anki/aqt **26.09.3** and **25.09.2**, installed in separate development Python
environments. These are desktop application tests, rather than mock main
windows. They do not replace a hands-on check of each official installer build.

Each run created a new base directory on M: with a unique Anki instance key,
a disposable profile, a pre-test collection backup and the packaged add-on
installed through Anki's `AddonManager.install`. No personal profile was opened.
AnkiWeb credentials were absent and automatic account sync was disabled.
Python networking was restricted to loopback; the successful application runs
recorded no attempted external connections. No provider account was connected,
no research package was sent and no backend configuration was changed.

The synthetic history contained 880 card histories, roughly 9,600 reviews and
198 study days, including 10 retained deleted-card histories. Health fixtures
covered 352 days with variable sleep phases and steps, missing deep-sleep values
on selected days, and separate main sleep and naps. Oura and Google caches were
populated locally through their storage APIs, without calling their online APIs.

## Results

| Check | Evidence |
| --- | --- |
| Fresh install | Packaged installer accepted the add-on on both Anki versions; all five Tools actions loaded. |
| Upgrade | A real v0.16.0 calculation-version-4 cache upgraded to version 5; the history remained readable and untracked prior exposure stayed unknown. |
| First-run choice | Yes, Not now and No were exercised before the first report; choosing Yes did not upload or expose results. |
| Later opt-in | Deferred consent followed by viewing then opt-in stayed classified as late. Early consent stayed early; legacy exposure stayed unknown. |
| Recall/formation | All four metrics rendered with numeric scores/counts, pending/completed follow-ups and insufficient-data labels. Uncertainty stayed unavailable when outcome-day support was insufficient. |
| Deleted histories | Agreed-preset histories appeared in the real report; disagreement/exclusion cases passed native regression tests. |
| Health/insights | Local Oura/Google histories, missing stages, main sleep and naps rendered independently. Fixed-total phase estimates and insufficient-data cases were visible. |
| Return visit | Reopening insights used the previous displayed baseline. Added/backfilled history, matured follow-ups, uncertainty changes and changed-method cases passed visit simulation/regression tests. |
| Daily CSV | Enabling the fixed path wrote a CSV readable by another interpreter. |
| Immediate close | A native final-card answer completed, the window closed on the next event, and a separate interpreter verified the final Anki day's answer in the CSV after Qt exit. Checked on both native Anki versions. |
| Locked CSV | A real Windows exclusive file handle prevented replacement. The previous complete file survived; retry succeeded after release without scanning or replaying FSRS. |
| Unavailable folder | A non-directory parent simulated an unavailable mount; the previous export survived and recovery published saved results without recalculation. |
| Interrupted publication | A separate writer was terminated after writing its complete temporary file but before replacement. The previous CSV stayed intact and a subsequent retry succeeded. |
| Appearance | Light/dark screens and 150% application text were rendered and inspected. Reports scroll while footer actions remain reachable. |

“Immediately after the answer” uses Anki's native answer-completion hook. An
answer button starts an asynchronous operation; unloading a collection directly
inside that still-pending operation is a different, invalid test sequence.
The runner closes on the next event after completion, without waiting for a
report or export. File-only retry and shutdown tests also exercise ordinary
review operations while workers are delayed.

## Fixes made

The asynchronously populated day selector clipped the final part of dates. It
now sizes itself when data arrives. Date editors and health/export controls
have clearer accessible names.

Memory and insights reports could become taller than the available window at
larger text sizes. Their content now scrolls, with footer actions kept outside
the scroll area. Insight headings use shorter lines and minimum readable column
widths; horizontal scrolling replaces truncated labels when necessary.

Google Health's screen incorrectly said it stored only daily aggregates. It now
states that sleep sessions and daily step totals are saved in the local profile.
The privacy draft names **JollyJoyJourney** as operator and uses its verified
registered business address and contact@jollyjoyjourney.com. Publication and
remaining study details stay explicit placeholders. The v0.17.3 and v0.17.4
documentation patches add the address, verified contact email and email-routing
disclosure without changing the desktop code tested here.

A desktop bug-report template and backup, disable and uninstall instructions
were added. No statistical calculations, provider permissions, sharing-notice
version or research package schema changed in this patch.

## Quantified Cognition rename, 2 November 2026

Version 0.18.0 updates the display name, all five Tools actions, dialog
titles, diagnostics and local package filename to Quantified Cognition.
The installer ID `quantified_self_memory`, profile directory
`quantified-self-memory` and OS credential lookup keys stay unchanged.
The release-ready AnkiWeb description is saved in `ANKIWEB_DESCRIPTION.txt`;
neither the add-on nor the description has been uploaded.

The regression suite passed **343 tests** and Ruff passed. An offline,
packaged upgrade from v0.17.4 to v0.18.0 in native AnkiQt **26.9.3**
passed all **14 walkthrough checks**, including the renamed menus, existing
results, consent timing, synthetic local Oura/Google insights, fixed CSV and
the final answer appearing after Qt exit. Installed manifest and add-on
metadata both show Quantified Cognition. The runner retries brief Windows
CSV read locks within its existing timeout; permanent failures still fail
the walkthrough. No personal profile was opened and the successful run
recorded no external requests. Oura, Google Health and research uploads
remain disabled; backend files, feature flags, metric/schema versions and
the sharing-notice hash are unchanged.

## Reproduce

Use a development environment with aqt installed and an existing packaged add-on:

```powershell
python tools/desktop_beta.py --root M:\CodexTemp\new-beta-profile --package dist/quantified-cognition-0.18.0.ankiaddon --upgrade-from dist/quantified-self-memory-0.17.4.ankiaddon --choice opt_in --health-fixture --review-close
```

The root must not already exist. The runner leaves screenshots and `result.json`
beside its synthetic profile and backup. Use `--choice deferred` or `--choice
declined`, `--empty --dark --scale 1.5`, or `--upgrade-from` with an existing
older package to exercise the other cases. The runner is a development utility,
not installed in the add-on. Do not point it at an existing personal profile.

```powershell
python -m pytest -q
python -m pytest -q tests/test_ui.py tests/test_windows_export.py
ruff check addon tests tools
```

## Remaining checks

macOS/Linux desktop, credential-store behaviour, official installer builds,
screen-reader use and independent user feedback still need direct testing.
Actual AnkiWeb synchronization was not performed without an account; native
synced-history edits and shutdown/export lifecycle regressions were tested
offline. Months of real-world follow-up cannot be replaced by the visit
simulations. These distinctions remain visible in the [beta checklist](BETA_CHECKLIST.md).

On 30 October the Supabase management API reported the project **INACTIVE**;
its public hostname did not resolve and SQL checks were unavailable. It was not
resumed. The last successful live checks on 26 October confirmed all three
feature flags false. The desktop tests made no backend changes and remained
fully offline. After an authorized restore, rerun the closed-state verifier
and operations report before any online pilot; the inactive project cannot
execute its own monitoring cron job.
