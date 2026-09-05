"""Execute the registered v2 measurement protocol against the frozen benchmark.

    python run_v2.py                       # the frozen protocol, exactly as registered
    python run_v2.py --profile=split       # the same protocol as a regression check

Implements `protocol-v2.md` including amendments A1-A5, which were registered before
this file was written. Where the protocol and this code disagree, the protocol wins and
the code is the bug.

Writes the complete run record to `results-v2.json` and prints a readable summary. The
JSON artifact is the preserved first result: build commit, input hashes, load order,
revision-id mapping, per-query figures at every cutoff, aggregates, and control outcomes.

`--profile` selects a search index profile for a **regression check** of a configuration
chosen on development data. It defaults to `exact`, which is the frozen behaviour, and a
non-default profile is written to its own file: the frozen record is never overwritten by
one. A regression check is not a fresh evaluation of v2 — the failures it moves were
already known when the configuration was chosen.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from nexus_memory.domain.errors import MemoryNotFound  # noqa: E402
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

HERE = Path(__file__).parent
CUTOFFS = (5, 10, 20)
HISTORY_LIMIT = 20
SEARCH_LIMIT = 20
SCOPE = Scope("eval-v2", "local")
INPUTS = ("corpus-v2.json", "judgments-v2.json", "protocol-v2.md", "judging-v2-assessor.md")


# --------------------------------------------------------------------------- provenance

def run_record() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True).stdout.strip()

    return {
        "build_commit": git("rev-parse", "HEAD"),
        "src_dirty": bool(git("status", "--porcelain", "--", str(HERE.parents[1] / "src"))),
        "input_sha256": {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in INPUTS
        },
        "cutoffs": list(CUTOFFS),
        "search_limit": SEARCH_LIMIT,
        "history_limit": HISTORY_LIMIT,
        "scope": [SCOPE.namespace, SCOPE.actor] if hasattr(SCOPE, "namespace") else str(SCOPE),
    }


# ------------------------------------------------------------------------------ fixture

def item_map(corpus: dict) -> dict[str, str]:
    """corpus revision id -> sheet item id, reproducing the frozen shuffle."""
    flat = [r for m in corpus["memories"] for r in m["revisions"]]
    order = list(range(len(flat)))
    random.Random(corpus["shuffle_seed"]).shuffle(order)
    return {flat[source]["revision_id"]: f"i{position + 1:02d}" for position, source in enumerate(order)}


def build_fixture(path: Path, corpus: dict, profile: str = "exact") -> tuple[MemoryService, dict, list[str]]:
    """Load every memory at its first revision, then revise forward. Nothing is forgotten.

    Returns the service, a map from live (memory_id, revision_id) to corpus revision id,
    and the load order actually used. Load order is recorded, not asserted away: B1's
    mutation-sequence tiebreaker makes insertion history relevant to ranking by design.
    """
    service = MemoryService(SQLiteRepository(path, index_profile=profile), SCOPE)
    live: dict[tuple[str, str], str] = {}
    order: list[str] = []
    for memory in corpus["memories"]:
        head = None
        receipt = None
        for index, revision in enumerate(memory["revisions"]):
            item = MemoryInput(revision["content"], revision["kind"], tuple(revision["tags"]))
            key = f"{memory['memory_id']}-{index}"
            if index == 0:
                receipt = service.record(item, key)
            else:
                receipt = service.revise(receipt.memory_id, head, item, key)
            head = receipt.revision_id
            live[(receipt.memory_id, receipt.revision_id)] = revision["revision_id"]
            order.append(revision["revision_id"])

    # the loaded head must be the revision the frozen corpus declares current
    for memory in corpus["memories"]:
        declared = [r["revision_id"] for r in memory["revisions"] if r["state"] == "current"]
        stored = {v: k for k, v in live.items()}
        memory_id, revision_id = stored[declared[0]]
        actual = service.get(memory_id).revision_id
        if actual != revision_id:
            raise SystemExit(f"fixture mismatch: {memory['memory_id']} head is not {declared[0]}")
    return service, live, order


def search_all(corpus: dict, service: MemoryService) -> dict[str, list[tuple[str, str]]]:
    return {
        query["query_id"]: [
            (hit.memory_id, hit.revision_id)
            for hit in service.search(SearchQuery(query=query["text"], limit=SEARCH_LIMIT)).hits
        ]
        for query in corpus["queries"]
    }


def expand(service: MemoryService, memory_ids: list[str]) -> set[tuple[str, str]]:
    """Bounded history expansion of RETURNED memories only. Never of a known target."""
    reached: set[tuple[str, str]] = set()
    for memory_id in dict.fromkeys(memory_ids):
        try:
            for entry in service.history(memory_id, limit=HISTORY_LIMIT).entries:
                reached.add((memory_id, entry.revision_id))
        except MemoryNotFound:
            continue
    return reached


# --------------------------------------------------------------------------- evaluation

def ratio(numerator: int, denominator: int) -> float | None:
    """N/A, never 0.0, when the denominator is empty (protocol A1)."""
    return None if denominator == 0 else round(numerator / denominator, 3)


def evaluate(corpus, judgments, live, results, service) -> dict:
    items = item_map(corpus)
    current = {items[r["revision_id"]] for m in corpus["memories"] for r in m["revisions"] if r["state"] == "current"}
    memory_of_item = {items[rev]: mid for (mid, _), rev in live.items()}

    report = {}
    for query in corpus["queries"]:
        qid = query["query_id"]
        judged = judgments["judgments"][qid]
        grade2, grade1 = set(judged["2"]), set(judged["1"])
        eligible_head = grade2 & current

        ranked = results[qid]
        per_k = {}
        for k in CUTOFFS:
            top = ranked[:k]
            returned_items = {items[live[pair]] for pair in top if pair in live}
            # union of search results and history-reached revisions, deduplicated by revision id
            reached = expand(service, [mid for mid, _ in top])
            union_items = returned_items | {items[live[pair]] for pair in reached if pair in live}

            per_k[str(k)] = {
                "returned": len(top),
                "returned_items": sorted(returned_items),
                "head_hits": sorted(returned_items & grade2),
                "expanded_hits": sorted(union_items & grade2),
                "head_recall": ratio(len(returned_items & eligible_head), len(eligible_head)),
                "head_recall_denominator": len(eligible_head),
                "history_recall": ratio(len(union_items & grade2), len(grade2)),
                "history_recall_denominator": len(grade2),
                "precision_at_k": round(len(returned_items & grade2) / k, 3),
                "returned_by_grade": {
                    "2": len(returned_items & grade2),
                    "1": len(returned_items & grade1),
                    "0": len(returned_items - grade2 - grade1),
                },
                "covered": bool(union_items & grade2),
            }

        report[qid] = {
            "text": query["text"],
            "grade2": sorted(grade2),
            "grade1": sorted(grade1),
            "eligible_current_head": sorted(eligible_head),
            "k": per_k,
        }

    # q15 diagnostic: was the memory reachable at all, independent of coverage
    m17 = {mid for (mid, _), rev in live.items() if rev.startswith("m17")}
    report["q15"]["diagnostic_m17_returned"] = {
        str(k): any(mid in m17 for mid, _ in results["q15"][:k]) for k in CUTOFFS
    }
    return report


def aggregate(report: dict, clean: list[str]) -> dict:
    out = {}
    for k in CUTOFFS:
        heads = [report[q]["k"][str(k)]["head_recall"] for q in clean]
        hists = [report[q]["k"][str(k)]["history_recall"] for q in clean]
        heads_ok = [v for v in heads if v is not None]
        hists_ok = [v for v in hists if v is not None]
        out[str(k)] = {
            "head_recall_mean": round(sum(heads_ok) / len(heads_ok), 3) if heads_ok else None,
            "head_recall_n": f"{len(heads_ok)}/{len(clean)}",
            "history_recall_mean": round(sum(hists_ok) / len(hists_ok), 3) if hists_ok else None,
            "history_recall_n": f"{len(hists_ok)}/{len(clean)}",
            "task_coverage": f"{sum(1 for q in clean if report[q]['k'][str(k)]['covered'])}/{len(clean)}",
        }
    return out


# ----------------------------------------------------------------------------- controls

def control_empty_index(corpus: dict) -> dict:
    """Clear ONLY the FTS postings. Authoritative rows and the head projection stay."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "control1.sqlite3"
        service, _, _ = build_fixture(path, corpus)
        db = sqlite3.connect(path)
        try:
            try:
                db.execute("INSERT INTO head_fts(head_fts) VALUES('delete-all')")
            except sqlite3.OperationalError:
                db.execute("DELETE FROM head_fts")
            db.commit()
            memories = db.execute("SELECT count(*) FROM memories WHERE tombstoned=0").fetchone()[0]
            projection = db.execute("SELECT count(*) FROM head_index").fetchone()[0]
            postings = db.execute("SELECT count(*) FROM head_fts").fetchone()[0]
        finally:
            db.close()
        returned = sum(len(hits) for hits in search_all(corpus, service).values())
        return {
            "fixture": "independent",
            "memories_retained": memories,
            "head_projection_retained": projection,
            "fts_postings": postings,
            "results_returned": returned,
            "verdict": "PASS" if returned == 0 and projection > 0 and memories > 0 else "FAIL",
            "note": "Only FTS postings were cleared. A non-zero result would prove retrieval bypasses the index; a zero head projection would mean the control proved less than it claims.",
        }


