"""Browse ordering: the two forms return the same pages, and only one of them seeks.

``search()`` with no query text ranks by recency. Production orders on ``h.durable_seq
DESC, h.seq``, which is the column order ``head_index_recent`` carries; the form shipped
through ``c82939a`` wrapped the select and ordered by the alias ``rank`` -- ``-h.durable_seq``,
an expression no index can answer -- so SQLite sorted every eligible row in the scope to
return one page. That form is retained in ``ordering_control.py`` and installed over the
seam for the duration of a test.

Two obligations, pulling in opposite directions on purpose:

* **Interchangeable.** Identical hits in identical order, identical cursor behaviour, over
  full pagination walks: first pages, deep pages, exhausted result sets, ties on
  ``durable_seq``, sparse and dense filters, and both statistics states. The oracle is the
  retained form rather than a restatement of the expected rows.
* **Not equivalent in cost.** The shipped form must reach the page without a sort, and a
  cursor page must seek from a range constraint on ``durable_seq`` rather than walk the
  prefix ahead of it. The control must be shown to do neither, or the guard proves nothing.

The second obligation is asserted structurally, through ``EXPLAIN QUERY PLAN``, and never
by timing. The measurement of record is ``tools/measure_candidate_selection.py``.

**Why a cursor page is checked separately from page two.** Page two is one page from the
top of the scope, so a predicate that filters after arriving looks indistinguishable from
one that seeks. The cost of the difference is proportional to how deep the page is, which
is why ``test_the_cursor_page_seeks_on_the_index_range`` asks the planner for the
constraint rather than asking a walk how it felt.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest
from ordering_control import installed, is_installed, wrapped_browse_statement
# The corpus of acceptance test 16, reused rather than copied: this file asks a question
# about the same planner on the same rows.
from test_reference_filter_plans import MEMORIES, SCOPE, build

from nexus_memory.domain.errors import CursorExpired
from nexus_memory.domain.models import SearchQuery
from nexus_memory.storage import SQLiteRepository

#: Sentinel for "delete this key", distinct from a JSON ``null``, which is its own case.
_ABSENT = object()


def _retamper(token: str, changes: dict) -> str:
    """Decode a *minted* cursor, apply ``changes``, and re-encode it.

    Built from a real cursor rather than from a hand-written payload so that the fingerprint
    and generation still match: the tampered field is then the only reason the call can
    fail, and the test cannot pass on an expiry it did not intend to provoke.
    """
    payload = json.loads(base64.urlsafe_b64decode(token.encode()))
    for key, value in changes.items():
        if value is _ABSENT:
            payload.pop(key, None)
        else:
            payload[key] = value
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("ordering") / "ordering.sqlite3"
    build(db)
    return db


def _analyzed(source: Path, tmp_path: Path) -> Path:
    """``ANALYZE`` writes ``sqlite_stat1`` into the file, so it is applied to a copy.

    In place it would leave every later reader of the module-scoped corpus measuring an
    analyzed database while believing it was measuring a fresh one -- the contamination
    that cost this project a 485x error once already.
    """
    db = tmp_path / "analyzed.sqlite3"
    db.write_bytes(source.read_bytes())
    connection = sqlite3.connect(db)
    connection.execute("ANALYZE")
    connection.commit()
    connection.close()
    assert connection_has_statistics(db), "ANALYZE left no statistics"
    return db


def connection_has_statistics(db: Path) -> bool:
    connection = sqlite3.connect(db)
    try:
        return bool(connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()[0])
    finally:
        connection.close()


def _database(corpus: Path, tmp_path: Path, statistics: bool) -> Path:
    db = _analyzed(corpus, tmp_path) if statistics else corpus
    assert connection_has_statistics(db) == statistics, "the arm is not in the state it claims"
    return db


# ---------------------------------------------------------------------------- walking ---

def _walk(db: Path, query: SearchQuery, pages: int,
          repository_id: str | None = None) -> list[tuple]:
    """The first ``pages`` pages as a caller observes them.

    Ordering is captured by position and whether each page offered a cursor is captured
    too: a form that returned the same rows but stopped paginating early would otherwise
    compare equal.
    """
    repository = SQLiteRepository(db)
    observed: list[tuple] = []
    cursor = None
    for _ in range(pages):
        page = repository.search(SCOPE, dataclasses.replace(query, cursor=cursor), repository_id)
        observed.append((
            tuple((hit.memory_id, hit.revision_id, hit.lexical_rank) for hit in page.hits),
            page.cursor is not None,
        ))
        cursor = page.cursor
        if cursor is None:
            break
    return observed


#: query, repository_id, pages to walk. The counts are chosen so that every walk here
#: reaches a page that offers no cursor: an exhausted result set is part of the protocol,
#: not an accident of the limit.
SHAPES = {
    # 20 of 2,000 -- sparser than one page, so the walk must exhaust its candidates.
    "sparse_path_exact": (SearchQuery(reference_paths=("src/pkg0050/mod0000.py",), limit=6), None, 8),
    "sparse_prefix": (SearchQuery(reference_path_prefix="src/pkg0050", limit=3), None, 12),
    "sparse_commit": (SearchQuery(reference_commits=("%040x" % 50,), limit=5), None, 8),
    # 667 of 2,000 -- dense enough that every page but the last fills from a partial walk,
    # which is the case a sparse shape cannot exercise.
    "dense_repository": (SearchQuery(repository="bound", limit=40), "repo-0", 20),
    "unfiltered": (SearchQuery(limit=100), None, 21),
}


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_both_orderings_return_identical_pages(corpus: Path, tmp_path: Path,
                                               name: str, statistics: bool,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    query, repository_id, pages = SHAPES[name]
    db = _database(corpus, tmp_path, statistics)
    assert not is_installed(), "the control form is installed over the shipped one"
    shipped = _walk(db, query, pages, repository_id)
    monkeypatch.setattr(SQLiteRepository, "_browse_statement", wrapped_browse_statement)
    assert is_installed(), "the control form is not installed"
    control = _walk(db, query, pages, repository_id)

    assert shipped == control, name
    # A comparison of two empty walks would pass while asserting nothing.
    assert sum(len(page[0]) for page in shipped) > 0, f"{name} selected no hits"
    assert len(shipped) > 1, f"{name} returned a single page"
    assert not shipped[-1][1], f"{name} did not reach the end of its result set"


def test_the_walk_covers_every_eligible_memory_exactly_once(corpus: Path) -> None:
    """Completeness, against the corpus rather than against the other form.

    Both orderings agreeing is not the same as either being right, and an ordering that
    walks an index can lose rows in a way a sort cannot -- so the whole scope is walked and
    compared to what is in it.
    """
    seen = [hit for page in _walk(corpus, SearchQuery(limit=97), 40) for hit in page[0]]
    ids = [hit[0] for hit in seen]
    assert len(ids) == MEMORIES, f"walked {len(ids)} of {MEMORIES}"
    assert len(set(ids)) == len(ids), "the walk returned a memory twice"
    connection = sqlite3.connect(corpus)
    try:
        expected = [row[0] for row in connection.execute(
            "SELECT memory_id FROM head_index WHERE namespace=? AND actor=?"
            " ORDER BY durable_seq DESC, seq", (SCOPE.namespace, SCOPE.actor))]
    finally:
        connection.close()
    assert ids == expected, "the walk did not return the scope in recency order"


def test_eligibility_still_applies_before_the_limit(tmp_path: Path) -> None:
    """A page fills to its limit *after* the ineligible rows are excluded.

    This is the failure an early-terminating walk invites and a sort of everything does
    not: stopping at the limit before the authority join has had its say would return a
    short page, or a tombstoned memory, at the top of the scope.
    """
    db = tmp_path / "eligibility.sqlite3"
    build(db)
    connection = sqlite3.connect(db)
    excluded = [row[0] for row in connection.execute(
        "SELECT memory_id FROM head_index WHERE namespace=? AND actor=?"
        " ORDER BY durable_seq DESC LIMIT 10", (SCOPE.namespace, SCOPE.actor))]
    connection.execute(
        f"UPDATE memories SET tombstoned=1 WHERE memory_id IN ({','.join('?' * len(excluded))})",
        excluded)
    connection.commit()
    connection.close()

    page = SQLiteRepository(db).search(SCOPE, SearchQuery(limit=20))
    assert len(page.hits) == 20, "the page did not fill after eligibility was applied"
    assert not ({hit.memory_id for hit in page.hits} & set(excluded)), "a tombstoned memory was returned"


# ------------------------------------------------------------------------------- ties ---

@pytest.fixture(scope="module")
def tied(tmp_path_factory) -> Path:
    """A corpus where eight head rows share two ``durable_seq`` values.

    ``head_index.durable_seq`` is not unique in the schema, and the tiebreak is what makes
    the order total. In a written corpus the value comes from the outbox's autoincrement
    and a tie cannot arise, so it is constructed here: an unreachable branch that is never
    exercised is an unreachable branch nobody has checked.
    """
    db = tmp_path_factory.mktemp("tied") / "tied.sqlite3"
    build(db)
    connection = sqlite3.connect(db)
    rows = [row[0] for row in connection.execute(
        "SELECT seq FROM head_index WHERE namespace=? AND actor=?"
        " ORDER BY durable_seq DESC LIMIT 8 OFFSET 4", (SCOPE.namespace, SCOPE.actor))]
    for value, group in ((MEMORIES - 10, rows[:4]), (MEMORIES - 11, rows[4:])):
        connection.execute(
            f"UPDATE head_index SET durable_seq=? WHERE seq IN ({','.join('?' * len(group))})",
            [value, *group])
    connection.commit()
    assert connection.execute(
        "SELECT count(*) - count(DISTINCT durable_seq) FROM head_index").fetchone()[0] == 6
    connection.close()
    return db


def test_ties_paginate_identically_and_break_on_seq(tied: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Limit 3 over groups of 4, so a page boundary falls inside a tie.

    The tiebreak has to hold *across* a cursor, not merely within one page, which a limit
    larger than the tie group would never test.
    """
    query = SearchQuery(limit=3)
    shipped = _walk(tied, query, 8)
    monkeypatch.setattr(SQLiteRepository, "_browse_statement", wrapped_browse_statement)
    control = _walk(tied, query, 8)
    assert shipped == control

    ids = [hit[0] for page in shipped for hit in page[0]]
    assert len(set(ids)) == len(ids), "a tie was returned twice across a page boundary"
    connection = sqlite3.connect(tied)
    try:
        expected = [row[0] for row in connection.execute(
            "SELECT memory_id FROM head_index WHERE namespace=? AND actor=?"
            " ORDER BY durable_seq DESC, seq LIMIT ?", (SCOPE.namespace, SCOPE.actor, len(ids)))]
    finally:
        connection.close()
    assert ids == expected, "ties did not break on seq ascending"


