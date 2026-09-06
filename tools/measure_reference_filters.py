"""B2b §9: measure the existing-index baseline before deciding whether migration 005 exists.

Four filter shapes, on a corpus large enough for the planner's choice to be load-bearing,
against the schema as it stands and then against each candidate index. Reports, per arm:

* the plan ``EXPLAIN QUERY PLAN`` chose — the access **strategy**, not a benefit;
* query work, counted as VDBE steps through ``set_progress_handler``, which is a real
  counter rather than an estimate and is comparable across arms;
* wall-clock latency, best-of-N, on a warm cache, measured with the step counter removed
  because counting steps costs several times the latency being measured;
* for a candidate index, the pages it occupies (from ``dbstat``, on a fresh copy of the
  baseline) and an index-maintenance microbenchmark on ``revision_references`` inserts,
  which is not the cost of a ``record`` or ``revise`` call.

Plans and steps are reported per executed stage -- candidate selection and evidence
loading -- because on this corpus the two respond very differently to the same change.

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


# The statements a browse search actually executes, reproduced here in the shape
# ``SQLiteRepository.search`` builds them. An earlier version of this harness planned a
# reconstruction that dropped the ``blobs`` join and left out evidence loading entirely,
# which is how the storage report came to attribute the ANALYZE gain to the candidate
# join when candidate selection is not where the gain is. See §9's amendment.
AUTHORITY = (
    " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
    " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
)


def _selection(repository: SQLiteRepository, query: SearchQuery,
               repository_id: str | None) -> tuple[str, list]:
    """Stage 1: choose the page. Mirrors the no-text branch of ``search``, blobs join included."""
    filters, parameters = repository._filters(query, repository_id)
    inner = (
        "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,h.has_parent,"
        "-h.durable_seq AS rank,"
        f"substr(CAST(b.body AS TEXT),1,{SQLiteRepository.EXCERPT_LIMIT}) AS excerpt"
        " FROM head_index h JOIN blobs b ON b.id=h.blob_id" + AUTHORITY + filters
    )
    return (f"SELECT * FROM ({inner}) ORDER BY rank, seq LIMIT ?",
            [SCOPE.namespace, SCOPE.actor, *parameters, query.limit + 1])


def _evidence(pairs: list[tuple[str, str]]) -> tuple[str, list]:
    """Stage 2: ``_hit_references`` for the page stage 1 returned.

    This is the stage the earlier harness never planned and never measured, and on this
    corpus it is the one that dominates: without statistics the OR-list is served by a
    range scan over the whole (namespace, actor) partition rather than by point lookups.
    """
    clauses = " OR ".join("(memory_id=? AND revision_id=?)" for _ in pairs)
    parameters: list = [SCOPE.namespace, SCOPE.actor]
    for memory_id, revision_id in pairs:
        parameters.extend([memory_id, revision_id])
    return (
        "SELECT memory_id,revision_id,repository_id,commit_oid,path,object_oid,entry_type,mode,checked_at"
        f" FROM revision_references WHERE namespace=? AND actor=? AND ({clauses})"
        " ORDER BY memory_id,revision_id,commit_oid,path",
        parameters,
    )


def _stages(db: Path, query: SearchQuery, repository_id: str | None) -> list[tuple[str, str, list]]:
    repository = SQLiteRepository(db)
    selection, parameters = _selection(repository, query, repository_id)
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute(selection, parameters).fetchall()
    finally:
        connection.close()
    stages = [("selection", selection, parameters)]
    pairs = [(row[1], row[2]) for row in rows[: query.limit]]
    if pairs:
        evidence, evidence_parameters = _evidence(pairs)
        stages.append(("evidence", evidence, evidence_parameters))
    return stages


def plan(db: Path, query: SearchQuery, repository_id: str | None) -> list[str]:
    """Plan lines for every statement the search executes, each tagged with its stage."""
    connection = sqlite3.connect(db)
    lines: list[str] = []
    try:
        for stage, statement, parameters in _stages(db, query, repository_id):
            rows = connection.execute("EXPLAIN QUERY PLAN " + statement, parameters).fetchall()
            # A 20-way MULTI-INDEX OR plans as twenty identical lines; they are collapsed so
            # the shape stays readable without hiding that the branches differ if they do.
            seen: list[str] = []
            for row in rows:
                text = row[3]
                if seen and seen[-1] == text:
                    continue
                seen.append(text)
            lines.extend(f"[{stage}] {text}" for text in seen)
    finally:
        connection.close()
    return lines


def stage_steps(db: Path, query: SearchQuery, repository_id: str | None) -> dict[str, int]:
    """VDBE steps per executed stage, so a change can be attributed to the stage that moved."""
    connection = sqlite3.connect(db)
    counts: dict[str, int] = {}
    try:
        for stage, statement, parameters in _stages(db, query, repository_id):
            steps = 0

            def count() -> int:
                nonlocal steps
                steps += 1
                return 0

            connection.set_progress_handler(count, 1)
            connection.execute(statement, parameters).fetchall()
            connection.set_progress_handler(None, 1)
            counts[stage] = steps
    finally:
        connection.close()
    return counts


def work_and_latency(db: Path, query: SearchQuery, repository_id: str | None) -> tuple[int, float, float, int]:
    """VDBE steps for one execution, then latency measured *without* the step counter.

    The two cannot be collected in one pass. ``set_progress_handler(count, 1)`` calls back
    into Python once per VDBE instruction, and an earlier version of this harness left it
    installed across the timing loop: every latency it reported was several times the real
    one -- 3.8x the uninstrumented median, measured on this corpus -- and the ratios between
    arms were ratios of an instrumented interpreter, not of the shipped system. So the
    counter is installed for one execution, removed, and only then is anything timed.
    """
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
    finally:
        SQLiteRepository._connect = original_connect

    # Uninstrumented from here: a fresh repository, a discarded warm-up, then timing.
    repository = SQLiteRepository(db)
    for _ in range(3):
        _timed(repository, query, repository_id)
    timings = sorted(_timed(repository, query, repository_id) for _ in range(15))
    return measured, timings[0], timings[len(timings) // 2], len(page.hits)


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


def index_pages(db: Path, name: str) -> int:
    """Pages the named index actually occupies, from ``dbstat``.

    ``PRAGMA page_count`` is a high-water mark: it counts the freelist, and dropping an
    index frees its pages without returning them to the filesystem. Measuring candidates
    one after another on a single database therefore charges each one the largest arm so
    far -- the repository index was reported at 618 pages (11.2%) when it occupies 279
    (5.1%), the other 339 being pages the commit arm left behind. Every candidate is now
    built on a fresh copy of the baseline *and* reported from ``dbstat``, so the two
    disagree only if something other than the index moved.
    """
    connection = sqlite3.connect(db)
    try:
        row = connection.execute(
            "SELECT sum(pgsize)/(SELECT page_size FROM pragma_page_size) FROM dbstat WHERE name=?",
            (name,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        connection.close()


def write_cost(db: Path, rounds: int = 2_000, repeats: int = 9) -> tuple[float, float]:
    """Index maintenance cost per ``revision_references`` row, as a microbenchmark.

    Read the scope before the ratio. This inserts reference rows directly, 2,000 to a
    transaction, with ``foreign_keys=OFF`` and no service call: no verification, no blob,
    no revision, no head row, no FTS write, no commit per operation. It measures what an
    extra index costs the row insert itself, which is the question a candidate index
    raises, and it is the right shape for that question.

    It is *not* the cost of ``record`` or ``revise``, and the ratios must not be reported
    as application write-latency multipliers: those operations do far more per call, so
    the same index overhead is a much smaller fraction of them. Nine repetitions bound the
    noise on this microbenchmark and nothing else.

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
    """Every arm is a browse query: none of the five shapes carries query text.

    That is deliberate -- the filters are what is under test -- but it bounds what the
    numbers below support. A text search runs the FTS branch of ``search``, a different
    statement with a different plan, and nothing measured here describes it.
    """
    print(f"\n--- {label} ---")
    print(f"{'shape':18s} {'rows':>7s} {'hits':>5s} {'steps':>10s} {'select':>9s} {'evid':>9s}"
          f" {'best ms':>8s} {'med ms':>8s}  plan")
    for name, query in SHAPES.items():
        repository_id = "repo-0" if query.repository == "bound" else None
        rows = matching_rows(db, query, repository_id)
        steps, best, median, hits = work_and_latency(db, query, repository_id)
        stages = stage_steps(db, query, repository_id)
        lines = plan(db, query, repository_id)
        scanning = [line for line in lines if "SCAN" in line and "revision_references" in line]
        marker = "  FULL SCAN" if scanning else ""
        print(f"{name:18s} {rows:7d} {hits:5d} {steps:10d} {stages.get('selection', 0):9d}"
              f" {stages.get('evidence', 0):9d} {best:8.2f} {median:8.2f}  {lines[0]}{marker}")
        if plans:
            for line in lines[1:]:
                print(f"{'':81s}{line}")


