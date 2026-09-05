from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_memory.domain.errors import CursorExpired, InvalidQuery, MemoryNotFound
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


def _service(path: Path, namespace: str = "b1", actor: str = "local") -> MemoryService:
    return MemoryService(SQLiteRepository(path), Scope(namespace, actor))


def _store(service: MemoryService, content: str, key: str, **kwargs) -> tuple[str, str]:
    receipt = service.record(MemoryInput(content, **kwargs), key)
    return receipt.memory_id, receipt.revision_id


def _active_heads(path: Path) -> set[tuple[str, str]]:
    """Authoritative state: (memory_id, revision_id) of every non-tombstoned head."""
    db = sqlite3.connect(path)
    try:
        return {
            (row[0], row[1])
            for row in db.execute(
                "SELECT memory_id,current_revision_id FROM memories WHERE tombstoned=0"
            )
        }
    finally:
        db.close()


def _indexed(path: Path) -> set[tuple[str, str]]:
    """Derived state: what the lexical index believes the active heads are."""
    db = sqlite3.connect(path)
    try:
        return {(row[0], row[1]) for row in db.execute("SELECT memory_id,revision_id FROM head_index")}
    finally:
        db.close()


# --- the invariant: I_g = Index(ActiveHeads(D_g)) -------------------------------------


