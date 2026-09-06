"""T2 historical-vocabulary harness — development data only.

Nothing this script produces is a v2 result. It loads `corpus-dev2.json` into a fresh
database, runs every development query through the budgeted retrieval path registered in
benchmarks/eval/v2-development-queries.md, and reports candidate recall, delivered
recall, and — new in T2 — whether a matched *superseded revision* was merely discovered
or actually delivered, with the provenance that came with it.

The distinction is the point. T1 recorded `d22r1` as reached because history expansion
listed its id; the answer text was never delivered. The charter is explicit that a
surviving revision id is discovery, not evidence, so this harness scores the two apart.

Policies are registered before they run. `baseline` is the pinned `stem` control and is
not one of the three configuration-search slots.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t2.py [policy ...]
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from nexus_memory.domain.models import SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
import corpus as corpus_module  # noqa: E402
from budgeted_retrieval import (  # noqa: E402
    DELIVERED_BYTES, DELIVERED_ITEMS, HISTORY_MEMORIES, HISTORY_REVISIONS, POOL_LIMIT,
    HISTORY_HITS, HISTORY_POLICIES, POLICY_HISTORY_PROFILE, retrieve,
)
from run_dev_t1 import index_sizes  # noqa: E402

CORPUS = HERE / "corpus-dev2.json"
BASELINE_PROFILE = "stem"   # pinned by T1-C; the T2 baseline is stem with policy `baseline`
DIAGNOSTIC_LIMIT = 100      # outside the budget: separates "never a candidate" from
                            # "a candidate the pool limit cut off"


@dataclass
class QueryResult:
    query_id: str
    query_class: str
    text: str
    grade2_memories: list[str]
    grade2_revisions: list[str]
    grade1_memories: list[str]
    pool: list[str] = field(default_factory=list)
    pool_provenance: dict[str, str] = field(default_factory=dict)
    pool_from_history: list[str] = field(default_factory=list)
    fusion: dict = field(default_factory=dict)
    matched_beyond_pool: list[str] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)
    discovered_revisions: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    delivered_provenance: dict[str, str] = field(default_factory=dict)
    delivered_bytes: int = 0
    delivered_bytes_by_grade: dict[str, int] = field(default_factory=lambda: {"2": 0, "1": 0, "0": 0})
    paired_revisions: dict[str, str] = field(default_factory=dict)
    history_budget_denied: list[str] = field(default_factory=list)
    history_slots_used: int = 0
    stopped_by: str | None = None
    misses: dict[str, str] = field(default_factory=dict)

    def grade(self, key: str) -> str:
        """Grade one delivered item.

        A paired policy can deliver a memory's head *and* one superseded revision, so the
        unit graded is the delivered item, not the memory. A superseded revision is
        grade 2 only where the labels say that revision answers the query; delivering
        obsolete text to a query that asked about the present is grade 0, which is what
        makes the volume cost of paired delivery visible.
        """
        memory_id, _, revision_id = key.partition("@")
        if revision_id:
            return "2" if revision_id in self.grade2_revisions else "0"
        if memory_id in self.grade2_memories:
            return "2"
        if memory_id in self.grade1_memories:
            return "1"
        return "0"

    def candidate_recall(self) -> float | None:
        if not self.grade2_memories:
            return None
        found = sum(1 for memory_id in self.grade2_memories if memory_id in self.pool)
        return found / len(self.grade2_memories)

    def delivered_recall(self) -> float | None:
        if not self.grade2_memories:
            return None
        found = sum(1 for memory_id in self.grade2_memories if memory_id in self.delivered)
        return found / len(self.grade2_memories)

    def obsolete_delivered(self) -> list[str]:
        """Superseded revisions delivered that no label says answer this query."""
        return [key for key in self.delivered_provenance
                if "@" in key and self.delivered_provenance[key] not in self.grade2_revisions]

    def revision_discovery_recall(self) -> float | None:
        """A revision id that came back. Discovery, not evidence — reported separately."""
        if not self.grade2_revisions:
            return None
        found = sum(1 for r in self.grade2_revisions if r in self.discovered_revisions)
        return found / len(self.grade2_revisions)

    def revision_delivered_recall(self) -> float | None:
        """The revision's *content* delivered inside the budget. The T2 target."""
        if not self.grade2_revisions:
            return None
        delivered = set(self.delivered_provenance.values())
        found = sum(1 for r in self.grade2_revisions if r in delivered)
        return found / len(self.grade2_revisions)


