"""Bounded evidence loading against a statistics-only control, per stage.

This is the slice B2b §9 deferred. Amendment 5 established that the `ANALYZE` gain B2b
recorded lives almost entirely in evidence loading rather than in candidate selection, and
that the comparison an automatic-`ANALYZE` policy should be decided against had not been
run. It is run here, over four configurations:

===================  ==========================================================
current              the loader shipped through ``cdf51e7``, no statistics
statistics only      that loader, after ``ANALYZE`` -- isolates the statistics
bounded only         the bounded loader, no statistics -- isolates the change
both                 the bounded loader after ``ANALYZE`` -- their interaction
===================  ==========================================================

**The SQL measured here is the SQL that ran.** The predecessor harness rebuilt the two
stages by hand next to the shipped code, and that reconstruction is exactly how the storage
report came to attribute a gain to a stage it was not in. Here every statement is captured
from ``sqlite3.Connection.set_trace_callback`` during a real ``SQLiteRepository.search()``
call, with its parameters already expanded by SQLite. Nothing below is rebuilt, and the
stage labels are applied *to captured text* -- the only judgement the harness makes about a
statement is which table it names.

What is measured, and separately:

* **Instructions**, as VDBE steps per executed statement, attributed by reading the step
  counter at each statement boundary the trace reports. A real counter, not an estimate.
* **Latency**, in a second pass with every instrument removed. The two cannot share a pass:
  ``set_progress_handler(count, 1)`` calls into Python once per instruction and inflated
  every latency in the B2b as-run file by a measured 3.8x.

What is required, and enforced rather than assumed: **all four configurations must return
identical results.** Not merely the same row count -- the same hits in the same order, with
complete reference evidence per hit, over a full pagination walk. A configuration that
returned a page faster by returning less of it is a defect, and the walk is what catches it.

Four controls run alongside, because a number from a harness nobody checked is not evidence:

1. **Contamination.** ``ANALYZE`` writes ``sqlite_stat1`` into the database file and it
   persists. Every arm therefore runs on a fresh copy of a pristine corpus, and each arm
   asserts the statistics state it believes it is in. Measuring this without the control
   silently reports analyzed numbers as baseline ones -- which it did, during this slice's
   pilot, at a 485x error.
2. **Repeatability.** The reference configuration is measured twice on separate copies
   before any ratio is taken, and the spread is printed first. A ratio smaller than the
   spread is not a result.
3. **Discrimination.** An unfiltered arm must do visibly different work from the filtered
   ones, or the corpus is not exercising the predicate.
4. **Non-vacuity.** Each shape must select a minority of the corpus and must actually load
   evidence, or an identity assertion between two empty results would pass while asserting
   nothing.

Run: PYTHONPATH=src .venv-sqlite/bin/python tools/measure_evidence_loading.py
"""
from __future__ import annotations

import dataclasses
import shutil
import sqlite3
import statistics
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# The ``current`` arm is the loader that shipped through ``cdf51e7``. It is no longer in
# ``SQLiteRepository`` -- production has one loader and no switch -- so it is imported from
# the test support that retains it. Restating it here would make this harness measure a
# copy, which is the defect its predecessor had.
sys.path.insert(0, str(ROOT / "tests" / "core"))

from evidence_control import installed  # noqa: E402
from measure_reference_filters import (  # noqa: E402  the corpus of record, not a third copy
    MEMORIES, REFERENCES_PER_MEMORY, REPOSITORIES, SCOPE, SHAPES, build,
)

from nexus_memory.domain.models import SearchQuery  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

#: bounded loader?, statistics?
CONFIGURATIONS = {
    "current": (False, False),
    "statistics only": (False, True),
    "bounded only": (True, False),
    "both": (True, True),
}
REFERENCE = "current"
PAGES = 4       # pages walked per shape when comparing results across configurations
TIMINGS = 15    # timed repetitions per measurement, after a discarded warm-up


# ---------------------------------------------------------------------------- capture ---

