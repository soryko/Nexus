"""Browse ordering against a statistics-only control, per stage and per depth.

This is the slice B2 §4 deferred. B2 closed with candidate selection as the dominant cost
of a browse search -- 261,000 to 780,000 progress callbacks against evidence loading's
1,368 -- and left one question open with it: whether `ANALYZE` is worth collecting for
that stage, where it cut unfiltered selection by 35.84%. Both are answered here, over four
configurations:

===================  ==========================================================
current              the ordering shipped through ``c82939a``, no statistics
statistics only      that ordering, after ``ANALYZE`` -- isolates the statistics
ordered              the indexed ordering, no statistics -- isolates the change
both                 the indexed ordering after ``ANALYZE`` -- their interaction
===================  ==========================================================

**The SQL measured here is the SQL that ran**, captured from a real ``SQLiteRepository
.search()`` through the instruments B2 built, and the counting boundary is B2's corrected
one: a stage's number is one whole call to its statement -- prepare it, run it, consume
every row -- rather than the gap between two statement boundaries, which charges each
statement's preparation to the statement before it.

**Every count is a progress callback.** SQLite invokes the handler during
``sqlite3_prepare`` as well as inside ``sqlite3_step``
(https://www.sqlite.org/c3ref/progress_handler.html), so a count covers preparation and
execution and is not a count of executed opcodes.

**Depth is a measured axis, not a footnote.** A cursor page one page from the top cannot
distinguish a predicate that seeks from one that arrives at every row ahead of it and
discards it -- the difference is proportional to depth. Every shape is therefore measured
at its first page *and* at the deepest page its own result set allows, and the rows
skipped to get there are printed next to the number.

Six controls run alongside:

1. **Contamination.** ``ANALYZE`` persists in the file, so every arm runs on a fresh copy
   of a pristine corpus and asserts the statistics state it believes it is in.
2. **Repeatability.** The reference configuration is measured twice on separate copies
   before any ratio is taken. The spread is printed first, and it is a description of this
   instrument's noise, **not a significance threshold**: a ratio inside it establishes
   neither a difference nor an equivalence.
3. **Discrimination.** The unfiltered arm must do visibly different work from the filtered
   ones, or the corpus is not exercising the predicates.
4. **Non-vacuity.** Each shape must select a minority of the corpus, and each measured
   page must return rows -- an identity assertion between two empty pages would pass while
   asserting nothing.
5. **Attribution.** Where two arms executed byte-identical SQL for a stage under the same
   statistics state, that stage must measure identically. Evidence loading is that
   statement here: this slice does not touch it, and a page of the same hits produces the
   same evidence text under either ordering.
6. **Identity.** All four configurations must return the same hits in the same order with
   the same evidence over a full pagination walk. A configuration that returned a page
   faster by returning less of it is a defect, and the walk is what catches it.

**One probe here is not part of the comparison and does not ship.** ``reference_driven``
measures a different candidate-generation shape -- one that drives the statement from
``revision_references`` instead of probing it per head row -- across the two statistics
states and with and without a path index. It is recorded as motivation for a later
experiment and nothing in this slice rests on it. It is a **new query shape**, so it does
not bear on B2b's decision to add no index for the existing one.

Run: PYTHONPATH=src .venv-sqlite/bin/python tools/measure_candidate_selection.py
"""
from __future__ import annotations

import dataclasses
import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
# The retained wrapper ordering, imported rather than restated: the arm labelled "current"
# must be the form that shipped, not a copy of it that drifted.
sys.path.insert(0, str(ROOT / "tests" / "core"))

from ordering_control import installed  # noqa: E402
# B2's instruments and B2's corpus, reused rather than rebuilt. A reconstruction next to
# the code is exactly how the B2b report came to attribute a gain to a stage it was not in.
from measure_evidence_loading import (  # noqa: E402
    MEMORIES, REFERENCES_PER_MEMORY, REPOSITORIES, SCOPE, SHAPES, arm, build, capture,
    latency, matching_rows, replay_latency, repository_for, stage_statement,
    statement_cost, verify_stages, walk,
)

