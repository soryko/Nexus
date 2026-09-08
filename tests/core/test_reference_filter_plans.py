"""B2b acceptance test 16: no filter shape full-scans ``revision_references``.

§9's obligation is the invariant, not a migration, so this test does not presuppose that
any new index exists — it asserts the access strategy each shape actually gets, whichever
index serves it. If migration ``005`` is ever written, this test is what it has to keep
satisfying, and it will pass unchanged.

The corpus is large enough that the planner is choosing rather than defaulting, and the
plans are taken twice: as shipped, and after ``ANALYZE``. The second pass matters because
a planner's choice moves with the statistics in ``sqlite_stat1``, so a plan asserted only
without them is asserted in one of the two states a real database can be in.

The measurement of record — query work, latency, index storage and write-path cost on a
twenty-thousand-memory corpus — is ``tools/measure_reference_filters.py``, and its result
is recorded in amendment 4 of the contract. This test is the guard, not the measurement.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_memory.domain.models import Scope, SearchQuery
from nexus_memory.storage import SQLiteRepository

SCOPE = Scope("plans", "local")
MEMORIES = 2_000
REPOSITORIES = 3

SHAPES = {
    "path_exact": (SearchQuery(reference_paths=("src/pkg0050/mod0000.py",), limit=20), None),
    "path_prefix": (SearchQuery(reference_path_prefix="src/pkg0050", limit=20), None),
    "commit_exact": (SearchQuery(reference_commits=("%040x" % 50,), limit=20), None),
    "repository_bound": (SearchQuery(repository="bound", limit=20), "repo-0"),
}


def build(db: Path) -> None:
    """The rows a verified write produces, written directly.

    This is a test about query plans, not about writing: twenty thousand verifying writes
    would measure the write path and take minutes. The foreign keys are satisfied the same
    way the service satisfies them.
    """
    SQLiteRepository(db)
    connection = sqlite3.connect(db)
    connection.execute("BEGIN")
    for index in range(REPOSITORIES):
        connection.execute(
            "INSERT INTO repositories(namespace,actor,repository_id,object_format,locator,registered_at)"
            " VALUES(?,?,?,'sha1',NULL,'2026-09-06T00:00:00+00:00')",
            (SCOPE.namespace, SCOPE.actor, f"repo-{index}"),
        )
    for seq in range(MEMORIES):
        memory_id, revision_id = f"m{seq:06d}", f"r{seq:06d}"
        body = f"memory number {seq} about package {seq % 100}"
        blob = connection.execute(
            "INSERT INTO blobs(namespace,actor,digest,body) VALUES(?,?,?,?)",
            (SCOPE.namespace, SCOPE.actor, f"d{seq:06d}", body.encode()),
        ).lastrowid
        connection.execute(
            "INSERT INTO memories(namespace,actor,memory_id,current_revision_id,tombstoned) VALUES(?,?,?,?,0)",
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
        for offset in range(2):
            connection.execute(
                "INSERT INTO revision_references(namespace,actor,memory_id,revision_id,repository_id,"
                "commit_oid,path,object_oid,entry_type,mode,checked_at)"
                " VALUES(?,?,?,?,?,?,?,?,'blob','100644','2026-09-06T00:00:00+00:00')",
                (SCOPE.namespace, SCOPE.actor, memory_id, revision_id, f"repo-{seq % REPOSITORIES}",
                 "%040x" % (seq % 100), f"src/pkg{seq % 100:04d}/mod{offset:04d}.py", "0" * 40),
            )
    connection.commit()
    connection.close()


def plan_lines(db: Path, query: SearchQuery, repository_id: str | None) -> list[str]:
    repository = SQLiteRepository(db)
    filters, parameters = repository._filters(query, repository_id)
    assert filters, "the shape under test contributed no predicate"
    statement = (
        "EXPLAIN QUERY PLAN SELECT h.seq FROM head_index h"
        " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
        " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
        # The ordering production builds, not a paraphrase of it: B3 moved the browse
        # page onto `h.durable_seq DESC` so the index answers it, and a reconstruction
        # left on the negated expression would ask the planner a question about a
        # statement that no longer runs.
        + filters + " ORDER BY h.durable_seq DESC, h.seq LIMIT 21"
    )
    connection = sqlite3.connect(db)
    try:
        return [row[3] for row in connection.execute(
            statement, [SCOPE.namespace, SCOPE.actor, *parameters]
        ).fetchall()]
    finally:
        connection.close()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("plans") / "plans.sqlite3"
    build(db)
    return db


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_filter_shape_full_scans_revision_references(corpus: Path, tmp_path: Path,
                                                        shape: str, statistics: bool) -> None:
    db = corpus
    if statistics:
        db = tmp_path / "analyzed.sqlite3"
        db.write_bytes(corpus.read_bytes())
        connection = sqlite3.connect(db)
        connection.execute("ANALYZE")
        connection.commit()
        connection.close()

    query, repository_id = SHAPES[shape]
    lines = plan_lines(db, query, repository_id)
    reference_lines = [line for line in lines if " r " in f" {line} " or line.endswith(" r") or "r EXISTS" in line]
    assert reference_lines, f"no plan line covers revision_references: {lines}"
    for line in reference_lines:
        assert "SCAN" not in line, f"{shape}: {line}"
    # The whole predicate must be served by an index, whichever one the planner picked.
    assert all("USING" in line and "INDEX" in line for line in reference_lines), lines


def test_the_shapes_actually_select_a_minority_of_the_corpus(corpus: Path) -> None:
    """A negative control on the fixture, not on the code.

    A plan is uninformative if every filter matches everything: the planner would be right
    to scan, and "no full scan" would be a claim about a predicate that is not filtering.
    """
    repository = SQLiteRepository(corpus)
    for shape, (query, repository_id) in SHAPES.items():
        filters, parameters = repository._filters(query, repository_id)
        connection = sqlite3.connect(corpus)
        try:
            matched = connection.execute(
                "SELECT count(*) FROM head_index h"
                " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
                " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
                + filters, [SCOPE.namespace, SCOPE.actor, *parameters],
            ).fetchone()[0]
        finally:
            connection.close()
        assert 0 < matched < MEMORIES, f"{shape} matched {matched} of {MEMORIES}"
