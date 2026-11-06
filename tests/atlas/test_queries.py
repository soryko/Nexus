from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from nexus_memory.atlas.binding import open_binding
from nexus_memory.atlas.queries import AtlasQueries
from nexus_memory.domain.errors import InvalidInput, MemoryNotFound
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
REPO_ONE = "repo-one"
REPO_TWO = "repo-two"
PATH = "src/hub.py"


def _cite(database, scope: Scope, memory_id: str, revision_id: str, repository_id: str,
          commit: str, path: str = PATH) -> None:
    """Record a reference directly, so the query layer is tested without a Git checkout."""
    with sqlite3.connect(database) as db:
        db.execute("INSERT OR IGNORE INTO repositories VALUES(?,?,?,?,?,?)",
                   (scope.namespace, scope.actor, repository_id, "sha1", None, "2026-11-05T00:00:00Z"))
        db.execute("INSERT INTO revision_references VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (scope.namespace, scope.actor, memory_id, revision_id, repository_id, commit, path,
                    "c" * 40, "blob", "100644", "2026-11-05T00:00:00Z"))


class Corpus:
    def __init__(self, enrolled) -> None:
        self.e = enrolled
        self.service = enrolled.writer()
        self.other = MemoryService(SQLiteRepository(enrolled.database), Scope("some-other-project", "local"))
        self.ids: dict[str, str] = {}
        self.revs: dict[str, str] = {}

    def add(self, name: str, text: str, **kw) -> str:
        receipt = self.service.record(MemoryInput(text, **kw), "k-" + name)
        self.ids[name], self.revs[name] = receipt.memory_id, receipt.revision_id
        return receipt.memory_id

    def queries(self) -> AtlasQueries:
        return AtlasQueries(open_binding(self.e.checkout, self.e.resolve))


@pytest.fixture
def corpus(enrolled) -> Corpus:
    c = Corpus(enrolled)
    for i in range(12):
        c.add(f"obs{i}", f"Caption queue note {i} about retention", kind="observation", tags=["queue"])
    for i in range(5):
        c.add(f"dec{i}", f"Decision {i}: caption retention is 72 hours", kind="decision", tags=["queue", "policy"])
    c.add("orphan", "An unlinked procedure for rotating keys", kind="procedure")
    c.add("gone", "Forgotten caption retention memory", kind="decision", tags=["queue"])
    c.service.forget(c.ids["gone"], c.revs["gone"], "k-forget-gone")
    c.other.record(MemoryInput("Caption retention in another project", kind="decision", tags=["queue"]), "k-o1")
    return c


def test_overview_counts_each_active_memory_once(corpus) -> None:
    overview = corpus.queries().overview()
    counts = dict(overview.counts)
    assert counts == {"decision": 5, "observation": 13, "procedure": 1}
    assert overview.total == sum(counts.values()) == 19


@pytest.mark.parametrize("profile", ["exact", "stem", "dual", "split"])
def test_search_pages_concatenate_to_core_order(corpus, profile) -> None:
    corpus.e.writer(index_profile=profile)  # switch the stored profile
    atlas = corpus.queries()
    core = SQLiteRepository(corpus.e.database, index_profile=profile)
    scope = corpus.e.scope
    query = SearchQuery(query="caption retention", limit=4)
    expected, cursor = [], None
    while True:
        page = core.search(scope, replace(query, cursor=cursor))
        expected += [h.memory_id for h in page.hits]
        cursor = page.cursor
        if cursor is None:
            break
    seen, cursor, matched = [], None, set()
    while True:
        page = atlas.search(replace(query, cursor=cursor))
        seen += [h.memory_id for h in page.items]
        matched.add((page.matched, page.total))
        cursor = page.cursor
        if cursor is None:
            break
    assert seen == expected and len(set(seen)) == len(seen)
    assert matched == {(len(expected), 19)}
    assert corpus.ids["gone"] not in seen


