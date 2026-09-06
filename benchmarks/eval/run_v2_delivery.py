"""Delivery regression check: `stem`/`all` against `stem`/`cutoff_40` on frozen v2.

    .venv-sqlite/bin/python benchmarks/eval/run_v2_delivery.py

**Not a fresh evaluation of v2.** `cutoff_40` was chosen on development data with v2's
failure modes already known. This run says whether a known failure moved and what the
chosen selection costs on the frozen set. It carries no gate: the 50% grade-0 reduction
registered for T3 is a development target measured against T3's own pinned baseline, and
nothing here may be tuned toward it.

Why this file exists rather than a flag on `run_v2.py`:

* `run_v2.py` measures **discovery** — what the index returns at k=5/10/20 — and calls
  `service.search` directly. It never invokes a selection, so running it under a
  `--profile` says nothing about `cutoff_40`. Its k-cutoff figures are the frozen v2
  record and stay separate from everything measured here.
* This file measures **delivered evidence** under the registered development budgets —
  pool 20, history 5 memories x 20 revisions, 5 items / 8,192 bytes including
  provenance — through `benchmarks/dev/budgeted_retrieval.retrieve`, the same function
  the development quality and performance harnesses run. Sharing that function is the
  point: a delivery figure here and a delivery figure in T3 mean the same thing.

Losses are reported against `stem`/`all` — the same candidate generation, the same
budgets, selection the only difference. Both arms and both controls run under the
configuration being assessed; a control built on a profile the run did not use
establishes nothing about the run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks" / "dev"))

from budgeted_retrieval import (  # noqa: E402
    DELIVERED_BYTES, DELIVERED_ITEMS, HISTORY_MEMORIES, HISTORY_REVISIONS, POOL_LIMIT,
    SELECTIONS, retrieve,
)
from run_v2 import build_fixture, item_map  # noqa: E402

PROFILE = "stem"            # carried unchanged from T1-C
POLICY = "baseline"         # head-only candidate generation, carried unchanged from T2
BASELINE_SELECTION = "all"  # the arm every loss is reported against
VARIANT_SELECTION = "cutoff_40"   # carried unchanged from T3; 0.40 is fixed here
INPUTS = ("corpus-v2.json", "judgments-v2.json", "protocol-v2.md", "judging-v2-assessor.md")
# Every file whose contents executed. Hashed so that a run made from a dirty tree still
# says which contents ran; a clean tree at commit time does not establish that by itself.
EXECUTED = (
    "benchmarks/eval/run_v2_delivery.py",
    "benchmarks/eval/run_v2.py",
    "benchmarks/dev/budgeted_retrieval.py",
    "src/nexus_memory/memory/service.py",
    "src/nexus_memory/storage/sqlite.py",
    "src/nexus_memory/domain/models.py",
)


# --------------------------------------------------------------------------- provenance

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def code_pin() -> dict:
    """Pin the application and the harness together, before anything runs."""
    dirty = _git("status", "--porcelain")
    return {
        "pinned_revision": _git("rev-parse", "HEAD"),
        "working_tree_clean_at_start": dirty == "",
        "uncommitted_paths_at_start": dirty.splitlines(),
        "executed_file_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in EXECUTED
        },
        "input_sha256": {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in INPUTS
        },
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
    }


# ---------------------------------------------------------------------------- evaluation

def grades(judgments: dict, qid: str) -> tuple[set[str], set[str]]:
    judged = judgments["judgments"][qid]
    return set(judged["2"]), set(judged["1"])


def run_arm(corpus: dict, judgments: dict, selection: str) -> dict:
    """One selection, its own fixture, every query through the shared budgeted path."""
    items = item_map(corpus)
    current = {items[r["revision_id"]]
               for m in corpus["memories"] for r in m["revisions"] if r["state"] == "current"}

    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "delivery.sqlite3"
        service, live, order = build_fixture(path, corpus, PROFILE)

        per_query = []
        for query in corpus["queries"]:
            qid = query["query_id"]
            grade2, grade1 = grades(judgments, qid)
            eligible = grade2 & current      # head-only delivery can reach no other

            out = retrieve(service, query["text"], policy=POLICY, selection=selection)

            # Grade each delivered item by the item id its delivered revision carries.
            # Labels are read here, outside the retrieval path, never inside it.
            delivered_items, bytes_by_grade = {}, {"2": 0, "1": 0, "0": 0}
            for key, revision_id in out.delivered_provenance.items():
                memory_id = key.partition("@")[0]
                item = items[live[(memory_id, revision_id)]]
                grade = "2" if item in grade2 else ("1" if item in grade1 else "0")
                delivered_items[item] = grade
                bytes_by_grade[grade] += out.delivered_item_bytes[key]

            found = {item for item, grade in delivered_items.items() if grade == "2"}
            per_query.append({
                "query_id": qid,
                "text": query["text"],
                "pool_size": len(out.pool),
                "selected_items": out.selected,
                "delivered": len(out.delivered),
                "delivered_items": sorted(delivered_items),
                "delivered_by_grade": {g: sorted(i for i, v in delivered_items.items() if v == g)
                                       for g in ("2", "1", "0")},
                "delivered_bytes": out.delivered_bytes,
                "grade2_delivered_bytes": bytes_by_grade["2"],
                "grade1_delivered_bytes": bytes_by_grade["1"],
                "grade0_delivered_bytes": bytes_by_grade["0"],
                # Two denominators, never collapsed: what head-only delivery can reach,
                # and every grade-2 item the labels name.
                "delivered_head_recall": (round(len(found & eligible) / len(eligible), 3)
                                          if eligible else None),
                "delivered_head_recall_denominator": len(eligible),
                "delivered_recall_all_grade2": (round(len(found) / len(grade2), 3)
                                                if grade2 else None),
                "grade2_denominator": len(grade2),
                "covered": bool(found),
                "grade1_only_query": not grade2,
                "revisions_discovered": len(out.discovered_revisions),
                "history_memories_used": len(out.revisions_by_memory),
                "max_revisions_for_one_memory": max((len(v) for v in out.revisions_by_memory.values()),
                                                    default=0),
                "stopped_by": out.stopped_by,
            })

        controls = {
            "empty_index": control_empty_index(corpus, selection),
            "forget_relevant": control_forget_relevant(corpus, judgments, selection),
        }

    return {
        "selection": selection,
        "index_profile": PROFILE,
        "policy": POLICY,
        "load_order": order,
        "grade0_delivered_bytes": sum(q["grade0_delivered_bytes"] for q in per_query),
        "grade1_delivered_bytes": sum(q["grade1_delivered_bytes"] for q in per_query),
        "grade2_delivered_bytes": sum(q["grade2_delivered_bytes"] for q in per_query),
        "delivered_items": sum(q["delivered"] for q in per_query),
        "per_query": per_query,
        "controls": controls,
    }


def losses(baseline: dict, arm: dict, judgments: dict) -> dict:
    """What the variant lost against `stem`/`all`. Reported, not gated."""
    base = {q["query_id"]: q for q in baseline["per_query"]}
    recall, coverage, support = [], [], []
    for query in arm["per_query"]:
        before = base[query["query_id"]]
        if (before["delivered_head_recall"] or 0) > (query["delivered_head_recall"] or 0):
            recall.append(query["query_id"])
        if before["grade2_delivered_bytes"] > 0 and query["grade2_delivered_bytes"] == 0:
            coverage.append(query["query_id"])
        # Grade-1-only support: where a query has no grade-2 item at all, the grade-1
        # bytes it received at baseline are the only support it has.
        if query["grade1_only_query"] and query["grade1_delivered_bytes"] < before["grade1_delivered_bytes"]:
            support.append(query["query_id"])
    baseline_grade0 = baseline["grade0_delivered_bytes"]
    return {
        "grade2_recall_losses": recall,
        "task_coverage_losses": coverage,
        "grade1_only_support_losses": support,
        "grade0_delivered_bytes": arm["grade0_delivered_bytes"],
        "grade0_baseline_bytes": baseline_grade0,
        "grade0_reduction": (round(1 - arm["grade0_delivered_bytes"] / baseline_grade0, 4)
                             if baseline_grade0 else None),
        "grade2_delivered_bytes_unchanged": arm["grade2_delivered_bytes"] == baseline["grade2_delivered_bytes"],
        "grade1_delivered_bytes_unchanged": arm["grade1_delivered_bytes"] == baseline["grade1_delivered_bytes"],
        "queries_whose_delivery_changed": [
            q["query_id"] for q in arm["per_query"]
            if q["delivered_items"] != base[q["query_id"]]["delivered_items"]
        ],
        "gate": None,
        "gate_note": (
            "No gate. The 50% grade-0 reduction is a development target measured against "
            "T3's own pinned baseline on the development corpus. Restating it here would "
            "turn the regression check into a second target to tune toward on frozen "
            "evaluation data."
        ),
    }


# ----------------------------------------------------------------------------- controls

def control_empty_index(corpus: dict, selection: str) -> dict:
    """Clear the head postings under the assessed configuration; deliver nothing."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "control1.sqlite3"
        service, _, _ = build_fixture(path, corpus, PROFILE)
        db = sqlite3.connect(path)
        try:
            try:
                db.execute("INSERT INTO head_fts(head_fts) VALUES('delete-all')")
            except sqlite3.OperationalError:
                db.execute("DELETE FROM head_fts")
            db.commit()
            memories = db.execute("SELECT count(*) FROM memories WHERE tombstoned=0").fetchone()[0]
            projection = db.execute("SELECT count(*) FROM head_index").fetchone()[0]
        finally:
            db.close()
        runs = [retrieve(service, q["text"], policy=POLICY, selection=selection)
                for q in corpus["queries"]]
    delivered = sum(len(r.delivered) for r in runs)
    return {
        "fixture": "independent",
        "index_profile": PROFILE,
        "policy": POLICY,
        "selection": selection,
        "memories_retained": memories,
        "head_projection_retained": projection,
        "pool_members": sum(len(r.pool) for r in runs),
        "delivered_items": delivered,
        "delivered_bytes": sum(r.delivered_bytes for r in runs),
        "verdict": "PASS" if delivered == 0 and projection > 0 and memories > 0 else "FAIL",
        "note": ("Only head postings were cleared, under the profile being assessed. Delivered "
                 "evidence with no postings would prove delivery bypasses the index; a zero head "
                 "projection would mean the control proved less than it claims."),
    }


