from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import InvalidInput, InvalidReference

KINDS = frozenset({"observation", "decision", "constraint", "procedure", "failure"})

# Version of the canonical request-digest format. A request carrying only milestone A's
# fields must keep producing a version-1 digest, so old idempotency keys still replay.
DIGEST_VERSION = 1
# Version of the digest used by a request that carries references. A different function
# computes it; the frozen version-1 function is never edited.
REFERENCE_DIGEST_VERSION = 2
TAG = re.compile(r"[a-z0-9][a-z0-9._/-]{0,63}\Z")

# Object formats git can report, with the OID width each one produces. Recorded at bind,
# never assumed: a fixed 40-hex rule would reject every SHA-256 repository.
OBJECT_FORMATS = {"sha1": 40, "sha256": 64}
MAX_REFERENCES = 8
HEX = re.compile(r"[0-9a-f]+\Z")
# What a verified reference establishes, and what it does not. Fixed for B2a.
EVIDENCE = ("commit_resolved", "path_resolved", "content_unverified")
SUPPORTED_MODES = frozenset({"100644", "100755"})


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


def validate_reference_path(path: object) -> str:
    """Repository-relative path rules, applied before git ever sees the value.

    Git's own rejection messages disclose the checkout location and on-disk existence,
    so every unsafe shape is refused here with a message that names nothing.
    """
    if not isinstance(path, str) or not path:
        raise InvalidReference("reference path must be a nonempty string")
    try:
        encoded = path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidReference("reference path must be valid UTF-8") from error
    if len(encoded) > 1024:
        raise InvalidReference("reference path is too long")
    if "\x00" in path:
        raise InvalidReference("reference path must not contain NUL")
    if path.startswith("/") or path.endswith("/"):
        raise InvalidReference("reference path must be repository-relative, without a leading or trailing slash")
    segments = path.split("/")
    if any(segment == "" for segment in segments):
        raise InvalidReference("reference path must not contain an empty segment")
    if any(segment in (".", "..") for segment in segments):
        raise InvalidReference("reference path must not contain . or .. segments")
    return path


def validate_commit_spec(spec: object) -> str:
    """A commit spec is an OID or a ref name; anything else is refused before git runs."""
    if not isinstance(spec, str) or not spec:
        raise InvalidReference("commit must be a nonempty string")
    try:
        encoded = spec.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidReference("commit must be valid UTF-8") from error
    if len(encoded) > 256:
        raise InvalidReference("commit spec is too long")
    if spec.startswith("-") or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in spec):
        raise InvalidReference("commit spec contains characters that are not part of an OID or ref name")
    return spec


@dataclass(frozen=True, slots=True)
class ReferenceInput:
    """Caller-supplied reference: a repository-relative path at an optional commit spec.

    An absent commit means the bound repository's ``HEAD`` at verification time. The value
    is caller input and is what the request digest covers; resolved OIDs never enter it.
    """

    path: str
    commit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", validate_reference_path(self.path))
        if self.commit is not None:
            object.__setattr__(self, "commit", validate_commit_spec(self.commit))

    @property
    def effective_spec(self) -> str:
        return self.commit if self.commit is not None else "HEAD"


def _normalized_references(values: object) -> tuple[ReferenceInput, ...]:
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise InvalidReference("references must be a sequence") from error
    if any(not isinstance(item, ReferenceInput) for item in items):
        raise InvalidReference("references must be reference inputs")
    normalized = tuple(sorted(set(items), key=lambda item: (item.commit is not None, item.commit or "", item.path)))
    if len(normalized) > MAX_REFERENCES:
        raise InvalidReference(f"at most {MAX_REFERENCES} references per revision")
    return normalized


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """One record of ``ls-tree -z`` output, exactly as returned and not yet accepted."""

    mode: str
    entry_type: str
    object_oid: str
    path: str


