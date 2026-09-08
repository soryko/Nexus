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

* **Progress callbacks**, and they are called that everywhere here rather than
  "instructions" or "VM steps", because that is what they are. SQLite invokes the progress
  handler during ``sqlite3_prepare`` as well as inside ``sqlite3_step``
  (https://www.sqlite.org/c3ref/progress_handler.html), so a callback count is preparation
  *and* execution, not executed opcodes. A real counter and a deterministic one -- but not
  the unit the earlier name implied, and fixing how the counter is attributed does not
  change what it counts. A stage's number here is the cost of one whole call to its
  statement: prepare it, run it, consume every row. It is measured per statement rather than
  read off the gaps between statement boundaries, because those gaps charge each statement's
  preparation to the statement before it. See ``capture`` for what that defect cost this
  measurement, and ``statement_cost`` for the attribution that replaced it.
* **Latency**, in a second pass with every instrument removed. The two cannot share a pass:
  ``set_progress_handler(count, 1)`` calls into Python once per callback and inflated every
  latency in the B2b as-run file by a measured 3.8x.

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
   before any ratio is taken, and the spread is printed first, because no number below can
   be read without knowing what this instrument returns when nothing changes. The spread is
   a description of that noise and **not a significance threshold**: a ratio inside it is
   not evidence that two arms differ, and equally not evidence that they are equivalent.
3. **Discrimination.** An unfiltered arm must do visibly different work from the filtered
   ones, or the corpus is not exercising the predicate.
4. **Non-vacuity.** Each shape must select a minority of the corpus and must actually load
   evidence, or an identity assertion between two empty results would pass while asserting
   nothing.
5. **Attribution.** Where two arms executed byte-identical SQL for a stage under the same
   statistics state, that stage must measure identically. The first version of this harness
   had no such check and reported candidate selection as 194 callbacks cheaper under bounded
   loading, on a statement whose text does not differ between the two arms at all.

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
    transaction and the generation read -- is overhead, and is neither costed nor folded
    into either stage.

    ``verify_stages`` enforces the shape this assumes, so a future statement that breaks it
    fails loudly instead of quietly moving work between columns.
    """
    if "head_index" in sql or "head_fts" in sql:
        return "selection"
    if "revision_references" in sql:
        return "evidence"
    return "other"


@dataclasses.dataclass(frozen=True)
class Statement:
    """One statement the search executed, as two texts and a cost.

    ``sql`` is what SQLite reports through the trace: the statement with its parameters
    already expanded. That is the right text to *label* and to *print* -- it is what ran,
    and two arms that ran the same statement have the same string. It is the wrong text to
    *re-execute*: expanded literals compile to a different program from bound parameters,
    so a statement replayed from trace text is not quite the statement that ran. ``call``
    and ``parameters`` are the pair the repository handed to SQLite, recorded at the call,
    and they are what the cost and latency measurements re-execute.

    ``span`` is the callback count between this statement's trace and the next one's. It is
    kept for the end-to-end total and is deliberately *not* this statement's work -- see
    ``capture``.
    """
    stage: str
    sql: str
    call: str
    parameters: object
    span: int


def verify_stages(captured: list[Statement], name: str) -> None:
    """The structural check that makes the labels above trustworthy.

    Exactly one candidate selection, at most one evidence load, and no *evidence* statement
    that also reads ``head_index``. The last of those is one-directional by necessity, not
    by oversight: under a reference filter the selection statement does name
    ``revision_references``, inside the correlated ``EXISTS`` the filter compiles to, so a
    symmetric check would fail on correct code. Stage labels must be exclusive; table names
    cannot be. Without this the classifier is an assumption; with it, it is checked against
    every statement the search actually ran.
    """
    selections = [entry for entry in captured if entry.stage == "selection"]
    evidence = [entry for entry in captured if entry.stage == "evidence"]
    assert len(selections) == 1, f"{name}: {len(selections)} candidate-selection statements"
    assert len(evidence) <= 1, f"{name}: {len(evidence)} evidence-loading statements"
    for entry in evidence:
        assert "head_index" not in entry.sql, f"{name}: evidence statement also reads head_index"


def capture(db: Path, query: SearchQuery,
            repository_id: str | None) -> tuple[list[Statement], int]:
    """Run the real ``search()``; return its statements and the whole call's callback total.

    Two instruments, on the same real call. The trace callback reports each statement as
    SQLite ran it, parameters expanded. A ``Connection`` subclass records the ``(sql,
    parameters)`` pair the repository passed, which is not a reconstruction -- it is the
    call itself, intercepted. The two lists are asserted to be the same length and to
    classify the same way, so anything reaching SQLite by a route this harness cannot see
    fails the run instead of quietly going unmeasured.

    **What this function does not do is attribute work to statements.** The progress handler
    is read at each statement boundary, but SQLite calls it while *preparing* a statement as
    well as while stepping one, and the trace fires when a statement starts running, after
    its own preparation. The span between two traces is therefore statement *n*'s execution
    plus statement *n+1*'s preparation, and the first version of this harness reported those
    spans as per-statement work. That is how preparing the OR-list evidence statement -- 209
    callbacks against the bounded form's 15, because preparing an OR-list grows with its
    terms and preparing the bounded form does not -- was reported as candidate selection
    being 194 callbacks cheaper under bounded loading, on a statement whose text is
    byte-identical between the two arms.

    Warming the statement cache was tried and does not fix it. **Measured:** with the
    connection held open across a discarded warm-up ``search()``, the surcharge vanished in
    the two arms without statistics and persisted, unchanged across further warm-ups and
    across repeated counted calls, in the two arms with them -- 1,142 callbacks for the
    OR-list and 56 for the bounded form, on a statement that connection had already prepared.
    The mechanism SQLite documents for that is ``ENABLE_STAT4``, which this build has: with
    statistics present the planner uses parameter *values*, so a statement is re-prepared
    when its bindings change (https://www.sqlite.org/c3ref/prepare.html), and evidence
    loading's bindings are the page's own pairs. The observation is measured; the mechanism
    is the documented explanation for it and was not separately isolated here. Either way a
    warm-cache boundary would be honest in two arms and wrong in the other two, so
    ``statement_cost`` charges preparation to the statement that pays for it instead, and
    the ``attribution`` control in ``main`` is what checks the result.
    """
    executed: list[list] = []
    records: list[tuple[str, object]] = []
    counter = 0
    searching = False
    recording = False

    def progress() -> int:
        nonlocal counter
        counter += 1
        return 0

    def trace(sql: str) -> None:
        executed.append([sql, counter])

    class Recording(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if recording:
                records.append((sql, parameters))
            return super().execute(sql, parameters)

        # ``search()`` opens its snapshot with ``execute("BEGIN")`` but closes it with
        # ``commit()``, which reaches SQLite without passing through ``execute``. The trace
        # sees the COMMIT either way, so without this the two lists differ by one and the
        # length assertion below fires -- which is what found this.
        def commit(self):
            if recording:
                records.append(("COMMIT", ()))
            return super().commit()

        def rollback(self):
            if recording:
                records.append(("ROLLBACK", ()))
            return super().rollback()

    original = SQLiteRepository._connect
    original_connect = sqlite3.connect

    def connect(self):
        nonlocal recording
        connection = original(self)
        if not searching:  # the migration's connection: not a search, not measured
            return connection
        # Both instruments start here, on the same statement, so the two lists cover the
        # same window by construction. ``_connect`` issues three pragmas of its own before
        # returning, and they belong to neither.
        connection.set_trace_callback(trace)
        connection.set_progress_handler(progress, 1)
        recording = True
        return connection

    def connecting(*args, **kwargs):
        # Only the corpus under measurement: ``_migrate`` opens an in-memory probe
        # connection, and it is not part of a search.
        if args and str(args[0]) == str(db):
            kwargs["factory"] = Recording
        return original_connect(*args, **kwargs)

    SQLiteRepository._connect = connect
    sqlite3.connect = connecting
    try:
        repository = SQLiteRepository(db)  # migration is not a search: neither is measured
        searching = True
        repository.search(SCOPE, query, repository_id)
    finally:
        recording = False
        SQLiteRepository._connect = original
        sqlite3.connect = original_connect

    assert len(records) == len(executed), (
        f"{len(records)} recorded calls against {len(executed)} traced statements:"
        " something in search() reached SQLite by a route this harness does not see")
    spans = [entry[1] for entry in executed[1:]] + [counter]
    captured = []
    for (sql, start), (call, parameters), end in zip(executed, records, spans):
        assert _stage(sql) == _stage(call), (
            f"traced and recorded text disagree on the stage: {sql[:60]!r} / {call[:60]!r}")
        captured.append(Statement(_stage(sql), sql, call, parameters, end - start))
    return captured, counter


def statement_cost(db: Path, statement: Statement, rounds: int = 3) -> int:
    """What one call to this statement costs: prepare it, run it, consume all of it.

    The boundary is the whole call rather than the gap between two traces, so the
    preparation a statement needs is charged to that statement and to nothing else. Each
    repetition gets a connection that has loaded the schema -- uncounted, and the load also
    reads ``sqlite_stat1`` -- and has never seen this statement, because whether the *second*
    call on a connection re-prepares varies with the statistics state, which is not something
    an attribution should have to know. The count is asserted to repeat exactly: a cost that
    varies across identical calls is not a counter.

    **What this number is not.** It is one figure for a first call on a schema-loaded
    connection, and it does not separate first preparation from re-preparation from
    execution. A difference between two arms measured this way is a difference in the whole
    prepare-run-consume cycle; attributing all of it to any one of the three would be a claim
    this instrument does not make.
    """
    counts = []
    for _ in range(rounds):
        connection = sqlite3.connect(db)
        counter = 0

        def progress() -> int:
            nonlocal counter
            counter += 1
            return 0

        try:
            connection.execute("SELECT count(*) FROM head_index WHERE 0").fetchall()
            connection.set_progress_handler(progress, 1)
            connection.execute(statement.call, statement.parameters).fetchall()
            connection.set_progress_handler(None, 0)
        finally:
            connection.close()
        counts.append(counter)
    assert len(set(counts)) == 1, (
        f"cost varied across identical calls: {counts} -- {statement.sql[:60]!r}")
    return counts[0]


def stage_callbacks(db: Path, captured: list[Statement]) -> dict[str, int]:
    return {entry.stage: statement_cost(db, entry)
            for entry in captured if entry.stage in ("selection", "evidence")}


def stage_statement(captured: list[Statement], stage: str) -> Statement | None:
    for entry in captured:
        if entry.stage == stage:
            return entry
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


def replay_latency(db: Path, statement: Statement) -> float:
    """Best-of-N for one captured statement, re-executed as the repository executed it.

    Per-stage latency cannot be read off the end-to-end number, and it cannot be timed in
    place without an instrument in the loop. What is replayed is the recorded ``(sql,
    parameters)`` pair rather than the trace's expanded text, so this is the program that
    ran and not a literal-folded relative of it. Unlike ``statement_cost`` this reuses one
    connection across the repetitions: it is a warm best-of-N by intention, and the
    preparation cost it therefore excludes is reported in the callback columns.
    """
    connection = sqlite3.connect(db)
    try:
        for _ in range(3):
            connection.execute(statement.call, statement.parameters).fetchall()
        timings = []
        for _ in range(TIMINGS):
            start = time.perf_counter()
            connection.execute(statement.call, statement.parameters).fetchall()
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
            captured, total = capture(db, query, repository_id)
            verify_stages(captured, name)
            callbacks = stage_callbacks(db, captured)
            best, median = latency(db, query, repository_id)
            evidence = stage_statement(captured, "evidence")
            selection = stage_statement(captured, "selection")
            out[name] = {
                "captured": captured,
                "callbacks": callbacks,
                "total": total,
                "best": best,
                "median": median,
                "selection_ms": replay_latency(db, selection) if selection else 0.0,
                "evidence_ms": replay_latency(db, evidence) if evidence else 0.0,
                "evidence_sql": evidence.sql if evidence else None,
                "walk": walk(db, query, repository_id),
            }
    return out


def report(label: str, results: dict[str, dict]) -> None:
    """Every count in this table is progress callbacks -- preparation and execution, not
    executed opcodes.

    ``select`` and ``evid`` are per-call costs for those two statements: prepare, run,
    consume. ``e2e calls`` is every callback the whole instrumented ``search()`` made, so the
    gap between it and the two stages is the overhead statements and nothing is quietly
    attributed. The two are different instruments and are not expected to agree to the digit
    -- but the gap should not depend on the arm, and in the run of record it did not: it was
    15 in all twenty rows.
    """
    print(f"\n--- {label} ---")
    print(f"{'shape':18s} {'select':>10s} {'evid':>10s} {'e2e calls':>11s} {'sel ms':>8s}"
          f" {'evid ms':>8s} {'e2e best':>9s} {'e2e med':>8s}")
    for name, row in results.items():
        callbacks = row["callbacks"]
        print(f"{name:18s} {callbacks.get('selection', 0):10,d}"
              f" {callbacks.get('evidence', 0):10,d}"
              f" {row['total']:11,d} {row['selection_ms']:8.3f} {row['evidence_ms']:8.3f}"
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
            first = repeats[0][name]["callbacks"].get("evidence", 0)
            second = repeats[1][name]["callbacks"].get("evidence", 0)
            spread = max(first, second) / max(1, min(first, second))
            print(f"  {name:18s} evidence callbacks {first:>10,d} vs {second:>10,d}"
                  f"  ({spread:.3f}x)")
        floor = max(
            max(repeats[0][name]["best"], repeats[1][name]["best"])
            / max(1e-9, min(repeats[0][name]["best"], repeats[1][name]["best"]))
            for name in SHAPES)
        print(f"  latency spread across identical runs: {floor:.2f}x -- what this instrument"
              f" returns when nothing changes. A description of its noise, not a threshold:"
              f"\n  a latency ratio inside it establishes neither a difference nor an"
              f" equivalence. The comparisons rest on the callback counts.")

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
        filtered = {name: measured[REFERENCE][name]["callbacks"].get("selection", 0)
                    for name in SHAPES if name != "unfiltered"}
        control = measured[REFERENCE]["unfiltered"]["callbacks"].get("selection", 0)
        assert all(value != control for value in filtered.values()), (
            "every filtered shape did the same selection work as the unfiltered control")
        print(f"  discrimination    unfiltered selection {control:,d} callbacks differs from"
              f" every filtered shape")

        # Control 5: two arms that ran byte-identical SQL for a stage must measure it
        # identically. Statistics are held fixed inside each pair because they change the
        # plan without changing the text. This is the control the first version of this
        # harness did not have, and it is what a boundary that charges one statement's
        # preparation to the statement before it fails: candidate selection is byte-identical
        # across all four arms, and it was reported 194 callbacks cheaper under bounded
        # loading only because the statement after it prepares faster. Under statistics the
        # same defect was worth 1,142 against 56: under statistics the surcharge survives a
        # warm connection, for which ENABLE_STAT4's re-preparation on changed bindings is the
        # documented explanation.
        for left, right in (("current", "bounded only"), ("statistics only", "both")):
            for name in SHAPES:
                for stage in ("selection", "evidence"):
                    one = stage_statement(measured[left][name]["captured"], stage)
                    other = stage_statement(measured[right][name]["captured"], stage)
                    if one is None or other is None or one.sql != other.sql:
                        continue
                    left_calls = measured[left][name]["callbacks"].get(stage, 0)
                    right_calls = measured[right][name]["callbacks"].get(stage, 0)
                    assert left_calls == right_calls, (
                        f"{name}/{stage}: byte-identical SQL measured {left_calls:,d} callbacks"
                        f" in '{left}' and {right_calls:,d} in '{right}'")
        print("  attribution       byte-identical SQL measures identically across arms")

        print("\nevidence-loading summary (progress callbacks, then best replayed ms):")
        header = "".join(f"{label:>20s}" for label in CONFIGURATIONS)
        print(f"{'shape':18s}{header}")
        for name in SHAPES:
            calls = "".join(f"{measured[label][name]['callbacks'].get('evidence', 0):>20,d}"
                            for label in CONFIGURATIONS)
            times = "".join(f"{measured[label][name]['evidence_ms']:>20.3f}"
                            for label in CONFIGURATIONS)
            print(f"{name:18s}{calls}")
            print(f"{'':18s}{times}")

        print("\nthe evidence-loading statement each configuration actually executed"
              " (path_exact, truncated):")
        for label in CONFIGURATIONS:
            sql = measured[label]["path_exact"]["evidence_sql"] or "(none)"
            print(f"  {label:18s} {sql[:150]}")


if __name__ == "__main__":
    main()
