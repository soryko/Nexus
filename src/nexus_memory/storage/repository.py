from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from nexus_memory.domain.models import (
    DIGEST_VERSION,
    HistoryPage,
    RevisionHit,
    MemoryInput,
    MemoryView,
    RepositoryBinding,
    Scope,
    SearchPage,
    SearchQuery,
    StoreStatus,
    VerifiedReference,
    WriteReceipt,
)


from nexus_memory.domain.spaces import MemoryRef, OperationRef


EXACT_COMMAND_VERSION = 1


@dataclass(frozen=True, slots=True)
class WriteContext:
    space_id: str
    operation: OperationRef
    principal_id: str
    agent_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    source_event_id: str | None = None
    compat_metadata: dict | None = None
    publication_sources: tuple[MemoryRef, ...] | None = None
    visibility_role: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedMutation:
    scope: Scope
    operation: str
    target: str | None
    expected: str | None
    item: MemoryInput | None
    key: str
    digest: str
    digest_version: int
    references: tuple[VerifiedReference, ...] = ()
    context: WriteContext | None = None
    command_digest: str | None = None
    replay: WriteReceipt | None = None
    compat_json: str | None = None
    sources_json: str | None = None


@dataclass(slots=True)
class WriteObservation:
    """Request-local receipt disposition, separate from the immutable public receipt."""

    replayed: bool | None = None


# A worker thread inherits the request context, including this observation object.
write_observation: ContextVar[WriteObservation | None] = ContextVar("write_observation", default=None)


@contextmanager
def observe_write() -> Iterator[WriteObservation]:
    observation = WriteObservation()
    token = write_observation.set(observation)
    try:
        yield observation
    finally:
        write_observation.reset(token)


class MemoryRepository(Protocol):
    def commit_prepared(self, command: PreparedMutation) -> WriteReceipt: ...
    def receipt_for_operation(self, scope: Scope, operation: OperationRef, command_digest: str) -> WriteReceipt | None: ...
    def record(self, scope: Scope, item: MemoryInput, key: str, digest: str,
               references: tuple[VerifiedReference, ...] = (), digest_version: int = DIGEST_VERSION) -> WriteReceipt: ...
    def revise(self, scope: Scope, memory_id: str, expected_revision_id: str, item: MemoryInput, key: str, digest: str,
               references: tuple[VerifiedReference, ...] = (), digest_version: int = DIGEST_VERSION) -> WriteReceipt: ...
    def forget(self, scope: Scope, memory_id: str, expected_revision_id: str, key: str, digest: str) -> WriteReceipt: ...
    def receipt(self, scope: Scope, key: str, digest: str) -> WriteReceipt | None: ...
    def get(self, scope: Scope, memory_id: str, revision_id: str | None = None) -> MemoryView: ...
    def search(self, scope: Scope, query: SearchQuery, repository_id: str | None = None) -> SearchPage: ...
    def search_history(self, scope: Scope, query: SearchQuery) -> tuple[RevisionHit, ...]: ...
    def history(self, scope: Scope, memory_id: str, limit: int, cursor: str | None = None) -> HistoryPage: ...
    def status(self, scope: Scope) -> StoreStatus: ...
    def bind_checkout(self, scope: Scope, token: str, locator: str | None, object_format: str,
                      repository_id: str | None = None) -> RepositoryBinding: ...
    def checkout_binding(self, scope: Scope, token: str) -> RepositoryBinding | None: ...
