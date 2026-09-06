"""T1 morphology experiment harness — development data only.

Nothing this script produces is a v2 result. It loads the development corpus into a
fresh database per index profile, runs every development query through the budgeted
retrieval pipeline registered in benchmarks/eval/v2-development-queries.md, and reports
candidate recall and delivered-evidence recall separately.

Usage:  .venv-sqlite/bin/python benchmarks/dev/run_dev_t1.py [profile ...]
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from budgeted_retrieval import (  # noqa: E402
    DELIVERED_BYTES, DELIVERED_ITEMS, HISTORY_MEMORIES, HISTORY_REVISIONS, POOL_LIMIT, retrieve,
)

CORPUS = Path(__file__).resolve().parent / "corpus-dev1.json"

# Budgets live in budgeted_retrieval, which both harnesses import, so the quality and
# performance runs cannot enforce different limits on the same registered budget.
DIAGNOSTIC_LIMIT = 100     # outside the budget: used only to separate "never a candidate"
                           # from "a candidate that the pool limit cut off"


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
    matched_beyond_pool: list[str] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)
    discovered_revisions: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    delivered_bytes: int = 0
    delivered_bytes_by_grade: dict[str, int] = field(default_factory=lambda: {"2": 0, "1": 0, "0": 0})
    stopped_by: str | None = None
    misses: dict[str, str] = field(default_factory=dict)

    def grade(self, memory_id: str) -> str:
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


def load_corpus() -> dict:
    return json.loads(CORPUS.read_text())


def build(path: Path, corpus: dict, profile: str) -> tuple[MemoryService, dict[str, str]]:
    """Load every memory in file order, replaying revisions so history is real."""
    service = MemoryService(SQLiteRepository(path, index_profile=profile), Scope("dev", "local"))
    fixture_to_memory: dict[str, str] = {}
    revision_map: dict[str, str] = {}
    for memory in corpus["memories"]:
        current_revision = None
        memory_id = None
        for index, revision in enumerate(memory["revisions"]):
            item = MemoryInput(revision["content"], kind=revision["kind"], tags=tuple(revision["tags"]))
            if index == 0:
                receipt = service.record(item, f"{memory['memory_id']}-{index}")
                memory_id = receipt.memory_id
            else:
                receipt = service.revise(memory_id, current_revision, item, f"{memory['memory_id']}-{index}")
            current_revision = receipt.revision_id
            revision_map[revision["revision_id"]] = receipt.revision_id
        fixture_to_memory[memory["memory_id"]] = memory_id
    return service, {
        "memories": fixture_to_memory,
        "revisions": revision_map,
        "to_fixture": {**{v: k for k, v in fixture_to_memory.items()},
                       **{v: k for k, v in revision_map.items()}},
    }


def run_query(service: MemoryService, query: dict, mapping: dict) -> QueryResult:
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

    # --- the budgeted path ----------------------------------------------------------
    # Identical to the function the performance harness times. Label-free by
    # construction: it never sees query["grade2"] or anything derived from it.
    budgeted = retrieve(service, query["text"])
    result.pool = budgeted.pool
    result.pool_provenance = budgeted.pool_provenance
    result.expanded = budgeted.expanded
    result.discovered_revisions = budgeted.discovered_revisions
    result.delivered = budgeted.delivered
    result.delivered_bytes = budgeted.delivered_bytes
    result.stopped_by = budgeted.stopped_by

    # --- reporting, outside the budget and outside the timed path --------------------
    # Grading happens here, after retrieval has finished, so no judgment can reach a
    # decision the budgeted path makes.
    for memory_id, size in budgeted.delivered_item_bytes.items():
        result.delivered_bytes_by_grade[result.grade(memory_id)] += size

    diagnostic = service.search(SearchQuery(query=query["text"], limit=DIAGNOSTIC_LIMIT))
    result.matched_beyond_pool = [h.memory_id for h in diagnostic.hits if h.memory_id not in result.pool]

    # --- miss attribution -----------------------------------------------------------
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
    for revision_id in result.grade2_revisions:
        if revision_id not in result.discovered_revisions:
            result.misses[revision_id] = "history_not_reached"
    return result


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def run_profile(corpus: dict, profile: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "memory.sqlite3"
        service, mapping = build(path, corpus, profile)
        results = [run_query(service, query, mapping) for query in corpus["queries"]]
        sizes = index_sizes(path)
        label = mapping["to_fixture"]

    by_class: dict[str, dict] = {}
    for result in results:
        bucket = by_class.setdefault(result.query_class, {"candidate": [], "delivered": []})
        if result.candidate_recall() is not None:
            bucket["candidate"].append(result.candidate_recall())
            bucket["delivered"].append(result.delivered_recall())
    return {
        "profile": profile,
        "index_bytes": sizes,
        "per_class": {
            name: {"candidate_recall": mean(values["candidate"]),
                   "delivered_recall": mean(values["delivered"]),
                   "queries": len(values["candidate"])}
            for name, values in sorted(by_class.items())
        },
        "grade0_delivered_bytes": sum(r.delivered_bytes_by_grade["0"] for r in results),
        "grade2_delivered_bytes": sum(r.delivered_bytes_by_grade["2"] for r in results),
        "grade1_delivered_bytes": sum(r.delivered_bytes_by_grade["1"] for r in results),
        "per_query": [
            {"query_id": r.query_id, "class": r.query_class, "text": r.text,
             "pool_size": len(r.pool), "delivered": len(r.delivered),
             "delivered_bytes": r.delivered_bytes,
             "candidate_recall": r.candidate_recall(), "delivered_recall": r.delivered_recall(),
             "rank_of_answers": {label[m]: (r.pool.index(m) + 1 if m in r.pool else None)
                                 for m in r.grade2_memories},
             "stopped_by": r.stopped_by,
             "misses": {label[k]: v for k, v in r.misses.items()},
             "revisions_discovered": len(r.discovered_revisions)}
            for r in results
        ],
    }


def index_sizes(path: Path) -> dict[str, int]:
    """Persistent retrieval structures, measured on a checkpointed database.

    Reported per table so an added index cannot hide inside the total, and with the
    whole-database size beside it so a growing outbox cannot be mistaken for one.
    """
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        sizes: dict[str, int] = {}
        for name in ("head_fts", "head_fts_stem", "head_fts_prose", "head_index", "head_tags"):
            exists = db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            if not exists:
                continue
            # Exact names, never a LIKE prefix: 'head_fts%' also matches head_fts_stem's own
            # shadow tables, which double-counts the auxiliary index into the baseline one.
            members = [name] + [f"{name}_{suffix}" for suffix in ("data", "idx", "content", "docsize", "config")]
            members += [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (name,)
            ).fetchall()]
            placeholders = ",".join("?" * len(members))
            total = db.execute(
                f"SELECT sum(pgsize) FROM dbstat WHERE name IN ({placeholders})", members
            ).fetchone()[0]
            sizes[name] = total or 0
        sizes["_retrieval_total"] = sum(value for key, value in sizes.items() if not key.startswith("_"))
        sizes["_database_file"] = path.stat().st_size
        sizes["_page_size"] = page_size
        return sizes
    finally:
        db.close()


def main() -> None:
    profiles = sys.argv[1:] or ["exact", "stem", "dual", "split"]
    corpus = load_corpus()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    record = {
        "experiment": "T1 morphology",
        "development_data": True,
        "not_a_v2_result": True,
        "build_commit": commit,
        "interpreter": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "corpus_sha256": hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        "budgets": {"pool": POOL_LIMIT, "history_memories": HISTORY_MEMORIES,
                    "history_revisions": HISTORY_REVISIONS, "delivered_items": DELIVERED_ITEMS,
                    "delivered_bytes": DELIVERED_BYTES, "diagnostic_limit": DIAGNOSTIC_LIMIT},
        "profiles": [run_profile(corpus, profile) for profile in profiles],
    }
    out = Path(__file__).resolve().parent / "results-dev-t1.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({p["profile"]: p["per_class"] for p in record["profiles"]}, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
