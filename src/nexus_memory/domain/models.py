from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import InvalidInput

KINDS = frozenset({"observation", "decision", "constraint", "procedure", "failure"})
TAG = re.compile(r"[a-z0-9][a-z0-9._/-]{0,63}\Z")


def _valid_utf8(value: object, field: str, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise InvalidInput(f"{field} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidInput(f"{field} must be valid UTF-8") from error
    if maximum is not None and len(value) > maximum:
        raise InvalidInput(f"{field} is too long")
    return value


@dataclass(frozen=True, slots=True)
class Scope:
    namespace: str
    actor: str = "local"

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not isinstance(self.actor, str) or not self.namespace or not self.actor:
            raise InvalidInput("scope components must be nonempty")
        _valid_utf8(self.namespace, "namespace")
        _valid_utf8(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class MemoryInput:
    content: str
    kind: str = "observation"
    tags: tuple[str, ...] = ()
    source_uri: str | None = None
    snapshot: str | None = None

    def __post_init__(self) -> None:
        try:
            content_size = len(self.content.encode("utf-8")) if isinstance(self.content, str) else 0
        except UnicodeEncodeError as error:
            raise InvalidInput("content must be valid UTF-8") from error
        if not isinstance(self.content, str) or not self.content or content_size > 65536:
            raise InvalidInput("content must be nonempty and at most 65536 UTF-8 bytes")
        if self.kind not in KINDS:
            raise InvalidInput("unsupported memory kind")
        try:
            normalized = tuple(sorted({tag.strip().lower() for tag in self.tags}))
        except (TypeError, AttributeError) as error:
            raise InvalidInput("tags must be strings") from error
        if len(normalized) > 16 or any(not TAG.fullmatch(tag) for tag in normalized):
            raise InvalidInput("invalid tags")
        if self.source_uri is not None:
            _valid_utf8(self.source_uri, "source_uri", 2048)
        if self.snapshot is not None:
            _valid_utf8(self.snapshot, "snapshot", 256)
        object.__setattr__(self, "tags", normalized)


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    memory_id: str
    revision_id: str
    operation_id: str
    durable_seq: int
    operation: str


@dataclass(frozen=True, slots=True)
class MemoryView:
    memory_id: str
    revision_id: str
    parent_revision_id: str | None
    content: str
    kind: str
    tags: tuple[str, ...]
    source_uri: str | None
    snapshot: str | None
    created_at: str
    current_revision_id: str


@dataclass(frozen=True, slots=True)
class StoreStatus:
    durable_storage: str
    exact_retrieval: str
    derived_indexes: str
    schema_version: int
    sqlite_version: str
    active_memories: int
    forgotten_memories: int
    revisions: int
    pending_outbox: int
    latest_durable_seq: int
