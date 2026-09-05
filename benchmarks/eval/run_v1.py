"""Execute the registered v1 measurement protocol against the frozen benchmark.

Reads protocol-v1.md for the rules this implements. History is expanded only for
memory IDs that search actually returned: injecting known targets would make this
an oracle-assisted test rather than a measurement.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

HERE = Path(__file__).parent
CUTOFFS = (5, 10, 20)
HISTORY_LIMIT = 20


def load(path: Path):
    corpus = json.loads((HERE / "corpus-v1.json").read_text())
    judgments = json.loads((HERE / "judgments-v1.json").read_text())
    service = MemoryService(SQLiteRepository(path), Scope("eval-v1", "local"))

    # item id <-> revision, reproducing the frozen shuffle
    import random
    flat = [{"memory_id": m["memory_id"], **r} for m in corpus["memories"] for r in m["revisions"]]
    order = list(range(len(flat)))
    random.Random(corpus["shuffle_seed"]).shuffle(order)
    item_of_revision = {flat[src]["revision_id"]: f"i{pos + 1:02d}" for pos, src in enumerate(order)}

    # record each memory at its first revision, then revise forward
    live = {}
    for memory in corpus["memories"]:
        head = None
        for index, revision in enumerate(memory["revisions"]):
            item = MemoryInput(revision["content"], revision["kind"], tuple(revision["tags"]))
            if index == 0:
                receipt = service.record(item, f"{memory['memory_id']}-{index}")
            else:
                receipt = service.revise(receipt.memory_id, head, item, f"{memory['memory_id']}-{index}")
            head = receipt.revision_id
            live[revision["revision_id"]] = (receipt.memory_id, receipt.revision_id)
    return corpus, judgments, service, item_of_revision, live


def run_queries(corpus, service, live):
    """Return, per query, the ranked memory ids and the revision each hit exposes."""
    stored_revision = {mid: rid for (mid, rid) in live.values()}
    results = {}
    for query in corpus["queries"]:
        page = service.search(SearchQuery(query=query["text"], limit=max(CUTOFFS)))
        results[query["query_id"]] = [(hit.memory_id, hit.revision_id) for hit in page.hits]
    return results, stored_revision


def evaluate(corpus, judgments, service, item_of_revision, live, results):
    reverse = {v: k for k, v in live.items()}          # (memory_id, revision_id) -> corpus revision id
    memory_of_item = {}
    for corpus_revision, (memory_id, revision_id) in live.items():
        memory_of_item[item_of_revision[corpus_revision]] = memory_id

    report = {}
    for query in corpus["queries"]:
        qid = query["query_id"]
        judged = judgments["judgments"][qid]
        relevant = set(judged["2"])
        eligible = {i for i in relevant if memory_of_item[i] and _is_head(service, memory_of_item[i], i, item_of_revision, live)}
        ranked = results[qid]

        per_k = {}
        for k in CUTOFFS:
            top = ranked[:k]
            # current-head retrieval: the revision the hit exposes
            found_head = set()
            for memory_id, revision_id in top:
                corpus_revision = reverse.get((memory_id, revision_id))
                if corpus_revision:
                    found_head.add(item_of_revision[corpus_revision])
            # search-then-history: expand ONLY retrieved memories
            expanded = set(found_head)
            for memory_id, _ in top:
                for entry in service.history(memory_id, limit=HISTORY_LIMIT).entries:
                    corpus_revision = reverse.get((memory_id, entry.revision_id))
                    if corpus_revision:
                        expanded.add(item_of_revision[corpus_revision])
            per_k[k] = {
                "returned": len(top),
                "head_hits": sorted(found_head & relevant),
                "expanded_hits": sorted(expanded & relevant),
                "head_recall": _ratio(len(found_head & eligible), len(eligible)),
                "expanded_recall": _ratio(len(expanded & relevant), len(relevant)),
                "precision": _ratio(len(found_head & relevant), len(top)),
                "covered": bool(expanded & relevant),
            }
        report[qid] = {"relevant": sorted(relevant), "eligible_current": sorted(eligible), "k": per_k}
    return report


def _is_head(service, memory_id, item, item_of_revision, live) -> bool:
    for corpus_revision, (mid, rid) in live.items():
        if item_of_revision[corpus_revision] == item:
            try:
                return service.get(mid).revision_id == rid
            except Exception:
                return False
    return False


def _ratio(numerator: int, denominator: int):
    return None if denominator == 0 else round(numerator / denominator, 3)


def main() -> None:
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "eval.sqlite3"
        corpus, judgments, service, item_of_revision, live = load(path)
        results, _ = run_queries(corpus, service, live)

        # determinism: identical orderings on a second pass
        again, _ = run_queries(corpus, service, live)
        deterministic = all(results[q] == again[q] for q in results)

        report = evaluate(corpus, judgments, service, item_of_revision, live, results)

        clean = judgments["disclosure"]["headline_eligibility"]["clean_relevance_queries"]
        flagged = judgments["disclosure"]["headline_eligibility"]["report_separately"]
        abstention = judgments["disclosure"]["headline_eligibility"]["abstention_queries"]

        print(f"determinism (two identical passes): {deterministic}\n")
        print("PER-QUERY  (2=grade-2 items; head=search only; hist=search-then-history)")
        print(f"{'q':5s}{'set':9s}{'rel':4s}{'elig':5s}  " + "  ".join(f"k={k}: head/hist" for k in CUTOFFS))
        for qid in [q["query_id"] for q in corpus["queries"]]:
            row = report[qid]
            group = "clean" if qid in clean else ("abstain" if qid in abstention else "flagged")
            cells = []
            for k in CUTOFFS:
                d = row["k"][k]
                cells.append(f"{len(d['head_hits'])}/{len(d['expanded_hits'])} of {len(row['relevant'])}".ljust(14))
            print(f"{qid:5s}{group:9s}{len(row['relevant']):<4d}{len(row['eligible_current']):<5d}  " + "  ".join(cells))

        print("\nHEADLINE  (seven clean relevance queries only)")
        for k in CUTOFFS:
            heads = [report[q]["k"][k]["head_recall"] for q in clean if report[q]["k"][k]["head_recall"] is not None]
            exps = [report[q]["k"][k]["expanded_recall"] for q in clean]
            cov = sum(1 for q in clean if report[q]["k"][k]["covered"])
            print(f"  k={k:<3d} current-head recall {sum(heads)/len(heads):.3f}   "
                  f"search-then-history recall {sum(exps)/len(exps):.3f}   "
                  f"task coverage {cov}/{len(clean)}")

        print("\nFLAGGED  (reported, never averaged into the headline)")
        for k in CUTOFFS:
            cov = sum(1 for q in flagged if report[q]["k"][k]["covered"])
            print(f"  k={k:<3d} task coverage {cov}/{len(flagged)}")

        print("\nABSTENTION  (no relevant items; recall undefined and not reported)")
        print("  exposure: the implementer disclosed that two queries have no answer, without saying which.")
        for qid in abstention:
            counts = ", ".join(f"k={k}: {report[qid]['k'][k]['returned']} returned" for k in CUTOFFS)
            print(f"  {qid}: {counts}")

        # --- negative controls, same corpus and configuration ---
        print("\nNEGATIVE CONTROLS")
        db = sqlite3.connect(path)
        try:
            db.execute("DELETE FROM head_fts")
            db.execute("DELETE FROM head_tags")
            db.execute("DELETE FROM head_index")
            db.commit()
        finally:
            db.close()
        emptied, _ = run_queries(corpus, service, live)
        total = sum(len(v) for v in emptied.values())
        heads_present = sqlite3.connect(path).execute("SELECT count(*) FROM memories WHERE tombstoned=0").fetchone()[0]
        print(f"  1. emptied index, {heads_present} eligible memories still present -> {total} results returned "
              f"({'PASS' if total == 0 else 'FAIL: retrieval is bypassing the index'})")

    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "eval2.sqlite3"
        corpus, judgments, service, item_of_revision, live = load(path)
        targets = {item for q in judgments["judgments"].values() for item in q["2"]}
        forgotten = set()
        for corpus_revision, (memory_id, revision_id) in live.items():
            if item_of_revision[corpus_revision] in targets and memory_id not in forgotten:
                head = service.get(memory_id).revision_id
                service.forget(memory_id, head, f"control-{memory_id}")
                forgotten.add(memory_id)
        after, _ = run_queries(corpus, service, live)
        report2 = evaluate(corpus, judgments, service, item_of_revision, live, after)
        reappeared = sum(len(report2[q]["k"][20]["expanded_hits"]) for q in report2)
        print(f"  2. forgot {len(forgotten)} memories holding grade-2 items -> {reappeared} grade-2 results "
              f"({'PASS' if reappeared == 0 else 'FAIL: search is not reading current state'})")


if __name__ == "__main__":
    main()
