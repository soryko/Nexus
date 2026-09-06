"""T1-C — bounded performance confirmation for `exact` and unchanged `stem`.

Registered in benchmarks/eval/v2-development-queries.md before this script was run.
Development data only; nothing here is a v2 result.

Why this exists: `stem` exceeded the registered retrieval gate at 1,000 memories in
forward order (1.8561x) and passed reversed (0.8905x). That disagreement licenses an
investigation into measurement instability. It does not establish that run position
explains the failure, and choosing the passing order after the fact would change the
rule rather than satisfy it. This measurement tests order sensitivity directly.

The registered schedule, fixed before the run:

* Two profiles only: `exact` and unchanged `stem`. No third profile, no new
  configuration. This is a measurement amendment.
* Both fixture sizes, 1,000 and 10,000, with the T1 seed, workload generation, fixture
  construction, interpreter and durability settings unchanged.
* Six paired blocks per fixture: three `exact->stem` and three `stem->exact`,
  **interleaved** (EF, FE, EF, FE, EF, FE) rather than run as two consecutive campaigns.
  Interleaving is the point: two consecutive runs confound order with drift in machine
  state, which is exactly the confound under investigation.
* Each pair uses **identical queries**, and each profile within a pair is measured on its
  own freshly copied database prepared the same way.
* 50 warm-up queries then 200 measured queries per profile per block.
* **Retrieval only.** No writes are measured; the write gate is not under review.

Decision rule, registered before the run: within each order, take each profile's worst
block p95. `stem` passes only if it is <=50 ms AND <=1.5x `exact` in both orders at both
fixture sizes -- four ratios, all of which must hold. The complete schedule runs once. No
early stopping, no dropped blocks, no retries until a pass appears.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t1c.py [--sizes 1000,10000]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sqlite3
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexus_memory.domain.models import Scope  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402
from budgeted_retrieval import retrieve  # noqa: E402
from run_dev_t1_perf import SEED, build_fixture, query_set, summarise  # noqa: E402

PROFILES = ("exact", "stem")
PAIRS_PER_ORDER = 3            # three exact->stem and three stem->exact
QUERIES_PER_BLOCK = 200
WARMUP_QUERIES = 50
GATE_MS = 50.0
GATE_RATIO = 1.5

# Interleaved, not two consecutive campaigns.
SCHEDULE = [("exact", "stem"), ("stem", "exact")] * PAIRS_PER_ORDER


def measure_block(fixture: Path, profile: str, queries: list[str]) -> dict:
    """One profile, one block: fresh copy, open, warm up, then 200 timed queries."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "memory.sqlite3"
        shutil.copy(fixture, path)
        for suffix in ("-wal", "-shm"):
            source = Path(str(fixture) + suffix)
            if source.exists():
                shutil.copy(source, Path(str(path) + suffix))

        started = time.perf_counter()
        service = MemoryService(SQLiteRepository(path, index_profile=profile), Scope("perf", "local"))
        open_seconds = time.perf_counter() - started

        for text in queries[:WARMUP_QUERIES]:
            retrieve(service, text)

        timings = []
        for text in queries[WARMUP_QUERIES:WARMUP_QUERIES + QUERIES_PER_BLOCK]:
            started = time.perf_counter()
            retrieve(service, text)
            timings.append(time.perf_counter() - started)

    summary = summarise(timings)
    summary["open_and_profile_build_seconds"] = round(open_seconds, 3)
    summary["durations_ms"] = [round(value * 1000, 6) for value in timings]
    return summary


def worst_block_p95(blocks: list[dict]) -> float:
    return max(block["p95"] for block in blocks)


