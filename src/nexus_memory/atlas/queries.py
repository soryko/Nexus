"""Counted, scoped reads for the Atlas viewer.

Every public method serves one request from one snapshot of a read-only store, and the
binding is checked again before the result is handed back. Nothing here writes, and every
SQL path that reads revisions or references directly joins ``memories`` with
``tombstoned = 0``: a forgotten memory keeps its revision and reference rows, so a join
that skips the flag would leak it.
"""
from __future__ import annotations

import difflib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator

from nexus_memory.atlas.binding import AtlasBinding
from nexus_memory.domain.errors import CursorExpired, InvalidInput
from nexus_memory.domain.models import HistoryPage, SearchHit, SearchQuery
from nexus_memory.storage import SQLiteRepository

TITLE_LIMIT = 120
COMPARE_LIMIT = 32 * 1024     # bytes per side; larger revisions are reported, not diffed
COMPARE_LINES = 2000


@dataclass(frozen=True, slots=True)
class Overview:
    total: int
    counts: tuple[tuple[str, int], ...]
    generation: int


@dataclass(frozen=True, slots=True)
class Page:
    """``matched`` satisfies the query and filters; ``total`` is the unfiltered set."""
    items: tuple[SearchHit, ...]
    matched: int
    total: int
    cursor: str | None
    generation: int
    restarted: bool = False


@dataclass(frozen=True, slots=True)
class Inspection:
    memory_id: str
    revision_id: str
    current_revision_id: str
    is_historical: bool
    title: str
    kind: str
    tags: tuple[str, ...]
    source_uri: str | None
    revision_created_at: str
    added_at: str
    parent_revision_id: str | None
    references: tuple
    generation: int


@dataclass(frozen=True, slots=True)
class Comparison:
    state: str                      # "ok", "root" or "too_large"
    parent_revision_id: str | None
    lines: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Relation:
    """One relation type around a focus, with its true total and what was left out."""
    kind: str                       # "revision-of" or "cites"
    total: int
    items: tuple[dict, ...]
    omitted: int


@dataclass(frozen=True, slots=True)
class Neighborhood:
    memory_id: str
    revision_id: str
    relations: tuple[Relation, ...]
    generation: int


@dataclass(frozen=True, slots=True)
class Related:
    reason: str
    page: Page