def test_index_equals_active_heads_across_every_mutation(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    assert _indexed(path) == _active_heads(path) == set()

    first, rev1 = _store(service, "payments retry policy", "k1", kind="decision", tags=["payments"])
    assert _indexed(path) == _active_heads(path)

    second, _ = _store(service, "unrelated observation", "k2")
    assert _indexed(path) == _active_heads(path)

    revised = service.revise(first, rev1, MemoryInput("payments retry policy, revised"), "k3")
    assert _indexed(path) == _active_heads(path)
    assert (first, revised.revision_id) in _indexed(path)
    assert (first, rev1) not in _indexed(path)

    service.forget(second, _active_head_of(path, second), "k4")
    assert _indexed(path) == _active_heads(path)
    assert not any(memory == second for memory, _ in _indexed(path))


def _active_head_of(path: Path, memory_id: str) -> str:
    db = sqlite3.connect(path)
    try:
        return db.execute(
            "SELECT current_revision_id FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()[0]
    finally:
        db.close()


def test_rolled_back_write_leaves_index_and_authority_consistent(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    _store(service, "durable body", "k1")
    before_index, before_heads = _indexed(path), _active_heads(path)

    repository = service.repository
    original = repository._bump_generation

    def explode(*args, **kwargs):  # runs last, after both the receipt and the index rows
        raise sqlite3.OperationalError("injected failure after index maintenance")

    repository._bump_generation = explode  # type: ignore[method-assign]
    with pytest.raises(Exception):
        service.record(MemoryInput("body that must not survive"), "k2")
    repository._bump_generation = original  # type: ignore[method-assign]

    assert _indexed(path) == before_index
    assert _active_heads(path) == before_heads
    assert _indexed(path) == _active_heads(path)


# --- search contract ------------------------------------------------------------------


def test_search_finds_by_body_terms_and_reports_reasons(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, _ = _store(service, "Run the payments integration suite after retry changes", "k1",
                          kind="procedure", tags=["payments", "testing"])
    _store(service, "Completely unrelated note about fonts", "k2")

    page = service.search(SearchQuery(query="payments"))
    assert [hit.memory_id for hit in page.hits] == [memory_id]
    hit = page.hits[0]
    assert "body_terms" in hit.match_reasons
    assert "payments" in hit.excerpt.lower()
    assert hit.lexical_rank is not None
    assert not hasattr(hit, "confidence")
    assert not hasattr(hit, "relevance")


def test_tags_all_and_tags_any_are_distinct_filters(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    both, _ = _store(service, "alpha", "k1", tags=["payments", "testing"])
    one, _ = _store(service, "beta", "k2", tags=["payments"])

    all_hits = {hit.memory_id for hit in service.search(SearchQuery(tags_all=("payments", "testing"))).hits}
    any_hits = {hit.memory_id for hit in service.search(SearchQuery(tags_any=("payments", "testing"))).hits}
    assert all_hits == {both}
    assert any_hits == {both, one}


def test_kinds_match_any_supplied_kind(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    decision, _ = _store(service, "a decision", "k1", kind="decision")
    procedure, _ = _store(service, "a procedure", "k2", kind="procedure")
    _store(service, "an observation", "k3", kind="observation")

    hits = {hit.memory_id for hit in service.search(SearchQuery(kinds=("decision", "procedure"))).hits}
    assert hits == {decision, procedure}


def test_empty_request_returns_recent_memories_by_mutation_order(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    first, _ = _store(service, "oldest", "k1")
    second, _ = _store(service, "middle", "k2")
    third, _ = _store(service, "newest", "k3")

    page = service.search(SearchQuery())
    assert [hit.memory_id for hit in page.hits] == [third, second, first]


def test_advanced_fts_syntax_requires_explicit_mode(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    only_alpha, _ = _store(service, "alpha on its own", "k1")
    both, _ = _store(service, "alpha and beta together", "k2")

    # literal mode: AND is an ordinary word, not an operator, so both documents match
    plain = {hit.memory_id for hit in service.search(SearchQuery(query="alpha AND beta")).hits}
    assert plain == {only_alpha, both}

    # advanced mode: AND is a conjunction, so only the document with both terms matches
    advanced = {hit.memory_id for hit in service.search(SearchQuery(query="alpha AND beta", advanced=True)).hits}
    assert advanced == {both}


def test_literal_multiword_query_does_not_require_a_contiguous_phrase(tmp_path: Path) -> None:
    """Regression: literal mode once quoted the whole query as one phrase."""
    service = _service(tmp_path / "memory.sqlite3")
    ledger, _ = _store(service, "The ledger is append-only. Corrections are compensating entries.", "k1")
    _store(service, "Unrelated note about typography", "k2")

    assert [hit.memory_id for hit in service.search(SearchQuery(query="append only ledger")).hits] == [ledger]
    assert [hit.memory_id for hit in service.search(SearchQuery(query="who approves the ledger")).hits] == [ledger]

    # a caller who genuinely wants a phrase asks for one explicitly
    assert service.search(SearchQuery(query='"append only ledger"', advanced=True)).hits == ()


def test_query_with_no_indexable_token_matches_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    _store(service, "a real memory", "k1")
    assert service.search(SearchQuery(query="!!! ???")).hits == ()


def test_malformed_advanced_query_raises_invalid_query(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    _store(service, "anything", "k1")
    with pytest.raises(InvalidQuery):
        service.search(SearchQuery(query='alpha AND ("unclosed', advanced=True))


def test_eligibility_filters_apply_before_the_limit(tmp_path: Path) -> None:
    """A global top-N followed by filtering would hide the only eligible result."""
    service = _service(tmp_path / "memory.sqlite3")
    for i in range(50):
        _store(service, f"payments document number {i}", f"noise-{i}", tags=["noise"])
    wanted, _ = _store(service, "payments document that is tagged correctly", "wanted", tags=["keep"])

    page = service.search(SearchQuery(query="payments", tags_all=("keep",), limit=5))
    assert [hit.memory_id for hit in page.hits] == [wanted]


def test_search_is_scoped_to_namespace_and_actor(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    mine = _service(path, namespace="mine")
    theirs = _service(path, namespace="theirs")
    _store(mine, "shared vocabulary payments", "k1")
    _store(theirs, "shared vocabulary payments", "k1")

    my_hits = mine.search(SearchQuery(query="payments")).hits
    their_hits = theirs.search(SearchQuery(query="payments")).hits
    assert len(my_hits) == 1 and len(their_hits) == 1
    assert my_hits[0].memory_id != their_hits[0].memory_id


def test_forgotten_memories_never_appear_in_search(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, revision_id = _store(service, "payments secret to be forgotten", "k1", tags=["payments"])
    assert service.search(SearchQuery(query="payments")).hits

    service.forget(memory_id, revision_id, "k2")
    assert service.search(SearchQuery(query="payments")).hits == ()
    assert service.search(SearchQuery(tags_all=("payments",))).hits == ()
    assert service.search(SearchQuery()).hits == ()


def test_search_hits_report_whether_earlier_revisions_exist(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, revision_id = _store(service, "first version about payments", "k1")
    only = service.search(SearchQuery(query="payments")).hits[0]
    assert only.has_earlier_revisions is False

    service.revise(memory_id, revision_id, MemoryInput("second version about payments"), "k2")
    revised = service.search(SearchQuery(query="payments")).hits[0]
    assert revised.has_earlier_revisions is True


def test_superseded_terms_are_not_discoverable_in_b1(tmp_path: Path) -> None:
    """Deliberate, documented B1 limitation - asserted so a later change is a decision."""
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, revision_id = _store(service, "the original mentions kerberos", "k1")
    service.revise(memory_id, revision_id, MemoryInput("the replacement mentions oauth"), "k2")

    assert service.search(SearchQuery(query="oauth")).hits
    assert service.search(SearchQuery(query="kerberos")).hits == ()


# --- generation-bound pagination -------------------------------------------------------


def test_pages_within_one_generation_are_disjoint_and_complete(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    stored = {_store(service, f"payments document {i}", f"k{i}")[0] for i in range(25)}

    seen: list[str] = []
    page = service.search(SearchQuery(query="payments", limit=10))
    while True:
        seen.extend(hit.memory_id for hit in page.hits)
        if page.cursor is None:
            break
        page = service.search(SearchQuery(query="payments", limit=10, cursor=page.cursor))

    assert len(seen) == len(set(seen)) == len(stored)
    assert set(seen) == stored


def test_cursor_expires_when_a_write_advances_the_generation(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    for i in range(25):
        _store(service, f"payments document {i}", f"k{i}")

    page = service.search(SearchQuery(query="payments", limit=10))
    assert page.cursor is not None
    _store(service, "a concurrent write that changes corpus statistics", "late")

    with pytest.raises(CursorExpired):
        service.search(SearchQuery(query="payments", limit=10, cursor=page.cursor))


def test_cursor_is_rejected_for_a_different_query_or_scope(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    for i in range(25):
        _store(service, f"payments document {i}", f"k{i}")
    page = service.search(SearchQuery(query="payments", limit=10))
    assert page.cursor is not None

    with pytest.raises(CursorExpired):
        service.search(SearchQuery(query="different", limit=10, cursor=page.cursor))
    with pytest.raises(CursorExpired):
        _service(path, namespace="other").search(SearchQuery(query="payments", limit=10, cursor=page.cursor))


def test_limit_is_bounded(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    _store(service, "anything", "k1")
    with pytest.raises(Exception):
        service.search(SearchQuery(limit=101))
    with pytest.raises(Exception):
        service.search(SearchQuery(limit=0))


# --- history browsing ------------------------------------------------------------------


def test_history_returns_revision_chain_with_parents_and_timestamps(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, first = _store(service, "version one", "k1")
    second = service.revise(memory_id, first, MemoryInput("version two"), "k2").revision_id
    third = service.revise(memory_id, second, MemoryInput("version three"), "k3").revision_id

    page = service.history(memory_id)
    assert [entry.revision_id for entry in page.entries] == [third, second, first]
    assert [entry.parent_revision_id for entry in page.entries] == [second, first, None]
    assert all(entry.created_at for entry in page.entries)
    assert page.entries[0].is_current is True
    assert page.entries[1].is_current is False


def test_history_is_bounded_and_paginates(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, revision_id = _store(service, "v0", "k0")
    for i in range(1, 12):
        revision_id = service.revise(memory_id, revision_id, MemoryInput(f"v{i}"), f"k{i}").revision_id

    page = service.history(memory_id, limit=5)
    assert len(page.entries) == 5
    assert page.cursor is not None

    seen = [entry.revision_id for entry in page.entries]
    while page.cursor is not None:
        page = service.history(memory_id, limit=5, cursor=page.cursor)
        seen.extend(entry.revision_id for entry in page.entries)
    assert len(seen) == len(set(seen)) == 12


def test_history_of_a_forgotten_memory_is_not_found(tmp_path: Path) -> None:
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, revision_id = _store(service, "to be forgotten", "k1")
    service.forget(memory_id, revision_id, "k2")
    with pytest.raises(MemoryNotFound):
        service.history(memory_id)


def test_history_then_get_retrieves_the_superseded_revision(tmp_path: Path) -> None:
    """The workflow the milestone promises: find current, browse history, read the old one."""
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, first = _store(service, "retry policy: three attempts", "k1", kind="decision")
    service.revise(memory_id, first, MemoryInput("retry policy: five attempts"), "k2")

    found = service.search(SearchQuery(query="retry")).hits[0]
    assert found.has_earlier_revisions is True
    older = service.history(found.memory_id).entries[-1]
    assert service.get(found.memory_id, older.revision_id).content == "retry policy: three attempts"


def test_history_reports_what_changed_not_why(tmp_path: Path) -> None:
    """A history entry must never carry a synthesised explanation of the change."""
    service = _service(tmp_path / "memory.sqlite3")
    memory_id, first = _store(service, "before", "k1")
    service.revise(memory_id, first, MemoryInput("after"), "k2")

    entry = service.history(memory_id).entries[0]
    for forbidden in ("reason", "rationale", "why", "explanation", "summary", "diff"):
        assert not hasattr(entry, forbidden)


# --- negative controls -----------------------------------------------------------------


def test_negative_control_empty_index_with_eligible_data_returns_nothing(tmp_path: Path) -> None:
    """If retrieval bypassed the index, these searches would still return results."""
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    _store(service, "payments retry policy", "k1", tags=["payments"])
    assert service.search(SearchQuery(query="payments")).hits
    assert service.search(SearchQuery(tags_all=("payments",))).hits

    db = sqlite3.connect(path)
    try:
        db.execute("DELETE FROM head_fts")
        db.execute("DELETE FROM head_tags")
        db.execute("DELETE FROM head_index")
        db.commit()
    finally:
        db.close()

    assert _active_heads(path), "authoritative data must still exist for this control to mean anything"
    assert service.search(SearchQuery(query="payments")).hits == ()
    assert service.search(SearchQuery(tags_all=("payments",))).hits == ()
    assert service.search(SearchQuery()).hits == ()


def test_negative_control_removing_relevant_memories_removes_their_results(tmp_path: Path) -> None:
    """If results reappeared after removal, the search would not be reading current state."""
    service = _service(tmp_path / "memory.sqlite3")
    relevant = []
    for i in range(3):
        memory_id, revision_id = _store(service, f"payments retry policy {i}", f"k{i}", tags=["payments"])
        relevant.append((memory_id, revision_id))
    _store(service, "an unrelated note about typography", "other")

    assert len(service.search(SearchQuery(query="payments")).hits) == 3
    for index, (memory_id, revision_id) in enumerate(relevant):
        service.forget(memory_id, revision_id, f"forget-{index}")

    assert service.search(SearchQuery(query="payments")).hits == ()
    assert len(service.search(SearchQuery()).hits) == 1


def test_stale_index_row_cannot_surface_content(tmp_path: Path) -> None:
    """External content does not self-synchronise; the authoritative join must catch it."""
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    memory_id, revision_id = _store(service, "payments body that must not leak", "k1")
    service.forget(memory_id, revision_id, "k2")

    db = sqlite3.connect(path)
    try:  # forge a stale index row pointing at the tombstoned memory
        db.execute(
            "INSERT INTO head_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,created_at,durable_seq)"
            " SELECT ?,?,?,?,blob_id,kind,tags_json,created_at,1 FROM revisions WHERE revision_id=?",
            ("b1", "local", memory_id, revision_id, revision_id),
        )
        db.commit()
    finally:
        db.close()

    assert service.search(SearchQuery()).hits == ()
    assert service.search(SearchQuery(query="payments")).hits == ()