from nexus_memory.domain.models import SearchQuery  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

#: indexed ordering?, statistics?
CONFIGURATIONS = {
    "current": (False, False),
    "statistics only": (False, True),
    "ordered": (True, False),
    "both": (True, True),
}
REFERENCE = "current"
DEPTH = 10_000   # rows to page past before measuring the deep page, where a shape has them


def deep_cursor(db: Path, query: SearchQuery, repository_id: str | None) -> tuple[str | None, int]:
    """A real cursor as deep into this shape's result set as the shape allows.

    Minted by paging, not by constructing a payload: a cursor this harness built by hand
    would be a cursor no caller can hold. Shapes with fewer rows than ``DEPTH`` stop at
    their last page, and the rows actually skipped are returned so that no depth is
    implied that the corpus does not support.
    """
    repository = SQLiteRepository(db)
    cursor, skipped = None, 0
    while skipped < DEPTH:
        page = repository.search(SCOPE, dataclasses.replace(query, cursor=cursor), repository_id)
        if page.cursor is None:
            break
        skipped += len(page.hits)
        cursor = page.cursor
    return cursor, skipped


def plan(db: Path, statement, parameters) -> list[str]:
    connection = sqlite3.connect(db)
    try:
        return [row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN " + statement, parameters).fetchall()]
    finally:
        connection.close()


def measure_page(db: Path, query: SearchQuery, repository_id: str | None,
                 cursor: str | None) -> dict:
    """One page under one arm: what it cost, what it ran, and how the planner reached it."""
    paged = dataclasses.replace(query, cursor=cursor)
    captured, total = capture(db, paged, repository_id)
    verify_stages(captured, "page")
    selection = stage_statement(captured, "selection")
    evidence = stage_statement(captured, "evidence")
    best, median = latency(db, paged, repository_id)
    return {
        "selection": statement_cost(db, selection),
        "evidence": statement_cost(db, evidence) if evidence else 0,
        "evidence_sql": evidence.sql if evidence else None,
        "total": total,
        "selection_ms": replay_latency(db, selection),
        "best": best,
        "median": median,
        "plan": plan(db, selection.call, selection.parameters),
        "rows": len(SQLiteRepository(db).search(SCOPE, paged, repository_id).hits),
    }


def measure(db: Path, indexed: bool, cursors: dict[str, tuple[str | None, int]]) -> dict[str, dict]:
    """Every shape at both depths under one arm.

    The ordering is chosen once, here, and held for the whole arm: the two ``current`` arms
    run with the retained control substituted over ``_browse_statement`` and the two
    ``ordered`` arms run the shipped form untouched. It is a context manager so that an
    exception inside an arm cannot leave the control installed for the arm after it.
    """
    out: dict[str, dict] = {}
    with nullcontext() if indexed else installed():
        for name, query in SHAPES.items():
            repository_id = repository_for(query)
            cursor, skipped = cursors[name]
            out[name] = {
                "first": measure_page(db, query, repository_id, None),
                "deep": measure_page(db, query, repository_id, cursor),
                "skipped": skipped,
                "walk": walk(db, query, repository_id),
            }
    return out


def report(label: str, results: dict[str, dict]) -> None:
    """Callbacks are preparation and execution both; ``sel`` is one whole call to the
    candidate-selection statement, ``e2e`` every callback the instrumented ``search()`` made.
    """
    print(f"\n--- {label} ---")
    print(f"{'shape':18s} {'sel (page 1)':>13s} {'sel (deep)':>12s} {'skipped':>8s}"
          f" {'e2e (page 1)':>13s} {'sel ms':>8s} {'e2e best':>9s} {'sorted?':>8s}")
    for name, row in results.items():
        sortless = not any("ORDER BY" in line for line in row["first"]["plan"])
        print(f"{name:18s} {row['first']['selection']:13,d} {row['deep']['selection']:12,d}"
              f" {row['skipped']:8,d} {row['first']['total']:13,d}"
              f" {row['first']['selection_ms']:8.3f} {row['first']['best']:9.3f}"
              f" {'no' if sortless else 'YES':>8s}")


# --------------------------------------------------------------- the load-bearing forms ---

def load_bearing(db: Path, cursors: dict[str, tuple[str | None, int]]) -> None:
    """Two details of the shipped statement, each measured against the way it is not written.

    Both alternatives return exactly the rows the shipped form returns -- that is why they
    are worth measuring, and why a test that only compared results would accept either. The
    variants are derived by substitution on the *captured* statement rather than restated
    here, so what is measured is the shipped statement with one detail changed.
    """
    print("\n--- the two load-bearing details (unfiltered, no statistics) ---")
    query = SHAPES["unfiltered"]
    cursor, skipped = cursors["unfiltered"]

    captured, _ = capture(db, query, None)
    first = stage_statement(captured, "selection")
    descending = _Replay(
        first.call.replace(" ORDER BY h.durable_seq DESC, h.seq LIMIT ?",
                           " ORDER BY h.durable_seq DESC, h.seq DESC LIMIT ?"),
        first.parameters)
    assert descending.call != first.call, "the tiebreak substitution did not apply"

    captured, _ = capture(db, dataclasses.replace(query, cursor=cursor), None)
    deep = stage_statement(captured, "selection")
    disjunction = _Replay(
        deep.call.replace(" AND h.durable_seq <= ? AND (h.durable_seq < ? OR h.seq > ?)",
                          " AND (h.durable_seq < ? OR (h.durable_seq = ? AND h.seq > ?))"),
        deep.parameters)
    assert disjunction.call != deep.call, "the cursor substitution did not apply"

    # No head row in this corpus shares a durable_seq, so the descending tiebreak returns
    # the same rows in the same order: results cannot tell the two apart, and cost can.
    # The tiebreak's *correctness* under ties is the guard's job, not this harness's.
    assert _rows(db, descending) == _rows(db, first), (
        "the tiebreak variants disagree on rows -- this corpus has ties and the comparison"
        " below is not between two orderings of one page")
    assert _rows(db, deep) == _rows(db, disjunction), (
        "the two cursor predicates do not return the same rows -- the comparison is not"
        " between two ways of writing one page")

    print(f"  tiebreak ASC (shipped)        {statement_cost(db, first):10,d}   page 1")
    print(f"  tiebreak DESC                 {statement_cost(db, descending):10,d}   page 1"
          "  -- the index carries rowid ascending within a durable_seq, so DESC is a sort")
    print(f"  cursor: range + disjunction   {statement_cost(db, deep):10,d}   {skipped:,} rows in")
    print(f"  cursor: disjunction alone     {statement_cost(db, disjunction):10,d}   {skipped:,} rows in"
          "  -- same rows, no range constraint, walks the prefix")
    for label, statement in (("range + disjunction", deep), ("disjunction alone", disjunction)):
        head = [line for line in plan(db, statement.call, statement.parameters)
                if "head_index" in line]
        print(f"    {label:22s} {head[0] if head else '?'}")


def _rows(db: Path, statement) -> list:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(statement.call, statement.parameters).fetchall()
    finally:
        connection.close()


# ------------------------------------------------------- the probe, which does not ship ---

DRIVEN = (
    "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,"
    "h.has_parent,-h.durable_seq AS rank,substr(CAST(b.body AS TEXT),1,240) AS excerpt"
    " FROM (SELECT DISTINCT r.memory_id AS mid, r.revision_id AS rid FROM revision_references r"
    "       WHERE r.namespace=? AND r.actor=? AND r.path IN (?)) s"
    " JOIN head_index h ON h.namespace=? AND h.actor=? AND h.memory_id=s.mid AND h.revision_id=s.rid"
    " JOIN blobs b ON b.id=h.blob_id"
    " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
    " WHERE m.tombstoned=0 AND m.current_revision_id=h.revision_id"
    " ORDER BY h.durable_seq DESC LIMIT ?")
DRIVEN_PARAMETERS = [SCOPE.namespace, SCOPE.actor, "src/pkg0500/mod0000.py",
                     SCOPE.namespace, SCOPE.actor, 21]
PATH_INDEX = ("CREATE INDEX revision_references_path"
              " ON revision_references(namespace, actor, path)")


def probe(pristine: Path, directory: Path) -> None:
    """Reference-driven candidate generation, across statistics and index states.

    Motivation for a later experiment and nothing else: a different query shape, measured
    at one page of one shape, with no pagination, no identity check and no walk. It is
    printed so the later experiment starts from a number rather than from an intuition.
    """
    print("\n--- probe: reference-driven candidate generation (path_exact, page 1, does not ship) ---")
    print(f"{'statistics':>11s} {'path index':>11s} {'callbacks':>12s}   plan")
    for statistics_on in (False, True):
        for index_on in (False, True):
            db = arm(pristine, directory, f"probe-{statistics_on}-{index_on}", False)
            connection = sqlite3.connect(db)
            if index_on:
                connection.execute(PATH_INDEX)
            if statistics_on:
                connection.execute("ANALYZE")
            connection.commit()
            connection.close()

            statement = _Replay(DRIVEN, DRIVEN_PARAMETERS)
            cost = statement_cost(db, statement)
            reference = [line for line in plan(db, DRIVEN, DRIVEN_PARAMETERS)
                         if "revision_references" in line]
            print(f"{str(statistics_on):>11s} {str(index_on):>11s} {cost:12,d}   "
                  f"{reference[0] if reference else '?'}")


@dataclasses.dataclass(frozen=True)
class _Replay:
    """The pair ``statement_cost`` re-executes, for a statement no ``search()`` issues."""
    call: str
    parameters: list
    sql: str = "reference-driven probe"


# ------------------------------------------------------------------------------- main ---

def main() -> None:
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        pristine = directory / "pristine.sqlite3"
        build(pristine)
        connection = sqlite3.connect(pristine)
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='sqlite_stat1'"
        ).fetchone()[0] == 0, "the pristine corpus already carries statistics"
        connection.close()
        print(f"corpus: {MEMORIES:,} memories, {MEMORIES * REFERENCES_PER_MEMORY:,} reference"
              f" rows, {REPOSITORIES} repositories, one scope, no statistics")

        # Control 4, before anything is compared.
        for name, query in SHAPES.items():
            rows = matching_rows(pristine, query, repository_for(query))
            share = rows / MEMORIES
            note = "  <- control arm, matches everything" if name == "unfiltered" else ""
            assert name == "unfiltered" or share < 0.5, f"{name} selects {share:.1%}"
            print(f"  {name:18s} selects {rows:6,d} memories ({share:6.1%}){note}")

        # The deep cursors are minted once, on the shipped ordering, and reused by every
        # arm: a cursor's payload is the scope's own (rank, seq), so it is portable across
        # copies of one corpus, and minting them per arm would measure a different page in
        # each. Asserted below rather than assumed.
        cursors = {}
        for name, query in SHAPES.items():
            cursors[name] = deep_cursor(pristine, query, repository_for(query))
        print("\ndeep page reached per shape (rows paged past before the measured page):")
        for name, (cursor, skipped) in cursors.items():
            note = "" if cursor is not None else "  <- result set exhausted; deep page is page 1"
            print(f"  {name:18s} {skipped:6,d}{note}")

        # Control 2: the reference configuration twice, on separate fresh copies.
        indexed, statistics_on = CONFIGURATIONS[REFERENCE]
        first = measure(arm(pristine, directory, "repeat-a", statistics_on), indexed, cursors)
        second = measure(arm(pristine, directory, "repeat-b", statistics_on), indexed, cursors)
        print("\nrepeatability control (identical configuration, separate fresh copies):")
        for name in SHAPES:
            a, b = first[name]["first"]["selection"], second[name]["first"]["selection"]
            print(f"  {name:18s} selection callbacks {a:10,d} vs {b:10,d}  ({b / a:.3f}x)")
            assert a == b, f"{name}: the callback counter is not deterministic"
        spread = max(second[name]["first"]["best"] / first[name]["first"]["best"]
                     for name in SHAPES)
        print(f"  latency spread across identical runs: {max(spread, 1 / spread):.2f}x -- what this"
              " instrument returns when nothing changes. A description of its noise, not a"
              " threshold:\n  a latency ratio inside it establishes neither a difference nor an"
              " equivalence. The comparisons rest on the callback counts.")

        results = {}
        for label, (indexed, statistics_on) in CONFIGURATIONS.items():
            db = arm(pristine, directory, label, statistics_on)
            results[label] = measure(db, indexed, cursors)
            report(label, results[label])

        print("\nresult identity (hits, order, evidence, pagination), vs 'current':")
        for label, rows in results.items():
            same = all(rows[name]["walk"] == results[REFERENCE][name]["walk"] for name in SHAPES)
            print(f"  {label:18s} {'identical' if same else 'DIFFERENT'}")
            assert same, f"{label} did not return what {REFERENCE} returned"

        # Control 3.
        for name in SHAPES:
            if name != "unfiltered":
                assert (results["ordered"]["unfiltered"]["first"]["selection"]
                        != results["ordered"][name]["first"]["selection"]), name
        print(f"  discrimination    unfiltered selection"
              f" {results['ordered']['unfiltered']['first']['selection']:,d} callbacks differs"
              " from every filtered shape")

        # Control 5: this slice does not touch evidence loading, so wherever the two
        # orderings produced byte-identical evidence SQL under the same statistics state,
        # it must cost the same. A difference there is the instrument drifting, not a result.
        for statistics_on, (before, after) in (
                (False, ("current", "ordered")), (True, ("statistics only", "both"))):
            for name in SHAPES:
                for depth in ("first", "deep"):
                    left, right = results[before][name][depth], results[after][name][depth]
                    if left["evidence_sql"] == right["evidence_sql"]:
                        assert left["evidence"] == right["evidence"], (
                            f"{name}/{depth}: byte-identical evidence SQL measured"
                            f" {left['evidence']:,d} against {right['evidence']:,d}")
        print("  attribution       byte-identical evidence SQL measures identically across arms")

        # Control 4, second half: a measured page that returned nothing would make the
        # identity assertions above vacuous.
        for label, rows in results.items():
            for name in SHAPES:
                for depth in ("first", "deep"):
                    assert rows[name][depth]["rows"] > 0, f"{label}/{name}/{depth} returned no rows"
        print("  non-vacuity       every measured page returned hits")

        print("\ncandidate selection (callbacks, page 1 then deepest page):")
        print(f"{'shape':18s}" + "".join(f"{label:>20s}" for label in CONFIGURATIONS))
        for name in SHAPES:
            print(f"{name:18s}" + "".join(
                f"{results[label][name]['first']['selection']:20,d}" for label in CONFIGURATIONS))
            print(f"{'':18s}" + "".join(
                f"{results[label][name]['deep']['selection']:20,d}" for label in CONFIGURATIONS))

        print("\nthe browse statement each configuration executed (unfiltered, page 1):")
        for label in CONFIGURATIONS:
            for line in results[label]["unfiltered"]["first"]["plan"]:
                print(f"  {label:18s} {line}")

        print("\nthe deep-page plan (unfiltered) -- whether the cursor is a range the seek starts"
              " from:")
        for label in CONFIGURATIONS:
            head = [line for line in results[label]["unfiltered"]["deep"]["plan"]
                    if "head_index" in line]
            print(f"  {label:18s} {head[0] if head else '?'}")

        load_bearing(arm(pristine, directory, "load-bearing", False), cursors)
        probe(pristine, directory)


if __name__ == "__main__":
    main()