def main() -> None:
    sizes = [1000, 10000]
    for argument in sys.argv[1:]:
        if argument.startswith("--sizes"):
            sizes = [int(value) for value in argument.split("=", 1)[1].split(",")]

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    record = {
        "experiment": "T1-C — performance confirmation (exact vs unchanged stem)",
        "development_data": True,
        "not_a_v2_result": True,
        "amendment_not_new_configuration": True,
        "pinned_revision": commit,
        "working_tree_clean_at_start": dirty == "",
        "uncommitted_paths_at_start": dirty.splitlines(),
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "durability": {"journal_mode": "WAL", "synchronous": "FULL"},
        "seed": SEED,
        "protocol": {
            "profiles": list(PROFILES),
            "pairs_per_order": PAIRS_PER_ORDER,
            "schedule": [list(pair) for pair in SCHEDULE],
            "interleaved": True,
            "queries_per_block": QUERIES_PER_BLOCK,
            "warmup_queries": WARMUP_QUERIES,
            "identical_queries_within_a_pair": True,
            # Every block uses the same 200 measured queries, so block-to-block spread
            # reflects machine state alone rather than a different query mix. That is
            # the quantity under investigation.
            "identical_queries_across_all_blocks": True,
            # Opening a copied `exact` fixture under `stem` rebuilds the FTS index, which
            # `exact` does not do. That work is outside the timed region and is reported
            # as open_and_profile_build_seconds, but it leaves the two profiles with
            # different page-cache state before warm-up. Recorded, not corrected.
            "stem_open_rebuilds_fts_index": True,
            "writes_measured": False,
            "fixture_built_once_per_size_then_copied": True,
            "retrieval_function": "budgeted_retrieval.retrieve (shared with the quality harness)",
        },
        "decision_rule": {
            "statistic": "worst block p95 per profile within each order",
            "gate_ms": GATE_MS,
            "gate_ratio": GATE_RATIO,
            "must_hold": "stem <=50 ms and <=1.5x exact in BOTH orders at BOTH fixture sizes",
            "registered_before_run": True,
            "single_complete_run": True,
        },
        "fixtures": {},
        "pairs": [],
        "summary": {},
    }

    queries = query_set(WARMUP_QUERIES + QUERIES_PER_BLOCK)

    for size in sizes:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.sqlite3"
            print(f"building fixture: {size} memories ...", flush=True)
            started = time.perf_counter()
            record["fixtures"][str(size)] = build_fixture(fixture, size)
            record["fixtures"][str(size)]["build_seconds"] = round(time.perf_counter() - started, 2)

            for index, (first, second) in enumerate(SCHEDULE):
                order = f"{first}->{second}"
                print(f"  pair {index + 1}/{len(SCHEDULE)} @ {size}: {order} ...", flush=True)
                blocks = {}
                for position, profile in enumerate((first, second), start=1):
                    blocks[profile] = measure_block(fixture, profile, queries)
                    blocks[profile]["position_in_pair"] = position
                record["pairs"].append({
                    "fixture_size": size, "pair_index": index, "order": order,
                    "exact": blocks["exact"], "stem": blocks["stem"],
                })

    # ---- decision, applied exactly as registered ------------------------------------
    summary = {}
    for size in sizes:
        for order in ("exact->stem", "stem->exact"):
            pairs = [p for p in record["pairs"] if p["fixture_size"] == size and p["order"] == order]
            exact_p95 = worst_block_p95([p["exact"] for p in pairs])
            stem_p95 = worst_block_p95([p["stem"] for p in pairs])
            # Unrounded: the gate is applied to the exact ratio. The report rounds for display.
            ratio = stem_p95 / exact_p95
            summary[f"{size}|{order}"] = {
                "blocks": len(pairs),
                "exact_worst_block_p95_ms": exact_p95,
                "stem_worst_block_p95_ms": stem_p95,
                "ratio": ratio,
                "under_50ms": stem_p95 <= GATE_MS,
                "under_1_5x": ratio <= GATE_RATIO,
                "cell_pass": stem_p95 <= GATE_MS and ratio <= GATE_RATIO,
            }
    record["summary"] = summary
    record["verdict"] = {
        "cells_evaluated": len(summary),
        "cells_passed": sum(1 for cell in summary.values() if cell["cell_pass"]),
        "stem_clears_registered_rule": all(cell["cell_pass"] for cell in summary.values()),
        "failing_cells": [key for key, cell in summary.items() if not cell["cell_pass"]],
    }

    out = Path(__file__).resolve().parent / "results-dev-t1c.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print("\n" + json.dumps({"summary": summary, "verdict": record["verdict"]}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
