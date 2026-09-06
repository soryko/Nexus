from __future__ import annotations

from typing import Protocol

from nexus_memory.domain.models import TreeEntry


class RepositoryVerifier(Protocol):
    """Resolve a commit spec once; read tree entries for a path against the resolved commit.

    A separate interface from storage because it depends on an external process and must
    fail independently of it: a verifier that cannot run refuses reference-carrying writes
    and nothing else.
    """

    def resolve_commit(self, spec: str) -> str | None: ...
    def tree_entries(self, commit_oid: str, path: str) -> tuple[TreeEntry, ...]: ...