@dataclass(frozen=True, slots=True)
class VerifiedReference:
    """Evidence about one path in one commit, recorded at the moment it was checked.

    It says the commit object existed in the bound repository, and that the path existed
    in that commit's tree with that object OID and entry type. It says nothing about the
    working tree, about currency, or about whether the memory's prose is true.
    """

    repository_id: str
    commit_oid: str
    path: str
    object_oid: str
    entry_type: str
    mode: str
    checked_at: str
    evidence: tuple[str, ...] = EVIDENCE


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    """The persisted identity a server process is bound to. Never selectable by a tool call."""

    repository_id: str
    object_format: str
    locator: str | None


@dataclass(frozen=True, slots=True)
class MemoryInput:
    content: str
    kind: str = "observation"
    tags: tuple[str, ...] = ()
    source_uri: str | None = None
    snapshot: str | None = None
    references: tuple[ReferenceInput, ...] = ()

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
        object.__setattr__(self, "references", _normalized_references(self.references))


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
    references: tuple[VerifiedReference, ...] = ()


@dataclass(frozen=True, slots=True)
class StoreStatus:
    durable_storage: str
    exact_retrieval: str
    lexical_index: str
    derived_indexes: str
    schema_version: int
    sqlite_version: str
    active_memories: int
    forgotten_memories: int
    revisions: int
    pending_outbox: int
    latest_durable_seq: int
    repository_id: str | None = None
    verification: str = "unavailable"


def _normalized_tags(values: object, field: str) -> tuple[str, ...]:
    try:
        normalized = tuple(sorted({tag.strip().lower() for tag in values}))  # type: ignore[union-attr]
    except (TypeError, AttributeError) as error:
        raise InvalidInput(f"{field} must be strings") from error
    if len(normalized) > 16 or any(not TAG.fullmatch(tag) for tag in normalized):
        raise InvalidInput(f"invalid {field}")
    return normalized


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str | None = None
    advanced: bool = False
    tags_all: tuple[str, ...] = ()
    tags_any: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    limit: int = 20
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None:
            _valid_utf8(self.query, "query", 1024)
        if not isinstance(self.advanced, bool):
            raise InvalidInput("advanced must be a boolean")
        object.__setattr__(self, "tags_all", _normalized_tags(self.tags_all, "tags_all"))
        object.__setattr__(self, "tags_any", _normalized_tags(self.tags_any, "tags_any"))
        try:
            kinds = tuple(sorted(set(self.kinds)))
        except TypeError as error:
            raise InvalidInput("kinds must be strings") from error
        if any(kind not in KINDS for kind in kinds):
            raise InvalidInput("unsupported memory kind")
        object.__setattr__(self, "kinds", kinds)
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise InvalidInput("limit must be between 1 and 100")
        if self.cursor is not None:
            _valid_utf8(self.cursor, "cursor", 4096)


@dataclass(frozen=True, slots=True)
class SearchHit:
    memory_id: str
    revision_id: str
    kind: str
    tags: tuple[str, ...]
    created_at: str
    excerpt: str
    lexical_rank: float | None
    match_reasons: tuple[str, ...]
    has_earlier_revisions: bool
    references: tuple[VerifiedReference, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    cursor: str | None
    generation: int


@dataclass(frozen=True, slots=True)
class RevisionHit:
    """A match on a superseded revision, carried with the head it belongs to.

    Provenance is not decoration here: a match on superseded text presented as a current
    answer is worse than no match at all, so the revision that matched and the live head
    it resolves to travel together and neither is optional.
    """

    memory_id: str
    revision_id: str
    current_revision_id: str
    kind: str
    tags: tuple[str, ...]
    created_at: str
    excerpt: str
    lexical_rank: float | None


@dataclass(frozen=True, slots=True)
class RevisionEntry:
    revision_id: str
    parent_revision_id: str | None
    kind: str
    created_at: str
    is_current: bool


@dataclass(frozen=True, slots=True)
class HistoryPage:
    memory_id: str
    entries: tuple[RevisionEntry, ...]
    cursor: str | None