def test_filters_combine_before_counting(corpus) -> None:
    atlas = corpus.queries()
    page = atlas.search(SearchQuery(query="caption", kinds=("decision",), tags_all=("queue", "policy"), limit=2))
    assert page.matched == 5 and len(page.items) == 2 and page.cursor
    empty = atlas.search(SearchQuery(query="no such term anywhere"))
    assert (empty.matched, empty.items, empty.cursor, empty.total) == (0, (), None, 19)
    last = atlas.search(SearchQuery(kinds=("procedure",)))
    assert (last.matched, len(last.items), last.cursor) == (1, 1, None)


def test_hub_returns_only_current_revisions_of_the_exact_triple(corpus) -> None:
    s = corpus.e.scope
    for name in ("obs0", "obs1", "obs2"):
        _cite(corpus.e.database, s, corpus.ids[name], corpus.revs[name], REPO_ONE, COMMIT_A)
    _cite(corpus.e.database, s, corpus.ids["obs3"], corpus.revs["obs3"], REPO_ONE, COMMIT_B)   # other commit
    _cite(corpus.e.database, s, corpus.ids["obs4"], corpus.revs["obs4"], REPO_TWO, COMMIT_A)   # other repository
    _cite(corpus.e.database, s, corpus.ids["obs5"], corpus.revs["obs5"], REPO_ONE, COMMIT_A, "src/other.py")
    atlas = corpus.queries()
    hub = atlas.citations(REPO_ONE, PATH, COMMIT_A)
    assert {h.memory_id for h in hub.items} == {corpus.ids[n] for n in ("obs0", "obs1", "obs2")}
    assert (hub.matched, hub.total) == (3, 3)
    assert {h.memory_id for h in atlas.citations(REPO_TWO, PATH, COMMIT_A).items} == {corpus.ids["obs4"]}
    assert atlas.citations(REPO_ONE, PATH, COMMIT_B).matched == 1
    filtered = atlas.citations(REPO_ONE, PATH, COMMIT_A, SearchQuery(query="1"))
    assert (filtered.matched, filtered.total) == (1, 3)


def test_historical_citation_stays_in_history(corpus) -> None:
    s = corpus.e.scope
    _cite(corpus.e.database, s, corpus.ids["dec0"], corpus.revs["dec0"], REPO_ONE, COMMIT_A)
    receipt = corpus.service.revise(corpus.ids["dec0"], corpus.revs["dec0"],
                                    MemoryInput("Decision 0 revised: retention is 48 hours", kind="decision",
                                                tags=["queue"]), "k-rev-dec0")
    _cite(corpus.e.database, s, corpus.ids["dec0"], receipt.revision_id, REPO_ONE, COMMIT_B)
    atlas = corpus.queries()
    assert atlas.citations(REPO_ONE, PATH, COMMIT_A).items == ()
    assert [h.memory_id for h in atlas.citations(REPO_ONE, PATH, COMMIT_B).items] == [corpus.ids["dec0"]]
    old = atlas.inspect(corpus.ids["dec0"], corpus.revs["dec0"])
    assert old.is_historical and [r.commit_oid for r in old.references] == [COMMIT_A]
    assert not atlas.inspect(corpus.ids["dec0"]).is_historical
    assert atlas.inspect(corpus.ids["dec0"]).added_at == old.revision_created_at


def test_forgotten_memory_is_absent_everywhere(corpus) -> None:
    atlas = corpus.queries()
    gone, rev = corpus.ids["gone"], corpus.revs["gone"]
    for call in (lambda: atlas.inspect(gone), lambda: atlas.inspect(gone, rev), lambda: atlas.body(gone, rev),
                 lambda: atlas.history(gone), lambda: atlas.compare(gone, rev),
                 lambda: atlas.related(gone, tag="queue")):
        with pytest.raises(MemoryNotFound):
            call()
    s = corpus.e.scope
    _cite(corpus.e.database, s, gone, rev, REPO_ONE, COMMIT_A)     # its rows survive the tombstone
    assert atlas.citations(REPO_ONE, PATH, COMMIT_A).items == ()
    assert gone not in {h.memory_id for h in atlas.related(corpus.ids["obs0"], tag="queue").page.items}


