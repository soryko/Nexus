"""The evidence loader shipped through ``cdf51e7``, retained outside production.

This is the control arm of the evidence-loading experiment in
``docs/plans/milestone-b2-evidence-loading.md`` and the oracle
``test_evidence_loading.py`` differentially tests the shipped form against. It lives here
rather than in ``SQLiteRepository`` because the only callers that may reach it are tests
and the measurement harness: with the form in the class behind a constant, "nothing in a
request can reach it" was a claim about the value of that constant. Here it is a claim
about what is importable, and production has one evidence loader with no branch.

The tests are what keep this honest, which is why the file is theirs. A control nobody
executes is not a control, and
``test_every_b2b_scenario_holds_under_the_unbounded_control`` runs all nineteen B2b
acceptance scenarios through it.

``tools/measure_evidence_loading.py`` imports the same module rather than restating the
statement: the arm labelled "current" must be the form that shipped, not a copy of it that
drifted.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from nexus_memory.domain.models import Scope
from nexus_memory.storage import SQLiteRepository


def unbounded_evidence_sql(repository: SQLiteRepository, scope: Scope,
                           pairs: list[tuple[str, str]]) -> tuple[str, list]:
    """The OR-list form, byte-for-byte as it shipped.

    Its cost is a function of the scope, not of the page: with no statistics the planner
    serves the OR-list by scanning the whole ``(namespace, actor)`` partition rather than
    by point lookups, and ``ANALYZE`` is what changes its mind. That dependence on
    statistics is the reason this is no longer the shipped path.

    It takes ``repository`` first because it is installed as a method on the class, which
    is how a scenario that builds its own ``SQLiteRepository`` deep inside the acceptance
    suite ends up running the control without knowing it exists.
    """
    clauses = " OR ".join("(memory_id=? AND revision_id=?)" for _ in pairs)
    parameters: list = [scope.namespace, scope.actor]
    for memory_id, revision_id in pairs:
        parameters.extend([memory_id, revision_id])
    return (
        f"SELECT memory_id,revision_id,{repository.REFERENCE_COLUMNS}"
        f" FROM revision_references WHERE namespace=? AND actor=? AND ({clauses})"
        " ORDER BY memory_id,revision_id,commit_oid,path",
        parameters,
    )


@contextmanager
def installed() -> Iterator[None]:
    """Substitute the control for the shipped loader on ``SQLiteRepository``, then restore.

    For callers with no ``monkeypatch`` fixture -- the measurement harness. Tests use
    ``monkeypatch.setattr`` against the same attribute, so that a failure mid-test cannot
    leave the control installed for the next one.
    """
    original = SQLiteRepository._evidence_sql
    SQLiteRepository._evidence_sql = unbounded_evidence_sql
    try:
        yield
    finally:
        SQLiteRepository._evidence_sql = original


def is_installed() -> bool:
    """Whether the control is the loader a ``SQLiteRepository`` built now would use."""
    return SQLiteRepository._evidence_sql is unbounded_evidence_sql
