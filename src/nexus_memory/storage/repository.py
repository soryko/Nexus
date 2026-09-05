from __future__ import annotations

from typing import Protocol

from nexus_memory.domain.models import (
    HistoryPage,
    MemoryInput,
    MemoryView,
    Scope,
    SearchPage,
    SearchQuery,
    StoreStatus,
    WriteReceipt,
)


class MemoryRepository(Protocol):
    def record(self, scope: Scope, item: MemoryInput, key: str, digest: str) -> WriteReceipt: ...
    def revise(self, scope: Scope, memory_id: str, expected_revision_id: str, item: MemoryInput, key: str, digest: str) -> WriteReceipt: ...
    def forget(self, scope: Scope, memory_id: str, expected_revision_id: str, key: str, digest: str) -> WriteReceipt: ...
    def get(self, scope: Scope, memory_id: str, revision_id: str | None = None) -> MemoryView: ...
    def search(self, scope: Scope, query: SearchQuery) -> SearchPage: ...
    def history(self, scope: Scope, memory_id: str, limit: int, cursor: str | None = None) -> HistoryPage: ...
    def status(self, scope: Scope) -> StoreStatus: ...