def test_one_request_never_mixes_snapshots(corpus, monkeypatch) -> None:
    atlas = corpus.queries()
    target = corpus.ids["obs0"]
    revised = corpus.service.revise(target, corpus.revs["obs0"],
                                    MemoryInput("Caption queue note 0, revised", kind="observation"), "k-snap")
    original = SQLiteRepository.get
    calls = []

    def forget_between_reads(self, scope, memory_id, revision_id=None):
        calls.append(revision_id)
        if len(calls) == 2:   # compare reads the revision, then its parent
            corpus.service.forget(target, revised.revision_id, "k-mid")
        return original(self, scope, memory_id, revision_id)

    monkeypatch.setattr(SQLiteRepository, "get", forget_between_reads)
    diff = atlas.compare(target, revised.revision_id)
    assert len(calls) == 2 and diff.state == "ok"          # entirely the earlier state
    monkeypatch.undo()
    with pytest.raises(MemoryNotFound):
        atlas.compare(target, revised.revision_id)          # and entirely the later one


def test_unrelated_write_restarts_a_deep_page_and_says_so(corpus) -> None:
    atlas = corpus.queries()
    first = atlas.search(SearchQuery(query="caption retention", limit=3))
    corpus.add("late", "Caption retention entry written after paging began", kind="observation")
    resumed = atlas.search(SearchQuery(query="caption retention", limit=3, cursor=first.cursor))
    assert resumed.restarted and not first.restarted
    assert resumed.matched == first.matched + 1
    browse = atlas.search(SearchQuery(limit=3))
    corpus.add("later", "Another unrelated write", kind="observation")
    assert atlas.search(SearchQuery(limit=3, cursor=browse.cursor)).restarted


def test_related_excludes_the_selected_memory_before_paging(corpus) -> None:
    atlas = corpus.queries()
    first = atlas.related(corpus.ids["dec0"], tag="policy", limit=2)
    assert "policy" in first.reason
    assert first.page.matched == 4 and first.page.total == 18
    ids, cursor = [], None
    while True:
        page = atlas.related(corpus.ids["dec0"], tag="policy", limit=2, cursor=cursor).page
        ids += [h.memory_id for h in page.items]
        cursor = page.cursor
        if cursor is None:
            break
    assert corpus.ids["dec0"] not in ids and len(ids) == 4
    by_term = atlas.related(corpus.ids["obs0"], term="retention").page
    assert corpus.ids["obs0"] not in {h.memory_id for h in by_term.items}
    with pytest.raises(InvalidInput):
        atlas.related(corpus.ids["obs0"], tag="policy")      # not on this memory
    with pytest.raises(InvalidInput):
        atlas.related(corpus.ids["obs0"], term="absentterm")
    with pytest.raises(InvalidInput):
        atlas.related(corpus.ids["obs0"])


def test_compare_reports_a_diff_a_root_and_an_oversize_pair(corpus) -> None:
    atlas = corpus.queries()
    receipt = corpus.service.revise(corpus.ids["obs0"], corpus.revs["obs0"],
                                    MemoryInput("Caption queue note 0, now revised", kind="observation"), "k-r0")
    diff = atlas.compare(corpus.ids["obs0"], receipt.revision_id)
    assert diff.state == "ok" and any(line.startswith("+Caption queue note 0, now") for line in diff.lines)
    assert atlas.compare(corpus.ids["obs0"], corpus.revs["obs0"]).state == "root"
    big = corpus.service.revise(corpus.ids["obs0"], receipt.revision_id,
                                MemoryInput("x" * 40_000, kind="observation"), "k-r1")
    assert atlas.compare(corpus.ids["obs0"], big.revision_id).state == "too_large"


def test_title_is_a_bounded_first_line(corpus) -> None:
    mid = corpus.add("long", "\n  " + "w" * 300 + "\nsecond line", kind="observation")
    title = corpus.queries().inspect(mid).title
    assert len(title) == 120 and title.endswith("…") and "second" not in title


def test_hub_probes_the_reference_key_instead_of_scanning_references(corpus) -> None:
    """Version one adds no index. The hub walks the scope's heads and probes the primary key
    of ``revision_references`` once per head; this guards against it becoming a table scan."""
    repository = corpus.queries()._binding.reader()
    query = SearchQuery(repository="bound", reference_paths=(PATH,), reference_commits=(COMMIT_A,))
    inner, parameters, _ = repository._candidates(corpus.e.scope, query, REPO_ONE)
    with sqlite3.connect(corpus.e.database) as db:
        plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN SELECT count(*) FROM (" + inner + ")", parameters)]
    assert any(line.startswith("SEARCH r EXISTS USING INDEX sqlite_autoindex_revision_references_1") for line in plan)
    assert not any(line.startswith("SCAN r") for line in plan)


