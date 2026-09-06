"""B2b negative controls: each mutation disables one invariant and must fail its scenario.

Failing more than the designated scenario is recorded, not treated as a defect; a mutation
that failed nothing would be investigated for equivalence and execution coverage before the
invariant was called unguarded. The full matrix is a development run
(``NEXUS_MUTATION_MATRIX=1``), printed the way B2a's is.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from b2b_scenarios import SCENARIOS

from nexus_memory.domain import models
from nexus_memory.domain.errors import (
    CursorExpired,
    InvalidInput,
    InvalidReference,
    RepositoryUnbound,
)
from nexus_memory.domain.models import REPOSITORY_BOUND, SearchQuery
from nexus_memory.memory.service import MemoryService
from nexus_memory.storage.sqlite import SQLiteRepository

DETECTED = (AssertionError, pytest.fail.Exception)


# --- ordering and the limit ---------------------------------------------------------------


def filter_after_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decide eligibility on the page instead of inside the query."""
    original = SQLiteRepository.search

    def search(self, scope, query, repository_id=None):
        stripped = replace(query, repository="any", reference_paths=(),
                           reference_path_prefix=None, reference_commits=())
        page = original(self, scope, stripped, None)
        prefix = query.reference_path_prefix
        paths = set(query.reference_paths or ())
        kept = tuple(
            hit for hit in page.hits
            if any(
                (not paths or reference.path in paths)
                and (prefix is None or reference.path == prefix or reference.path.startswith(prefix + "/"))
                and (not query.reference_commits or reference.commit_oid in query.reference_commits)
                and (query.repository != REPOSITORY_BOUND or reference.repository_id == repository_id)
                for reference in hit.references
            )
        )
        return replace(page, hits=kept)

    monkeypatch.setattr(SQLiteRepository, "search", search)


# --- the cursor fingerprint ------------------------------------------------------------------


def omit_filters_from_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SQLiteRepository._fingerprint

    def fingerprint(scope, query, repository_id=None):
        return original(scope, replace(query, repository="any", reference_paths=(),
                                       reference_path_prefix=None, reference_commits=()), None)

    monkeypatch.setattr(SQLiteRepository, "_fingerprint", staticmethod(fingerprint))


def fingerprint_the_literal_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SQLiteRepository._fingerprint
    monkeypatch.setattr(SQLiteRepository, "_fingerprint",
                        staticmethod(lambda scope, query, repository_id=None: original(scope, query, "bound")))


def drop_repository_from_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A "bound"-minted cursor is then accepted under repository: "any"."""
    original = SQLiteRepository._fingerprint

    def fingerprint(scope, query, repository_id=None):
        return original(scope, replace(query, repository="any"), None)

    monkeypatch.setattr(SQLiteRepository, "_fingerprint", staticmethod(fingerprint))


def fingerprint_the_binding_under_any(monkeypatch: pytest.MonkeyPatch) -> None:
    """An "any"-minted cursor then expires merely because the process binding differs."""
    original = SQLiteRepository._fingerprint

    def fingerprint(scope, query, repository_id=None):
        return original(scope, replace(query, repository=REPOSITORY_BOUND), repository_id)

    def resolve(self, query):
        return None if self.binding is None else self.binding.repository_id

    monkeypatch.setattr(SQLiteRepository, "_fingerprint", staticmethod(fingerprint))
    monkeypatch.setattr(MemoryService, "_filter_repository_id", resolve)


def fingerprint_an_empty_list_apart_from_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop normalising the empty forms, and let the fingerprint see what arrived."""
    monkeypatch.setattr(models, "_normalized_reference_paths", lambda values: values)
    monkeypatch.setattr(models, "_normalized_reference_commits", lambda values: values)
    original = SQLiteRepository._fingerprint

    def fingerprint(scope, query, repository_id=None):
        return original(scope, query, repository_id) + str(
            (repr(query.reference_paths), repr(query.reference_commits))
        )

    monkeypatch.setattr(SQLiteRepository, "_fingerprint", staticmethod(fingerprint))


