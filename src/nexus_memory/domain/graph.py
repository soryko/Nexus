"""Space-local, immutable graph commands. No store access or authority lookup here."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, fields
from datetime import datetime
from functools import cache
from types import MappingProxyType, UnionType
from typing import ClassVar, Literal, Protocol, Self, get_args, get_origin, get_type_hints

from .errors import InvalidInput
from .models import validate_reference_path
from .spaces import MemoryRef, OperationRef, SpaceBinding

MAX_SPAN_BYTES = 65536
MAX_BATCH_BYTES = 1 << 20
PREDICATE_VERSION = 1
_ENTITY_PREDICATES = frozenset({'IMPLEMENTS', 'MOTIVATED_BY', 'REGRESSION_FOR', 'DEFINED_IN', 'CALLS', 'IMPORTS', 'DEPENDS_ON'})
_CONTROL_PREDICATES = frozenset({'SUPPORTS', 'DISPUTES', 'SUPERSEDES'})
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidInput('invalid graph input')


def _pairs(items):
    result = dict(items)
    _require(len(result) == len(items))
    return result


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=_pairs)
        except (ValueError, RecursionError) as error:
            raise InvalidInput('invalid graph JSON') from error
    _require(type(value) is dict)
    return value


def _text(value):
    _require(type(value) is str)
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise InvalidInput('invalid graph text') from error
    return value


def _freeze_json(value):
    if isinstance(value, Mapping):
        return MappingProxyType({_text(k): _freeze_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(v) for v in value)
    if type(value) is str:
        return _text(value)
    _require(value is None or type(value) in (bool, int) or type(value) is float and math.isfinite(value))
    return value


def _wire(value):
    if isinstance(value, _Record):
        obj = {f.name: _wire(getattr(value, f.name)) for f in fields(value)}
        if value._tag:
            obj['kind'] = value._tag
        return obj
    if isinstance(value, MemoryRef):
        return json.loads(value.to_json())
    if isinstance(value, Mapping):
        return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_wire(v) for v in value]
    return value


@cache
def _schema(cls):
    # Resolve in module scope: AssertionDraft.object is a slot, not builtins.object.
    hints = get_type_hints(cls, globalns=globals(), localns=globals())
    return {f.name: hints[f.name] for f in fields(cls)}


def _field(annotation, value, *, decoding):
    """Interpret the small closed vocabulary of graph wire field types, without coercion."""
    args = get_args(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        _require(type(value) is str and value in args)
    elif origin is UnionType:
        if type(None) in args:
            return None if value is None else _field(args[0], value, decoding=decoding)
        for cls in args:  # Discriminated endpoint or source union.
            if decoding and type(value) is dict and value.get('kind') == cls._tag:
                return cls._decode(value)
            if not decoding and type(value) is cls:
                return value
        raise InvalidInput('invalid graph variant')
    elif origin is tuple:
        _require(type(value) is list if decoding else type(value) in (list, tuple))
        return tuple(_field(args[0], item, decoding=decoding) for item in value)
    elif origin is Mapping:
        _require(isinstance(value, Mapping))
        if args[1] is object:
            return _freeze_json(value)
        return MappingProxyType({_text(k): _field(args[1], v, decoding=decoding) for k, v in value.items()})
    elif annotation is str:
        _text(value)
        _require(bool(value))
    elif annotation is int:
        _require(type(value) is int and value >= 0)
    elif decoding:
        _require(type(value) is dict)
        return annotation.from_json(value)
    else:
        _require(type(value) is annotation)
    return value


class _Validated:
    __slots__ = ()

    def __post_init__(self):
        for key, annotation in _schema(type(self)).items():
            object.__setattr__(self, key, _field(annotation, getattr(self, key), decoding=False))


class _Record(_Validated):
    __slots__ = ()
    _tag: ClassVar[str | None] = None

    def to_json(self) -> str:
        return json.dumps(_wire(self), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

    @classmethod
    def from_json(cls, value: object) -> Self:
        return cls._decode(_object(value))

    @classmethod
    def _decode(cls, value):
        schema = _schema(cls)
        if cls._tag:
            _require(value.get('kind') == cls._tag)
            value = {k: v for k, v in value.items() if k != 'kind'}
        _require(value.keys() <= schema.keys())
        try:
            return cls(**{k: _field(schema[k], v, decoding=True) for k, v in value.items()})
        except (TypeError, RecursionError) as error:
            raise InvalidInput('invalid graph record') from error


@dataclass(frozen=True, slots=True)
class EntityRef(_Record):
    _tag = 'entity'
    entity_id: str


@dataclass(frozen=True, slots=True)
class LiteralRef(_Record):
    _tag = 'literal'
    datatype: str
    canonical_value: str


@dataclass(frozen=True, slots=True)
class AssertionRevisionRef(_Record):
    _tag = 'assertion_revision'
    assertion_id: str
    revision_id: str


Endpoint = EntityRef | LiteralRef | AssertionRevisionRef


def decode_endpoint(value: object) -> Endpoint:
    return _field(Endpoint, _object(value), decoding=True)


DecodeEndpoint = decode_endpoint


_UTC_TIMESTAMP = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z', re.ASCII)


def _timestamp_key(value: str) -> tuple[datetime, str]:
    match = _UTC_TIMESTAMP.fullmatch(value)
    if match is None:
        raise InvalidInput('invalid UTC timestamp')
    try:
        whole_seconds = datetime.fromisoformat(match[1])
    except ValueError as error:
        raise InvalidInput('invalid UTC timestamp') from error
    # Canonical fraction digits sort exactly, without datetime's microsecond truncation.
    return whole_seconds, (match[2] or '').rstrip('0')


@dataclass(frozen=True, slots=True)
class Validity(_Record):
    kind: Literal['unknown', 'unbounded', 'interval']
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self):
        _Record.__post_init__(self)
        if self.kind != 'interval':
            _require(self.valid_from is None and self.valid_to is None)
            return
        _require(self.valid_from is not None and self.valid_to is not None)
        _require(_timestamp_key(self.valid_from) < _timestamp_key(self.valid_to))

    @classmethod
    def unknown(cls) -> Self:
        return cls('unknown')

    @classmethod
    def unbounded(cls) -> Self:
        return cls('unbounded')


@dataclass(frozen=True, slots=True)
class Span(_Record):
    start: int
    end: int
    sha256: str

    def __post_init__(self):
        _Record.__post_init__(self)
        _require(0 < self.end - self.start <= MAX_SPAN_BYTES and re.fullmatch('[0-9a-f]{64}', self.sha256) is not None)


@dataclass(frozen=True, slots=True)
class MemorySource(_Record):
    _tag = 'memory'
    ref: MemoryRef


@dataclass(frozen=True, slots=True)
class CodeSource(_Record):
    _tag = 'code'
    repository_id: str
    commit_oid: str
    path: str
    blob_oid: str

    def __post_init__(self):
        _Record.__post_init__(self)
        validate_reference_path(self.path)
        _require(bool(_OID.fullmatch(self.commit_oid) and _OID.fullmatch(self.blob_oid)))
        _require(len(self.blob_oid) == len(self.commit_oid))


@dataclass(frozen=True, slots=True)
class EvidenceInput(_Record):
    target: str
    role: Literal['support', 'dispute', 'context']
    source: MemorySource | CodeSource
    span: Span
    temp_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceWithdrawal(_Record):
    evidence_id: str
    expected_head_event_seq: int


@dataclass(frozen=True, slots=True)
class EntityDraft(_Record):
    temp_id: str
    kind: str
    qualified_key: str


@dataclass(frozen=True, slots=True)
class Applicability(_Record):
    kind: Literal['unknown', 'project_wide', 'commit_set'] = 'unknown'
    repository_id: str | None = None
    commits: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self):
        _Record.__post_init__(self)
        _require((self.kind == 'unknown') == (self.repository_id is None))
        _require((self.kind == 'commit_set') == bool(self.commits))
        _require((self.kind != 'unknown') == bool(self.evidence))
        _require(all(_OID.fullmatch(oid) for oid in self.commits))


@dataclass(frozen=True, slots=True)
class _AssertionContent(_Validated):
    subject: Endpoint
    predicate: str
    object: Endpoint
    qualifiers: Mapping[str, object] = field(default_factory=dict)
    origin_kind: Literal['human_asserted', 'parser', 'model_extracted'] = 'model_extracted'
    review_state: Literal['proposed', 'accepted', 'retracted'] = 'proposed'
    producer_version: str | None = None
    validity: Validity = Validity.unknown()
    applicability: Applicability = Applicability()

    def __post_init__(self):
        _Validated.__post_init__(self)
        s, o = type(self.subject), type(self.object)
        # APPLIES_TO is a projection, never a writable relationship.
        _require((self.predicate in _ENTITY_PREDICATES and s is EntityRef and o is EntityRef)
                 or (self.predicate in _CONTROL_PREDICATES and s is o is AssertionRevisionRef)
                 or (self.predicate == 'MENTIONS' and s in (EntityRef, AssertionRevisionRef) and o is EntityRef))


@dataclass(frozen=True, slots=True)
class AssertionDraft(_AssertionContent, _Record):
    support_groups: tuple[tuple[str, ...], ...] = ()
    temp_id: str | None = None

    def __post_init__(self):
        _AssertionContent.__post_init__(self)
        _require(len(self.support_groups) <= 8 and all(0 < len(g) <= 16 for g in self.support_groups))

    @classmethod
    def _decode(cls, value):
        # Trusted channels set provenance/review explicitly, not via model-supplied metadata.
        _require(value.get('origin_kind', 'model_extracted') == 'model_extracted')
        _require(value.get('review_state', 'proposed') == 'proposed')
        return _Record._decode.__func__(cls, value)


@dataclass(frozen=True, slots=True)
class CurationBatch(_Record):
    entities: tuple[EntityDraft, ...] = ()
    assertions: tuple[AssertionDraft, ...] = ()
    evidence: tuple[EvidenceInput, ...] = ()
    withdrawals: tuple[EvidenceWithdrawal, ...] = ()

    def __post_init__(self):
        _Record.__post_init__(self)
        _require(len(self.entities) <= 64 and len(self.assertions) <= 64 and len(self.evidence) <= 128)
        _require(len(self.to_json().encode('utf-8')) <= MAX_BATCH_BYTES)


@dataclass(frozen=True, slots=True)
class SupersessionInput(_Record):
    predecessor: AssertionRevisionRef
    replacement: AssertionDraft
    control: AssertionDraft

    def __post_init__(self):
        _Record.__post_init__(self)
        _require(self.control.predicate == 'SUPERSEDES' and self.control.object == self.predecessor)


@dataclass(frozen=True, slots=True)
class EntityResolutionInput(_Record):
    action: Literal['alias', 'merge', 'split', 'retract']
    expected_generation: int
    memberships: tuple[str, ...]
    target: str

    def __post_init__(self):
        _Record.__post_init__(self)
        _require(bool(self.memberships))


@dataclass(frozen=True, slots=True)
class GraphWriteReceipt(_Record):
    operation_id: str
    id_mappings: Mapping[str, str]
    assertion_revisions: tuple[AssertionRevisionRef, ...]
    graph_event_seq: int
    projection_generation: int


@dataclass(frozen=True, slots=True)
class EligibilityResult(_Record):
    status: Literal['eligible', 'ineligible', 'unknown']
    reasons: tuple[str, ...]
    eligible_support_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphPage(_Record):
    refs: tuple[AssertionRevisionRef, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AssertionRecord(_AssertionContent):
    """Exact stored revision. Support groups are looked up separately by ref."""
    ref: AssertionRevisionRef
    recorded_at: str
    graph_event_seq: int

    def __post_init__(self):
        _AssertionContent.__post_init__(self)
        _timestamp_key(self.recorded_at)


@dataclass(frozen=True, slots=True)
class EvidenceRecord(_Validated):
    """Resolved association and exact source capture; no source re-read needed."""
    evidence_id: str
    target: AssertionRevisionRef
    role: Literal['support', 'dispute', 'context']
    source: MemorySource | CodeSource
    span: Span
    captured_bytes: bytes
    origin_identity: str

    def __post_init__(self):
        _Validated.__post_init__(self)
        _require(len(self.captured_bytes) == self.span.end - self.span.start)
        _require(hashlib.sha256(self.captured_bytes).hexdigest() == self.span.sha256)
        try:
            self.captured_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise InvalidInput('invalid UTF-8 evidence span') from None


class GraphReadSnapshot(Protocol):
    def assertion(self, ref: AssertionRevisionRef) -> AssertionRecord | None: ...
    def support_groups(self, ref: AssertionRevisionRef) -> tuple[tuple[str, ...], ...]: ...
    def evidence(self, ref: AssertionRevisionRef) -> tuple[EvidenceRecord, ...]: ...
    def adjacent(self, endpoint: Endpoint, steps: tuple[str, ...], after: str | None, limit: int) -> GraphPage: ...
    def control_relations(self, ref: AssertionRevisionRef, after: str | None, limit: int) -> GraphPage: ...


class GraphRepository(Protocol):
    def open_snapshot(self, binding: SpaceBinding) -> AbstractContextManager[GraphReadSnapshot]: ...


class GraphService(Protocol):
    def __init__(self, repository: GraphRepository, binding: SpaceBinding): ...
    def curate(self, batch: CurationBatch, *, operation: OperationRef) -> GraphWriteReceipt: ...
    def correct(self, assertion_id: str, expected_revision_id: str, replacement: AssertionDraft,
                *, operation: OperationRef) -> GraphWriteReceipt: ...
    def supersede(self, change: SupersessionInput, *, operation: OperationRef) -> GraphWriteReceipt: ...
    def resolve(self, change: EntityResolutionInput, *, operation: OperationRef) -> GraphWriteReceipt: ...