def _cite_many(corpus, memory_id: str, revision_id: str, count: int) -> None:
    for n in range(count):
        _cite(corpus.e.database, corpus.e.scope, memory_id, revision_id, REPO_ONE, COMMIT_A, f"src/file_{n:02d}.py")


def test_neighbors_share_the_allowance_so_a_dense_type_cannot_hide_another(corpus) -> None:
    target, revision = corpus.ids["dec1"], corpus.revs["dec1"]
    for n in range(6):
        revision = corpus.service.revise(target, revision, MemoryInput(f"Decision 1, revision {n}", kind="decision"),
                                         f"k-chain-{n}").revision_id
    _cite_many(corpus, target, revision, 30)
    hood = corpus.queries().neighbors(target)
    relations = {r.kind: r for r in hood.relations}
    assert (relations["revision-of"].total, relations["cites"].total) == (6, 30)
    assert len(relations["revision-of"].items) == 6 and len(relations["cites"].items) == 19
    assert sum(len(r.items) for r in hood.relations) == 25
    assert (relations["revision-of"].omitted, relations["cites"].omitted) == (0, 11)
    assert hood.revision_id == revision
    # newest revision first, selected revision excluded, then stable order
    created = [item["created_at"] for item in relations["revision-of"].items]
    assert created == sorted(created, reverse=True)
    assert revision not in {item["revision_id"] for item in relations["revision-of"].items}


def test_neighbors_of_an_isolated_memory_are_empty_and_the_limit_is_honoured(corpus) -> None:
    hood = corpus.queries().neighbors(corpus.ids["orphan"])
    assert [(r.kind, r.total, r.items) for r in hood.relations] == [("revision-of", 0, ()), ("cites", 0, ())]
    target = corpus.ids["dec2"]
    _cite_many(corpus, target, corpus.revs["dec2"], 10)
    small = corpus.queries().neighbors(target, limit=4)
    assert sum(len(r.items) for r in small.relations) == 4
    assert {r.kind: r.omitted for r in small.relations} == {"revision-of": 0, "cites": 6}
    again = corpus.queries().neighbors(target, limit=4)
    assert small == again       # deterministic


def test_neighbors_of_an_older_revision_use_that_revisions_own_citations(corpus) -> None:
    target, first = corpus.ids["dec3"], corpus.revs["dec3"]
    _cite_many(corpus, target, first, 2)
    second = corpus.service.revise(target, first, MemoryInput("Decision 3, revised", kind="decision"), "k-n2").revision_id
    _cite_many(corpus, target, second, 5)
    atlas = corpus.queries()
    assert {r.kind: r.total for r in atlas.neighbors(target, first).relations} == {"revision-of": 1, "cites": 2}
    assert {r.kind: r.total for r in atlas.neighbors(target).relations} == {"revision-of": 1, "cites": 5}


def test_neighbors_refuse_a_forgotten_memory(corpus) -> None:
    with pytest.raises(MemoryNotFound):
        corpus.queries().neighbors(corpus.ids["gone"])


def test_two_dense_relation_types_both_stay_visible_when_each_exceeds_the_allowance(corpus) -> None:
    from nexus_memory.atlas.queries import _allocate
    given = _allocate([30, 60], 25)
    assert sum(given) == 25 and min(given) >= 10, given          # a first-type-first split would give [25, 0]
    target, revision = corpus.ids["dec1"], corpus.revs["dec1"]
    for n in range(30):
        revision = corpus.service.revise(target, revision, MemoryInput(f"Decision 1, long chain {n}", kind="decision"),
                                         f"k-long-{n}").revision_id
    _cite_many(corpus, target, revision, 60)
    relations = {r.kind: r for r in corpus.queries().neighbors(target).relations}
    assert len(relations["revision-of"].items) >= 10 and len(relations["cites"].items) >= 10
    assert len(relations["revision-of"].items) + len(relations["cites"].items) == 25
