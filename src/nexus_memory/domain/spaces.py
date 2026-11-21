"""Scoped identities and publication contracts. Decoding an identity grants no authority."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Literal, Self

from .errors import InvalidInput
from .models import OBJECT_FORMATS, MemoryInput, ReferenceInput, RepositoryBinding, Scope, _valid_utf8

MAX_OPERATION_KEY_CHARS = 128


def _ids(*values: str) -> None:
    for value in values:
        if not _valid_utf8(value, 'identifier'):
            raise InvalidInput('identifier must be nonempty')


def _generation(value: int) -> None:
    if type(value) is not int or value < 0:
        raise InvalidInput('generation must be a nonnegative integer')


def _bool(value: bool) -> None:
    if type(value) is not bool:
        raise InvalidInput('expected a boolean')


def _tuple_of(values: tuple, item_type: type) -> None:
    if not isinstance(values, tuple) or not all(isinstance(item, item_type) for item in values):
        raise InvalidInput('invalid tuple members')


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput('duplicate JSON field')
        result[key] = value
    return result


def _load(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise InvalidInput('invalid JSON') from None
    return value


def _object(value: object, required: str, optional: str = '') -> dict[str, Any]:
    if not isinstance(value, dict) or not set(required.split()) <= value.keys() <= set((required + ' ' + optional).split()):
        raise InvalidInput('invalid object fields')
    return value


def _array(value: object) -> list:
    if not isinstance(value, list):
        raise InvalidInput('expected an array')
    return value


def _canonical(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
        text.encode('utf-8')
        return text
    except (ValueError, TypeError, RecursionError):
        raise InvalidInput('invalid canonical JSON') from None


def canonical_command_digest(schema_version: int, operation_kind: str, command: dict) -> str:
    if type(schema_version) is not int or schema_version < 1 or not isinstance(command, dict):
        raise InvalidInput('invalid command or schema version')
    _ids(operation_kind)
    # json.dumps coerces integer/boolean keys to strings; forbid digest aliases.
    def validate_keys(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise InvalidInput('expected JSON string key')
                validate_keys(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                validate_keys(item)

    try:
        validate_keys(command)
    except RecursionError:
        raise InvalidInput('invalid command nesting') from None
    return hashlib.sha256(_canonical({'command': command, 'operation_kind': operation_kind,
                                      'schema_version': schema_version}).encode('utf-8')).hexdigest()


class _Record:
    """Flat strict codec. Composite records override decoding to validate each nested object."""
    __slots__ = ()

    def to_json(self) -> str:
        return _canonical(asdict(self))

    @classmethod
    def from_json(cls, value: object) -> Self:
        return cls(**_object(_load(value), ' '.join(field.name for field in fields(cls))))


@dataclass(frozen=True, slots=True)
class OperationRef(_Record):
    writer_stream_id: str
    operation_key: str

    def __post_init__(self) -> None:
        _ids(self.writer_stream_id, self.operation_key)
        if len(self.operation_key) > MAX_OPERATION_KEY_CHARS:
            raise InvalidInput('operation key is too long')


@dataclass(frozen=True, slots=True)
class SpaceBinding(_Record):
    """Catalog identity snapshot; never a caller-selectable repository location."""
    space_id: str
    store_id: str
    scope: Scope
    repository_bindings: tuple[RepositoryBinding, ...]

    def __post_init__(self) -> None:
        _ids(self.space_id, self.store_id)
        if not isinstance(self.scope, Scope):
            raise InvalidInput('invalid scope')
        _tuple_of(self.repository_bindings, RepositoryBinding)
        safe = []
        for repo in self.repository_bindings:
            _ids(repo.repository_id, repo.object_format)
            if repo.object_format not in OBJECT_FORMATS:
                raise InvalidInput('unsupported object format')
            safe.append(RepositoryBinding(repo.repository_id, repo.object_format, None))
        object.__setattr__(self, 'repository_bindings', tuple(safe))

    def to_json(self) -> str:
        return _canonical({'space_id': self.space_id, 'store_id': self.store_id, 'scope': asdict(self.scope),
                           'repository_bindings': [{'repository_id': r.repository_id, 'object_format': r.object_format}
                                                   for r in self.repository_bindings]})

    @classmethod
    def from_json(cls, value: object) -> Self:
        data = _object(_load(value), 'space_id store_id scope repository_bindings')
        scope = Scope(**_object(data['scope'], 'namespace actor'))
        repos = tuple(RepositoryBinding(**_object(r, 'repository_id object_format'), locator=None)
                      for r in _array(data['repository_bindings']))
        return cls(data['space_id'], data['store_id'], scope, repos)


@dataclass(frozen=True, slots=True)
class GrantContext:
    principal_id: str
    session_id: str
    grant_id: str
    epoch: int
    writer_stream_id: str
    exposed_space_ids: frozenset[str]

    def __post_init__(self) -> None:
        _ids(self.principal_id, self.session_id, self.grant_id, self.writer_stream_id)
        _generation(self.epoch)
        _space_set(self.exposed_space_ids)


def _space_set(values: frozenset[str]) -> None:
    if not isinstance(values, frozenset):
        raise InvalidInput('expected an immutable space set')
    _ids(*values)


@dataclass(frozen=True, slots=True)
class JobAuthorityContext:
    authority_id: str
    space_id: str
    job_id: str
    owner_principal_id: str
    source_space_ids: frozenset[str]
    exposed_space_ids: frozenset[str]
    policy_generation: int
    writer_stream_id: str
    revoked: bool

    def __post_init__(self) -> None:
        _ids(self.authority_id, self.space_id, self.job_id, self.owner_principal_id, self.writer_stream_id)
        _space_set(self.source_space_ids)
        _space_set(self.exposed_space_ids)
        _generation(self.policy_generation)
        _bool(self.revoked)


@dataclass(frozen=True, slots=True)
class Mount(_Record):
    alias: str
    space_id: str
    mode: Literal['read_only', 'read_write']
    default_write: bool

    def __post_init__(self) -> None:
        _ids(self.alias, self.space_id)
        _bool(self.default_write)
        if self.mode not in ('read_only', 'read_write') or (self.default_write and self.mode != 'read_write'):
            raise InvalidInput('invalid mount mode')


@dataclass(frozen=True, slots=True)
class MemoryRef(_Record):
    space_id: str
    memory_id: str
    revision_id: str

    def __post_init__(self) -> None:
        _ids(self.space_id, self.memory_id, self.revision_id)


@dataclass(frozen=True, slots=True)
class AssertionRef(_Record):
    space_id: str
    assertion_id: str
    assertion_revision_id: str

    def __post_init__(self) -> None:
        _ids(self.space_id, self.assertion_id, self.assertion_revision_id)


def _memory_input(value: object) -> MemoryInput:
    data = _object(value, 'content', 'kind tags source_uri snapshot references').copy()
    if 'kind' in data and not isinstance(data['kind'], str):
        raise InvalidInput('expected memory kind string')
    data['tags'] = tuple(_array(data.get('tags', [])))
    _tuple_of(data['tags'], str)
    data['references'] = tuple(ReferenceInput(**_object(ref, 'path', 'commit'))
                               for ref in _array(data.get('references', [])))
    return MemoryInput(**data)


@dataclass(frozen=True, slots=True)
class PublicationCommand(_Record):
    input: MemoryInput
    source_refs: tuple[MemoryRef, ...]
    destination_space_id: str
    independent_copy: bool

    def __post_init__(self) -> None:
        if not isinstance(self.input, MemoryInput):
            raise InvalidInput('expected memory input')
        _tuple_of(self.source_refs, MemoryRef)
        _ids(self.destination_space_id)
        _bool(self.independent_copy)

    @classmethod
    def from_json(cls, value: object) -> Self:
        data = _object(_load(value), 'input source_refs destination_space_id independent_copy')
        refs = tuple(MemoryRef(**_object(ref, 'space_id memory_id revision_id')) for ref in _array(data['source_refs']))
        return cls(_memory_input(data['input']), refs, data['destination_space_id'], data['independent_copy'])


@dataclass(frozen=True, slots=True)
class PublicationEnvelope:
    publication_id: str
    grant_id: str
    epoch: int
    response_digest: str
    payload: bytes
    enqueued_at: str

    def __post_init__(self) -> None:
        _ids(self.publication_id, self.grant_id)
        _generation(self.epoch)
        if not isinstance(self.payload, bytes) or self.response_digest != hashlib.sha256(self.payload).hexdigest():
            raise InvalidInput('invalid publication payload or digest')
        if not isinstance(self.enqueued_at, str) or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z', self.enqueued_at
        ):
            raise InvalidInput('expected UTC enqueue timestamp')
        try:
            datetime.fromisoformat(self.enqueued_at)
        except ValueError:
            raise InvalidInput('invalid enqueue timestamp') from None
