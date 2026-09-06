"""Bounded evidence loading: the two forms are interchangeable, and only one is bounded.

``_hit_references`` loads the reference evidence for a page of hits. Production has one
form, which drives the lookup from the page's pairs. The form shipped through ``cdf51e7``
hands the planner an OR-list whose cost is a function of the scope; it is retained in
``evidence_control.py``, which is test support rather than production code, and is
installed over the seam for the duration of a test. Keeping it correct is what makes it a
control rather than a relic.

Two obligations, and they pull in opposite directions on purpose:

* **Interchangeable.** Every B2b scenario must hold under either form, and the rows,
  ordering, pagination and evidence completeness must be identical. This is asserted by
  running the acceptance suite's own scenarios against the control form, so the oracle is
  the shipped semantics rather than a restatement of them.
* **Not equivalent in cost.** The shipped form must reach ``revision_references`` through
  the full ``(namespace, actor, memory_id, revision_id)`` key in *both* statistics states.
  The control must be shown not to, as-shipped, or the guard proves nothing.

The measurement of record is ``tools/measure_evidence_loading.py``. This is the guard.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from b2b_scenarios import SCENARIOS
from evidence_control import is_installed, unbounded_evidence_sql
# The corpus builder for acceptance test 16, reused rather than copied a third time: this
# file asks a question about the same planner on the same rows.
from test_reference_filter_plans import SCOPE, build

from nexus_memory.domain.models import SearchQuery
from nexus_memory.storage import SQLiteRepository


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("evidence") / "evidence.sqlite3"
    build(db)
    return db


@pytest.fixture
def unbounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the retained control over the seam for one test.

    ``monkeypatch`` rather than ``evidence_control.installed`` so that a test failing
    mid-body cannot leave the control installed for whatever runs next.
    """
    monkeypatch.setattr(SQLiteRepository, "_evidence_sql", unbounded_evidence_sql)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_b2b_scenario_holds_under_the_unbounded_control(tmp_path: Path, name: str,
                                                              unbounded: None) -> None:
    """The control form still satisfies the whole contract it was shipped under.

    Without this the retained form would be untested code that a refactor could break
    silently, and the experiment's control arm would stop being the thing it claims to be.
    """
    assert is_installed(), "the control form is not installed"
    SCENARIOS[name](tmp_path)


def _walk(db: Path, query: SearchQuery, pages: int) -> list[tuple]:
    """The first ``pages`` pages of a pagination walk, as a caller can observe them.

    Ordering is captured by position, evidence by value, and whether each page offered a
    cursor is captured too -- a form that returned the same rows but stopped paginating a
    page early would otherwise compare equal.
    """
    repository = SQLiteRepository(db)
    observed: list[tuple] = []
    cursor = None
    for _ in range(pages):
        page = repository.search(SCOPE, dataclasses.replace(query, cursor=cursor))
        observed.append((
            tuple((hit.memory_id, hit.revision_id, hit.references) for hit in page.hits),
            page.cursor is not None,
        ))
        cursor = page.cursor
        if cursor is None:
            break
    return observed


#: query, and how many pages to walk. Where the count exhausts the result set the walk is
#: complete and the final page is asserted to offer no cursor; where it does not, the
#: comparison covers the pages walked, which is what the count is chosen to make explicit.
QUERIES = {
    "prefix_paged": (SearchQuery(reference_path_prefix="src/pkg0050", limit=3), 20),
    # limit 6 over the 20 memories this path matches, so the walk covers four pages and
    # ends on a page that offers no cursor.
    "path_exact": (SearchQuery(reference_paths=("src/pkg0050/mod0000.py",), limit=6), 20),
    "commit_exact": (SearchQuery(reference_commits=("%040x" % 50,), limit=5), 20),
    "unfiltered_paged": (SearchQuery(limit=7), 6),
}


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_both_forms_return_identical_pages(corpus: Path, name: str,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    query, pages = QUERIES[name]
    # The shipped form needs no installation, but saying so is what keeps the first walk
    # from silently becoming a second run of the control.
    assert not is_installed(), "the control form is installed over the shipped one"
    bounded = _walk(corpus, query, pages)
    monkeypatch.setattr(SQLiteRepository, "_evidence_sql", unbounded_evidence_sql)
    assert is_installed(), "the control form is not installed"
    control = _walk(corpus, query, pages)

    assert bounded == control, name
    # A comparison of two empty walks would pass while asserting nothing.
    assert sum(len(page[0]) for page in bounded) > 0, f"{name} selected no hits"
    assert any(hit[2] for page in bounded for hit in page[0]), f"{name} loaded no evidence"
    # More than one page, or the pagination half of the comparison is untested.
    assert len(bounded) > 1, f"{name} returned a single page"


def _reference_plan(db: Path, bounded: bool, pairs: list[tuple[str, str]]) -> list[str]:
    """Plan lines for the statement the form under test actually builds.

    The statement comes from the builder that runs -- the shipped one, or the retained
    control -- not from a restatement of it here.
    """
    repository = SQLiteRepository(db)
    statement, parameters = (repository._evidence_sql(SCOPE, pairs) if bounded
                             else unbounded_evidence_sql(repository, SCOPE, pairs))
    connection = sqlite3.connect(db)
    try:
        return [row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN " + statement, parameters).fetchall()]
    finally:
        connection.close()


def _pairs(db: Path, limit: int = 20) -> list[tuple[str, str]]:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(
            "SELECT memory_id,revision_id FROM head_index WHERE namespace=? AND actor=?"
            " ORDER BY durable_seq DESC LIMIT ?", (SCOPE.namespace, SCOPE.actor, limit),
        ).fetchall()
    finally:
        connection.close()


def _analyzed(corpus: Path, tmp_path: Path) -> Path:
    """``ANALYZE`` writes ``sqlite_stat1`` into the file, so it is applied to a copy.

    Applying it in place would leave every later reader of the module-scoped corpus
    measuring an analyzed database while believing it was measuring a fresh one.
    """
    db = tmp_path / "analyzed.sqlite3"
    db.write_bytes(corpus.read_bytes())
    connection = sqlite3.connect(db)
    connection.execute("ANALYZE")
    connection.commit()
    connection.close()
    return db


def _reference_lines(lines: list[str]) -> list[str]:
    selected = [line for line in lines if "revision_references" in line]
    assert selected, f"no plan line covers revision_references: {lines}"
    return selected


@pytest.mark.parametrize("statistics", [False, True], ids=["as_shipped", "after_analyze"])
def test_bounded_evidence_loading_is_bounded_in_both_statistics_states(
        corpus: Path, tmp_path: Path, statistics: bool) -> None:
    """The page's pairs constrain the lookup whether or not statistics exist.

    Boundedness that only holds after ``ANALYZE`` is the property the shipped form lacked;
    asserting it in both states is the whole point of the test.
    """
    db = _analyzed(corpus, tmp_path) if statistics else corpus
    for line in _reference_lines(_reference_plan(db, True, _pairs(db))):
        assert "SCAN" not in line, line
        assert "memory_id=?" in line and "revision_id=?" in line, line


def test_the_control_form_is_not_bounded_as_shipped(corpus: Path) -> None:
    """The negative control for the test above.

    If the control form also reached ``revision_references`` through the full key with
    no statistics, the assertion above would be satisfied by every form and would be
    measuring nothing. It does not: the OR-list is served by a scan of the whole
    ``(namespace, actor)`` partition, which is the cost this slice removes.
    """
    lines = _reference_lines(_reference_plan(corpus, False, _pairs(corpus)))
    assert not any("memory_id=?" in line and "revision_id=?" in line for line in lines), lines