def _stage(sql: str) -> str:
    """Label a captured statement by the table it names.

    This is the only interpretation the harness applies to captured text, and it is
    deliberately mechanical. **The order of these tests is load-bearing.** Under a
    reference filter the *selection* statement also names ``revision_references``, in the
    correlated ``EXISTS`` the filter compiles to, so a classifier that asked about
    ``revision_references`` first labelled candidate selection as evidence loading, summed
    the two stages into one column, and reported selection as zero work for every filtered
    shape. It did exactly that on this harness's first run. Candidate selection is the
    statement that reads ``head_index``; evidence loading is the one that reads
    ``revision_references`` and does *not* read ``head_index``; everything else -- the
    transaction, the generation read, the profile read -- is overhead, reported as such
    rather than folded into either stage.

    ``verify_stages`` enforces the shape this assumes, so a future statement that breaks it
    fails loudly instead of quietly moving work between columns.
    """
    if "head_index" in sql or "head_fts" in sql:
        return "selection"
    if "revision_references" in sql:
        return "evidence"
    return "other"


def verify_stages(captured: list[tuple[str, str, int]], name: str) -> None:
    """The structural check that makes the labels above trustworthy.

    Exactly one candidate selection, at most one evidence load, and no statement carrying
    both tables. Without this the classifier is an assumption; with it, it is checked
    against every statement the search actually ran.
    """
    selections = [sql for stage, sql, _ in captured if stage == "selection"]
    evidence = [sql for stage, sql, _ in captured if stage == "evidence"]
    assert len(selections) == 1, f"{name}: {len(selections)} candidate-selection statements"
    assert len(evidence) <= 1, f"{name}: {len(evidence)} evidence-loading statements"
    for sql in evidence:
        assert "head_index" not in sql, f"{name}: evidence statement also reads head_index"


def capture(db: Path, query: SearchQuery,
            repository_id: str | None) -> list[tuple[str, str, int]]:
    """Run the real ``search()`` and return ``(stage, sql, steps)`` per executed statement.

    The statement text comes from SQLite with its parameters already expanded, so what is
    reported is what executed. Steps are attributed by reading the instruction counter at
    each statement boundary: the count between statement *n* starting and statement *n+1*
    starting is statement *n*'s, and the last statement takes the remainder.
    """
    executed: list[list] = []
    counter = 0

    def progress() -> int:
        nonlocal counter
        counter += 1
        return 0

    def trace(sql: str) -> None:
        executed.append([sql, counter])

    original = SQLiteRepository._connect

    def connect(self):
        connection = original(self)
        connection.set_trace_callback(trace)
        connection.set_progress_handler(progress, 1)
        return connection

    SQLiteRepository._connect = connect
    try:
        SQLiteRepository(db).search(SCOPE, query, repository_id)
    finally:
        SQLiteRepository._connect = original

    boundaries = [entry[1] for entry in executed[1:]] + [counter]
    return [(_stage(sql), sql, end - start)
            for (sql, start), end in zip(executed, boundaries)]


