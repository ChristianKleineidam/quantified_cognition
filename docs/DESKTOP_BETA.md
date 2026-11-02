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
python tools/desktop_beta.py --root M:\CodexTemp\new-beta-profile --package dist/quantified-cognition-0.18.1.ankiaddon --upgrade-from dist/quantified-self-memory-0.17.4.ankiaddon --choice opt_in --health-fixture --review-close
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

## Official builds and Linux, 2 November 2026

The official Windows **26.09.3 x64 MSI** was downloaded from Ankitects' release
and its signature verified as **Anki Software LLC**. Administrative extraction
to M: provided the actual official executable without changing the normal Anki
installation. The official Linux **26.09.3 x86-64** archive was checked against
GitHub's release-asset SHA-256:
`d70e01971a7aa190b189927573ee643f2525ff840eafaecc7a8f8cde505650b3`.
Its executable ran under Xvfb on Debian 13 on Silentworker. Both official builds
reported Python **3.13.15**, Anki/aqt **26.9.3** and Qt **6.11.0**.

Each platform passed all **14 checks** on a fresh v0.18.0 profile and again on
a dark-theme **v0.18.0-to-v0.18.1 upgrade**. The driver ran as a separate test
add-on inside the official application. The product package and official
runtime were unmodified. Reports, local synthetic health/nap/phase insights,
consent timing, return visits, fixed CSV and the native final answer/close
sequence passed. An independent development interpreter checked the final
answer in CSV after the official executable exited. Successful runs recorded
no application errors or blocked Python-network attempts. Anki's own Rust
update check can attempt networking; no AnkiWeb account or health provider was
connected during these automated runs.

Linux also passed the development-runtime walkthrough and real Secret Service
save/read/delete and profile-isolation checks. A new session bus, disposable home
and synthetic keyring were used, with no access to personal credentials. The
server test needed `libsecret-tools`, which was installed; missing Secret Service
remains an explicit connection error with no plaintext fallback. The virtual
display/keyring result does not establish macOS support or every Linux desktop
and keyring combination. Test processes had restricted CPU/memory and external
network access. No accessibility audit was completed; it was excluded at the
user's request and the screen-reader test was stopped.

The sync regression reproduced a stale-CSV bug: a sync completed after the day's
export could import history without requesting fresh analysis. **v0.18.1** adds
sync lifecycle handling. Existing automatic captures are cancelled before sync,
including full-download reopen of the same Collection object. Completion queues
a background input check; unchanged inputs reuse the saved FSRS result. Closing
sync arms the durable export handoff even without local answers. Failed CSV
publication still retries from saved results without scanning history again.
Native collection/Qt hook regressions cover these paths and the old-callback
race; they do not replace a live AnkiWeb round trip.

The final Windows regression run passed **373 tests** and Ruff passed. The
focused sync/daily/shutdown suite also passed **23 tests** with Anki **25.09.2**.
The v0.18.1 archive contains 49 source/config/documentation entries, preserves
the installer identity and saved AnkiWeb description, and excludes profiles,
credentials and the disposable test driver. Its SHA-256 sidecar is in `dist/`.
Oura, Google Health and research uploads remain disabled; statistical methods,
schema versions and research-notice text/hash are unchanged.

The reusable official-build driver is `tools/official_desktop_beta.py`:

```powershell
python tools/official_desktop_beta.py --root M:\CodexTemp\new-official-beta --package dist/quantified-cognition-0.18.1.ankiaddon --upgrade-from dist/quantified-cognition-0.18.0.ankiaddon --anki-exe M:\path\to\official\Anki.exe --dark
```

Use a new root every time. It creates only synthetic data and adds a development
driver in that disposable profile; the driver is never included in the product.
The existing development runner accepts `--platform xcb --timeout 240` for a
virtual-display Linux run. Screenshots, runtime identity and `result.json` remain
in the run directory, outside Git.

## Remaining checks

Version **0.18.2** is a documentation/package update: it includes the prepared
research programme, participant information, policy and operating plan. The
archive now has **52 entries**. The application source, statistical methods,
beta sharing-notice text/hash and disabled deployment settings are unchanged
from v0.18.1; this update does not open research enrollment. Research-document
links, source/archive identity, compilation, exclusions and Ruff were checked.

macOS desktop/Keychain and independent user feedback still need direct testing.
Actual AnkiWeb synchronization remains pending because there is no empty test
account. A separate Windows profile on M: is prepared for interactive sign-in;
it has no personal collection or copied account credentials. Native synced-history
edits and shutdown/export lifecycle regressions passed offline. Months of
real-world follow-up cannot be replaced by visit simulations. These distinctions
remain visible in the [beta checklist](BETA_CHECKLIST.md).

On 30 October the Supabase management API reported the project **INACTIVE**;
its public hostname did not resolve and SQL checks were unavailable. It was not
resumed. The last successful live checks on 26 October confirmed all three
feature flags false. The desktop tests made no backend changes and remained
fully offline. After an authorized restore, rerun the closed-state verifier
and operations report before any online pilot; the inactive project cannot
execute its own monitoring cron job.