def check_the_cursor_before_the_repository_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MemoryService.search

    def search(self, query):
        try:
            return original(self, query)
        except RepositoryUnbound:
            # The cursor is looked at first, so an unbound process is told its cursor expired.
            return self.repository.search(self.scope, replace(query, repository="any"), None)

    monkeypatch.setattr(MemoryService, "search", search)


# --- the predicate ------------------------------------------------------------------------------


def bare_string_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        SQLiteRepository, "_path_prefix_clause",
        staticmethod(lambda prefix: ("r.path>=? AND r.path<?", [prefix, prefix + "\U0010FFFF"])),
    )


def like_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The obvious SQL spelling: ASCII case folds, and % and _ become wildcards."""
    monkeypatch.setattr(
        SQLiteRepository, "_path_prefix_clause",
        staticmethod(lambda prefix: ("r.path=? OR r.path LIKE ?", [prefix, prefix + "/%"])),
    )


def conditions_across_different_references(monkeypatch: pytest.MonkeyPatch) -> None:
    """One EXISTS per condition, so the conditions no longer meet on a single row."""
    def reference_filter(query, repository_id):
        conditions, parameters = SQLiteRepository._reference_conditions(query, repository_id)
        if not conditions:
            return "", []
        clause = " ".join(
            "AND EXISTS(SELECT 1 FROM revision_references r WHERE "
            + SQLiteRepository.REFERENCE_CORRELATION + " " + condition + ")"
            for condition in conditions
        )
        return clause, parameters

    monkeypatch.setattr(SQLiteRepository, "_reference_filter", staticmethod(reference_filter))


def join_instead_of_existence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real join: the conditions stay in the WHERE, so parameters bind in the same order."""
    def reference_filter(query, repository_id):
        conditions, parameters = SQLiteRepository._reference_conditions(query, repository_id)
        return (" ".join(conditions), parameters) if conditions else ("", [])

    original_connect = SQLiteRepository._connect
    monkeypatch.setattr(SQLiteRepository, "_reference_filter", staticmethod(reference_filter))
    monkeypatch.setattr(SQLiteRepository, "_connect", lambda self: _Joining(original_connect(self)))


class _Joining:
    """Delegates to a real connection, adding the join the bare conditions now depend on.

    ``sqlite3.Connection.execute`` is read-only, so the statement is rewritten here rather
    than patched onto the connection. The conditions stay in the WHERE clause where
    ``_reference_filter`` put them, so every parameter still binds in the order it was built.
    """

    JOIN = " JOIN revision_references r ON " + SQLiteRepository.REFERENCE_CORRELATION
    ANCHOR = " JOIN memories m ON "

    def __init__(self, connection) -> None:
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def execute(self, statement, parameters=()):
        if " r." in statement and self.ANCHOR in statement:
            statement = statement.replace(self.ANCHOR, self.JOIN + self.ANCHOR)
        return self._connection.execute(statement, parameters)


def filter_any_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        SQLiteRepository, "REFERENCE_CORRELATION",
        "r.namespace=h.namespace AND r.actor=h.actor AND r.memory_id=h.memory_id",
    )