def stage_steps(captured: list[tuple[str, str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for stage, _, steps in captured:
        totals[stage] = totals.get(stage, 0) + steps
    return totals


def stage_sql(captured: list[tuple[str, str, int]], stage: str) -> str | None:
    for entry_stage, sql, _ in captured:
        if entry_stage == stage:
            return sql
    return None


# ---------------------------------------------------------------------------- timing ----

def latency(db: Path, query: SearchQuery,
            repository_id: str | None) -> tuple[float, float]:
    """End-to-end ``search()`` latency with every instrument removed.

    A fresh repository and a discarded warm-up, then ``TIMINGS`` timings; best and median.
    """
    repository = SQLiteRepository(db)
    for _ in range(3):
        repository.search(SCOPE, query, repository_id)
    timings = []
    for _ in range(TIMINGS):
        start = time.perf_counter()
        repository.search(SCOPE, query, repository_id)
        timings.append((time.perf_counter() - start) * 1000)
    timings.sort()
    return timings[0], statistics.median(timings)


def replay_latency(db: Path, sql: str) -> float:
    """Best-of-N for one captured statement, re-executed exactly as it was executed.

    Per-stage latency cannot be read off the end-to-end number, and it cannot be timed in
    place without an instrument in the loop. Replaying the captured, fully expanded text
    needs no parameters and introduces no reconstruction: this is the statement that ran.
    """
    connection = sqlite3.connect(db)
    try:
        for _ in range(3):
            connection.execute(sql).fetchall()
        timings = []
        for _ in range(TIMINGS):
            start = time.perf_counter()
            connection.execute(sql).fetchall()
            timings.append((time.perf_counter() - start) * 1000)
        return min(timings)
    finally:
        connection.close()


# ---------------------------------------------------------------------------- results ---

def walk(db: Path, query: SearchQuery, repository_id: str | None,
         pages: int = PAGES) -> list[tuple]:
    """The observable result of a pagination walk: hits, order, evidence, cursor presence.

    Ordering is carried by position and evidence by value, so a configuration that returned
    the right rows with the wrong evidence, or in the wrong order, compares unequal.
    """
    repository = SQLiteRepository(db)
    observed: list[tuple] = []
    cursor = None
    for _ in range(pages):
        page = repository.search(SCOPE, dataclasses.replace(query, cursor=cursor),
                                 repository_id)
        observed.append((
            tuple((hit.memory_id, hit.revision_id, hit.references) for hit in page.hits),
            page.cursor is not None,
        ))
        cursor = page.cursor
        if cursor is None:
            break
    return observed


def matching_rows(db: Path, query: SearchQuery, repository_id: str | None) -> int:
    repository = SQLiteRepository(db)
    filters, parameters = repository._filters(query, repository_id)
    connection = sqlite3.connect(db)
    try:
        return connection.execute(
            "SELECT count(*) FROM head_index h"
            " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor"
            " AND m.memory_id=h.memory_id"
            " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0"
            " AND m.current_revision_id=h.revision_id " + filters,
            [SCOPE.namespace, SCOPE.actor, *parameters],
        ).fetchone()[0]
    finally:
        connection.close()


# ------------------------------------------------------------------------------- arms ---

def arm(pristine: Path, directory: Path, label: str, statistics_on: bool) -> Path:
    """A fresh copy of the pristine corpus, analyzed only if this arm says so.

    The copy is the contamination control. ``ANALYZE`` persists in the file, so an arm that
    reused a database another arm analyzed would report analyzed numbers as baseline ones.
    """
    db = directory / f"arm-{label.replace(' ', '-')}.sqlite3"
    if db.exists():
        db.unlink()
    shutil.copy(pristine, db)
    connection = sqlite3.connect(db)
    try:
        if statistics_on:
            connection.execute("ANALYZE")
            connection.commit()
        present = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()[0]
        assert present == int(statistics_on), (
            f"{label}: statistics state is {present}, expected {int(statistics_on)}")
    finally:
        connection.close()
    return db


def repository_for(query: SearchQuery) -> str | None:
    return "repo-0" if query.repository == "bound" else None


def measure(db: Path, bounded: bool) -> dict[str, dict]:
    """Every shape under one arm's loader.

    The loader is chosen once, here, and held for the whole arm: the unbounded arms run
    with the retained control substituted over ``_evidence_sql`` and the bounded ones run
    the shipped form untouched. It is a context manager so that an exception inside an arm
    cannot leave the control installed for the arm after it -- which would report a
    control's numbers under the shipped form's label.
    """
    out: dict[str, dict] = {}
    with nullcontext() if bounded else installed():
        for name, query in SHAPES.items():
            repository_id = repository_for(query)
            captured = capture(db, query, repository_id)
            verify_stages(captured, name)
            steps = stage_steps(captured)
            best, median = latency(db, query, repository_id)
            evidence_sql = stage_sql(captured, "evidence")
            selection_sql = stage_sql(captured, "selection")
            out[name] = {
                "captured": captured,
                "steps": steps,
                "best": best,
                "median": median,
                "selection_ms": replay_latency(db, selection_sql) if selection_sql else 0.0,
                "evidence_ms": replay_latency(db, evidence_sql) if evidence_sql else 0.0,
                "evidence_sql": evidence_sql,
                "walk": walk(db, query, repository_id),
            }
    return out


def report(label: str, results: dict[str, dict]) -> None:
    print(f"\n--- {label} ---")
    print(f"{'shape':18s} {'select':>10s} {'evid':>10s} {'other':>7s} {'sel ms':>8s}"
          f" {'evid ms':>8s} {'e2e best':>9s} {'e2e med':>8s}")
    for name, row in results.items():
        steps = row["steps"]
        print(f"{name:18s} {steps.get('selection', 0):10,d} {steps.get('evidence', 0):10,d}"
              f" {steps.get('other', 0):7,d} {row['selection_ms']:8.3f} {row['evidence_ms']:8.3f}"
              f" {row['best']:9.3f} {row['median']:8.3f}")


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

        # Control 4, before anything is compared: every shape must select a minority of the
        # corpus, or the filters are not filtering and the identity assertions are cheap.
        for name, query in SHAPES.items():
            rows = matching_rows(pristine, query, repository_for(query))
            share = rows / MEMORIES
            note = "  <- control arm, matches everything" if name == "unfiltered" else ""
            assert name == "unfiltered" or share < 0.5, f"{name} selects {share:.1%}"
            print(f"  {name:18s} selects {rows:6,d} memories ({share:6.1%}){note}")

        # Control 2: the reference configuration measured twice, on separate fresh copies,
        # before any ratio is taken against it. Printed first so no number below can be read
        # without knowing what this instrument returns when nothing changes.
        bounded, stats = CONFIGURATIONS[REFERENCE]
        repeats = [measure(arm(pristine, directory, f"floor-{index}", stats), bounded)
                   for index in range(2)]
        print("\nrepeatability control (identical configuration, separate fresh copies):")
        for name in SHAPES:
            first = repeats[0][name]["steps"].get("evidence", 0)
            second = repeats[1][name]["steps"].get("evidence", 0)
            spread = max(first, second) / max(1, min(first, second))
            print(f"  {name:18s} evidence steps {first:>10,d} vs {second:>10,d}"
                  f"  ({spread:.3f}x)")
        floor = max(
            max(repeats[0][name]["best"], repeats[1][name]["best"])
            / max(1e-9, min(repeats[0][name]["best"], repeats[1][name]["best"]))
            for name in SHAPES)
        print(f"  latency spread across identical runs: {floor:.2f}x."
              f" Latency ratios below smaller than this are not results.")

        measured: dict[str, dict] = {}
        for label, (bounded, stats) in CONFIGURATIONS.items():
            measured[label] = measure(arm(pristine, directory, label, stats), bounded)
            report(f"{label} (bounded={bounded}, statistics={stats})", measured[label])

        # The requirement, enforced: identical results everywhere.
        print("\nresult identity (hits, order, evidence, pagination), vs"
              f" '{REFERENCE}' over up to {PAGES} pages:")
        for label in CONFIGURATIONS:
            for name in SHAPES:
                expected = measured[REFERENCE][name]["walk"]
                actual = measured[label][name]["walk"]
                assert actual == expected, f"{label}/{name} returned a different result"
                # Control 4 again, per shape: an identity between two empty walks is vacuous.
                assert sum(len(page[0]) for page in expected) > 0, f"{name}: no hits"
                assert any(hit[2] for page in expected for hit in page[0]), f"{name}: no evidence"
            print(f"  {label:18s} identical")

        # Control 3: the unfiltered arm must be doing visibly different work.
        filtered = {name: measured[REFERENCE][name]["steps"].get("selection", 0)
                    for name in SHAPES if name != "unfiltered"}
        control = measured[REFERENCE]["unfiltered"]["steps"].get("selection", 0)
        assert all(value != control for value in filtered.values()), (
            "every filtered shape did the same selection work as the unfiltered control")
        print(f"  discrimination    unfiltered selection {control:,d} steps differs from every"
              f" filtered shape")

        print("\nevidence-loading summary (steps, then best replayed ms):")
        header = "".join(f"{label:>20s}" for label in CONFIGURATIONS)
        print(f"{'shape':18s}{header}")
        for name in SHAPES:
            steps = "".join(f"{measured[label][name]['steps'].get('evidence', 0):>20,d}"
                            for label in CONFIGURATIONS)
            times = "".join(f"{measured[label][name]['evidence_ms']:>20.3f}"
                            for label in CONFIGURATIONS)
            print(f"{name:18s}{steps}")
            print(f"{'':18s}{times}")

        print("\nthe evidence-loading statement each configuration actually executed"
              " (path_exact, truncated):")
        for label in CONFIGURATIONS:
            sql = measured[label]["path_exact"]["evidence_sql"] or "(none)"
            print(f"  {label:18s} {sql[:150]}")


if __name__ == "__main__":
    main()
