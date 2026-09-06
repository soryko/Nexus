"""T2 performance and storage measurement — development data only.

Nothing this script produces is a v2 result. It prices the T2 historical channel against
the **pinned `exact` baseline**, measured fresh in the same run: the charter anchors every
ratio to `exact` rather than to the incoming stage baseline, so successive promotions
cannot multiply the allowed cost.

The schedule follows T1-C, for the reason T1-C exists. Two arms are measured in
**interleaved pairs** (AB, BA, AB, BA, AB, BA) on identical queries from equivalently
prepared copies, rather than as two consecutive campaigns, because two consecutive runs
confound arm order with drift in machine state. Retrieval is timed through the same
`budgeted_retrieval.retrieve` the quality harness runs, under the same registered policy,
so the two cannot enforce different limits on the same budget.

| Arm | Index profile | History profile | Policy |
| --- | --- | --- | --- |
| `anchor` | `exact` | `none` | `baseline` |
| `stage_baseline` | `stem` | `none` | `baseline` |
| `variant` | `stem` | `all` | as given (`history_cued` unless overridden) |

The gates are computed `variant` against `anchor`, as registered. The third arm is not a
configuration and spends no variant slot: it is the pinned stage baseline, measured in the
same schedule so that the cost of stemming and the cost of the historical channel can be
told apart instead of being attributed to whichever one is under review.

The three arms run in all six orders per fixture size, so each arm occupies each position
twice and no arm is systematically measured first.

Writes are measured in every block, after that block's retrieval, because the historical
index is maintained by `revise`: a retrieval-only measurement would price half the change.

Percentiles and ratios are compared to the gates unrounded; rounding is for the report.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t2_perf.py [--sizes 1000,10000]
                                                                 [--policy history_cued]
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
import sqlite3
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from nexus_memory.domain.models import MemoryInput, Scope  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402
from budgeted_retrieval import POLICY_HISTORY_PROFILE, SELECTIONS, retrieve  # noqa: E402
from run_dev_t1 import index_sizes  # noqa: E402
from run_dev_t1_perf import SEED, body, build_fixture, query_set, summarise  # noqa: E402

PAIRS_PER_ORDER = 3
QUERIES_PER_BLOCK = 200
WARMUP_QUERIES = 50
MEASURED_WRITES = 200
WARMUP_WRITES = 50
GATE_MS = 50.0
GATE_RETRIEVAL_RATIO = 1.5
GATE_WRITE_RATIO = 2.0
GATE_STORAGE_RATIO = 2.0

ARMS = ("anchor", "stage_baseline", "variant")
# Every permutation of the three arms: each arm sits in each position exactly twice.
SCHEDULE = [
    ("anchor", "stage_baseline", "variant"),
    ("variant", "stage_baseline", "anchor"),
    ("stage_baseline", "variant", "anchor"),
    ("anchor", "variant", "stage_baseline"),
    ("variant", "anchor", "stage_baseline"),
    ("stage_baseline", "anchor", "variant"),
]


def arm_configuration(arm: str, policy: str, selection: str) -> tuple[str, str, str, str]:
    """(index profile, history profile, retrieval policy, selection) for one arm.

    The anchor is the pinned `exact` baseline in every stage. The stage baseline is what
    the variant is being compared *against* for attribution — for T2 that was `stem` with
    head-only generation, and for T3 it is the same configuration with the baseline
    selection, so that the variant differs from it in exactly one thing.
    """
    if arm == "anchor":
        return "exact", "none", "baseline", "all"
    if arm == "stage_baseline":
        return "stem", "none", "baseline", "all"
    return "stem", POLICY_HISTORY_PROFILE[policy], policy, selection


def measure_block(fixture: Path, arm: str, policy: str, queries: list[str],
                  selection: str = "all") -> dict:
    """One arm, one block: fresh copy, open, warm up, 200 timed queries, then writes."""
    profile, history_profile, arm_policy, arm_selection = arm_configuration(arm, policy, selection)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "memory.sqlite3"
        shutil.copy(fixture, path)
        for suffix in ("-wal", "-shm"):
            source = Path(str(fixture) + suffix)
            if source.exists():
                shutil.copy(source, Path(str(path) + suffix))

        started = time.perf_counter()
        service = MemoryService(
            SQLiteRepository(path, index_profile=profile, history_profile=history_profile),
            Scope("perf", "local"),
        )
        open_seconds = time.perf_counter() - started

        for text in queries[:WARMUP_QUERIES]:
            retrieve(service, text, policy=arm_policy, selection=arm_selection)

        timings = []
        for text in queries[WARMUP_QUERIES:WARMUP_QUERIES + QUERIES_PER_BLOCK]:
            started = time.perf_counter()
            retrieve(service, text, policy=arm_policy, selection=arm_selection)
            timings.append(time.perf_counter() - started)

        # Storage is read before any write, so the figure describes the fixture both arms
        # were given rather than the 400 rows this block is about to add to it.
        sizes = index_sizes(path)

        record_timings: list[float] = []
        revise_timings: list[float] = []
        rng = random.Random(SEED + 2)
        # Unmeasured write warm-up: without it the first arm in a pair absorbs the
        # cold-cache cost of the write path and looks slower for that reason alone.
        for index in range(WARMUP_WRITES):
            warm = service.record(MemoryInput(body(rng, 30_000_000 + index), kind="observation",
                                              tags=("perf",)), f"warm-record-{index}")
            service.revise(warm.memory_id, warm.revision_id,
                           MemoryInput(body(rng, 40_000_000 + index), kind="observation", tags=("perf",)),
                           f"warm-revise-{index}")
        for index in range(MEASURED_WRITES):
            started = time.perf_counter()
            receipt = service.record(MemoryInput(body(rng, 10_000_000 + index), kind="observation",
                                                 tags=("perf",)), f"measure-record-{index}")
            record_timings.append(time.perf_counter() - started)
            started = time.perf_counter()
            service.revise(receipt.memory_id, receipt.revision_id,
                           MemoryInput(body(rng, 20_000_000 + index), kind="observation", tags=("perf",)),
                           f"measure-revise-{index}")
            revise_timings.append(time.perf_counter() - started)

    summary = summarise(timings)
    summary["arm"] = arm
    summary["index_profile"] = profile
    summary["history_profile"] = history_profile
    summary["policy"] = arm_policy
    summary["selection"] = arm_selection
    summary["open_and_index_build_seconds"] = open_seconds
    summary["durations_ms"] = [value * 1000.0 for value in timings]
    summary["record"] = summarise(record_timings)
    summary["revise"] = summarise(revise_timings)
    summary["index_bytes"] = sizes
    return summary


def worst(blocks: list[dict], key: str = "p95") -> float:
    return max(block[key] for block in blocks)


def main() -> None:
    sizes = [1000, 10000]
    policy = "history_cued"
    selection = "all"
    for argument in sys.argv[1:]:
        if argument.startswith("--sizes"):
            sizes = [int(value) for value in argument.split("=", 1)[1].split(",")]
        elif argument.startswith("--policy"):
            policy = argument.split("=", 1)[1]
        elif argument.startswith("--selection"):
            selection = argument.split("=", 1)[1]
    if selection not in SELECTIONS:
        raise SystemExit(f"unregistered selection: {selection}")

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    record = {
        "experiment": f"performance and storage — policy {policy}, selection {selection}, against a fresh paired exact",
        "development_data": True,
        "not_a_v2_result": True,
        "pinned_revision": commit,
        "working_tree_clean_at_start": dirty == "",
        "uncommitted_paths_at_start": dirty.splitlines(),
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "durability": {"journal_mode": "WAL", "synchronous": "FULL"},
        "seed": SEED,
        "protocol": {
            "arms": {"anchor": "exact / none / baseline",
                     "stage_baseline": "stem / none / baseline",
                     "variant": f"stem / {POLICY_HISTORY_PROFILE[policy]} / {policy} / {selection}"},
            "gate_computed": "variant against anchor",
            "third_arm_is_not_a_configuration": True,
            "anchored_to_exact_not_to_the_stage_baseline": True,
            "pairs_per_order": PAIRS_PER_ORDER,
            "schedule": [list(pair) for pair in SCHEDULE],
            "interleaved": True,
            "queries_per_block": QUERIES_PER_BLOCK,
            "warmup_queries": WARMUP_QUERIES,
            "identical_queries_within_a_pair": True,
            "identical_queries_across_all_blocks": True,
            "measured_writes_per_block": MEASURED_WRITES,
            "warmup_writes_per_block": WARMUP_WRITES,
            "storage_read_before_this_block_writes": True,
            # Opening a copied `exact` fixture under the variant rebuilds the head index
            # and builds the historical one. That work is outside the timed region and is
            # reported as open_and_index_build_seconds; it leaves the two arms with
            # different page-cache state before warm-up. Recorded, not corrected — the
            # same property T1-C recorded.
            "variant_open_builds_both_indexes": True,
            "no_network_or_model_calls_in_retrieval": True,
            "retrieval_function": "budgeted_retrieval.retrieve (shared with the quality harness)",
        },
        "gates": {
            "retrieval_ms": GATE_MS,
            "retrieval_ratio": GATE_RETRIEVAL_RATIO,
            "write_ratio": GATE_WRITE_RATIO,
            "storage_ratio": GATE_STORAGE_RATIO,
            "statistic": "worst block p95 per arm within each order; ratios unrounded",
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
            record["fixtures"][str(size)]["build_seconds"] = time.perf_counter() - started

            for index, ordering in enumerate(SCHEDULE):
                order = "->".join(ordering)
                print(f"  block set {index + 1}/{len(SCHEDULE)} @ {size}: {order} ...", flush=True)
                blocks = {}
                for position, arm in enumerate(ordering, start=1):
                    blocks[arm] = measure_block(fixture, arm, policy, queries, selection)
                    blocks[arm]["position_in_pair"] = position
                record["pairs"].append({
                    "fixture_size": size, "pair_index": index, "order": order,
                    # The registered cell is defined by where the variant sits relative to
                    # the anchor it is gated against.
                    "variant_before_anchor": ordering.index("variant") < ordering.index("anchor"),
                    **{arm: blocks[arm] for arm in ARMS},
                })

    # ---- the gates, applied to unrounded values --------------------------------------
    summary = {}
    for size in sizes:
        for order, variant_first in (("anchor->variant", False), ("variant->anchor", True)):
            pairs = [p for p in record["pairs"]
                     if p["fixture_size"] == size and p["variant_before_anchor"] == variant_first]
            cell = {"blocks": len(pairs)}
            anchor_p95 = worst([p["anchor"] for p in pairs])
            variant_p95 = worst([p["variant"] for p in pairs])
            cell["anchor_retrieval_p95_ms"] = anchor_p95
            cell["variant_retrieval_p95_ms"] = variant_p95
            cell["retrieval_ratio"] = variant_p95 / anchor_p95
            cell["retrieval_under_50ms"] = variant_p95 <= GATE_MS
            cell["retrieval_under_ratio"] = cell["retrieval_ratio"] <= GATE_RETRIEVAL_RATIO
            for operation in ("record", "revise"):
                anchor_write = max(p["anchor"][operation]["p95"] for p in pairs)
                variant_write = max(p["variant"][operation]["p95"] for p in pairs)
                cell[f"{operation}_ratio"] = variant_write / anchor_write
                cell[f"{operation}_p95_ms"] = {"anchor": anchor_write, "variant": variant_write}
                cell[f"{operation}_under_ratio"] = cell[f"{operation}_ratio"] <= GATE_WRITE_RATIO
            anchor_bytes = max(p["anchor"]["index_bytes"]["_retrieval_total"] for p in pairs)
            variant_bytes = max(p["variant"]["index_bytes"]["_retrieval_total"] for p in pairs)
            cell["storage_bytes"] = {"anchor": anchor_bytes, "variant": variant_bytes}
            cell["storage_ratio"] = variant_bytes / anchor_bytes
            cell["storage_under_ratio"] = cell["storage_ratio"] <= GATE_STORAGE_RATIO
            cell["cell_pass"] = all(cell[key] for key in (
                "retrieval_under_50ms", "retrieval_under_ratio", "record_under_ratio",
                "revise_under_ratio", "storage_under_ratio"))
            summary[f"{size}|{order}"] = cell

    # The paired reading, reported beside the registered one for the reason T1-C gave:
    # two independently selected maxima can mask a worse pair.
    paired = {}
    for size in sizes:
        cells = [p for p in record["pairs"] if p["fixture_size"] == size]
        paired[str(size)] = {
            "worst_pair_retrieval_ratio": max(p["variant"]["p95"] / p["anchor"]["p95"] for p in cells),
            "worst_pair_record_ratio": max(p["variant"]["record"]["p95"] / p["anchor"]["record"]["p95"] for p in cells),
            "worst_pair_revise_ratio": max(p["variant"]["revise"]["p95"] / p["anchor"]["revise"]["p95"] for p in cells),
            # What the stemmer costs and what the historical channel costs, told apart.
            "worst_pair_stage_baseline_over_anchor": max(
                p["stage_baseline"]["p95"] / p["anchor"]["p95"] for p in cells),
            "worst_pair_variant_over_stage_baseline": max(
                p["variant"]["p95"] / p["stage_baseline"]["p95"] for p in cells),
            "worst_pair_revise_variant_over_stage_baseline": max(
                p["variant"]["revise"]["p95"] / p["stage_baseline"]["revise"]["p95"] for p in cells),
            "storage_stage_baseline_over_anchor": max(
                p["stage_baseline"]["index_bytes"]["_retrieval_total"]
                / p["anchor"]["index_bytes"]["_retrieval_total"] for p in cells),
        }

    record["summary"] = summary
    record["paired_statistic"] = paired
    record["verdict"] = {
        "policy": policy,
        "selection": selection,
        "cells_evaluated": len(summary),
        "cells_passed": sum(1 for cell in summary.values() if cell["cell_pass"]),
        "clears_every_ceiling": all(cell["cell_pass"] for cell in summary.values()),
        "failing_cells": [key for key, cell in summary.items() if not cell["cell_pass"]],
    }

    tag = policy if selection == "all" else f"{policy}-{selection}"
    out = HERE / f"results-dev-t2-perf-{tag}.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print("\n" + json.dumps({"summary": summary, "paired": paired, "verdict": record["verdict"]}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