def _allocate(totals: list[int], limit: int) -> list[int]:
    """Split ``limit`` across relation types so that no type can crowd out another."""
    given = [0] * len(totals)
    remaining = limit
    open_types = [i for i, total in enumerate(totals) if total > 0]
    while remaining > 0 and open_types:
        share = max(1, remaining // len(open_types))
        progressed = False
        for i in list(open_types):
            take = min(share, totals[i] - given[i], remaining)
            given[i] += take
            remaining -= take
            progressed = progressed or take > 0
            if given[i] >= totals[i]:
                open_types.remove(i)
            if remaining == 0:
                break
        if not progressed:
            break
    return given


def _title(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= TITLE_LIMIT else first[:TITLE_LIMIT - 1] + "…"


class AtlasQueries:
    def __init__(self, binding: AtlasBinding) -> None:
        self._binding = binding

    @contextmanager
    def _request(self) -> Iterator[SQLiteRepository]:
        repository = self._binding.reader()
        with repository.read_snapshot():
            yield repository
        self._binding.check()   # nothing is released if the binding changed meanwhile

    @property
    def _scope(self):
        return self._binding.scope

    @staticmethod
    def _rows(repository: SQLiteRepository, sql: str, parameters: tuple = ()) -> list:
        db = repository._connect()
        return db.execute(sql, parameters).fetchall()

    def _generation(self, repository: SQLiteRepository) -> int:
        return self._rows(repository, "SELECT generation FROM index_state WHERE id=1")[0][0]

    def _active(self, repository: SQLiteRepository) -> int:
        return self._rows(
            repository,
            "SELECT count(*) FROM head_index h JOIN memories m ON m.namespace=h.namespace"
            " AND m.actor=h.actor AND m.memory_id=h.memory_id"
            " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id",
            (self._scope.namespace, self._scope.actor))[0][0]

    def overview(self) -> Overview:
        with self._request() as repository:
            rows = self._rows(
                repository,
                "SELECT h.kind,count(*) FROM head_index h JOIN memories m ON m.namespace=h.namespace"
                " AND m.actor=h.actor AND m.memory_id=h.memory_id"
                " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id"
                " GROUP BY h.kind ORDER BY h.kind",
                (self._scope.namespace, self._scope.actor))
            counts = tuple((kind, count) for kind, count in rows)
            return Overview(sum(count for _, count in counts), counts, self._generation(repository))

    def _page(self, repository: SQLiteRepository, query: SearchQuery, *, repository_id: str | None = None,
              exclude: str | None = None, unfiltered: SearchQuery | None = None) -> Page:
        scope = self._scope
        restarted = False
        try:
            found = repository.search(scope, query, repository_id, exclude)
        except CursorExpired:
            # Any write advances the store generation, so an old cursor cannot be honoured.
            # Say so and start the list again rather than pretend to resume it.
            query = replace(query, cursor=None)
            found = repository.search(scope, query, repository_id, exclude)
            restarted = True
        matched = repository.count_matches(scope, replace(query, cursor=None), repository_id, exclude)
        if unfiltered is None:
            total = self._active(repository)
        else:
            total = repository.count_matches(scope, unfiltered, repository_id, exclude)
        return Page(found.hits, matched, total, found.cursor, found.generation, restarted)

    def search(self, query: SearchQuery, repository_id: str | None = None) -> Page:
        if repository_id is not None:
            query = replace(query, repository="bound")
        with self._request() as repository:
            return self._page(repository, query, repository_id=repository_id)

    def inspect(self, memory_id: str, revision_id: str | None = None) -> Inspection:
        with self._request() as repository:
            scope = self._scope
            view = repository.get(scope, memory_id, revision_id)
            root = self._rows(
                repository,
                "SELECT created_at FROM revisions WHERE namespace=? AND actor=? AND memory_id=?"
                " AND parent_revision_id IS NULL", (scope.namespace, scope.actor, memory_id))
            return Inspection(
                view.memory_id, view.revision_id, view.current_revision_id,
                view.revision_id != view.current_revision_id, _title(view.content), view.kind, view.tags,
                view.source_uri, view.created_at, root[0][0] if root else view.created_at,
                view.parent_revision_id, view.references, self._generation(repository))

    def neighbors(self, memory_id: str, revision_id: str | None = None, limit: int = 25) -> Neighborhood:
        """The recorded connections of one revision: its sibling revisions and the files it cites."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise InvalidInput("limit must be a positive integer")
        with self._request() as repository:
            scope = self._scope
            view = repository.get(scope, memory_id, revision_id)   # refuses a forgotten memory
            revisions = self._rows(
                repository,
                "SELECT revision_id,parent_revision_id,created_at FROM revisions"
                " WHERE namespace=? AND actor=? AND memory_id=? AND revision_id<>?"
                " ORDER BY created_at DESC, revision_id", (scope.namespace, scope.actor, memory_id, view.revision_id))
            cites = sorted(view.references, key=lambda r: (r.checked_at, r.repository_id, r.path, r.commit_oid),
                           reverse=True)
            given = _allocate([len(revisions), len(cites)], limit)
            relations = (
                Relation("revision-of", len(revisions),
                         tuple({"revision_id": r[0], "parent_revision_id": r[1], "created_at": r[2]}
                               for r in revisions[:given[0]]), len(revisions) - given[0]),
                Relation("cites", len(cites),
                         tuple({"repository_id": r.repository_id, "path": r.path, "commit_oid": r.commit_oid,
                                "checked_at": r.checked_at} for r in cites[:given[1]]), len(cites) - given[1]),
            )
            return Neighborhood(memory_id, view.revision_id, relations, self._generation(repository))

    def body(self, memory_id: str, revision_id: str) -> str:
        with self._request() as repository:
            return repository.get(self._scope, memory_id, revision_id).content

    def history(self, memory_id: str, cursor: str | None = None, limit: int = 20) -> HistoryPage:
        with self._request() as repository:
            return repository.history(self._scope, memory_id, limit, cursor)

    def compare(self, memory_id: str, revision_id: str) -> Comparison:
        with self._request() as repository:
            scope = self._scope
            view = repository.get(scope, memory_id, revision_id)
            if view.parent_revision_id is None:
                return Comparison("root", None)
            parent = repository.get(scope, memory_id, view.parent_revision_id)
            if max(len(view.content.encode()), len(parent.content.encode())) > COMPARE_LIMIT:
                return Comparison("too_large", parent.revision_id)
            lines = list(difflib.unified_diff(
                parent.content.splitlines(), view.content.splitlines(),
                fromfile=parent.revision_id, tofile=view.revision_id, lineterm=""))
            return Comparison("ok", parent.revision_id, tuple(lines[:COMPARE_LINES]), len(lines) > COMPARE_LINES)

    def citations(self, repository_id: str, path: str, commit_oid: str, query: SearchQuery | None = None) -> Page:
        """Current revisions citing exactly this repository, path and commit."""
        base = replace(query or SearchQuery(), repository="bound",
                       reference_paths=(path,), reference_commits=(commit_oid,))
        hub = replace(base, query=None, tags_all=(), tags_any=(), kinds=(), cursor=None)
        with self._request() as repository:
            return self._page(repository, base, repository_id=repository_id, unfiltered=hub)

    def related(self, memory_id: str, *, tag: str | None = None, term: str | None = None,
                limit: int = 20, cursor: str | None = None) -> Related:
        if (tag is None) == (term is None):
            raise InvalidInput("choose either one tag or one term")
        with self._request() as repository:
            view = repository.get(self._scope, memory_id)
            if tag is not None:
                if tag not in view.tags:
                    raise InvalidInput("the tag is not on this memory")
                query = SearchQuery(tags_any=(tag,), limit=limit, cursor=cursor)
                reason = f"shares the tag “{tag}”"
            else:
                if term.casefold() not in view.content.casefold():
                    raise InvalidInput("the term does not appear in this memory")
                query = SearchQuery(query=term, limit=limit, cursor=cursor)
                reason = f"matches the term “{term}”"
            page = self._page(repository, query, exclude=memory_id, unfiltered=SearchQuery(limit=1))
            return Related(reason, page)
