from __future__ import annotations

import hashlib
import json

from nexus_memory.domain.errors import InvalidInput
from nexus_memory.domain.models import MemoryInput, MemoryView, Scope, StoreStatus, WriteReceipt
from nexus_memory.storage.repository import MemoryRepository


class MemoryService:
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

    def status(self) -> StoreStatus:
        return self.repository.status(self.scope)
