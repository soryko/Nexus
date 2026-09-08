"""The browse ordering shipped through ``c82939a``, retained outside production.

This is the control arm of the candidate-ordering experiment in
``docs/plans/milestone-b3-candidate-ordering.md`` and the oracle
``test_candidate_ordering.py`` differentially tests the shipped form against. It lives in
the tests for the same reason ``evidence_control.py`` does: with the form in the class
behind a flag, "nothing in a request can reach it" would be a claim about the value of
that flag, and here it is a claim about what is importable. Production has one browse
ordering and no branch.

The form is not wrong -- it returns exactly the rows the shipped one returns, which is
what makes it usable as an oracle. It is *unbounded*: ``rank`` is ``-h.durable_seq``, an
expression ``head_index_recent`` cannot answer, so every eligible row in the scope is
sorted to return one page, and the cursor predicate is a filter applied after arriving
rather than a range the seek can start from.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from nexus_memory.storage import SQLiteRepository


def wrapped_browse_statement(repository: SQLiteRepository, inner: str,
                             after: tuple[float, int] | None) -> tuple[str, list]:
    """The wrapper form, byte-for-byte as it shipped.

    It takes ``repository`` first because it is installed as a method on the class.
    """
    statement = f"SELECT * FROM ({inner})"
    if after is not None:
        statement += " WHERE (rank > ?) OR (rank = ? AND seq > ?)"
        return statement + " ORDER BY rank, seq LIMIT ?", [after[0], after[0], after[1]]
    return statement + " ORDER BY rank, seq LIMIT ?", []


@contextmanager
def installed() -> Iterator[None]:
    """Substitute the control for the shipped ordering, then restore.

    For callers with no ``monkeypatch`` fixture. Tests use ``monkeypatch.setattr`` against
    the same attribute, so a failure mid-test cannot leave the control installed.
    """
    original = SQLiteRepository._browse_statement
    SQLiteRepository._browse_statement = wrapped_browse_statement
    try:
        yield
    finally:
        SQLiteRepository._browse_statement = original


def is_installed() -> bool:
    """Whether the control is the ordering a ``SQLiteRepository`` built now would use."""
    return SQLiteRepository._browse_statement is wrapped_browse_statement