def resolve_commits_through_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept a revision spec, and ask the verifier what it means."""
    monkeypatch.setattr(models, "_normalized_reference_commits",
                        lambda values: () if values is None else tuple(values))
    original = MemoryService.search

    def search(self, query):
        commits = query.reference_commits or ()
        if commits and self.verifier is not None:
            resolved = tuple(self.verifier.resolve_commit(commit) or commit for commit in commits)
            query = replace(query, reference_commits=resolved)
        return original(self, query)

    monkeypatch.setattr(MemoryService, "search", search)


# --- what a hit carries ---------------------------------------------------------------------------


def return_only_matching_references(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SQLiteRepository.search

    def search(self, scope, query, repository_id=None):
        page = original(self, scope, query, repository_id)
        paths = set(query.reference_paths or ())
        if not paths:
            return page
        hits = tuple(
            replace(hit, references=tuple(
                reference for reference in hit.references if reference.path in paths
            ))
            for hit in page.hits
        )
        return replace(page, hits=hits)

    monkeypatch.setattr(SQLiteRepository, "search", search)


def references_reason_for_a_non_restricting_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SQLiteRepository, "_restricts_references", staticmethod(lambda query: True))


# --- the binding ---------------------------------------------------------------------------------


def empty_page_when_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MemoryService._filter_repository_id

    def resolve(self, query):
        if query.repository == REPOSITORY_BOUND and self.binding is None:
            return "\x00no-such-repository"
        return original(self, query)

    monkeypatch.setattr(MemoryService, "_filter_repository_id", resolve)


def unbound_when_verification_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    original = MemoryService._filter_repository_id

    def resolve(self, query):
        if query.repository == REPOSITORY_BOUND and self.verifier is None:
            raise RepositoryUnbound("no repository is bound to this server")
        return original(self, query)

    monkeypatch.setattr(MemoryService, "_filter_repository_id", resolve)


def width_check_under_any(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(self, query):
        if self.binding is not None:
            width = models.OBJECT_FORMATS[self.binding.object_format]
            if any(len(commit) != width for commit in query.reference_commits or ()):
                raise models.InvalidReference("reference commit width does not match")
        if query.repository != REPOSITORY_BOUND:
            return None
        if self.binding is None:
            raise RepositoryUnbound("no repository is bound to this server")
        return self.binding.repository_id

    monkeypatch.setattr(MemoryService, "_filter_repository_id", resolve)


def reduce_a_mixed_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the values that do not fit instead of rejecting the request."""
    original = MemoryService.search

    def search(self, query):
        if query.repository == REPOSITORY_BOUND and self.binding is not None and query.reference_commits:
            width = models.OBJECT_FORMATS[self.binding.object_format]
            query = replace(query, reference_commits=tuple(
                commit for commit in query.reference_commits if len(commit) == width
            ))
        return original(self, query)

    monkeypatch.setattr(MemoryService, "search", search)


# --- empty and null --------------------------------------------------------------------------------


def empty_list_means_match_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    original = models._normalized_reference_paths

    def normalize(values):
        if isinstance(values, list) and not values:
            return ("\x00unsatisfiable",)
        return original(values)

    monkeypatch.setattr(models, "_normalized_reference_paths", normalize)


def reject_null_on_a_reference_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    original = models._normalized_reference_paths

    def normalize(values):
        if values is None:
            raise InvalidInput("reference_paths must be a sequence")
        return original(values)

    monkeypatch.setattr(models, "_normalized_reference_paths", normalize)


def reject_null_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    original = models._normalized_repository

    def normalize(value):
        if value is None:
            raise models.InvalidReference('repository must be "any" or "bound"')
        return original(value)

    monkeypatch.setattr(models, "_normalized_repository", normalize)


def accept_null_on_the_existing_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    original = SearchQuery.__post_init__

    def post_init(self):
        for field in ("tags_all", "tags_any", "kinds"):
            if getattr(self, field) is None:
                object.__setattr__(self, field, ())
        original(self)

    monkeypatch.setattr(SearchQuery, "__post_init__", post_init)