def run_query(service: MemoryService, query: dict, mapping: dict, policy: str) -> QueryResult:
    to_memory = mapping["memories"]
    to_revision = mapping["revisions"]
    result = QueryResult(
        query_id=query["query_id"],
        query_class=query["class"],
        text=query["text"],
        grade2_memories=[to_memory[m] for m in query.get("grade2", [])],
        grade2_revisions=[to_revision[r] for r in query.get("grade2_revisions", [])],
        grade1_memories=[to_memory[m] for m in query.get("grade1", [])],
    )

    # --- the budgeted path ------------------------------------------------------------
    # The same function the T2 performance harness times, and label-free by construction:
    # it never sees query["grade2"] or anything derived from it.
    budgeted = retrieve(service, query["text"], policy=policy)
    result.pool = budgeted.pool
    result.pool_provenance = budgeted.pool_provenance
    result.pool_from_history = budgeted.pool_from_history
    result.fusion = budgeted.fusion
    result.expanded = budgeted.expanded
    result.discovered_revisions = budgeted.discovered_revisions
    result.delivered = budgeted.delivered
    result.delivered_provenance = budgeted.delivered_provenance
    result.delivered_bytes = budgeted.delivered_bytes
    result.paired_revisions = budgeted.paired_revisions
    result.history_budget_denied = budgeted.history_budget_denied
    result.history_slots_used = budgeted.history_slots_used
    result.stopped_by = budgeted.stopped_by

    # --- reporting, outside the budget and outside any timed section -------------------
    for memory_id, size in budgeted.delivered_item_bytes.items():
        result.delivered_bytes_by_grade[result.grade(memory_id)] += size

    diagnostic = service.search(SearchQuery(query=query["text"], limit=DIAGNOSTIC_LIMIT))
    result.matched_beyond_pool = [h.memory_id for h in diagnostic.hits if h.memory_id not in result.pool]

    # --- miss attribution -------------------------------------------------------------
    for memory_id in result.grade2_memories:
        if memory_id in result.delivered:
            continue
        if memory_id not in result.pool:
            reason = "pool_overflow" if memory_id in result.matched_beyond_pool else "candidate_generation"
        elif result.stopped_by == "delivered_byte_cap":
            reason = "delivered_byte_cap"
        else:
            reason = "delivered_item_cap"
        result.misses[memory_id] = reason
    delivered_revisions = set(result.delivered_provenance.values())
    for revision_id in result.grade2_revisions:
        if revision_id in delivered_revisions:
            continue
        if revision_id in result.discovered_revisions:
            result.misses[revision_id] = "discovered_not_delivered"
        else:
            result.misses[revision_id] = "history_not_reached"
    return result