def test_the_tie_fixture_would_catch_a_descending_tiebreak(tied: Path) -> None:
    """The negative control for the fixture above.

    ``h.seq`` must be ASC because the index carries rowid ascending within one
    ``durable_seq``; DESC would still be a total order and would still paginate, so the
    test above is only meaningful if its expectation distinguishes the two. It does: the
    fixture ties four rows at a time, so the two orders differ.
    """
    connection = sqlite3.connect(tied)
    try:
        ascending = connection.execute(
            "SELECT memory_id FROM head_index WHERE namespace=? AND actor=?"
            " ORDER BY durable_seq DESC, seq LIMIT 12", (SCOPE.namespace, SCOPE.actor)).fetchall()
        descending = connection.execute(
            "SELECT memory_id FROM head_index WHERE namespace=? AND actor=?"
            " ORDER BY durable_seq DESC, seq DESC LIMIT 12", (SCOPE.namespace, SCOPE.actor)).fetchall()
    finally:
        connection.close()
    assert ascending != descending, "the fixture has no tie the tiebreak direction can show"


# ------------------------------------------------------------------------------ plans ---

def _executed_browse(db: Path, query: SearchQuery, repository_id: str | None = None,
                     cursor: str | None = None) -> tuple[str, list]:
    """The browse statement ``search()`` executed, captured at the call.

    Captured rather than reconstructed. A reconstruction next to the code is how the B2b
    report came to attribute a gain to a stage it was not in, and a plan assertion against
    a statement production does not run is a plan assertion about nothing.
    """
    captured: list[tuple[str, object]] = []
    original = sqlite3.connect

    class Recording(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            captured.append((sql, parameters))
            return super().execute(sql, parameters)

    def connecting(*args, **kwargs):
        if args and str(args[0]) == str(db):
            kwargs["factory"] = Recording
        return original(*args, **kwargs)

    sqlite3.connect = connecting
    try:
        repository = SQLiteRepository(db)
        repository.search(SCOPE, dataclasses.replace(query, cursor=cursor), repository_id)
    finally:
        sqlite3.connect = original

    selected = [entry for entry in captured
                if "head_index" in entry[0] and "head_fts" not in entry[0]]
    assert len(selected) == 1, f"{len(selected)} browse statements, expected exactly one"
    return selected[0][0], list(selected[0][1])


def _plan(db: Path, statement: str, parameters: list) -> list[str]:
    connection = sqlite3.connect(db)
    try:
        return [row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN " + statement, parameters).fetchall()]
    finally:
        connection.close()


def _head_line(lines: list[str]) -> str:
    selected = [line for line in lines if "head_index" in line]
    assert selected, f"no plan line covers head_index: {lines}"
    return selected[0]


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
def test_the_browse_page_is_not_sorted_in_either_statistics_state(
        corpus: Path, tmp_path: Path, statistics: bool) -> None:
    """The page comes off ``head_index_recent`` in order, with no temporary b-tree.

    In both states, because an ordering that only avoids the sort after ``ANALYZE`` is the
    property the shipped evidence loader lacked, and production never runs ``ANALYZE``.
    """
    db = _database(corpus, tmp_path, statistics)
    lines = _plan(db, *_executed_browse(db, SearchQuery(limit=20)))
    assert not any("ORDER BY" in line for line in lines), lines
    assert "head_index_recent" in _head_line(lines), lines


def test_the_retained_wrapper_does_sort(corpus: Path) -> None:
    """The negative control for the test above.

    If the wrapper form also came off the index in order, the assertion above would be
    satisfied by every form and would be measuring nothing. It is not: ``rank`` is an
    expression, and the whole scope is sorted to return one page.
    """
    with installed():
        lines = _plan(corpus, *_executed_browse(corpus, SearchQuery(limit=20)))
    assert any("USE TEMP B-TREE FOR ORDER BY" in line for line in lines), lines


def _cursor(db: Path, query: SearchQuery, repository_id: str | None = None) -> str:
    page = SQLiteRepository(db).search(SCOPE, query, repository_id)
    assert page.cursor is not None, "the first page offered no cursor"
    return page.cursor


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
def test_the_cursor_page_seeks_on_the_index_range(corpus: Path, tmp_path: Path,
                                                  statistics: bool) -> None:
    """A cursor page starts at the cursor, not at the top of the scope.

    ``durable_seq<?`` in the ``head_index_recent`` line is the seek: without it the walk
    arrives at every row ahead of the page and discards it, and the cost of a page grows
    with how deep it is. Page two cannot tell the two apart -- it is one page deep -- so
    the constraint is asked of the planner directly.
    """
    db = _database(corpus, tmp_path, statistics)
    query = SearchQuery(limit=20)
    statement, parameters = _executed_browse(db, query, cursor=_cursor(db, query))
    line = _head_line(_plan(db, statement, parameters))
    assert "head_index_recent" in line, line
    assert "durable_seq<" in line.replace(" ", ""), line


def test_the_disjunction_alone_would_not_seek(corpus: Path) -> None:
    """The negative control for the test above, and the reason the predicate has two halves.

    ``(durable_seq < ? OR (durable_seq = ? AND seq > ?))`` is the direct translation of the
    wrapper's cursor predicate and the obvious way to write it. It returns the right rows
    and it is not a range constraint: the seek still starts at the top of the scope. The
    shipped form adds ``durable_seq <= ?`` in front precisely so that it is one, and this
    test fails if someone folds the two back together.
    """
    query = SearchQuery(limit=20)
    statement, parameters = _executed_browse(corpus, query, cursor=_cursor(corpus, query))
    disjunction = statement.replace(
        " AND h.durable_seq <= ? AND (h.durable_seq < ? OR h.seq > ?)",
        " AND (h.durable_seq < ? OR (h.durable_seq = ? AND h.seq > ?))")
    assert disjunction != statement, "the shipped cursor predicate is not the one this test rewrites"
    line = _head_line(_plan(corpus, disjunction, parameters))
    assert "durable_seq<" not in line.replace(" ", ""), line


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
def test_a_cursor_minted_under_either_ordering_is_accepted_by_the_other(
        corpus: Path, tmp_path: Path, statistics: bool,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The cursor payload is unchanged, so the two forms can page each other's pages.

    ``rank`` is still ``-h.durable_seq`` and ``seq`` is still the row id: this slice
    changed how the page is reached, not what a cursor says. A caller holding a cursor
    across the deploy is the case that matters, and it is asserted rather than assumed.
    """
    db = _database(corpus, tmp_path, statistics)
    query = SearchQuery(limit=20)
    shipped_cursor = _cursor(db, query)
    with installed():
        control_cursor = _cursor(db, query)
        crossed = SQLiteRepository(db).search(
            SCOPE, dataclasses.replace(query, cursor=shipped_cursor))
    assert control_cursor == shipped_cursor, "the two forms mint different cursors"
    monkeypatch.undo()
    native = SQLiteRepository(db).search(SCOPE, dataclasses.replace(query, cursor=control_cursor))
    assert [hit.memory_id for hit in native.hits] == [hit.memory_id for hit in crossed.hits]


# A cursor is caller-supplied text. ``_browse_statement`` negates its ``rank`` in Python to
# recover ``durable_seq`` -- the one place in this slice where a decoded payload reaches
# arithmetic instead of a bind parameter -- so a malformed rank raised ``TypeError`` (or
# ``KeyError`` when absent) rather than a cursor-domain error. Neither is a ``NexusError``
# and neither is in ``search``'s except clause, so both left storage as ``internal_error``.
# The wrapper form this replaced bound ``rank`` and never negated it, which is why the
# defect arrived with the reordering and not before it.
MALFORMED_CURSOR_FIELDS = {
    "rank_is_text": {"rank": "not-a-number"},
    "rank_is_null": {"rank": None},
    "rank_is_a_list": {"rank": [1, 2]},
    "rank_is_a_bool": {"rank": True},       # bool subclasses int; -True is -1, a silent page
    "rank_is_absent": {"rank": _ABSENT},
    "seq_is_text": {"seq": "not-a-number"},
    "seq_is_null": {"seq": None},
    "seq_is_a_float": {"seq": 1.5},
    "seq_is_a_bool": {"seq": True},
    "seq_is_absent": {"seq": _ABSENT},
}

# Being a number is not enough to be a *bindable, comparable* one, and the guard above --
# shipped at dc75347 -- checked only the type. JSON integers are unbounded and ``json.loads``
# accepts ``Infinity`` and ``NaN``, so each payload below decodes to a number and then either
# fails at bind time or pages wrongly in silence:
#
# * ``10**100`` exceeds SQLite's signed 64-bit integer, and ``sqlite3`` raises
#   ``OverflowError`` binding it -- the same uncaught, non-``NexusError`` route out of storage
#   that the type check closed, so the caller is handed ``internal_error`` again.
# * ``-2**63`` binds; ``-rank``, which is what the browse page actually binds, does not.
# * ``-Infinity`` negates to ``+Infinity``, which no ``durable_seq`` exceeds, so a cursor
#   promising the next page silently returns page one; ``Infinity`` and ``NaN`` compare false
#   against every row and end the walk early. Neither raises.
#
# These are properties of the values, not of the reordering: unlike the type cases above they
# do not arrive with B3 §1. They are closed here because this is the guard that owns the
# question of what ``(rank, seq)`` may be.
UNREPRESENTABLE_CURSOR_FIELDS = {
    "rank_exceeds_sqlites_integer": {"rank": 10 ** 100},
    "rank_below_sqlites_integer": {"rank": -(10 ** 100)},
    "rank_negation_overflows": {"rank": -2 ** 63},   # binds; its negation does not
    "seq_exceeds_sqlites_integer": {"seq": 10 ** 100},
    "rank_is_infinite": {"rank": float("inf")},
    "rank_is_negative_infinity": {"rank": float("-inf")},  # negates to +inf: page one again
    "rank_is_nan": {"rank": float("nan")},
}


@pytest.mark.parametrize("field", MALFORMED_CURSOR_FIELDS.values(),
                         ids=list(MALFORMED_CURSOR_FIELDS))
def test_a_malformed_cursor_field_is_a_cursor_error_not_an_internal_one(
        corpus: Path, field: dict) -> None:
    """Every malformed ``(rank, seq)`` is answered in the cursor's own domain.

    Asserted as ``CursorExpired`` and not merely as "some ``NexusError``": the transport
    renders the code, and the whole point of the finding is which code a caller is handed.
    """
    query = SearchQuery(limit=20)
    tampered = _retamper(_cursor(corpus, query), field)
    with pytest.raises(CursorExpired):
        SQLiteRepository(corpus).search(SCOPE, dataclasses.replace(query, cursor=tampered))


@pytest.mark.parametrize("field", UNREPRESENTABLE_CURSOR_FIELDS.values(),
                         ids=list(UNREPRESENTABLE_CURSOR_FIELDS))
def test_a_numeric_cursor_field_sqlite_cannot_carry_is_a_cursor_error(
        corpus: Path, field: dict) -> None:
    """A number SQLite cannot bind, or cannot order against, is as malformed as a string.

    Split from the parametrisation above because the failure mode differs: four of these
    raised ``OverflowError`` (``internal_error`` to the caller) and three returned a wrong
    page without raising anything at all -- ``-Infinity`` returning page one verbatim under
    a cursor that asked for the page after it. Both are answered as ``CursorExpired``.
    """
    query = SearchQuery(limit=20)
    tampered = _retamper(_cursor(corpus, query), field)
    with pytest.raises(CursorExpired):
        SQLiteRepository(corpus).search(SCOPE, dataclasses.replace(query, cursor=tampered))


def test_a_representable_rank_at_the_bound_is_still_accepted(corpus: Path) -> None:
    """The bound rejects what SQLite cannot carry and not one value more.

    ``2**63 - 1`` and its negation both bind, so the guard must let them through -- this is
    the control that keeps the range check from being a blanket rejection of large numbers.
    The page it returns is empty, which is the honest answer to a position past every row,
    and is asserted as a page rather than as an error.
    """
    query = SearchQuery(limit=20)
    tampered = _retamper(_cursor(corpus, query), {"rank": 2 ** 63 - 1})
    page = SQLiteRepository(corpus).search(SCOPE, dataclasses.replace(query, cursor=tampered))
    assert page.hits == ()


def test_a_valid_cursor_still_pages_after_the_field_check(corpus: Path) -> None:
    """The negative control: the guard must reject malformed fields and nothing else.

    Re-encoding an untampered payload exercises the same path the tampered cases take, so a
    guard that rejected every re-encoded cursor -- or every cursor -- would fail here rather
    than pass the parametrisation above vacuously.
    """
    query = SearchQuery(limit=20)
    minted = _cursor(corpus, query)
    repository = SQLiteRepository(corpus)
    expected = repository.search(SCOPE, dataclasses.replace(query, cursor=minted))
    reencoded = repository.search(
        SCOPE, dataclasses.replace(query, cursor=_retamper(minted, {})))
    assert [hit.memory_id for hit in reencoded.hits] == [hit.memory_id for hit in expected.hits]
    assert expected.hits, "the control page is empty and asserts nothing"
