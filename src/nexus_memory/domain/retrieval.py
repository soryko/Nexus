"""Immutable retrieval contracts. A decoded receipt is data, never query authority."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from fractions import Fraction
from functools import cache
from types import MappingProxyType, UnionType
from typing import Literal, Protocol, Self, get_args, get_origin, get_type_hints

from .errors import InvalidGraphPattern, InvalidInput, InvalidRetrievalBudget

from .graph import (Applicability, AssertionRevisionRef, CodeSource, Endpoint, EntityRef,
                    GraphReadSnapshot, GraphRepository, LiteralRef, MemorySource, Span, Validity)
from .models import Scope
from .spaces import SpaceBinding

HISTORICAL_INDEX_UNAVAILABLE = 'historical_index_unavailable'
_UTC_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z')


def _validate_timestamp(value: str) -> None:
    if not _UTC_TIMESTAMP.fullmatch(value):
        raise InvalidInput('expected RFC3339 UTC timestamp')
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise InvalidInput('invalid UTC timestamp') from None


_GRAPH_RECORDS = (Applicability, CodeSource, MemorySource, Span, Validity, EntityRef, LiteralRef, AssertionRevisionRef)

BUDGET_LIMITS = MappingProxyType({
    'seeds': (8, 32), 'semantic_depth': (2, 5), 'beam_width': (16, 64),
    'alternative_states': (3, 8), 'edges_per_endpoint': (16, 64),
    'expansion_rows': (200, 2000), 'proof_premise_rows': (256, 2048),
    'conflict_rows': (128, 1024), 'candidate_pool': (32, 128),
    'finalist_bundles': (8, 32), 'query_deadline_ms': (250, 2000),
    'execution_deadline_ms': (500, 4000), 'token_target': (1600, 8192),
    'serialized_bytes': (6400, 32768),
})
ANCHOR_DEFAULTS = MappingProxyType({k: BUDGET_LIMITS[k][0] for k in ('seeds', 'candidate_pool')})


def _invalid():
    raise InvalidInput('invalid retrieval record')


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            _invalid()
        obj[key] = value
    return obj


@cache
def _schema(cls):
    hints = get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


def _typed(annotation, value, *, wire):
    """Small closed wire grammar: no coercion, object hooks or arbitrary mappings."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        if not any(type(value) is type(v) and value == v for v in args):
            _invalid()
    elif origin is UnionType:
        if value is None and type(None) in args:
            return None
        for member in args:
            try:
                return _typed(member, value, wire=wire)
            except InvalidInput:
                continue
        _invalid()
    elif origin is tuple:
        if type(value) is not (list if wire else tuple):
            _invalid()
        return tuple(_typed(args[0], item, wire=wire) for item in value)
    elif wire and issubclass(annotation, _Record):
        return annotation._decode(value)
    elif wire and annotation in _GRAPH_RECORDS:
        if type(value) is not dict:
            _invalid()
        return annotation.from_json(value)
    elif type(value) is not annotation:
        _invalid()
    elif annotation is str:
        try:
            value.encode('utf-8')
        except UnicodeError:
            _invalid()
    elif annotation is int and value < 0:
        _invalid()
    return value


def _wire(value):
    if isinstance(value, _Record):
        return {key: _wire(getattr(value, key)) for key in _schema(type(value))}
    if isinstance(value, _GRAPH_RECORDS):
        return json.loads(value.to_json())
    if type(value) is tuple:
        return [_wire(item) for item in value]
    return value