def main() -> None:
    with TemporaryDirectory() as directory:
        db = Path(directory) / "measure.sqlite3"
        build(db)
        print(f"corpus: {MEMORIES} memories, {MEMORIES * REFERENCES_PER_MEMORY} reference rows,"
              f" {REPOSITORIES} repositories, one scope")
        baseline_pages = page_count(db)

        # The noise floor of write_cost, measured before any ratio is taken against it: the
        # same database, no schema change, the whole measurement repeated. Amendment 4
        # reported multiples of 12.33x, 8.28x and 1.75x from this instrument without ever
        # asking what it returns when nothing changes. The answer is a 1.73x spread across
        # identical runs, with the baseline itself moving 2.6x between sessions -- so the
        # smaller of those multiples was inside the noise and none of them reproduce. The
        # control is printed first so no ratio below can be read without it.
        floor = sorted(write_cost(db)[0] for _ in range(5))
        baseline_write, baseline_best = write_cost(db)
        print(f"write-cost noise floor (identical runs, no schema change): {floor[0]:.5f} to"
              f" {floor[-1]:.5f} ms median, a {floor[-1] / floor[0]:.2f}x spread."
              f" Ratios below smaller than this are not results.")
        print(f"baseline: {baseline_pages} pages, {baseline_write:.5f} ms median /"
              f" {baseline_best:.5f} ms best per reference-row insert"
              f" (batched microbenchmark, foreign keys off)")
        report("baseline (existing indexes only)", db)

        # One fresh copy of the baseline per candidate, rather than create-and-drop on a
        # single database. Dropping an index leaves its pages on the freelist, so the
        # create-and-drop sequence charged each candidate the high-water mark of every arm
        # before it; see index_pages.
        for name, ddl in CANDIDATES.items():
            arm = Path(directory) / f"candidate-{name}.sqlite3"
            arm.write_bytes(db.read_bytes())
            connection = sqlite3.connect(arm)
            connection.execute(ddl)
            connection.commit()
            connection.close()
            pages = page_count(arm)
            occupied = index_pages(arm, ddl.split()[2])
            write, best = write_cost(arm)
            print(f"\ncandidate {name}: +{pages - baseline_pages} pages"
                  f" ({(pages - baseline_pages) / baseline_pages:.1%} of baseline),"
                  f" index occupies {occupied} pages ({occupied / baseline_pages:.1%});"
                  f" index-maintenance microbenchmark {write:.5f} ms median"
                  f" ({write / baseline_write:.2f}x baseline), {best:.5f} ms best"
                  f" ({best / baseline_best:.2f}x) per reference row -- not a record/revise cost")
            report(f"with {name} index", arm)
            arm.unlink()

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