def _label(label: dict, key: str) -> str:
    """Fixture-facing name for a delivered item, which may be `memory@revision`."""
    memory_id, _, revision_id = key.partition("@")
    if revision_id:
        return f"{label.get(memory_id, memory_id)}@{label.get(revision_id, revision_id)}"
    return label.get(memory_id, memory_id)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def run_policy(corpus: dict, policy: str, profile: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "memory.sqlite3"
        service, mapping = corpus_module.build(path, corpus, profile,
                                               POLICY_HISTORY_PROFILE[policy])
        results = [run_query(service, query, mapping, policy) for query in corpus["queries"]]
        sizes = index_sizes(path)
        label = mapping["to_fixture"]

    by_class: dict[str, dict] = {}
    for result in results:
        bucket = by_class.setdefault(result.query_class, {"candidate": [], "delivered": []})
        if result.candidate_recall() is not None:
            bucket["candidate"].append(result.candidate_recall())
            bucket["delivered"].append(result.delivered_recall())

    revision_discovery = [r.revision_discovery_recall() for r in results if r.revision_discovery_recall() is not None]
    revision_delivered = [r.revision_delivered_recall() for r in results if r.revision_delivered_recall() is not None]
    return {
        "policy": policy,
        "index_profile": profile,
        "index_bytes": sizes,
        "per_class": {
            name: {"candidate_recall": mean(values["candidate"]),
                   "delivered_recall": mean(values["delivered"]),
                   "queries": len(values["candidate"])}
            for name, values in sorted(by_class.items())
        },
        "revision_discovery_recall": mean(revision_discovery),
        "revision_delivered_recall": mean(revision_delivered),
        "obsolete_revisions_delivered": sum(len(r.obsolete_delivered()) for r in results),
        "grade0_delivered_bytes": sum(r.delivered_bytes_by_grade["0"] for r in results),
        "grade1_delivered_bytes": sum(r.delivered_bytes_by_grade["1"] for r in results),
        "grade2_delivered_bytes": sum(r.delivered_bytes_by_grade["2"] for r in results),
        "per_query": [
            {"query_id": r.query_id, "class": r.query_class, "text": r.text,
             "pool_size": len(r.pool), "pool_from_history": [label[m] for m in r.pool_from_history],
             "fusion": r.fusion,
             "delivered": len(r.delivered), "delivered_bytes": r.delivered_bytes,
             "delivered_provenance": {_label(label, key): label.get(rev, rev)
                                      for key, rev in r.delivered_provenance.items()},
             "obsolete_delivered": [_label(label, key) for key in r.obsolete_delivered()],
             "history_budget_denied": [label[m] for m in r.history_budget_denied],
             "candidate_recall": r.candidate_recall(), "delivered_recall": r.delivered_recall(),
             "revision_discovery_recall": r.revision_discovery_recall(),
             "revision_delivered_recall": r.revision_delivered_recall(),
             "rank_of_answers": {label[m]: (r.pool.index(m) + 1 if m in r.pool else None)
                                 for m in r.grade2_memories},
             "history_slots_used": r.history_slots_used,
             "stopped_by": r.stopped_by,
             "misses": {label.get(k, k): v for k, v in r.misses.items()},
             "revisions_discovered": len(r.discovered_revisions)}
            for r in results
        ],
    }


def main() -> None:
    policies = sys.argv[1:] or ["baseline"]
    for policy in policies:
        if policy not in HISTORY_POLICIES:
            raise SystemExit(f"unregistered policy: {policy}; registered: {HISTORY_POLICIES}")
    corpus = corpus_module.load(CORPUS.name)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    record = {
        "experiment": "T2 historical vocabulary",
        "development_data": True,
        "not_a_v2_result": True,
        "build_commit": commit,
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "corpus": CORPUS.name,
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "index_profile": BASELINE_PROFILE,
        "fusion_hits": {"head": POOL_LIMIT, "history": HISTORY_HITS},
        "budgets": {"pool": POOL_LIMIT, "history_memories": HISTORY_MEMORIES,
                    "history_revisions": HISTORY_REVISIONS, "delivered_items": DELIVERED_ITEMS,
                    "delivered_bytes": DELIVERED_BYTES, "diagnostic_limit": DIAGNOSTIC_LIMIT},
        "policies": [run_policy(corpus, policy, BASELINE_PROFILE) for policy in policies],
    }
    suffix = "-".join(policies)
    out = HERE / (f"results-dev-t2-{suffix}.json" if policies != ["baseline"] else "results-dev-t2-baseline.json")
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({p["policy"]: p["per_class"] for p in record["policies"]}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
