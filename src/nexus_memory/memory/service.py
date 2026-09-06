from __future__ import annotations

import hashlib
import json

from nexus_memory.domain.errors import InvalidInput
from nexus_memory.domain.models import (
    DIGEST_VERSION,
    HistoryPage,
    RevisionHit,
    MemoryInput,
    MemoryView,
    Scope,
    SearchPage,
    SearchQuery,
    StoreStatus,
    WriteReceipt,
)
from nexus_memory.storage.repository import MemoryRepository


class MemoryService:
    DIGEST_VERSION = DIGEST_VERSION

    def __init__(self, repository: MemoryRepository, scope: Scope) -> None:
        self.repository = repository
        self.scope = scope

    @staticmethod
    def _key(key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 128:
            raise InvalidInput("idempotency key must be nonempty and at most 128 characters")
        try:
            key.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidInput("idempotency key must be valid UTF-8") from error
        return key

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise InvalidInput(f"{name} must be a nonempty string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise InvalidInput(f"{name} must be valid UTF-8") from error
        return value

    @staticmethod
    def _digest(operation: str, target: str | None, expected: str | None, item: MemoryInput | None) -> str:
        """Version 1 of the canonical request digest.

        This function is frozen. A request carrying only milestone A's fields must keep
        hashing to the same value forever, or an idempotency key recorded before a
        migration would stop replaying its original receipt. A later version adds fields
        under a new function and a new DIGEST_VERSION, never by editing this one.
        """
        value = {"operation": operation, "target": target, "expected_revision": expected, "input": None}
        if item is not None:
            value["input"] = {"content": item.content, "kind": item.kind, "tags": item.tags, "source_uri": item.source_uri, "snapshot": item.snapshot}
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def record(self, input: MemoryInput, idempotency_key: str) -> WriteReceipt:
        key = self._key(idempotency_key)
        return self.repository.record(self.scope, input, key, self._digest("record", None, None, input))

    def revise(self, memory_id: str, expected_revision_id: str, input: MemoryInput, idempotency_key: str) -> WriteReceipt:
        key = self._key(idempotency_key)
        memory_id = self._identifier(memory_id, "memory_id")
        expected_revision_id = self._identifier(expected_revision_id, "expected_revision_id")
        return self.repository.revise(self.scope, memory_id, expected_revision_id, input, key, self._digest("revise", memory_id, expected_revision_id, input))

    def forget(self, memory_id: str, expected_revision_id: str, idempotency_key: str) -> WriteReceipt:
        key = self._key(idempotency_key)
        memory_id = self._identifier(memory_id, "memory_id")
        expected_revision_id = self._identifier(expected_revision_id, "expected_revision_id")
        return self.repository.forget(self.scope, memory_id, expected_revision_id, key, self._digest("forget", memory_id, expected_revision_id, None))

    def get(self, memory_id: str, revision_id: str | None = None) -> MemoryView:
        memory_id = self._identifier(memory_id, "memory_id")
        if revision_id is not None:
            revision_id = self._identifier(revision_id, "revision_id")
        return self.repository.get(self.scope, memory_id, revision_id)

    def search(self, query: SearchQuery) -> SearchPage:
        if not isinstance(query, SearchQuery):
            raise InvalidInput("query must be a SearchQuery")
        return self.repository.search(self.scope, query)

    def search_history(self, query: SearchQuery) -> tuple[RevisionHit, ...]:
        """Candidate generation over non-head revisions.

        Returns nothing unless the repository was opened with a history index profile.
        Every hit carries the revision that matched and the live head it resolves to;
        ineligible memories are filtered inside the query, not afterwards, so a filtered
        row never occupies one of the caller's hit slots.
        """
        if not isinstance(query, SearchQuery):
            raise InvalidInput("query must be a SearchQuery")
        return self.repository.search_history(self.scope, query)

    def history(self, memory_id: str, limit: int = 20, cursor: str | None = None) -> HistoryPage:
        memory_id = self._identifier(memory_id, "memory_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise InvalidInput("limit must be between 1 and 100")
        if cursor is not None:
            cursor = self._identifier(cursor, "cursor")
        return self.repository.history(self.scope, memory_id, limit, cursor)

    def status(self) -> StoreStatus:
        return self.repository.status(self.scope)