MUTATIONS = {
    "filter_after_the_limit": (filter_after_the_limit, "filters_before_limit", DETECTED),
    "omit_filters_from_the_fingerprint": (omit_filters_from_the_fingerprint, "cursor_filters", DETECTED),
    "bare_string_prefix": (bare_string_prefix, "prefix_boundary", DETECTED),
    "like_prefix": (like_prefix, "prefix_boundary", DETECTED),
    "conditions_across_different_references":
        (conditions_across_different_references, "single_reference", DETECTED),
    "join_instead_of_existence": (join_instead_of_existence, "multiplicity", DETECTED),
    "resolve_commits_through_git": (resolve_commits_through_git, "never_spawns_git", DETECTED),
    "return_only_matching_references": (return_only_matching_references, "complete_evidence", DETECTED),
    "empty_page_when_unbound": (empty_page_when_unbound, "never_spawns_git", DETECTED),
    "filter_any_revision": (filter_any_revision, "head_only", DETECTED),
    "unbound_when_verification_is_unavailable":
        (unbound_when_verification_is_unavailable, "binding_without_git", (RepositoryUnbound, *DETECTED)),
    "fingerprint_the_literal_bound": (fingerprint_the_literal_bound, "resolved_repository_cursor", DETECTED),
    "drop_repository_from_the_fingerprint":
        (drop_repository_from_the_fingerprint, "resolved_repository_cursor", DETECTED),
    "fingerprint_the_binding_under_any":
        (fingerprint_the_binding_under_any, "resolved_repository_cursor", DETECTED),
    "check_the_cursor_before_the_repository_argument":
        (check_the_cursor_before_the_repository_argument, "resolved_repository_cursor",
         (CursorExpired, *DETECTED)),
    "width_check_under_any": (width_check_under_any, "mixed_formats", (InvalidReference, *DETECTED)),
    "reduce_a_mixed_list": (reduce_a_mixed_list, "mixed_formats", DETECTED),
    "empty_list_means_match_nothing": (empty_list_means_match_nothing, "empty_is_absent", DETECTED),
    "fingerprint_an_empty_list_apart_from_absence":
        (fingerprint_an_empty_list_apart_from_absence, "empty_is_absent", (CursorExpired, *DETECTED)),
    "references_reason_for_a_non_restricting_argument":
        (references_reason_for_a_non_restricting_argument, "empty_is_absent", DETECTED),
    "reject_null_on_a_reference_filter": (reject_null_on_a_reference_filter, "null_is_absent", (InvalidInput, *DETECTED)),
    "reject_null_repository": (reject_null_repository, "null_is_absent", (InvalidReference, *DETECTED)),
    "accept_null_on_the_existing_filters": (accept_null_on_the_existing_filters, "null_is_absent", DETECTED),
}

# The second scenario a mutation must also fail, where §11 names two.
ALSO = {
    "join_instead_of_existence": "pagination",
    "resolve_commits_through_git": "rejections",
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_fails_its_designated_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    mutate, designated, expected = MUTATIONS[name]
    mutate(monkeypatch)
    directory = tmp_path / "designated"
    directory.mkdir()
    with pytest.raises(expected):
        SCENARIOS[designated](directory)


@pytest.mark.parametrize("name", sorted(ALSO))
def test_mutation_fails_its_second_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    mutate, _, expected = MUTATIONS[name]
    mutate(monkeypatch)
    directory = tmp_path / "second"
    directory.mkdir()
    with pytest.raises(expected):
        SCENARIOS[ALSO[name]](directory)


@pytest.mark.skipif(not os.environ.get("NEXUS_MUTATION_MATRIX"), reason="development run: set NEXUS_MUTATION_MATRIX=1")
def test_mutation_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Every mutation against every scenario, printed as a matrix for investigation."""
    names = sorted(SCENARIOS)
    lines = ["", f"{'mutation':50s} " + " ".join(f"{name[:10]:>10s}" for name in names)]
    for mutation in sorted(MUTATIONS):
        mutate, designated, _ = MUTATIONS[mutation]
        cells = []
        for scenario in names:
            with monkeypatch.context() as context:
                mutate(context)
                directory = tmp_path / mutation / scenario
                directory.mkdir(parents=True)
                try:
                    SCENARIOS[scenario](directory)
                except BaseException:  # noqa: BLE001 - any failure is the signal
                    cells.append("FAIL*" if scenario == designated else "fail")
                else:
                    cells.append("MISS*" if scenario == designated else "pass")
        lines.append(f"{mutation:50s} " + " ".join(f"{cell:>10s}" for cell in cells))
    with capsys.disabled():
        print("\n".join(lines))
