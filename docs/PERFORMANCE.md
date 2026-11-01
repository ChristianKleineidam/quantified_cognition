# Performance verification

## Deleted-card histories in 0.16.0

The benchmark collection contains a substantial history of deleted cards. Its
presets disagree on active FSRS parameters and ignored-history cutoffs. Method
v4 excludes those logs under the agreement rule. Scored values and review
anchors match the previous version exactly. Actual-settings runs took 6.9–9.0 s for
initial analysis, about 1 s for the final unchanged scan, and 0.054–0.056 s for
CSV replacement. No source presets were changed.

A separate disposable test copy deliberately assigned the first preset's FSRS
reconstruction settings to every preset. Including deleted histories increased
the work the analysis performed. Preview, reset and unmodelled-state exclusions
still apply within these histories. A missing next review remains pending.

| Matching-settings test copy, without profiler | Time |
| --- | ---: |
| Initial full analysis, fingerprint/copy/save included | 25.33 s |
| Full analysis after one native Good answer on the copy | 24.85 s |
| Unchanged input scan and report reuse | 2.32 s |
| Full daily CSV replacement | 0.094 s |

The final matching-settings process peaked at 273 MiB working set, compared
with 167 MiB for the actual-settings process. These are process lifetime peaks
across initial analysis, the simulated answer, export and reuse checks. The
benchmark uses only standard-library platform memory counters. An earlier
unprofiled matching run took 26.62 s initially and 24.01 s after an answer.

All work after snapshot capture remains on the independent worker. These are
individual Windows observations with other local jobs running. The inclusion
case does more work because it now uses most of the retained history.
Native decay is reused only when all presets agree; exact report digests before
and after that optimization match across all daily metrics and review anchors.
Main-database and nonempty-WAL hashes confirm unchanged source contents.

Reconstruction creates one blank note and card placeholders, retaining original
card/review IDs only on the disposable copy. One transaction batches all writes;
cancellation rolls back placeholders and review truncation together. Native
tests compare deleted histories with the same histories before card deletion,
including resets, previews, truncated logs, changed current card states, and
missing original notes/note types. They also exercise all three disagreement
reasons, explicit/default parameter agreement, cache invalidation by older log
changes, conflict resolution and same-day calculation-method upgrades.

Use `--common-presets` with the benchmark below to exercise this hypothetical
branch only on its copy. JSON clearly labels `common_presets_test_copy=true`.
Never use that result as if it came from the unchanged personal presets. Method
v4 adds local included/skipped provenance and invalidates old analysis once;
the fixed daily CSV schema and research upload contract remain unchanged.
The final full suite passed 313 tests on Anki 26.09.3; all 19 deleted-history
tests also passed on Anki 25.09. Both benchmark scenarios passed automatic
metric/anchor/count comparisons, and all private temporary copies were removed.

## Private-history benchmark, 26 October 2026

Anki 26.09.3 on Windows, using a large local collection with FSRS enabled.
Testing used disposable copies, with no
network access, health-account connection or changes to the source collection
contents. The copies and generated daily CSV were removed after each run.
Only timing/count summaries remain in the ignored `.reference` directory.

| Operation | Before | After |
| --- | ---: | ---: |
| First full analysis, including fingerprint/copy/save, with cProfile | 341.0 s | 13.5 s |
| Unchanged input check and saved-report reuse, without cProfile | 3.24 s | 1.11 s |
| Full analysis after one native Good answer on a copy, without cProfile | Not measured | 10.5 s |
| Next unchanged check after that answer | Not measured | 1.19 s |
| Load saved scores | — | 0.043 s |
| Write and atomically replace the full daily CSV | — | 0.51 s |
| Capture a consistent native collection snapshot | 1.53 s | 1.95 s |

After the regression jobs finished, a final unprofiled run measured 7.94 s for
the initial analysis, 7.00 s after one answer, 0.88 s for unchanged-history
reuse, 0.036 s for loading saved scores, 0.051 s for CSV replacement and 1.18 s
for native snapshot capture. Its automatic comparison against the original
report passed for metrics/anchors, history counts and exclusions.

