"""T3 evidence-selection harness — development data only.

Nothing this script produces is a v2 result. Candidate generation is **frozen**: every
selection runs on the pinned T3 baseline — `stem`, head-only candidate generation, the
policy T2 left standing — and differs only in how much of the pool it is willing to
deliver. A selection may reject; it may not reach past the pool, reorder what became a
candidate, or consult anything a label touched.

The accounting is `run_dev_t2`'s, imported rather than reimplemented, so a T3 figure and
a T2 figure mean the same thing. The gate quantities T3 adds are aggregate delivered
grade-0 bytes, per-query grade-2 delivered recall, task coverage, and grade-1-only
support.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t3.py [selection ...]
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import corpus as corpus_module  # noqa: E402
from budgeted_retrieval import (  # noqa: E402
    DELIVERED_BYTES, DELIVERED_ITEMS, HISTORY_MEMORIES, HISTORY_REVISIONS, POOL_LIMIT, SELECTIONS,
)
from run_dev_t2 import BASELINE_PROFILE, DIAGNOSTIC_LIMIT, run_policy  # noqa: E402

CORPUS = HERE / "corpus-dev2.json"
BASELINE_SELECTION = "all"      # the pinned T3 baseline; spends no configuration slot
GRADE0_REDUCTION = 0.50         # registered: at least half the baseline's grade-0 bytes


def gate(baseline: dict, arm: dict) -> dict:
    """The registered T3 contract, applied to one selection against the baseline row."""
    base = {q["query_id"]: q for q in baseline["per_query"]}
    grade0_limit = int(baseline["grade0_delivered_bytes"] * (1 - GRADE0_REDUCTION))

    recall_losses, coverage_losses, support_losses = [], [], []
    for query in arm["per_query"]:
        before = base[query["query_id"]]
        if (before["delivered_recall"] or 0) > (query["delivered_recall"] or 0):
            recall_losses.append(query["query_id"])
        # Task coverage: a query that received grade-2 evidence must still receive some.
        if before["grade2_delivered_bytes"] > 0 and query["grade2_delivered_bytes"] == 0:
            coverage_losses.append(query["query_id"])
        # Grade-1-only support: where grade 1 is the only support a query has, it must
        # survive. Measured in bytes actually delivered, not in ids that came back.
        if not before["rank_of_answers"] and before["grade1_delivered_bytes"] > 0 \
                and query["grade1_delivered_bytes"] < before["grade1_delivered_bytes"]:
            support_losses.append(query["query_id"])

    return {
        "grade0_delivered_bytes": arm["grade0_delivered_bytes"],
        "grade0_baseline_bytes": baseline["grade0_delivered_bytes"],
        "grade0_limit_bytes": grade0_limit,
        "grade0_reduction": 1 - arm["grade0_delivered_bytes"] / baseline["grade0_delivered_bytes"],
        "grade0_under_limit": arm["grade0_delivered_bytes"] <= grade0_limit,
        "grade2_recall_losses": recall_losses,
        "task_coverage_losses": coverage_losses,
        "grade1_only_support_losses": support_losses,
        "grade2_delivered_bytes": arm["grade2_delivered_bytes"],
        "grade1_delivered_bytes": arm["grade1_delivered_bytes"],
        "delivered_items": sum(q["delivered"] for q in arm["per_query"]),
        "passes": (arm["grade0_delivered_bytes"] <= grade0_limit
                   and not recall_losses and not coverage_losses and not support_losses),
    }


def main() -> None:
    selections = sys.argv[1:] or [BASELINE_SELECTION]
    for selection in selections:
        if selection not in SELECTIONS:
            raise SystemExit(f"unregistered selection: {selection}; registered: {SELECTIONS}")
    if selections[0] != BASELINE_SELECTION:
        selections = [BASELINE_SELECTION] + selections

    corpus = corpus_module.load(CORPUS.name)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    arms = [run_policy(corpus, "baseline", BASELINE_PROFILE, selection) for selection in selections]
    baseline = arms[0]
    record = {
        "experiment": "T3 evidence selection",
        "development_data": True,
        "not_a_v2_result": True,
        "build_commit": commit,
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "corpus": CORPUS.name,
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "baseline": {"index_profile": BASELINE_PROFILE, "candidate_generation": "head-only (T2 outcome)",
                     "selection": BASELINE_SELECTION},
        "candidate_generation_frozen": True,
        "budgets": {"pool": POOL_LIMIT, "history_memories": HISTORY_MEMORIES,
                    "history_revisions": HISTORY_REVISIONS, "delivered_items": DELIVERED_ITEMS,
                    "delivered_bytes": DELIVERED_BYTES, "diagnostic_limit": DIAGNOSTIC_LIMIT},
        "contract": {"grade0_reduction": GRADE0_REDUCTION,
                     "no_per_query_grade2_recall_loss": True,
                     "no_task_coverage_loss": True,
                     "grade1_only_support_preserved": True},
        "arms": arms,
        "gates": {arm["selection"]: gate(baseline, arm) for arm in arms[1:]},
    }
    out = HERE / ("results-dev-t3-baseline.json" if len(arms) == 1 else "results-dev-t3.json")
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record["gates"], indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