def control_forget_relevant(corpus: dict, judgments: dict, selection: str) -> dict:
    """Forget every memory holding a grade-2 item; none may be delivered or discovered."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "control2.sqlite3"
        service, live, _ = build_fixture(path, corpus, PROFILE)
        items = item_map(corpus)
        targets = {i for j in judgments["judgments"].values() for i in j["2"]}
        forgotten: set[str] = set()
        for (memory_id, _), revision in live.items():
            if items[revision] in targets and memory_id not in forgotten:
                service.forget(memory_id, service.get(memory_id).revision_id, f"control-{memory_id}")
                forgotten.add(memory_id)

        reappeared = []
        for query in corpus["queries"]:
            out = retrieve(service, query["text"], policy=POLICY, selection=selection)
            for key in out.delivered_provenance:
                memory_id = key.partition("@")[0]
                if memory_id in forgotten:
                    reappeared.append({"query": query["query_id"], "memory": memory_id,
                                       "where": "delivered"})
            for memory_id in out.revisions_by_memory:
                if memory_id in forgotten:
                    reappeared.append({"query": query["query_id"], "memory": memory_id,
                                       "where": "history_expansion"})
    return {
        "fixture": "independent",
        "index_profile": PROFILE,
        "policy": POLICY,
        "selection": selection,
        "memories_forgotten": len(forgotten),
        "forgotten_memories_reached": len(reappeared),
        "detail": reappeared[:10],
        "verdict": "PASS" if not reappeared else "FAIL",
        "note": ("Asserts no forgotten memory is delivered or expanded anywhere in the budgeted "
                 "path, regardless of its grade for the query at hand."),
    }


# --------------------------------------------------------------------------------- main

def partition_view(arm: dict, partition: dict) -> dict:
    """Delivered figures by v2's declared partition. Flagged queries are never averaged."""
    by_query = {q["query_id"]: q for q in arm["per_query"]}
    out = {}
    for name in ("clean", "flagged_post_result_authoring", "no_direct_answer"):
        queries = partition[name]
        recalls = [by_query[q]["delivered_head_recall"] for q in queries
                   if by_query[q]["delivered_head_recall"] is not None]
        out[name] = {
            "queries": len(queries),
            "delivered_head_recall_mean": (round(sum(recalls) / len(recalls), 3) if recalls else None),
            "delivered_head_recall_n": f"{len(recalls)}/{len(queries)}",
            "task_coverage": f"{sum(1 for q in queries if by_query[q]['covered'])}/{len(queries)}",
            "grade0_delivered_bytes": sum(by_query[q]["grade0_delivered_bytes"] for q in queries),
            "grade1_delivered_bytes": sum(by_query[q]["grade1_delivered_bytes"] for q in queries),
            "grade2_delivered_bytes": sum(by_query[q]["grade2_delivered_bytes"] for q in queries),
            "delivered_items": sum(by_query[q]["delivered"] for q in queries),
        }
    return out


