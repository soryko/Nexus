"""A scripted verifier for tests that need control over resolution, not a real repository."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from nexus_memory.domain.models import HEX, RepositoryBinding, Scope, TreeEntry

BLOB = "78981922613b2afb6025042ff6bd878ac1994e85"
OID1 = "1" * 40
OID2 = "2" * 40
OID3 = "3" * 40


def entry(path: str, oid: str = BLOB, mode: str = "100644", entry_type: str = "blob") -> TreeEntry:
    return TreeEntry(mode, entry_type, oid, path)


class FakeVerifier:
    """Resolves specs from a table and answers tree lookups from another.

    ``resolutions`` and ``checks`` record every call, so resolution counts are asserted
    rather than inferred. ``pause`` blocks inside a tree lookup until released, which is
    how the lock boundary is tested directly.
    """

    def __init__(self, commits: dict[str, str] | None = None,
                 trees: dict[tuple[str, str], tuple[TreeEntry, ...]] | None = None) -> None:
        self.commits = dict(commits or {})
        self.trees = dict(trees or {})
        self.resolutions: list[str] = []
        self.checks: list[tuple[str, str]] = []
        self.pause: threading.Event | None = None
        self.paused = threading.Event()
        self.barrier: threading.Barrier | None = None
        self.missing: set[str] = set()

    def resolve_commit(self, spec: str) -> str | None:
        self.resolutions.append(spec)
        if spec in self.commits:
            return self.commits[spec]
        if HEX.fullmatch(spec) and len(spec) == 40 and spec not in self.missing:
            return spec  # an object does not disappear because a branch moved off it
        return None

    def tree_entries(self, commit_oid: str, path: str) -> tuple[TreeEntry, ...]:
        self.checks.append((commit_oid, path))
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        if self.pause is not None:
            self.paused.set()
            self.pause.wait(timeout=10)
        return self.trees.get((commit_oid, path), ())


class RaisingVerifier:
    """A verifier that must never be reached: any call is a test failure."""

    def resolve_commit(self, spec: str) -> str | None:
        raise AssertionError(f"verifier was invoked for {spec!r}")

    def tree_entries(self, commit_oid: str, path: str) -> tuple[TreeEntry, ...]:
        raise AssertionError(f"verifier was invoked for {path!r}")


def binding(repository_id: str = "repo-a", object_format: str = "sha1") -> RepositoryBinding:
    return RepositoryBinding(repository_id, object_format, None)


def register(db: Path, scope: Scope, *repository_ids: str, object_format: str = "sha1") -> None:
    """Give a scripted binding the repository row a real bind_checkout would have created.

    Evidence rows reference the repositories table by foreign key, so an identity that
    was never registered in this scope cannot label anything. That is the constraint the
    tests want kept, which is why they satisfy it here instead of relaxing it.
    """
    connection = sqlite3.connect(db)
    try:
        for repository_id in repository_ids:
            connection.execute(
                "INSERT OR IGNORE INTO repositories(namespace,actor,repository_id,object_format,locator,registered_at)"
                " VALUES(?,?,?,?,NULL,'2026-09-06T00:00:00+00:00')",
                (scope.namespace, scope.actor, repository_id, object_format),
            )
        connection.commit()
    finally:
        connection.close()
