from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

from nexus_memory.domain.errors import (
    CommitNotFound,
    InvalidInput,
    InvalidReference,
    PathNotInCommit,
    RepositoryMismatch,
    UnsupportedReferenceType,
    VerificationUnavailable,
)
from nexus_memory.domain.models import (
    DIGEST_VERSION,
    HEX,
    OBJECT_FORMATS,
    REFERENCE_DIGEST_VERSION,
    SUPPORTED_MODES,
    HistoryPage,
    RevisionHit,
    MemoryInput,
    MemoryView,
    ReferenceInput,
    RepositoryBinding,
    Scope,
    SearchPage,
    SearchQuery,
    StoreStatus,
    TreeEntry,
    VerifiedReference,
    WriteReceipt,
)
from nexus_memory.storage.repository import MemoryRepository

from .verifier import RepositoryVerifier


class MemoryService:
    DIGEST_VERSION = DIGEST_VERSION
    REFERENCE_DIGEST_VERSION = REFERENCE_DIGEST_VERSION

    def __init__(self, repository: MemoryRepository, scope: Scope, binding: RepositoryBinding | None = None,
                 verifier: RepositoryVerifier | None = None) -> None:
        self.repository = repository
        self.scope = scope
        self.binding = binding
        self.verifier = verifier

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

    @staticmethod
    def _digest_v2(operation: str, target: str | None, expected: str | None, item: MemoryInput) -> str:
        """Version 2: the version-1 fields plus the caller's reference input, never resolved OIDs.

        Hashing a resolved commit would make a retry of ``{path, commit: "HEAD"}`` after
        ``HEAD`` moved hash differently, and raise a conflict instead of replaying.
        """
        value = {
            "version": REFERENCE_DIGEST_VERSION, "operation": operation, "target": target,
            "expected_revision": expected,
            "input": {
                "content": item.content, "kind": item.kind, "tags": item.tags,
                "source_uri": item.source_uri, "snapshot": item.snapshot,
                "references": [{"commit": reference.commit, "path": reference.path} for reference in item.references],
            },
        }
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _request_digest(self, operation: str, target: str | None, expected: str | None, item: MemoryInput) -> tuple[str, int]:
        if item.references:
            return self._digest_v2(operation, target, expected, item), REFERENCE_DIGEST_VERSION
        return self._digest(operation, target, expected, item), DIGEST_VERSION

    @staticmethod
    def _accept(path: str, entries: tuple[TreeEntry, ...]) -> TreeEntry:
        """Tree-entry acceptance: one record, byte-equal pathname, blob, supported mode.

        Non-empty output proves only that *something* matched a pathspec. Absence is
        empty output, so it is read here and never from an exit status.
        """
        if len(entries) != 1:
            raise PathNotInCommit("path is not in that commit")
        entry = entries[0]
        if entry.path != path:
            raise PathNotInCommit("path is not in that commit")
        if entry.entry_type != "blob" or entry.mode not in SUPPORTED_MODES:
            raise UnsupportedReferenceType("reference is not a regular tracked file")
        return entry

    def _verify(self, references: tuple[ReferenceInput, ...]) -> tuple[VerifiedReference, ...]:
        """Resolve each distinct effective spec exactly once, then check every path against it.

        Runs before the write transaction opens: no subprocess is ever spawned while the
        write lock is held. When the result is not the one that commits, it is discarded.
        """
        if self.binding is None or self.verifier is None:
            raise VerificationUnavailable("reference verification is unavailable in this mode")
        width = OBJECT_FORMATS[self.binding.object_format]
        resolved: dict[str, str] = {}
        for reference in references:
            spec = reference.effective_spec
            if spec in resolved:
                continue
            if HEX.fullmatch(spec) and len(spec) in OBJECT_FORMATS.values() and len(spec) != width:
                raise InvalidReference("commit OID width does not match the repository object format")
            oid = self.verifier.resolve_commit(spec)
            if oid is None:
                raise CommitNotFound("commit not found in the bound repository")
            if not HEX.fullmatch(oid) or len(oid) != width:
                raise RepositoryMismatch("repository object format differs from the registered one")
            resolved[spec] = oid
        checked_at = datetime.now(UTC).isoformat()
        verified: dict[tuple[str, str], VerifiedReference] = {}
        for reference in references:
            commit_oid = resolved[reference.effective_spec]
            if (commit_oid, reference.path) in verified:
                continue  # two specs resolved to one commit: one observation, one row
            entry = self._accept(reference.path, self.verifier.tree_entries(commit_oid, reference.path))
            verified[(commit_oid, reference.path)] = VerifiedReference(
                self.binding.repository_id, commit_oid, reference.path, entry.object_oid,
                entry.entry_type, entry.mode, checked_at,
            )
        return tuple(sorted(verified.values(), key=lambda item: (item.commit_oid, item.path)))

    def _prepare(self, operation: str, target: str | None, expected: str | None, item: MemoryInput,
                 key: str) -> tuple[str, int, tuple[VerifiedReference, ...], WriteReceipt | None]:
        digest, version = self._request_digest(operation, target, expected, item)
        if not item.references:
            return digest, version, (), None
        # A retry never invokes git: an already-committed receipt is returned before verification.
        if receipt := self.repository.receipt(self.scope, key, digest):
            return digest, version, (), receipt
        return digest, version, self._verify(item.references), None

    def record(self, input: MemoryInput, idempotency_key: str) -> WriteReceipt:
        key = self._key(idempotency_key)
        digest, version, verified, replay = self._prepare("record", None, None, input, key)
        if replay is not None:
            return replay
        return self.repository.record(self.scope, input, key, digest, verified, version)

    def revise(self, memory_id: str, expected_revision_id: str, input: MemoryInput, idempotency_key: str) -> WriteReceipt:
        key = self._key(idempotency_key)
        memory_id = self._identifier(memory_id, "memory_id")
        expected_revision_id = self._identifier(expected_revision_id, "expected_revision_id")
        digest, version, verified, replay = self._prepare("revise", memory_id, expected_revision_id, input, key)
        if replay is not None:
            return replay
        return self.repository.revise(self.scope, memory_id, expected_revision_id, input, key, digest, verified, version)

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
        stored = self.repository.status(self.scope)
        return replace(
            stored,
            repository_id=self.binding.repository_id if self.binding is not None else None,
            verification="available" if self.binding is not None and self.verifier is not None else "unavailable",
        )