def control_forget_relevant(corpus: dict, judgments: dict) -> dict:
    """Forget every memory holding a grade-2 item; no revision of one may reappear."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "control2.sqlite3"
        service, live, _ = build_fixture(path, corpus)
        items = item_map(corpus)
        targets = {i for j in judgments["judgments"].values() for i in j["2"]}
        forgotten = set()
        for (memory_id, _), revision in live.items():
            if items[revision] in targets and memory_id not in forgotten:
                service.forget(memory_id, service.get(memory_id).revision_id, f"control-{memory_id}")
                forgotten.add(memory_id)

        results = search_all(corpus, service)
        reappeared = []
        for qid, hits in results.items():
            for memory_id, revision_id in hits:
                if memory_id in forgotten:
                    reappeared.append({"query": qid, "memory": memory_id, "revision": revision_id, "where": "search"})
            for memory_id, revision_id in expand(service, [m for m, _ in hits]):
                if memory_id in forgotten:
                    reappeared.append({"query": qid, "memory": memory_id, "revision": revision_id, "where": "history"})
        return {
            "fixture": "independent",
            "memories_forgotten": len(forgotten),
            "revisions_of_forgotten_memories_returned": len(reappeared),
            "detail": reappeared[:10],
            "verdict": "PASS" if not reappeared else "FAIL",
            "note": "Asserts no revision of a forgotten memory reappears anywhere, regardless of its grade for the query at hand.",
        }


# --------------------------------------------------------------------------------- main

def main() -> None:
    profile = "exact"
    for argument in sys.argv[1:]:
        if argument.startswith("--profile="):
            profile = argument.split("=", 1)[1]
    destination = HERE / ("results-v2.json" if profile == "exact" else f"results-v2-regression-{profile}.json")

    corpus = json.loads((HERE / "corpus-v2.json").read_text())
    judgments = json.loads((HERE / "judgments-v2.json").read_text())
    partition = judgments["partition"]
    clean, flagged, no_answer = partition["clean"], partition["flagged_post_result_authoring"], partition["no_direct_answer"]

    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "eval-v2.sqlite3"
        service, live, order = build_fixture(path, corpus, profile)
        results = search_all(corpus, service)
        again = search_all(corpus, service)
        repeatable = all(results[q] == again[q] for q in results)
        report = evaluate(corpus, judgments, live, results, service)
        headline = aggregate(report, clean)

    record = {
        **run_record(),
        "load_order": order,
        "revision_mapping": {f"{mid}:{rid}": corpus_rev for (mid, rid), corpus_rev in live.items()},
        "item_mapping": item_map(corpus),
        "queries": {q["query_id"]: q["text"] for q in corpus["queries"]},
        "partition": {"clean": clean, "flagged_post_result_authoring": flagged, "no_direct_answer": no_answer},
        "repeatability": {
            "two_passes_same_database": repeatable,
            "claim": "Repeatability only. This does NOT establish independence from ingestion order: B1's mutation-sequence tiebreaker makes insertion history relevant to ranking by design. Load order is recorded above.",
        },
        "per_query": report,
        "headline_clean_unweighted_mean": headline,
        "controls": {
            "empty_index": control_empty_index(corpus),
            "forget_relevant": control_forget_relevant(corpus, judgments),
        },
    }
    record["index_profile"] = profile
    if profile != "exact":
        record["regression_check"] = (
            "Not a fresh evaluation of v2. This configuration was chosen on development data "
            "with v2's failure modes already known; the run says whether a known failure moved."
        )
    destination.write_text(json.dumps(record, indent=2) + "\n")

    # ------------------------------------------------------------------ readable summary
    print(f"build {record['build_commit'][:7]}  src dirty: {record['src_dirty']}")
    for name, digest in record["input_sha256"].items():
        print(f"  sha256 {digest[:16]}…  {name}")
    print(f"\nrepeatability (two passes, one unchanged database): {repeatable}")
    print("  not an ordering-invariance claim; load order recorded in results-v2.json\n")

    print("PER-QUERY   head=search only, hist=search-then-history, N/A=zero denominator")
    print(f"{'q':5s}{'set':9s}{'k':>3s}  {'ret':>4s} {'2/1/0':>8s}  {'head':>11s}  {'hist':>11s}  {'P@k':>5s}")
    for query in corpus["queries"]:
        qid = query["query_id"]
        group = "clean" if qid in clean else ("flagged" if qid in flagged else "no-answer")
        for k in CUTOFFS:
            d = report[qid]["k"][str(k)]
            g = d["returned_by_grade"]
            head = "N/A" if d["head_recall"] is None else f"{d['head_recall']:.3f}"
            hist = "N/A" if d["history_recall"] is None else f"{d['history_recall']:.3f}"
            print(f"{qid if k == CUTOFFS[0] else '':5s}{group if k == CUTOFFS[0] else '':9s}{k:>3d}  "
                  f"{d['returned']:>4d} {g['2']:>2d}/{g['1']}/{g['0']:<3d}  "
                  f"{head:>7s} /{d['head_recall_denominator']:>2d}  {hist:>7s} /{d['history_recall_denominator']:>2d}  {d['precision_at_k']:>5.3f}")

    print(f"\nq15 diagnostic — was m17 returned at all? {report['q15']['diagnostic_m17_returned']}")

    print("\nHEADLINE  unweighted mean over the eleven clean queries")
    for k in CUTOFFS:
        h = headline[str(k)]
        print(f"  k={k:<3d} head recall {h['head_recall_mean']} ({h['head_recall_n']})   "
              f"history recall {h['history_recall_mean']} ({h['history_recall_n']})   "
              f"task coverage {h['task_coverage']}")

    print("\nFLAGGED (post-result authoring; reported, never averaged into the headline)")
    for qid in flagged:
        cells = "  ".join(f"k={k}: {'covered' if report[qid]['k'][str(k)]['covered'] else 'missed'}" for k in CUTOFFS)
        print(f"  {qid} \"{report[qid]['text']}\"  {cells}")

    print("\nNO DIRECT ANSWER (recall undefined; returned results split by grade)")
    for qid in no_answer:
        cells = "  ".join(
            f"k={k}: {report[qid]['k'][str(k)]['returned']} returned "
            f"({report[qid]['k'][str(k)]['returned_by_grade']['1']} grade-1, "
            f"{report[qid]['k'][str(k)]['returned_by_grade']['0']} grade-0)" for k in CUTOFFS)
        print(f"  {qid} \"{report[qid]['text']}\"\n      {cells}")

    print("\nNEGATIVE CONTROLS (independent fixtures)")
    c1, c2 = record["controls"]["empty_index"], record["controls"]["forget_relevant"]
    print(f"  1. FTS postings cleared, {c1['memories_retained']} memories and {c1['head_projection_retained']} "
          f"head-projection rows retained -> {c1['results_returned']} results  {c1['verdict']}")
    print(f"  2. {c2['memories_forgotten']} memories forgotten -> "
          f"{c2['revisions_of_forgotten_memories_returned']} revisions of forgotten memories anywhere  {c2['verdict']}")
    print("\nwrote results-v2.json")


if __name__ == "__main__":
    main()
