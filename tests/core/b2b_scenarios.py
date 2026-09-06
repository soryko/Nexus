"""B2b acceptance scenarios 1-14 and 17-21, as plain functions.

Each scenario is a function of ``tmp_path`` so the positive tests and the mutation
controls run exactly the same code: a positive test calls it and expects it to return, a
mutation control applies one mutation and expects the designated scenario to raise. This
is the arrangement B2a settled on, and B2b keeps it for the same reason — a control that
exercises a different code path from the test it is meant to validate proves nothing.

Every scenario reads evidence that a verified write recorded, so the writes go through a
scripted verifier and the reads go through none. That split is the point: B2b §1 says
``search`` never spawns git, so the read half runs against a verifier that fails the test
if it is invoked at all, or against no verifier whatsoever.

Test 15, the real MCP round trip, is in ``tests/transport/test_mcp_references.py``; test
16, the query plans, is in ``tests/core/test_reference_filter_plans.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from reference_fakes import BLOB, RaisingVerifier, entry, register

from nexus_memory.domain.errors import CursorExpired, InvalidInput, InvalidReference, RepositoryUnbound
from nexus_memory.domain.models import (
    HEX,
    OBJECT_FORMATS,
    MemoryInput,
    ReferenceInput,
    RepositoryBinding,
    Scope,
    SearchPage,
    SearchQuery,
)
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

SCOPE = Scope("b2b", "local")
SHA1_A = "a" * 40
SHA1_B = "b" * 40
SHA256_C = "c" * 64


class WritingVerifier:
    """Resolves an object id to itself and finds every path asked of it.

    B2b filters recorded evidence; how that evidence got recorded is B2a's subject and is
    tested there against real repositories. This exists only so a scenario can state the
    rows it wants to filter.
    """

    def __init__(self, object_format: str = "sha1") -> None:
        self.width = OBJECT_FORMATS[object_format]

    def check_identity(self) -> None:
        return None

    def resolve_commit(self, spec: str) -> str | None:
        return spec if HEX.fullmatch(spec) and len(spec) == self.width else None

    def tree_entries(self, commit_oid: str, path: str) -> tuple:
        return (entry(path),)


def writer(db: Path, repository_id: str = "repo-a", object_format: str = "sha1",
           scope: Scope = SCOPE) -> MemoryService:
    bound = RepositoryBinding(repository_id, object_format, None)
    repository = SQLiteRepository(db)  # constructed first: it is what migrates the schema
    register(db, scope, repository_id, object_format=object_format)
    return MemoryService(repository, scope, bound, WritingVerifier(object_format))


def reader(db: Path, repository_id: str | None = "repo-a", object_format: str = "sha1",
           scope: Scope = SCOPE, verifier: object | None = None) -> MemoryService:
    """A service for the read half, whose verifier must never be reached.

    ``repository_id=None`` is an unbound process — no binding at all, which is the only
    thing that makes ``repository: "bound"`` unanswerable.
    """
    bound = None if repository_id is None else RepositoryBinding(repository_id, object_format, None)
    if verifier is None and bound is not None:
        verifier = RaisingVerifier()
    return MemoryService(SQLiteRepository(db), scope, bound, verifier)


def store(service: MemoryService, content: str, key: str,
          references: tuple[tuple[str, str], ...] = ()) -> str:
    item = MemoryInput(content, references=tuple(ReferenceInput(path, commit) for path, commit in references))
    return service.record(item, key).memory_id


def ids(page: SearchPage) -> list[str]:
    return [hit.memory_id for hit in page.hits]


def seed(db: Path) -> dict[str, str]:
    """Four memories whose references are what the matchers select on."""
    service = writer(db)
    return {
        "handler": store(service, "the handler", "k-handler", (("src/api/handler.py", SHA1_A),)),
        "apiary": store(service, "the apiary", "k-apiary", (("src/apiary.py", SHA1_A),)),
        "exact": store(service, "the directory itself", "k-exact", (("src/api", SHA1_B),)),
        "plain": store(service, "no references at all", "k-plain"),
    }


# --- 1: each matcher alone ---------------------------------------------------------------


def scenario_each_matcher(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    marks = seed(db)
    service = reader(db)
    assert ids(service.search(SearchQuery(reference_paths=("src/api/handler.py",)))) == [marks["handler"]]
    assert set(ids(service.search(SearchQuery(reference_path_prefix="src/api")))) == {
        marks["handler"], marks["exact"]
    }
    assert ids(service.search(SearchQuery(reference_commits=(SHA1_B,)))) == [marks["exact"]]
    assert set(ids(service.search(SearchQuery(repository="bound")))) == {
        marks["handler"], marks["apiary"], marks["exact"]
    }


# --- 2: the prefix boundary, and the prefix is not a pattern -----------------------------


def scenario_prefix_boundary(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    marks = {
        "handler": store(service, "handler", "p1", (("src/api/handler.py", SHA1_A),)),
        "exact": store(service, "exact", "p2", (("src/api", SHA1_A),)),
        "apiary": store(service, "apiary", "p3", (("src/apiary.py", SHA1_A),)),
        "upper": store(service, "upper", "p4", (("SRC/API/handler.py", SHA1_A),)),
        "percent": store(service, "percent", "p5", (("a%c/f.py", SHA1_A),)),
        "under": store(service, "under", "p6", (("a_c/f.py", SHA1_A),)),
        "abc": store(service, "abc", "p7", (("abc/f.py", SHA1_A),)),
    }
    read = reader(db)

    def prefixed(prefix: str) -> set[str]:
        return set(ids(read.search(SearchQuery(reference_path_prefix=prefix, limit=100))))

    # The boundary, asserted in both directions: a bare string prefix passes the first half.
    assert prefixed("src/api") == {marks["handler"], marks["exact"]}
    # Case is not folded. LIKE folds ASCII case and would return the upper-case path too.
    assert prefixed("SRC/api") == set()
    assert prefixed("src/API") == set()
    assert prefixed("SRC/API") == {marks["upper"]}
    # A recorded path holding % or _ is reachable by naming those bytes...
    assert prefixed("a%c") == {marks["percent"]}
    assert prefixed("a_c") == {marks["under"]}
    # ...and a prefix holding them is not a wildcard. Under LIKE both would match abc/f.py.
    assert marks["abc"] not in prefixed("a%c")
    assert marks["abc"] not in prefixed("a_c")
    assert prefixed("abc") == {marks["abc"]}
    # Exact-path matching is byte-exact for the same reasons.
    assert ids(read.search(SearchQuery(reference_paths=("A%C/F.PY",)))) == []
    assert ids(read.search(SearchQuery(reference_paths=("a%c/f.py",)))) == [marks["percent"]]


# --- 3: one reference satisfies everything -----------------------------------------------


def scenario_single_reference(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    both = store(service, "two references", "s1", (("a.py", SHA1_A), ("b.py", SHA1_B)))
    read = reader(db)
    # a.py is referenced at X and b.py at Y; a.py at Y is not a reference this memory holds.
    assert ids(read.search(SearchQuery(reference_paths=("a.py",), reference_commits=(SHA1_B,)))) == []
    assert ids(read.search(SearchQuery(reference_paths=("a.py",), reference_commits=(SHA1_A,)))) == [both]


# --- 4: multiplicity does not duplicate --------------------------------------------------


def scenario_multiplicity(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    many = store(service, "three matches", "m1",
                 (("src/a.py", SHA1_A), ("src/b.py", SHA1_A), ("src/c.py", SHA1_A)))
    other = store(service, "one match", "m2", (("src/d.py", SHA1_A),))
    read = reader(db)
    page = read.search(SearchQuery(reference_path_prefix="src", limit=100))
    assert ids(page).count(many) == 1
    assert set(ids(page)) == {many, other}
    # A join would fan the commit filter out as well; an existence test cannot.
    assert ids(read.search(SearchQuery(reference_commits=(SHA1_A,), limit=100))).count(many) == 1


# --- 5: filters precede the limit --------------------------------------------------------


def scenario_filters_before_limit(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    eligible = []
    for index in range(30):
        eligible.append(store(service, f"eligible {index}", f"e{index}", (("keep/f.py", SHA1_A),)))
        store(service, f"ineligible {index}", f"i{index}", (("drop/f.py", SHA1_A),))
    page = reader(db).search(SearchQuery(reference_path_prefix="keep", limit=20))
    assert len(page.hits) == 20
    assert set(ids(page)) <= set(eligible)


# --- 6: pagination is exhaustive and non-overlapping --------------------------------------


def scenario_pagination(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    # Two matching references each, deliberately: a join fans a multi-reference memory out
    # across pages, and a single-reference fixture cannot tell a join from an existence test.
    eligible = {
        store(service, f"keep {index}", f"pk{index}",
              (("keep/one.py", SHA1_A), ("keep/two.py", SHA1_A)))
        for index in range(25)
    }
    for index in range(25):
        store(service, f"drop {index}", f"pd{index}", (("drop/f.py", SHA1_A),))
    read = reader(db)
    seen: list[str] = []
    cursor = None
    for _ in range(20):  # bounded, so a non-advancing cursor fails rather than hangs
        page = read.search(SearchQuery(reference_path_prefix="keep", limit=7, cursor=cursor))
        seen.extend(ids(page))
        cursor = page.cursor
        if cursor is None:
            break
    assert cursor is None
    assert len(seen) == len(set(seen)) == len(eligible)
    assert set(seen) == eligible


# --- 7: cursors are bound to filters ------------------------------------------------------


def scenario_cursor_filters(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    for index in range(4):
        store(service, f"body {index}", f"c{index}", (("src/api/f.py", SHA1_A),))
    read = reader(db)
    cursor = read.search(SearchQuery(reference_paths=("src/api/f.py",), limit=2)).cursor
    assert cursor is not None

    # Each argument independently, and the change from present to absent.
    for changed in (
        SearchQuery(reference_paths=("src/api/f.py", "other.py"), limit=2, cursor=cursor),
        SearchQuery(reference_paths=("src/api/f.py",), reference_path_prefix="src", limit=2, cursor=cursor),
        SearchQuery(reference_paths=("src/api/f.py",), reference_commits=(SHA1_A,), limit=2, cursor=cursor),
        SearchQuery(reference_paths=("src/api/f.py",), repository="bound", limit=2, cursor=cursor),
        SearchQuery(limit=2, cursor=cursor),
    ):
        with pytest.raises(CursorExpired):
            read.search(changed)
    # The unchanged query still spends it, so the expiries above are not vacuous.
    assert read.search(SearchQuery(reference_paths=("src/api/f.py",), limit=2, cursor=cursor)).hits

    # And the change from absent to present, which is the direction a fingerprint that
    # simply ignores the new fields would otherwise pass.
    plain = read.search(SearchQuery(limit=2)).cursor
    assert plain is not None
    for added in (
        SearchQuery(reference_paths=("src/api/f.py",), limit=2, cursor=plain),
        SearchQuery(reference_path_prefix="src", limit=2, cursor=plain),
        SearchQuery(reference_commits=(SHA1_A,), limit=2, cursor=plain),
        SearchQuery(repository="bound", limit=2, cursor=plain),
    ):
        with pytest.raises(CursorExpired):
            read.search(added)


# --- 8: cursors are still bound to writes -------------------------------------------------


def scenario_cursor_writes(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    for index in range(4):
        store(service, f"body {index}", f"w{index}", (("src/api/f.py", SHA1_A),))
    read = reader(db)
    cursor = read.search(SearchQuery(reference_paths=("src/api/f.py",), limit=2)).cursor
    assert cursor is not None
    store(service, "a concurrent write", "w-new")
    with pytest.raises(CursorExpired):
        read.search(SearchQuery(reference_paths=("src/api/f.py",), limit=2, cursor=cursor))


# --- 9: reference-free memories never match -----------------------------------------------


def scenario_reference_free(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    marks = seed(db)
    read = reader(db)
    for query in (
        SearchQuery(reference_paths=("src/api/handler.py",), limit=100),
        SearchQuery(reference_path_prefix="src", limit=100),
        SearchQuery(reference_commits=(SHA1_A, SHA1_B), limit=100),
        SearchQuery(repository="bound", limit=100),
    ):
        assert marks["plain"] not in ids(read.search(query))
    assert marks["plain"] in ids(read.search(SearchQuery(limit=100)))


# --- 10: head revisions only ---------------------------------------------------------------


def scenario_head_only(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    receipt = service.record(MemoryInput("first", references=(ReferenceInput("old/f.py", SHA1_A),)), "h1")
    revised = service.revise(receipt.memory_id, receipt.revision_id,
                             MemoryInput("second", references=(ReferenceInput("new/f.py", SHA1_A),)), "h2")
    read = reader(db)
    assert ids(read.search(SearchQuery(reference_paths=("old/f.py",)))) == []
    assert ids(read.search(SearchQuery(reference_paths=("new/f.py",)))) == [receipt.memory_id]
    # The earlier evidence is still reachable through the channels that read revisions.
    earlier = read.get(receipt.memory_id, receipt.revision_id)
    assert [reference.path for reference in earlier.references] == ["old/f.py"]
    assert {entry.revision_id for entry in read.history(receipt.memory_id).entries} == {
        receipt.revision_id, revised.revision_id
    }


# --- 11: scope is still the boundary --------------------------------------------------------


def scenario_scope_boundary(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    other = Scope("b2b", "other-actor")
    elsewhere = Scope("b2b-other", "local")
    mine = store(writer(db), "mine", "x1", (("shared/f.py", SHA1_A),))
    theirs = store(writer(db, scope=other), "theirs", "x2", (("shared/f.py", SHA1_A),))
    far = store(writer(db, scope=elsewhere), "far", "x3", (("shared/f.py", SHA1_A),))
    # Identical references, under the same repository_id, in three scopes.
    query = SearchQuery(reference_paths=("shared/f.py",), repository="bound", limit=100)
    assert ids(reader(db).search(query)) == [mine]
    assert ids(reader(db, scope=other).search(query)) == [theirs]
    assert ids(reader(db, scope=elsewhere).search(query)) == [far]


# --- 12: search never spawns git -------------------------------------------------------------


SHAPES = (
    SearchQuery(reference_paths=("src/api/handler.py",), limit=100),
    SearchQuery(reference_path_prefix="src/api", limit=100),
    SearchQuery(reference_commits=(SHA1_A,), limit=100),
    SearchQuery(limit=100),
)


def scenario_never_spawns_git(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    seed(db)
    raising = reader(db)  # a verifier that fails the test if it is executed at all
    absent = reader(db, verifier=False)  # git absent: a binding with no verifier
    absent.verifier = None
    unbound = reader(db, repository_id=None)  # no binding at all
    for service in (raising, absent, unbound):
        for query in SHAPES:
            assert service.search(query).hits
    for service in (raising, absent):
        assert service.search(SearchQuery(repository="bound", limit=100)).hits
    # An empty page would be indistinguishable from "nothing matched"; it is an error instead.
    with pytest.raises(RepositoryUnbound):
        unbound.search(SearchQuery(repository="bound"))
    with pytest.raises(RepositoryUnbound):
        unbound.search(SearchQuery(repository="bound", reference_paths=("src/api/handler.py",)))


# --- 13: evidence is complete in a hit ---------------------------------------------------------


def scenario_complete_evidence(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service = writer(db)
    memory_id = store(service, "three references", "e1",
                      (("a.py", SHA1_A), ("b.py", SHA1_A), ("c.py", SHA1_B)))
    page = reader(db).search(SearchQuery(reference_paths=("a.py",)))
    assert ids(page) == [memory_id]
    assert sorted(reference.path for reference in page.hits[0].references) == ["a.py", "b.py", "c.py"]
    assert page.hits[0].references[0].object_oid == BLOB


# --- 14: rejections, one row of the section 8 table at a time ------------------------------------


REJECTED = (
    {"reference_paths": ("../etc/passwd",)},
    {"reference_paths": ("/absolute",)},
    {"reference_paths": ("trailing/",)},
    {"reference_paths": ("double//segment",)},
    {"reference_path_prefix": ""},
    {"reference_path_prefix": "."},
    {"reference_commits": ("HEAD",)},
    {"reference_commits": ("main",)},
    {"reference_commits": ("v1.2",)},
    {"reference_commits": ("abc123",)},
    {"reference_commits": ("HEAD~3",)},
    {"reference_commits": ("A" * 40,)},
    {"reference_commits": ("a" * 41,)},
    {"repository": ""},
    {"repository": "Bound"},
    {"repository": "repo-a"},
    {"reference_paths": tuple(f"p{index}.py" for index in range(33))},
    {"reference_commits": tuple(f"{index:040x}" for index in range(33))},
)


def scenario_rejections(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    store(writer(db), "sha1 evidence", "b1", (("f.py", SHA1_A),))
    read = reader(db)
    for arguments in REJECTED:
        with pytest.raises(InvalidReference):
            SearchQuery(**arguments)
    # Thirty-two is accepted, so the cap is the cap and not an off-by-one.
    read.search(SearchQuery(reference_paths=tuple(f"p{index}.py" for index in range(32))))
    read.search(SearchQuery(reference_commits=tuple(f"{index:040x}" for index in range(32))))
    # The width check needs the binding, so it is the service's and not the query's.
    with pytest.raises(InvalidReference):
        read.search(SearchQuery(repository="bound", reference_commits=(SHA256_C,)))
    assert read.search(SearchQuery(reference_commits=(SHA256_C,))).hits == ()


# --- 17: a binding without git still answers "bound" -----------------------------------------------


def scenario_binding_without_git(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    marks = seed(db)
    service = MemoryService(SQLiteRepository(db), SCOPE, RepositoryBinding("repo-a", "sha1", None), None)
    page = service.search(SearchQuery(repository="bound", limit=100))
    assert set(ids(page)) == {marks["handler"], marks["apiary"], marks["exact"]}
    status = service.status()
    assert status.verification == "unavailable"
    assert status.repository_id == "repo-a"


# --- 18: cursors are bound to the resolved repository ------------------------------------------------


def scenario_resolved_repository_cursor(tmp_path: Path) -> None:
    """Two cursors, minted separately, because they answer two different questions."""
    db = tmp_path / "memory.sqlite3"
    first = writer(db, "repo-a")
    second = writer(db, "repo-b")
    for index in range(3):
        store(first, f"a{index}", f"ra{index}", (("f.py", SHA1_A),))
        store(second, f"b{index}", f"rb{index}", (("f.py", SHA1_A),))

    reader_a = reader(db, "repo-a")
    reader_b = reader(db, "repo-b")
    unbound = reader(db, repository_id=None)

    bound_cursor = reader_a.search(SearchQuery(repository="bound", limit=2)).cursor
    any_cursor = reader_a.search(SearchQuery(limit=2)).cursor
    assert bound_cursor is not None and any_cursor is not None and bound_cursor != any_cursor

    # A "bound" cursor is refused by a differently bound process in the same scope...
    with pytest.raises(CursorExpired):
        reader_b.search(SearchQuery(repository="bound", limit=2, cursor=bound_cursor))
    # ...and refused under "any", where the argument itself has changed.
    with pytest.raises(CursorExpired):
        reader_a.search(SearchQuery(limit=2, cursor=bound_cursor))
    # It is still good in the process that minted it, so neither expiry is vacuous.
    assert reader_a.search(SearchQuery(repository="bound", limit=2, cursor=bound_cursor)).hits

    # The separately minted "any" cursor does not depend on the binding at all.
    assert reader_b.search(SearchQuery(limit=2, cursor=any_cursor)).hits
    assert unbound.search(SearchQuery(limit=2, cursor=any_cursor)).hits
    assert ids(reader_b.search(SearchQuery(limit=2, cursor=any_cursor))) == \
        ids(reader_a.search(SearchQuery(limit=2, cursor=any_cursor)))

    # Argument validation precedes cursor validation, with either cursor.
    for cursor in (bound_cursor, any_cursor):
        with pytest.raises(RepositoryUnbound):
            unbound.search(SearchQuery(repository="bound", limit=2, cursor=cursor))


# --- 19: mixed commit formats -------------------------------------------------------------------


def scenario_mixed_formats(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    sha1 = store(writer(db, "repo-a", "sha1"), "sha1 evidence", "f1", (("f.py", SHA1_A),))
    sha256 = store(writer(db, "repo-c", "sha256"), "sha256 evidence", "f2", (("f.py", SHA256_C),))

    mixed = SearchQuery(reference_commits=(SHA1_A, SHA256_C), limit=100)
    assert set(ids(reader(db).search(mixed))) == {sha1, sha256}
    # Under "any" the answer does not depend on what this process is bound to.
    assert set(ids(reader(db, "repo-c", "sha256").search(mixed))) == {sha1, sha256}

    bound_sha1 = reader(db, "repo-a", "sha1")
    with pytest.raises(InvalidReference):  # the width the bound repository does not use
        bound_sha1.search(SearchQuery(repository="bound", reference_commits=(SHA256_C,)))
    with pytest.raises(InvalidReference):  # a mixed list is rejected whole, never reduced
        bound_sha1.search(SearchQuery(repository="bound", reference_commits=(SHA1_A, SHA256_C)))
    # Silent reduction would have returned exactly this page; rejection is the difference.
    assert ids(bound_sha1.search(SearchQuery(repository="bound", reference_commits=(SHA1_A,)))) == [sha1]


# --- 20: empty is absent ---------------------------------------------------------------------------


EMPTY_FORMS = (
    {"reference_paths": ()},
    {"reference_paths": []},
    {"reference_commits": ()},
    {"reference_commits": []},
    {"repository": "any"},
)


def scenario_empty_is_absent(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    marks = seed(db)
    for index in range(4):
        store(writer(db), f"body {index}", f"z{index}", (("f.py", SHA1_A),))
    read = reader(db)

    baseline = read.search(SearchQuery(limit=100))
    for form in EMPTY_FORMS:
        page = read.search(SearchQuery(limit=100, **form))
        assert ids(page) == ids(baseline), form
        assert [hit.match_reasons for hit in page.hits] == [hit.match_reasons for hit in baseline.hits], form
        assert all("references" not in hit.match_reasons for hit in page.hits), form

    # The same cursor, in both directions: minted empty and spent absent, and the reverse.
    for form in EMPTY_FORMS:
        empty_cursor = read.search(SearchQuery(limit=2, **form)).cursor
        assert empty_cursor is not None, form
        assert read.search(SearchQuery(limit=2, cursor=empty_cursor)).hits, form
        plain = read.search(SearchQuery(limit=2)).cursor
        assert read.search(SearchQuery(limit=2, cursor=plain, **form)).hits, form

    # A restricting argument does earn the reason, so the assertions above are not vacuous.
    for query in (
        SearchQuery(reference_path_prefix="src/api", limit=100),
        SearchQuery(repository="bound", limit=100),
        SearchQuery(reference_paths=("src/api/handler.py",), limit=100),
        SearchQuery(reference_commits=(SHA1_A,), limit=100),
    ):
        page = read.search(query)
        assert page.hits and all("references" in hit.match_reasons for hit in page.hits), query
    assert marks["handler"]


# --- 21: null is absent, for the four reference filters only -------------------------------------------


def scenario_null_is_absent(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    seed(db)
    for index in range(4):
        store(writer(db), f"body {index}", f"n{index}", (("f.py", SHA1_A),))
    read = reader(db)

    baseline = read.search(SearchQuery(limit=100))
    nulled = read.search(SearchQuery(
        limit=100, repository=None, reference_paths=None,
        reference_path_prefix=None, reference_commits=None,
    ))
    assert ids(nulled) == ids(baseline)
    assert [hit.match_reasons for hit in nulled.hits] == [hit.match_reasons for hit in baseline.hits]
    # One field at a time, so none can hide behind the others.
    for field in ("repository", "reference_paths", "reference_path_prefix", "reference_commits"):
        assert ids(read.search(SearchQuery(limit=100, **{field: None}))) == ids(baseline), field
    # And the same cursor fingerprint, which is where a "null is its own value" bug lands.
    cursor = read.search(SearchQuery(
        limit=2, repository=None, reference_paths=None,
        reference_path_prefix=None, reference_commits=None,
    )).cursor
    assert cursor is not None
    assert read.search(SearchQuery(limit=2, cursor=cursor)).hits

    # The divergence, pinned in the same scenario and on every argument it covers.
    for field in ("tags_all", "tags_any", "kinds"):
        with pytest.raises(InvalidInput):
            SearchQuery(**{field: None})


SCENARIOS = {
    "each_matcher": scenario_each_matcher,
    "prefix_boundary": scenario_prefix_boundary,
    "single_reference": scenario_single_reference,
    "multiplicity": scenario_multiplicity,
    "filters_before_limit": scenario_filters_before_limit,
    "pagination": scenario_pagination,
    "cursor_filters": scenario_cursor_filters,
    "cursor_writes": scenario_cursor_writes,
    "reference_free": scenario_reference_free,
    "head_only": scenario_head_only,
    "scope_boundary": scenario_scope_boundary,
    "never_spawns_git": scenario_never_spawns_git,
    "complete_evidence": scenario_complete_evidence,
    "rejections": scenario_rejections,
    "binding_without_git": scenario_binding_without_git,
    "resolved_repository_cursor": scenario_resolved_repository_cursor,
    "mixed_formats": scenario_mixed_formats,
    "empty_is_absent": scenario_empty_is_absent,
    "null_is_absent": scenario_null_is_absent,
}