class _Record:
    def __post_init__(self):
        for key, kind in _schema(type(self)).items():
            _typed(kind, getattr(self, key), wire=False)

    @classmethod
    def _decode(cls, obj):
        schema = _schema(cls)
        if type(obj) is not dict or obj.keys() - schema.keys():
            _invalid()
        try:
            return cls(**{k: _typed(schema[k], v, wire=True) for k, v in obj.items()})
        except TypeError:
            _invalid()

    @classmethod
    def from_json(cls, payload: object) -> Self:
        if type(payload) is str:
            try:
                payload = json.loads(payload, object_pairs_hook=_unique)
            except (ValueError, RecursionError):
                _invalid()
        return cls._decode(payload)

    def to_json(self) -> str:
        return json.dumps(_wire(self), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class RecallBudget(_Record):
    seeds: int = 8
    semantic_depth: int = 2
    beam_width: int = 16
    alternative_states: int = 3
    edges_per_endpoint: int = 16
    expansion_rows: int = 200
    proof_premise_rows: int = 256
    conflict_rows: int = 128
    candidate_pool: int = 32
    finalist_bundles: int = 8
    query_deadline_ms: int = 250
    execution_deadline_ms: int = 500
    token_target: int = 1600
    serialized_bytes: int = 6400

    def __post_init__(self):
        for name, (_, cap) in BUDGET_LIMITS.items():
            value = getattr(self, name)
            floor = 1024 if name == 'serialized_bytes' else 0
            if type(value) is not int or not floor <= value <= cap:
                raise InvalidRetrievalBudget('retrieval budget outside limits')

    @classmethod
    def _decode(cls, obj):
        if type(obj) is not dict or obj.keys() - BUDGET_LIMITS.keys():
            raise InvalidRetrievalBudget('invalid retrieval budget')
        return cls(**obj)


Predicate = Literal['IMPLEMENTS', 'MOTIVATED_BY', 'REGRESSION_FOR', 'DEFINED_IN', 'CALLS', 'IMPORTS',
                    'DEPENDS_ON', 'MENTIONS', 'SUPPORTS', 'DISPUTES', 'SUPERSEDES', 'APPLIES_TO']
Intent = Literal['lookup', 'rationale', 'impact', 'history', 'compound']


@dataclass(frozen=True)
class RelationStep(_Record):
    predicate: Predicate
    direction: Literal['forward', 'inverse']

    def __post_init__(self):
        try:
            super().__post_init__()
        except InvalidInput as exc:
            raise InvalidGraphPattern('invalid relation step') from exc


@dataclass(frozen=True)
class RecallQuery(_Record):
    text: str = ''
    anchors: tuple[str, ...] = ()
    intent: Intent = 'lookup'
    pattern: tuple[RelationStep, ...] = ()
    mode: Literal['current', 'historical'] = 'current'
    valid_as_of: str | None = None
    known_as_of: str | None = None
    budget: RecallBudget | None = None
    exact: bool = False

    @classmethod
    def _decode(cls, obj):
        if type(obj) is dict and obj.get('intent') in ('rationale', 'compound') and type(obj.get('budget')) is dict:
            obj = {**obj, 'budget': {'semantic_depth': 3, **obj['budget']}}
        return super()._decode(obj)

    def __post_init__(self):
        super().__post_init__()
        if not self.text.strip() and not self.anchors or not all(self.anchors):
            _invalid()
        if len(self.pattern) > BUDGET_LIMITS['semantic_depth'][1]:
            raise InvalidGraphPattern('relation pattern exceeds depth ceiling')
        if self.mode == 'current' and (self.valid_as_of is not None or self.known_as_of is not None):
            _invalid()
        for cutoff in (self.valid_as_of, self.known_as_of):
            if cutoff is not None:
                _validate_timestamp(cutoff)
        if self.budget is None:
            depth = 3 if self.intent in ('compound', 'rationale') else 2
            object.__setattr__(self, 'budget', RecallBudget(semantic_depth=depth))


@dataclass(frozen=True)
class TraverseQuery(RecallQuery):
    def __post_init__(self):
        if not self.anchors or not self.pattern:
            raise InvalidGraphPattern('traversal requires an anchor and finite pattern')
        super().__post_init__()


@dataclass(frozen=True)
class PackSnapshot(_Record):
    store_id: str
    memory_sequence: int
    snapshot_digest: str
    index_version: str
    graph_sequence: int = 0
    authority_epoch: int = 0
    policy_epoch: int = 0

    def __post_init__(self):
        super().__post_init__()
        if not self.store_id or not self.index_version or not _digest(self.snapshot_digest):
            _invalid()


def _digest(text):
    return re.fullmatch('[0-9a-f]{64}', text) is not None


@dataclass(frozen=True)
class TrustedQueryContext:
    """Construct under the authority fence, never from a query or pack receipt."""
    repository_id: str | None
    resolved_commit: str | None
    host_time: str
    snapshots: tuple[PackSnapshot, ...]
    authority_epoch: int
    policy_epoch: int

    def __post_init__(self):
        # Reuse exact field checks without inheriting the public JSON decoder.
        for key, kind in _schema(type(self)).items():
            _typed(kind, getattr(self, key), wire=False)
        _validate_timestamp(self.host_time)
        if not self.snapshots or any(
            (s.authority_epoch, s.policy_epoch) != (self.authority_epoch, self.policy_epoch)
            for s in self.snapshots
        ):
            _invalid()


@dataclass(frozen=True)
class ClaimRevision(_Record):
    space_id: str
    assertion_id: str
    revision_id: str

    def __post_init__(self):
        super().__post_init__()
        if not all((self.space_id, self.assertion_id, self.revision_id)):
            _invalid()


@dataclass(frozen=True)
class PathEdge(_Record):
    claim: ClaimRevision
    step: RelationStep
    subject: Endpoint
    object: Endpoint


@dataclass(frozen=True)
class PathState:
    """One internal traversal alternative, not a model-facing query or receipt."""
    seed_group: str
    endpoint: Endpoint
    path: tuple[PathEdge, ...]
    visited: frozenset[Endpoint]
    pattern_position: int
    support_signatures: tuple[tuple[str, ...], ...]
    scope: Scope
    # Accumulated intersection across the path, not merely the final edge's bounds.
    applicability: Applicability
    validity: Validity
    # Progress, coverage, exact priority, seed order/RRF, hops, degree, exact revisions.
    rank: tuple[int, int, int, int | Fraction, int, int, tuple[str, ...]]

    def __post_init__(self):
        if (type(self.scope) is not Scope or type(self.applicability) is not Applicability
                or type(self.validity) is not Validity or type(self.path) is not tuple
                or not all(type(edge) is PathEdge for edge in self.path)):
            _invalid()
        if (type(self.rank) is not tuple or len(self.rank) != 7
                or any(type(self.rank[i]) is not int for i in (0, 1, 2, 4, 5))
                or type(self.rank[3]) not in (int, Fraction)
                or self.rank[-1] != tuple(edge.claim.revision_id for edge in self.path)):
            _invalid()


@dataclass(frozen=True)
class Citation(_Record):
    evidence_id: str
    source: MemorySource | CodeSource
    span: Span
    text: str

    def __post_init__(self):
        super().__post_init__()
        data = self.text.encode('utf-8')
        if self.span.end - self.span.start != len(data) or hashlib.sha256(data).hexdigest() != self.span.sha256:
            _invalid()


@dataclass(frozen=True)
class EvidenceBundle(_Record):
    claim: ClaimRevision
    path: tuple[PathEdge, ...]
    support_groups: tuple[tuple[str, ...], ...]
    citations: tuple[Citation, ...]
    origin_kind: Literal['human_asserted', 'parser', 'model_extracted']
    review_state: Literal['proposed', 'accepted', 'retracted']
    validity: Validity
    applicability: Applicability
    disputes: tuple[ClaimRevision, ...]
    suppression_explanations: tuple[str, ...]
    contested: bool

    def __post_init__(self):
        super().__post_init__()
        if (not self.citations or not 1 <= len(self.support_groups) <= 8
                or not all(1 <= len(g) <= 16 and all(g) for g in self.support_groups)):
            _invalid()


@dataclass(frozen=True)
class AnchorCandidate(_Record):
    endpoint: Endpoint
    provider_version: str
    index_generation: int
    exact_match: bool = False


class AnchorProvider(Protocol):
    def search(self, query: RecallQuery, snapshot: GraphReadSnapshot, limit: int) -> tuple[AnchorCandidate, ...]: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...], *, deadline: float) -> tuple[tuple[float, ...], ...]: ...


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class EvidencePack(_Record):
    schema_version: Literal[1]
    pack_id: str
    query_digest: str
    context_digest: str
    snapshots: tuple[PackSnapshot, ...]
    status: Literal['incomplete', 'contested', 'supported', 'no_eligible_matches', 'no_supported_path']
    limitations: tuple[str, ...]
    bundles: tuple[EvidenceBundle, ...]
    expansion_rows: int
    proof_premise_rows: int
    conflict_rows: int
    token_count_kind: Literal['estimated', 'exact']
    token_count: int
    serialized_utf8_bytes: int
    counter_identity: str = 'utf8_bytes_div_4'
    conflict_coverage: Literal['recorded_explicit_only'] = 'recorded_explicit_only'
    proof_edge_rows: int = 0
    proof_edges: int = 0

    def __post_init__(self):
        super().__post_init__()
        if not self.pack_id or not all(map(_digest, (self.query_digest, self.context_digest))):
            _invalid()
        if not self.counter_identity or (self.token_count_kind == 'exact') == (self.counter_identity == 'utf8_bytes_div_4'):
            _invalid()
        if len(self.bundles) > BUDGET_LIMITS['finalist_bundles'][1]:
            _invalid()
        for field in ('expansion_rows', 'proof_premise_rows', 'conflict_rows'):
            if getattr(self, field) > BUDGET_LIMITS[field][1]:
                _invalid()
        if self.status != 'incomplete':
            if HISTORICAL_INDEX_UNAVAILABLE in self.limitations:
                _invalid()
            contested = any(bundle.contested for bundle in self.bundles)
            if (self.status == 'contested') != contested or (self.status == 'supported') != (bool(self.bundles) and not contested):
                _invalid()

    @classmethod
    def from_json(cls, payload: object) -> Self:
        if type(payload) is str:
            try:
                if len(payload) > 32768 or len(payload.encode('utf-8')) > 32768:
                    _invalid()
            except UnicodeError:
                _invalid()
            try:
                payload = json.loads(payload, object_pairs_hook=_unique)
            except (ValueError, RecursionError):
                _invalid()
        pack = cls._decode(payload)
        # Unlike a request, a receipt cannot omit metadata and inherit local defaults.
        if payload != json.loads(pack.to_json()):
            _invalid()
        return pack

    def to_json(self) -> str:
        text = super().to_json()
        size = len(text.encode('utf-8'))
        if size != self.serialized_utf8_bytes or size > BUDGET_LIMITS['serialized_bytes'][1]:
            _invalid()
        if self.token_count_kind == 'estimated' and self.token_count != (size + 3) // 4:
            _invalid()
        return text

    def measured(self, budget: RecallBudget, counter: TokenCounter | None = None) -> Self:
        """Close the self-referential counters before publication; never split a bundle."""
        if (counter is None) != (self.token_count_kind == 'estimated'):
            _invalid()
        pack = self
        for _ in range(16):
            text = _Record.to_json(pack)
            size = len(text.encode('utf-8'))
            count = (size + 3) // 4 if counter is None else counter.count(text)
            if type(count) is not int or count < 0:
                _invalid()
            if (size, count) == (pack.serialized_utf8_bytes, pack.token_count):
                if size > budget.serialized_bytes or count > budget.token_target:
                    raise InvalidRetrievalBudget('pack exceeds output budget')
                return pack
            pack = replace(pack, serialized_utf8_bytes=size, token_count=count)
        raise InvalidRetrievalBudget('pack counters did not stabilize')


class RecallService(Protocol):
    def __init__(self, repository: GraphRepository, binding: SpaceBinding): ...
    def recall(self, query: RecallQuery, context: TrustedQueryContext) -> EvidencePack: ...
    def traverse(self, query: TraverseQuery, context: TrustedQueryContext) -> EvidencePack: ...
    def explain(self, pack_id: str, context: TrustedQueryContext) -> EvidencePack: ...
