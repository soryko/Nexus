"""Deterministic fixture for the Atlas benchmark, and the assertions that define it.

``check_fixture`` is the contract and is run before and after generation; a store that does not meet it
is not a benchmark subject. The generator writes through the ordinary service (so the search index and
revisions are real) and adds recorded references with SQL, because references normally need a Git checkout.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

HUB_PATH = "src/hub.py"
FOREIGN_NAMESPACE = "bench-foreign"
REPOSITORY = "repo-bench"
COMMIT = "a" * 40
STAMP = "2026-11-06T00:00:00Z"
KINDS = ("observation", "decision", "constraint", "procedure", "failure")
TAGS = ("queue", "retention", "billing", "search", "release", "ops")
FILE_POOL = 300


@dataclass(frozen=True)
class Spec:
    memories: int = 10_000        # active, in scope
    hub_citers: int = 1_200       # distinct current revisions citing HUB_PATH
    hub_min: int = 1_000          # the contract: at least this many
    revised: int = 500            # memories with a second revision
    forgotten: int = 50           # tombstoned controls
    foreign: int = 300            # another scope, overlapping text and paths


def check_fixture(database: Path, scope: Scope, spec: Spec) -> list[str]:
    """Return the unmet requirements; an empty list means the store is a valid benchmark subject."""
    problems: list[str] = []
    if not Path(database).exists():
        return [f"store missing: {database}"]
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        one = lambda sql, *a: db.execute(sql, a).fetchone()[0]   # noqa: E731
        ns, actor = scope.namespace, scope.actor
        active = one("SELECT count(*) FROM memories WHERE namespace=? AND actor=? AND tombstoned=0", ns, actor)
        if active != spec.memories:
            problems.append(f"active scoped memories: {active}, required exactly {spec.memories}")
        hub = db.execute(
            """SELECT count(DISTINCT m.memory_id) FROM revision_references r JOIN memories m
               ON m.namespace=r.namespace AND m.actor=r.actor AND m.memory_id=r.memory_id AND m.current_revision_id=r.revision_id
               WHERE r.namespace=? AND r.actor=? AND m.tombstoned=0 GROUP BY r.repository_id, r.path
               ORDER BY 1 DESC LIMIT 1""", (ns, actor)).fetchone()
        if not hub or hub[0] < spec.hub_min:
            problems.append(f"hub file: most current citations to one file {hub[0] if hub else 0}, required at least {spec.hub_min}")
        isolated = one(
            """SELECT count(*) FROM memories m JOIN revisions v ON v.namespace=m.namespace AND v.actor=m.actor
               AND v.memory_id=m.memory_id AND v.revision_id=m.current_revision_id
               WHERE m.namespace=? AND m.actor=? AND m.tombstoned=0 AND v.tags_json='[]'
               AND NOT EXISTS (SELECT 1 FROM revision_references r WHERE r.namespace=m.namespace AND r.actor=m.actor AND r.memory_id=m.memory_id)""",
            ns, actor)
        if isolated < 1:
            problems.append("isolated memory (no tags, no references): none")
        revisions = one("SELECT count(*) FROM revisions WHERE namespace=? AND actor=? AND parent_revision_id IS NOT NULL", ns, actor)
        if revisions < spec.revised:
            problems.append(f"revision chains: {revisions} revised, required at least {spec.revised}")
        forgotten = one("SELECT count(*) FROM memories WHERE namespace=? AND actor=? AND tombstoned=1", ns, actor)
        if forgotten < min(1, spec.forgotten) or forgotten != spec.forgotten:
            problems.append(f"forgotten controls: {forgotten}, required {spec.forgotten}")
        foreign = one("SELECT count(*) FROM memories WHERE namespace=?", FOREIGN_NAMESPACE)
        foreign_hub = one("SELECT count(*) FROM revision_references WHERE namespace=? AND path=?", FOREIGN_NAMESPACE, HUB_PATH)
        if foreign < spec.foreign or foreign_hub < 1:
            problems.append(f"foreign scope control: {foreign} memories, {foreign_hub} citing the hub path; required {spec.foreign} and at least 1")
    finally:
        db.close()
    return problems


def _text(n: int, who: str) -> str:
    topic = ("queue retention", "billing export", "search ranking", "release checklist", "incident review", "cache policy")[n % 6]
    return f"Benchmark memory {n:05d} bm{n:05d} about {topic} for the {who} scope. Decision log entry {n % 97}."


def build_fixture(database: Path, scope: Scope, spec: Spec, progress=None) -> dict:
    """Create the store contents. Deterministic: the same spec yields the same shape and text."""
    service = MemoryService(SQLiteRepository(database), scope)
    stratum_of: dict[int, str] = {}
    cited_range = range(spec.hub_citers, spec.hub_citers + max(spec.memories // 3, 1))
    isolated_start = spec.memories - max(spec.memories // 4, 1)
    revised_pick = [n for n in cited_range if n % 5 == 0][: spec.revised]
    if len(revised_pick) < spec.revised:                       # small specs: top up from anywhere ordinary
        extra = [n for n in range(spec.hub_citers, spec.memories) if n not in revised_pick]
        revised_pick += extra[: spec.revised - len(revised_pick)]
    ids: dict[int, str] = {}
    for n in range(spec.memories + spec.forgotten):
        isolated = isolated_start <= n < spec.memories
        tags = () if isolated or n < spec.hub_citers and n % 2 else (TAGS[n % 6],)
        ids[n] = service.record(MemoryInput(_text(n, "bench"), kind=KINDS[n % 5], tags=tags), f"b-{n}").memory_id
        if progress and n % 1000 == 0:
            progress(f"recorded {n}")
    revision: dict[int, str] = {}
    for n in revised_pick:
        view = service.get(ids[n])
        revision[n] = service.revise(ids[n], view.revision_id, MemoryInput(_text(n, "bench") + " Revised: the first wording was superseded.",
                                                                      kind=KINDS[n % 5], tags=(TAGS[n % 6],)), f"r-{n}").revision_id
    foreign = MemoryService(SQLiteRepository(database), Scope(FOREIGN_NAMESPACE, "local"))
    foreign_ids = [foreign.record(MemoryInput(_text(n, "foreign"), kind=KINDS[n % 5], tags=(TAGS[n % 6],)), f"f-{n}").memory_id
                   for n in range(spec.foreign)]
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for namespace in (scope.namespace, FOREIGN_NAMESPACE):
            db.execute("INSERT OR IGNORE INTO repositories VALUES(?,?,?,?,?,?)", (namespace, "local", REPOSITORY, "sha1", None, STAMP))

        def cite(namespace: str, memory_id: str, path: str) -> None:
            head = db.execute("SELECT current_revision_id FROM memories WHERE namespace=? AND actor='local' AND memory_id=?",
                              (namespace, memory_id)).fetchone()[0]
            db.execute("INSERT OR IGNORE INTO revision_references VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (namespace, "local", memory_id, head, REPOSITORY, COMMIT, path, "c" * 40, "blob", "100644", STAMP))

        for n in range(spec.hub_citers):
            cite(scope.namespace, ids[n], HUB_PATH)
            stratum_of[n] = "hub"
        for n in cited_range:
            for step in range(1 + n % 3):
                cite(scope.namespace, ids[n], f"src/mod{(n * 7 + step * 31) % FILE_POOL:03d}.py")
            stratum_of.setdefault(n, "ordinary")
        for n in revised_pick:
            stratum_of[n] = "revised"
            cite(scope.namespace, ids[n], f"src/mod{n % FILE_POOL:03d}.py")
        for n, memory_id in enumerate(foreign_ids):
            cite(FOREIGN_NAMESPACE, memory_id, HUB_PATH)
        for n in range(isolated_start, spec.memories):
            stratum_of[n] = "isolated"
    for n in range(spec.memories, spec.memories + spec.forgotten):
        service.forget(ids[n], service.get(ids[n]).revision_id, f"x-{n}")
    strata: dict[str, list[dict]] = {"hub": [], "revised": [], "isolated": [], "ordinary": []}
    for n in range(spec.memories):
        label = stratum_of.get(n, "ordinary")
        strata[label].append({"memory_id": ids[n], "token": f"bm{n:05d}"})
    return {
        "counts": {"active": spec.memories, "hub_citers": spec.hub_citers, "revised": len(revised_pick),
                   "forgotten": spec.forgotten, "foreign": spec.foreign, "isolated": len(strata["isolated"])},
        "hub": {"path": HUB_PATH, "repository_id": REPOSITORY},
        "strata": strata,
    }