The before/after reports have identical daily metric values, uncertainty,
counts and first/last review anchors, verified by their serialized SHA-256
digests. Report timestamps are intentionally excluded from this comparison.
Excluded-preview and deleted-card counts also match. Main-database and nonempty
WAL hashes confirm unchanged source contents. Read-only SQLite can create empty
WAL/shared-memory coordination files; those contain no new review data.

These are individual wall-clock observations, not controlled hardware medians.
Other local tests were running, filesystem caches differ between runs, and
cProfile adds overhead. An earlier profiled original run took 286.6 s, while
batched runs took 13.5–15.1 s. The result establishes the large reduction in
per-card disk synchronization, rather than a universal speed guarantee.
CSV measurements here have no connected health history.

## Changes in 0.15.1

Native card-info calls can write a last-review cache. Reconstruction also
truncates review logs on the copy to reconstruct older reset episodes. Previously
each card's incidental writes could require a separate durable disk flush. The
whole reconstruction now uses one transaction on the disposable copy. Failure
or cancellation rolls it back; the original collection is never in that
transaction. Reconstruction still runs on the independent worker.

The fingerprint scans every surviving card's review, including older edits,
insertions and deletions synced from other devices. For deleted cards, only the
reported excluded-answer count affects output. Hashing their unused historical
dates/intervals was unnecessary. Changes to that count, restoration of a card,
or changes to surviving-card history still invalidate saved results. This is
particularly useful when a collection retains many deleted-card review logs.
The updated fingerprint causes one freshness recalculation after upgrading;
the calculation method, scores, uncertainty and database/CSV schemas are unchanged.

The benchmark's review simulation uses Anki's scheduler on the test copy. It
checks that one new answer causes recalculation and the next unchanged check
reuses the result. Native regression tests cover cancellation rollback, reset
episodes, historical sync edits, changed settings, exports, normal reviewing
and shutdown recovery. The minimum supported Anki version is checked separately.

## Reproduce locally

Close Anki first, and use a disk with room for several collection copies.
Install the project's development requirements. In PowerShell:

```powershell
$env:TEMP = 'M:\CodexTemp\anki-quantified-self'
$env:TMP = $env:TEMP
$collectionPath = Join-Path $env:APPDATA 'Anki2\User 1\collection.anki2'
python tools/benchmark_history.py $collectionPath --work-root $env:TEMP --output .reference/history-before.json --profile --autocommit
python tools/benchmark_history.py $collectionPath --work-root $env:TEMP --output .reference/history-after.json --profile --answer-card --compare .reference/history-before.json
```

Replace the profile path as needed. `--autocommit` reproduces the old per-card
write behavior on a disposable copy; it retains the current fingerprint scan.
`--profile` profiles the initial analysis only. Omit it for ordinary timings.
`--answer-card` answers one available card Good only on the copy. If FSRS is
disabled, `--enable-fsrs` enables it only on that copy; the source stays unchanged.
Compare on the same Anki day with unchanged source history/settings.

The tool opens the source with SQLite `mode=ro` and takes a consistent backup.
Only copies are opened with Anki. Its JSON contains counts, timing, date bounds
and output digests, with no question/answer text, card identifiers, parameters
or daily health measurements. Keep these reports local unless intentionally
sharing them. Raw collections, profiling files and daily CSVs remain inside
the temporary benchmark directory and are cleaned up even on failure.

## Remaining performance limits

An unchanged automatic day that is already complete needs no snapshot or scan.
A due freshness check still requires a consistent copy and input scan. Snapshot
capture briefly uses Anki's serialized collection queue; heavy reconstruction,
result saving and CSV export run independently. It is not an instantaneous copy.

Changed surviving-card history still rebuilds the full report. Per-card
incremental reconstruction is a possible later optimization, but must preserve
reset handling, current-parameter replay, historical edits/deletions and daily
formation aggregation. It was unnecessary to obtain this improvement.

This test does not establish performance for million-review surviving-card
histories, macOS/Linux credential flows, or actual Oura/Google insight histories.
