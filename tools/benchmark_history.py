"""Benchmark a private read-only collection copy; emit timings/counts, never card text.

Raw copies and profiles belong outside the repository. The source is opened only
by SQLite in read-only mode, never by Anki's backend. Anki works on disposable
copies because native history reconstruction can write its internal caches.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import pstats
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def file_hash(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(source):
    # A read-only WAL connection can create empty coordination sidecars. An
    # absent and empty WAL both contain no changes to the database contents.
    wal = Path(str(source) + "-wal")
    return {
        "main": file_hash(source),
        "wal": file_hash(wal) if wal.exists() and wal.stat().st_size else None,
    }


def readonly_copy(source, destination):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(destination)) as copy:
            live.backup(copy)


def counts(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return {
            "cards": connection.execute("select count(*) from cards").fetchone()[0],
            "reviews": connection.execute("select count(*) from revlog").fetchone()[0],
            "reviewed_cards": connection.execute(
                "select count(distinct cid) from revlog where cid in (select id from cards)"
            ).fetchone()[0],
            "review_types": {
                str(kind): count
                for kind, count in connection.execute(
                    "select type, count(*) from revlog group by type"
                )
            },
        }


def timed(name, function, result):
    start = time.perf_counter()
    value = function()
    result["seconds"][name] = round(time.perf_counter() - start, 6)
    print(json.dumps({"phase": name, "seconds": result["seconds"][name]}), flush=True)
    return value


def report_hash(report):
    value = {"days": [asdict(day) for day in report.days], "study_windows": report.study_windows}
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def peak_process_memory():
    if os.name != "nt":
        from resource import RUSAGE_SELF, getrusage

        return getrusage(RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    query.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    query.restype = wintypes.BOOL
    if query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return counters.peak
    return None


def benchmark(
    source,
    work,
    profile=False,
    enable_fsrs=False,
    autocommit=False,
    answer_card=False,
    common_presets=False,
):
    from anki.buildinfo import version
    from anki.collection import Collection

    from addon import backend
    from addon.export import export_csv_atomic
    from addon.storage import database_path, load_report, update_results

    result = {
        "anki_version": version,
        "seconds": {},
        "source_bytes": source.stat().st_size,
        "mode": "old_autocommit" if autocommit else "batched_snapshot",
        "common_presets_test_copy": common_presets,
    }
    before = source_hashes(source)
    frozen = work / "frozen.anki2"
    timed("readonly_source_backup", lambda: readonly_copy(source, frozen), result)
    result["history"] = counts(frozen)
    print(json.dumps({"history": result["history"]}), flush=True)
    candidate = work / "profile" / "collection.anki2"
    candidate.parent.mkdir()
    shutil.copyfile(frozen, candidate)
    collection = timed("open_copy", lambda: Collection(str(candidate)), result)
    original_reconstruction = backend._build_snapshot_report
    try:
        if autocommit:
            # Reproduce the pre-0.15.1 hot path for like-for-like timing/digest
            # comparisons. This switch exists only in the developer benchmark.
            backend._build_snapshot_report = backend._reconstruct_snapshot_report
        fsrs = collection.decks.get_deck_configs_for_update(1).fsrs
        result["source_fsrs_enabled"] = fsrs
        if not fsrs:
            if not enable_fsrs:
                raise RuntimeError(
                    "FSRS is disabled. Use --enable-fsrs to enable only the test copy."
                )
            collection.set_config("fsrs", True)
        if common_presets:
            # Deliberate hypothetical test, never a change to the source. Keep
            # names and scheduling preferences; copy only reconstruction inputs.
            from copy import deepcopy

            presets = collection.decks.all_config()
            common = min(presets, key=lambda item: item["id"])
            for preset in presets:
                for key in (
                    "fsrsWeights",
                    "fsrsParams5",
                    "fsrsParams6",
                    "sm2Retention",
                    "ignoreRevlogsBeforeDate",
                ):
                    preset[key] = deepcopy(common[key])
                collection.decks.update_config(preset)
        profiler = cProfile.Profile()
        if profile:
            profiler.enable()
        first = timed("initial_update", lambda: update_results(collection), result)
        if profile:
            profiler.disable()
            profiler.dump_stats(str(work / "initial.pstats"))
            stats = pstats.Stats(profiler).strip_dirs().sort_stats("cumulative")
            stats.print_stats(35)
        result["output"] = {
            "days": len(first.report.days),
            "first_day": str(first.report.days[0].day) if first.report.days else None,
            "last_day": str(first.report.days[-1].day) if first.report.days else None,
            "analyzed_cards": first.report.analyzed_cards,
            "excluded_preview_reviews": first.report.excluded_preview_reviews,
            "deleted_card_reviews": first.report.deleted_card_reviews,
            "reconstructed_deleted_cards": first.report.reconstructed_deleted_cards,
            "reconstructed_deleted_reviews": first.report.reconstructed_deleted_reviews,
            "deleted_history_status": first.report.deleted_history_status,
            "digest": report_hash(first.report),
        }
        second = timed("unchanged_update", lambda: update_results(collection), result)
        assert not second.recalculated and second.report == first.report
        loaded = timed("saved_report_load", lambda: load_report(database_path(candidate)), result)
        assert loaded == first.report
        csv = work / "daily.csv"
        timed("csv_export", lambda: export_csv_atomic(first.report, csv), result)
        result["output"]["csv_bytes"] = csv.stat().st_size
        snapshot = work / "snapshot.anki2"
        timed("native_snapshot", lambda: backend.capture_snapshot(collection, snapshot), result)
        result["output"]["results_bytes"] = database_path(candidate).stat().st_size
        if answer_card:
            from anki.cards import Card
            from anki.scheduler.v3 import CardAnswer

            queue = collection.sched.get_queued_cards()
            if queue.cards:
                top = queue.cards[0]
                card = Card(collection, backend_card=top.card)
                card.start_timer()
                collection.sched.answer_card(
                    collection.sched.build_answer(
                        card=card, states=top.states, rating=CardAnswer.GOOD
                    )
                )
                changed = timed("after_one_answer", lambda: update_results(collection), result)
                assert changed.recalculated and changed.report != first.report
                cached = timed("after_answer_cached", lambda: update_results(collection), result)
                assert not cached.recalculated and cached.report == changed.report
            else:
                result["answer_test"] = "No available card in test copy"
    finally:
        backend._build_snapshot_report = original_reconstruction
        collection.close()
        result["source_unchanged"] = source_hashes(source) == before
    if not result["source_unchanged"]:
        raise RuntimeError("Source changed during benchmark; compare results only when idle.")
    result["peak_process_memory_bytes"] = peak_process_memory()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--compare", type=Path, help="Require identical results to a saved benchmark"
    )
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--enable-fsrs", action="store_true")
    parser.add_argument(
        "--common-presets",
        action="store_true",
        help="Hypothetical test: make all presets agree only on the disposable copy",
    )
    parser.add_argument("--autocommit", action="store_true", help="Reproduce old per-card commits")
    parser.add_argument(
        "--answer-card", action="store_true", help="Simulate one Good answer on the copy"
    )
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(args.work_root)
    tempfile.tempdir = str(args.work_root)
    with tempfile.TemporaryDirectory(prefix="history-benchmark-", dir=args.work_root) as directory:
        result = benchmark(
            args.source,
            Path(directory),
            args.profile,
            args.enable_fsrs,
            args.autocommit,
            args.answer_card,
            args.common_presets,
        )
    if args.compare:
        previous = json.loads(args.compare.read_text(encoding="utf-8"))
        result["comparison_checks"] = {
            "metrics_and_anchors": result["output"]["digest"] == previous["output"]["digest"],
            "history_counts": result["history"] == previous["history"],
            "exclusions": all(
                result["output"][key] == previous["output"][key]
                for key in ("analyzed_cards", "excluded_preview_reviews", "deleted_card_reviews")
            ),
        }
        result["matches_comparison"] = all(result["comparison_checks"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    if result.get("matches_comparison") is False:
        raise RuntimeError("Benchmark results differ from the comparison report.")


if __name__ == "__main__":
    main()
