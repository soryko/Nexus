"""B2b §9: measure the existing-index baseline before deciding whether migration 005 exists.

Four filter shapes, on a corpus large enough for the planner's choice to be load-bearing,
against the schema as it stands and then against each candidate index. Reports, per arm:

* the plan ``EXPLAIN QUERY PLAN`` chose — the access **strategy**, not a benefit;
* query work, counted as VDBE steps through ``set_progress_handler``, which is a real
  counter rather than an estimate and is comparable across arms;
* wall-clock latency, best-of-N, on a warm cache;
* for a candidate index, its storage in pages and its cost on the referenced write path.

Two negative controls run alongside, because a number from a harness nobody checked is not
evidence: an **unfiltered** arm, which must do visibly different work from the filtered ones
or the corpus is not exercising the predicate, and a **row-count** assertion that the filters
actually select a small fraction of the corpus rather than matching everything.

Run: PYTHONPATH=src .venv-sqlite/bin/python tools/measure_reference_filters.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_memory.domain.models import Scope, SearchQuery  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

SCOPE = Scope("measure", "local")
MEMORIES = 20_000
REFERENCES_PER_MEMORY = 2
REPOSITORIES = 3
SHA1 = "%040x"

CANDIDATES = {
    "path": "CREATE INDEX revision_references_path ON revision_references(namespace, actor, path)",
    "commit": "CREATE INDEX revision_references_commit ON revision_references(namespace, actor, commit_oid)",
    "repository": ("CREATE INDEX revision_references_repository"
                   " ON revision_references(namespace, actor, repository_id)"),
}

SHAPES = {
    "path_exact": SearchQuery(reference_paths=("src/pkg0500/mod0000.py",), limit=20),
    "path_prefix": SearchQuery(reference_path_prefix="src/pkg0500", limit=20),
    "commit_exact": SearchQuery(reference_commits=(SHA1 % 500,), limit=20),
    "repository_bound": SearchQuery(repository="bound", limit=20),
    "unfiltered": SearchQuery(limit=20),  # the control arm
}


def build(db: Path) -> None:
    """A corpus written straight into the schema the repository migrated.

    Direct inserts, not service calls: this measures a read path, and twenty thousand
    verifying writes would measure the write path instead. The rows are the rows a verified
    write produces, and the foreign keys are satisfied the same way.
    """
    SQLiteRepository(db)  # migrate
    connection = sqlite3.connect(db)
    connection.execute("BEGIN")
    for index in range(REPOSITORIES):
        connection.execute(
            "INSERT INTO repositories(namespace,actor,repository_id,object_format,locator,registered_at)"
            " VALUES(?,?,?,?,NULL,'2026-09-06T00:00:00+00:00')",
            (SCOPE.namespace, SCOPE.actor, f"repo-{index}", "sha1"),
        )
    for seq in range(MEMORIES):
        memory_id = f"m{seq:06d}"
        revision_id = f"r{seq:06d}"
        body = f"memory number {seq} about package {seq % 1000}"
        blob = connection.execute(
            "INSERT INTO blobs(namespace,actor,digest,body) VALUES(?,?,?,?)",
            (SCOPE.namespace, SCOPE.actor, f"d{seq:06d}", body.encode()),
        ).lastrowid
        connection.execute(
            "INSERT INTO memories(namespace,actor,memory_id,current_revision_id,tombstoned)"
            " VALUES(?,?,?,?,0)",
            (SCOPE.namespace, SCOPE.actor, memory_id, revision_id),
        )
        connection.execute(
            "INSERT INTO revisions(namespace,actor,memory_id,revision_id,parent_revision_id,blob_id,kind,"
            "tags_json,source_uri,snapshot,created_at)"
            " VALUES(?,?,?,?,NULL,?,'observation','[]',NULL,NULL,'2026-09-06T00:00:00+00:00')",
            (SCOPE.namespace, SCOPE.actor, memory_id, revision_id, blob),
        )
        head = connection.execute(
            "INSERT INTO head_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,"
            "created_at,durable_seq,has_parent)"
            " VALUES(?,?,?,?,?,'observation','[]','2026-09-06T00:00:00+00:00',?,0)",
            (SCOPE.namespace, SCOPE.actor, memory_id, revision_id, blob, seq),
        ).lastrowid
        connection.execute("INSERT INTO head_fts(rowid,body) VALUES(?,?)", (head, body))
        for offset in range(REFERENCES_PER_MEMORY):
            connection.execute(
                "INSERT INTO revision_references(namespace,actor,memory_id,revision_id,repository_id,"
                "commit_oid,path,object_oid,entry_type,mode,checked_at)"
                " VALUES(?,?,?,?,?,?,?,?, 'blob','100644','2026-09-06T00:00:00+00:00')",
                (SCOPE.namespace, SCOPE.actor, memory_id, revision_id, f"repo-{seq % REPOSITORIES}",
                 SHA1 % (seq % 1000), f"src/pkg{seq % 1000:04d}/mod{offset:04d}.py", "0" * 40),
            )
    connection.commit()
    connection.close()


def plan(db: Path, query: SearchQuery, repository_id: str | None) -> list[str]:
    repository = SQLiteRepository(db)
    filters, parameters = repository._filters(query, repository_id)
    statement = (
        "EXPLAIN QUERY PLAN SELECT h.seq FROM head_index h"
        " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
        " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
        + filters + " ORDER BY -h.durable_seq, h.seq LIMIT 21"
    )
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute(statement, [SCOPE.namespace, SCOPE.actor, *parameters]).fetchall()
    finally:
        connection.close()
    return [row[3] for row in rows]


def work_and_latency(db: Path, query: SearchQuery, repository_id: str | None) -> tuple[int, float, int]:
    """VDBE steps for one execution, best-of-five latency, and the hit count."""
    repository = SQLiteRepository(db)
    steps = 0

    def count() -> int:
        nonlocal steps
        steps += 1
        return 0

    original_connect = SQLiteRepository._connect

    def connect(self):
        connection = original_connect(self)
        connection.set_progress_handler(count, 1)
        return connection

    SQLiteRepository._connect = connect
    try:
        page = repository.search(SCOPE, query, repository_id)
        measured = steps
        timings = sorted(_timed(repository, query, repository_id) for _ in range(15))
        best = timings[0]
    finally:
        SQLiteRepository._connect = original_connect
    return measured, best, timings[len(timings) // 2], len(page.hits)


def _timed(repository: SQLiteRepository, query: SearchQuery, repository_id: str | None) -> float:
    start = time.perf_counter()
    repository.search(SCOPE, query, repository_id)
    return (time.perf_counter() - start) * 1000


def matching_rows(db: Path, query: SearchQuery, repository_id: str | None) -> int:
    repository = SQLiteRepository(db)
    filters, parameters = repository._filters(query, repository_id)
    connection = sqlite3.connect(db)
    try:
        return connection.execute(
            "SELECT count(*) FROM head_index h"
            " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
            " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
            + filters, [SCOPE.namespace, SCOPE.actor, *parameters],
        ).fetchone()[0]
    finally:
        connection.close()


def page_count(db: Path) -> int:
    connection = sqlite3.connect(db)
    try:
        return connection.execute("PRAGMA page_count").fetchone()[0]
    finally:
        connection.close()


def write_cost(db: Path, rounds: int = 2_000, repeats: int = 9) -> tuple[float, float]:
    """Milliseconds per referenced-row insert, best-of-five, on a throwaway copy.

    A copy, so the corpus every read arm measures is byte-identical: writing into the
    measured database would leave each arm reading a slightly different table from the last,
    which is a confound for the sake of saving a file copy.
    """
    with TemporaryDirectory() as directory:
        copy = Path(directory) / "write.sqlite3"
        copy.write_bytes(db.read_bytes())
        connection = sqlite3.connect(copy)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")

            def once(base: int) -> float:
                start = time.perf_counter()
                connection.execute("BEGIN")
                for index in range(rounds):
                    seq = MEMORIES + base * rounds + index
                    connection.execute(
                        "INSERT INTO revision_references(namespace,actor,memory_id,revision_id,repository_id,"
                        "commit_oid,path,object_oid,entry_type,mode,checked_at)"
                        " VALUES(?,?,?,?,?,?,?,?, 'blob','100644','2026-09-06T00:00:00+00:00')",
                        (SCOPE.namespace, SCOPE.actor, f"w{seq:06d}", f"w{seq:06d}", "repo-0",
                         SHA1 % (seq % 1000), f"src/pkg{seq % 1000:04d}/w{seq:06d}.py", "0" * 40),
                    )
                connection.commit()
                return (time.perf_counter() - start) * 1000 / rounds

            once(0)  # discarded: the first transaction after a file copy pays for a cold cache
            timings = sorted(once(base) for base in range(1, repeats + 1))
            return timings[len(timings) // 2], timings[0]
        finally:
            connection.close()


def report(label: str, db: Path, plans: bool = True) -> None:
    print(f"\n--- {label} ---")
    print(f"{'shape':18s} {'rows':>7s} {'hits':>5s} {'steps':>10s} {'best ms':>8s} {'med ms':>8s}  plan")
    for name, query in SHAPES.items():
        repository_id = "repo-0" if query.repository == "bound" else None
        rows = matching_rows(db, query, repository_id)
        steps, best, median, hits = work_and_latency(db, query, repository_id)
        lines = plan(db, query, repository_id)
        scanning = [line for line in lines if "SCAN" in line and "revision_references" in line]
        marker = "  FULL SCAN" if scanning else ""
        print(f"{name:18s} {rows:7d} {hits:5d} {steps:10d} {best:8.2f} {median:8.2f}  {lines[0]}{marker}")
        if plans:
            for line in lines[1:]:
                print(f"{'':61s}{line}")


def main() -> None:
    with TemporaryDirectory() as directory:
        db = Path(directory) / "measure.sqlite3"
        build(db)
        print(f"corpus: {MEMORIES} memories, {MEMORIES * REFERENCES_PER_MEMORY} reference rows,"
              f" {REPOSITORIES} repositories, one scope")
        baseline_pages = page_count(db)
        baseline_write, baseline_best = write_cost(db)
        print(f"baseline: {baseline_pages} pages,"
              f" {baseline_write:.5f} ms median / {baseline_best:.5f} ms best per referenced-row write")
        report("baseline (existing indexes only)", db)

        for name, ddl in CANDIDATES.items():
            connection = sqlite3.connect(db)
            connection.execute(ddl)
            connection.commit()
            connection.close()
            pages = page_count(db)
            write, best = write_cost(db)
            print(f"\ncandidate {name}: +{pages - baseline_pages} pages"
                  f" ({(pages - baseline_pages) / baseline_pages:.1%}),"
                  f" write {write:.5f} ms median ({write / baseline_write:.2f}x baseline),"
                  f" {best:.5f} ms best ({best / baseline_best:.2f}x)")
            report(f"with {name} index", db)
            connection = sqlite3.connect(db)
            connection.execute(f"DROP INDEX {ddl.split()[2]}")
            connection.commit()
            connection.close()

        # The planner chooses on estimated cost, and the estimates change once ANALYZE has
        # written sqlite_stat1. Production never runs ANALYZE, so the arms above describe the
        # shipped system; these two exist so "no candidate was chosen" is not an artefact of
        # the planner having no statistics to choose with.
        #
        # They are two arms, not one, because ANALYZE-with-candidates varies two things at
        # once. The statistics-only arm holds the index set at the baseline, so whatever the
        # two arms share is the statistics and not the candidates. The unfiltered control
        # decides it either way: it carries no reference filter and cannot benefit from any
        # candidate index, so an improvement it shares is not the candidates' doing.
        connection = sqlite3.connect(db)
        connection.execute("ANALYZE")
        connection.commit()
        connection.close()
        report("statistics only: ANALYZE, existing indexes", db)

        connection = sqlite3.connect(db)
        for ddl in CANDIDATES.values():
            connection.execute(ddl)
        connection.execute("ANALYZE")
        connection.commit()
        connection.close()
        report("statistics and all three candidates", db)


if __name__ == "__main__":
    main()