def main() -> None:
    for selection in (BASELINE_SELECTION, VARIANT_SELECTION):
        if selection not in SELECTIONS:
            raise SystemExit(f"unregistered selection: {selection}")

    pin = code_pin()
    corpus = json.loads((HERE / "corpus-v2.json").read_text())
    judgments = json.loads((HERE / "judgments-v2.json").read_text())
    partition = judgments["partition"]

    baseline = run_arm(corpus, judgments, BASELINE_SELECTION)
    variant = run_arm(corpus, judgments, VARIANT_SELECTION)

    record = {
        "check": "v2 delivery regression — selection carried unchanged from T3",
        "fresh_evaluation_of_v2": False,
        "regression_check": (
            "`cutoff_40` was chosen on development data with v2's failure modes already known. "
            "This says whether a known failure moved and what selection costs on the frozen set."
        ),
        "separate_from_discovery_metrics": (
            "Delivered-evidence metrics only. v2's registered k=5/10/20 discovery figures live in "
            "results-v2.json and results-v2-regression-stem.json and are untouched by this run."
        ),
        **pin,
        "configuration": {"index_profile": PROFILE, "candidate_generation": "head-only (T2 outcome)",
                          "baseline_selection": BASELINE_SELECTION,
                          "variant_selection": VARIANT_SELECTION,
                          "cutoff_fraction_fixed_at": 0.40},
        "budgets": {"pool": POOL_LIMIT, "history_memories": HISTORY_MEMORIES,
                    "history_revisions": HISTORY_REVISIONS, "delivered_items": DELIVERED_ITEMS,
                    "delivered_bytes": DELIVERED_BYTES},
        "retrieval_function": "budgeted_retrieval.retrieve (shared with the development harnesses)",
        "partition": {k: partition[k] for k in
                      ("clean", "flagged_post_result_authoring", "no_direct_answer")},
        "arms": {BASELINE_SELECTION: baseline, VARIANT_SELECTION: variant},
        "by_partition": {BASELINE_SELECTION: partition_view(baseline, partition),
                         VARIANT_SELECTION: partition_view(variant, partition)},
        "losses_against_baseline": losses(baseline, variant, judgments),
    }
    destination = HERE / "results-v2-delivery-regression.json"
    destination.write_text(json.dumps(record, indent=2) + "\n")

    # ------------------------------------------------------------------ readable summary
    print(f"pinned {pin['pinned_revision'][:7]}  clean tree at start: "
          f"{pin['working_tree_clean_at_start']}")
    for name, digest in pin["executed_file_sha256"].items():
        print(f"  sha256 {digest[:16]}…  {name}")
    print(f"\n{PROFILE} / head-only / budgets: pool {POOL_LIMIT}, history {HISTORY_MEMORIES}x"
          f"{HISTORY_REVISIONS}, delivered {DELIVERED_ITEMS} items / {DELIVERED_BYTES} bytes")

    print("\nDELIVERED EVIDENCE   items · bytes by grade · head recall (N/A = no reachable grade-2)")
    print(f"{'q':5s}{'set':9s}{'all':>18s}{'cutoff_40':>20s}   {'head recall':>22s}")
    base_by = {q["query_id"]: q for q in baseline["per_query"]}
    for query in variant["per_query"]:
        qid = query["query_id"]
        before = base_by[qid]
        group = ("clean" if qid in partition["clean"]
                 else "flagged" if qid in partition["flagged_post_result_authoring"] else "no-answer")

        def cell(row):
            return f"{row['delivered']}i {row['grade2_delivered_bytes']}/{row['grade1_delivered_bytes']}/{row['grade0_delivered_bytes']}"

        def recall(row):
            return "N/A" if row["delivered_head_recall"] is None else f"{row['delivered_head_recall']:.3f}"

        print(f"{qid:5s}{group:9s}{cell(before):>18s}{cell(query):>20s}   "
              f"{recall(before):>10s} -> {recall(query):>8s}")

    loss = record["losses_against_baseline"]
    print(f"\nAGGREGATE (all 17 queries)   grade-2 / grade-1 / grade-0 delivered bytes")
    for name, arm in (("all", baseline), ("cutoff_40", variant)):
        print(f"  {name:10s} {arm['grade2_delivered_bytes']:>6d} / {arm['grade1_delivered_bytes']:>5d} / "
              f"{arm['grade0_delivered_bytes']:>6d}   items {arm['delivered_items']}")
    print(f"  grade-0 change: {loss['grade0_baseline_bytes']} -> {loss['grade0_delivered_bytes']} "
          f"({loss['grade0_reduction']:.2%} reduction; reported, not gated)")

    print("\nLOSSES AGAINST stem/all")
    print(f"  grade-2 delivered-recall losses : {loss['grade2_recall_losses'] or 'none'}")
    print(f"  task-coverage losses            : {loss['task_coverage_losses'] or 'none'}")
    print(f"  grade-1-only support losses     : {loss['grade1_only_support_losses'] or 'none'}")
    print(f"  queries whose delivery changed  : {loss['queries_whose_delivery_changed'] or 'none'}")

    print("\nBY PARTITION (flagged queries reported, never averaged into a headline)")
    for name in ("clean", "flagged_post_result_authoring", "no_direct_answer"):
        b = record["by_partition"]["all"][name]
        v = record["by_partition"]["cutoff_40"][name]
        print(f"  {name:30s} coverage {b['task_coverage']} -> {v['task_coverage']}   "
              f"grade-0 bytes {b['grade0_delivered_bytes']} -> {v['grade0_delivered_bytes']}")

    print("\nNEGATIVE CONTROLS (independent fixtures, run under the assessed configuration)")
    for name, arm in (("all", baseline), ("cutoff_40", variant)):
        c1, c2 = arm["controls"]["empty_index"], arm["controls"]["forget_relevant"]
        print(f"  {name:10s} postings cleared -> {c1['delivered_items']} items delivered "
              f"({c1['memories_retained']} memories, {c1['head_projection_retained']} head rows "
              f"retained)  {c1['verdict']}")
        print(f"  {'':10s} {c2['memories_forgotten']} memories forgotten -> "
              f"{c2['forgotten_memories_reached']} reached anywhere  {c2['verdict']}")

    print(f"\nwrote {destination.name}")


if __name__ == "__main__":
    main()
