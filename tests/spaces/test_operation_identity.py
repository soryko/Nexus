"""Exact broker operations in disposable destination stores."""
import json
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, replace

import pytest

from nexus_memory.domain.errors import IdempotencyConflict, InvalidInput, RevisionConflict, StorageIntegrityError
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope, VerifiedReference
from nexus_memory.domain.spaces import MemoryRef, OperationRef, canonical_command_digest
from nexus_memory.memory.service import MemoryService
from nexus_memory.storage.sqlite import SQLiteRepository

SCOPE = Scope('synthetic', 'writer')
ITEM = MemoryInput('synthetic content')


def opened(path, key='request', session='first'):
    repo = SQLiteRepository(path, owner_schema5=True, configured_scope=SCOPE)
    with sqlite3.connect(path) as db:
        space, = db.execute('SELECT space_id FROM space_partitions').fetchone()
    from nexus_memory.storage.repository import WriteContext
    return MemoryService(repo, SCOPE), WriteContext(space, OperationRef('stream', key), 'principal',
            'agent', session, 'run', 'event', {'category': 'note'}, None, None)


def record(service, context, item=ITEM):
    return service.commit_prepared(service.prepare_record(item, context.operation.operation_key, context=context))


def test_retry_survives_new_process_and_session(tmp_path):
    path = tmp_path / 'synthetic.db'
    service, ctx = opened(path)
    first = record(service, ctx)
    replay = subprocess.check_output([sys.executable, __file__, str(path)], text=True)
    assert json.loads(replay) == asdict(first)
    with sqlite3.connect(path) as db:
        for table in ('revisions', 'outbox', 'operation_keys', 'receipts'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (1,)
        for table in ('operation_provenance', 'revision_provenance'):
            assert db.execute(f'SELECT session_id,run_id,source_event_id FROM {table}').fetchall() == [('first','run','event')]
    with pytest.raises(IdempotencyConflict):
        record(service, ctx, MemoryInput('changed'))
    ctx = replace(ctx, operation=OperationRef('stream', 'revise'))
    def revise(expected):
        return service.commit_prepared(service.prepare_revise(first.memory_id, expected, ITEM, 'revise', context=ctx))
    revised = revise(first.revision_id)
    assert revise(first.revision_id) == revised
    with pytest.raises(IdempotencyConflict):
        revise(revised.revision_id)
    ctx = replace(ctx, operation=OperationRef('stream', 'forget'), compat_metadata=None)
    def forget():
        return service.commit_prepared(service.prepare_forget(first.memory_id, revised.revision_id, 'forget', context=ctx))
    assert forget() == forget()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM revision_provenance').fetchone() == (2,)
        assert db.execute('SELECT count(*) FROM operation_provenance').fetchone() == (3,)


def test_legacy_key_cannot_capture_broker_operation(tmp_path, monkeypatch):
    key = 'exact:' + 'a' * 64
    path = tmp_path / 'synthetic.db'
    legacy = MemoryService(SQLiteRepository(path), SCOPE).record(ITEM, key)
    service, ctx = opened(path, key)
    # Force even a UUID-shaped pre-existing key into the allocator's first draw.
    occupied = uuid.uuid4()
    service.record(ITEM, str(occupied))
    random_uuid = uuid.uuid4
    candidates = iter((occupied,))
    monkeypatch.setattr(uuid, 'uuid4', lambda: next(candidates, None) or random_uuid())
    broker = record(service, ctx)
    assert broker != legacy
    assert service.record(ITEM, key) == legacy
    assert record(service, ctx) == broker


def test_sidecars_rollback_with_write(tmp_path):
    path = tmp_path / 'synthetic.db'
    service, ctx = opened(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail BEFORE INSERT ON operation_keys BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(StorageIntegrityError):
        record(service, ctx)
    with sqlite3.connect(path) as db:
        for table in ('memories', 'revisions', 'receipts', 'outbox', 'revision_provenance', 'operation_keys', 'operation_provenance'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (0,)


def test_replay_skips_git_verification(tmp_path, monkeypatch):
    service, ctx = opened(tmp_path / 'synthetic.db')
    item = MemoryInput('reference', references=(ReferenceInput(path='file'),))
    # The fake observation is discarded: another prepared command wins first.
    repo = service.repository.bind_checkout(SCOPE, 'synthetic-checkout', None, 'sha1')
    original = VerifiedReference(repo.repository_id, '1' * 40, 'file', '2' * 40, 'blob', '100644', 'synthetic')
    winner = replace(original, commit_oid='3' * 40)
    observations = iter(((original,), (winner,)))
    monkeypatch.setattr(service, '_verify', lambda refs: next(observations))
    loser = service.prepare_record(item, 'request', context=ctx)
    won = record(service, replace(ctx, session_id='winner'), item)
    monkeypatch.setattr(service, '_verify', lambda refs: pytest.fail('replay verified Git'))
    assert service.commit_prepared(loser) == record(service, ctx, item) == won
    assert service.get(won.memory_id).references == (winner,)


def test_wrong_scope_space_or_key_cannot_publish(tmp_path):
    service, ctx = opened(tmp_path / 'synthetic.db')
    prepared = service.prepare_record(ITEM, 'request', context=ctx)
    with pytest.raises(InvalidInput):
        MemoryService(service.repository, Scope('other', 'writer')).commit_prepared(prepared)
    with pytest.raises(InvalidInput):
        service.prepare_record(ITEM, 'other', context=ctx)
    with pytest.raises(InvalidInput):
        record(service, replace(ctx, space_id='other'))


def test_local_publication_source_rechecked_after_preparation(tmp_path):
    service, ctx = opened(tmp_path / 'synthetic.db')
    source = service.record(ITEM, 'source')
    ref = MemoryRef(ctx.space_id, source.memory_id, source.revision_id)
    ctx = replace(ctx, publication_sources=(ref,), visibility_role='derived')
    pending = service.prepare_record(ITEM, 'request', context=ctx)
    service.revise(source.memory_id, source.revision_id, MemoryInput('new head'), 'update')
    with pytest.raises(RevisionConflict):
        service.commit_prepared(pending)
    assert service.repository.receipt_for_operation(SCOPE, ctx.operation, pending.command_digest) is None


@pytest.mark.parametrize('operation,version', [('record', 1), ('record', 2), ('revise', 1), ('revise', 2), ('forget', 1)])
def test_broker_receipt_and_mapping_are_independent(tmp_path, monkeypatch, operation, version):
    path = tmp_path / 'synthetic.db'
    service, ctx = opened(path)
    ctx = replace(ctx, compat_metadata=None)
    target = expected = None
    item = ITEM
    if version == 2:
        item = replace(ITEM, references=(ReferenceInput(path='file'),))
        binding = service.repository.bind_checkout(SCOPE, 'fixture', None, 'sha1')
        ref = VerifiedReference(binding.repository_id, '1' * 40, 'file', '2' * 40, 'blob', '100644', 'fixture')
        monkeypatch.setattr(service, '_verify', lambda refs: (ref,))
    if operation != 'record':
        source = service.record(ITEM, 'setup')
        target, expected = source.memory_id, source.revision_id
    if operation == 'forget':
        item = None
        prepared = service.prepare_forget(target, expected, 'request', context=ctx)
    elif operation == 'revise':
        prepared = service.prepare_revise(target, expected, item, 'request', context=ctx)
    else:
        prepared = service.prepare_record(item, 'request', context=ctx)
    service.commit_prepared(prepared)
    frozen = (service._digest_v2 if version == 2 else service._digest)(operation, target, expected, item)
    with sqlite3.connect(path) as db:
        digest, legacy_version, command, wrapper_version = db.execute(
            'SELECT r.request_digest,r.digest_version,k.command_digest,k.digest_version FROM operation_keys k '
            'JOIN receipts r ON (r.namespace,r.actor,r.idempotency_key)=(k.namespace,k.actor,k.receipt_key)'
        ).fetchone()
    assert (digest, legacy_version) == (frozen, version)
    assert wrapper_version == 1
    assert command == canonical_command_digest(wrapper_version, 'exact', {
        'space_id': ctx.space_id, 'legacy_digest': digest, 'legacy_digest_version': legacy_version,
        'compat_metadata': None, 'publication_sources': None, 'visibility_role': None,
    })


def test_forget_cannot_hash_unpersisted_revision_metadata(tmp_path):
    service, ctx = opened(tmp_path / 'synthetic.db')
    source = service.record(ITEM, 'setup')
    with pytest.raises(InvalidInput):
        service.prepare_forget(source.memory_id, source.revision_id, 'request', context=ctx)


def test_cross_space_source_is_not_verified_by_local_id_collision(tmp_path):
    path = tmp_path / 'synthetic.db'
    service, ctx = opened(path)
    source = service.record(ITEM, 'local')
    local = MemoryRef(ctx.space_id, source.memory_id, source.revision_id)
    foreign = replace(local, space_id='unresolved-space')
    ctx = replace(ctx, publication_sources=(local, foreign))
    with pytest.raises(InvalidInput) as exc:
        record(service, ctx)
    assert all(identifier not in str(exc.value) for identifier in asdict(foreign).values())
    with sqlite3.connect(path) as db:
        for table in ('operation_keys', 'operation_provenance', 'revision_provenance'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (0,)
        for table in ('memories', 'revisions', 'receipts', 'outbox'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone() == (1,)


def test_unicode_sidecars_reconstruct_durable_command(tmp_path):
    path = tmp_path / 'synthetic.db'
    service, ctx = opened(path)
    source = service.record(ITEM, 'local')
    ref = MemoryRef(ctx.space_id, source.memory_id, source.revision_id)
    ctx = replace(ctx, compat_metadata={'雪': 'café', 'a': [1, 2]}, publication_sources=(ref,), visibility_role='derived')
    receipt = record(service, ctx)
    with sqlite3.connect(path) as db:
        compat, sources, role = db.execute('SELECT compat_metadata_json,publication_sources_json,visibility_role '
            'FROM revision_provenance WHERE revision_id=?', (receipt.revision_id,)).fetchone()
        digest, version, space, family, wrapper_version, command = db.execute(
            'SELECT r.request_digest,r.digest_version,p.space_id,k.family,k.digest_version,k.command_digest '
            'FROM operation_keys k JOIN receipts r ON (r.namespace,r.actor,r.idempotency_key)='
            '(k.namespace,k.actor,k.receipt_key) JOIN space_partitions p USING(namespace,actor)').fetchone()
    assert compat == '{"a":[1,2],"雪":"café"}'
    assert sources == json.dumps({'version': 1, 'sources': [asdict(ref)]}, ensure_ascii=False,
                                sort_keys=True, separators=(',', ':'))
    assert canonical_command_digest(wrapper_version, family, {
        'space_id': space, 'legacy_digest': digest, 'legacy_digest_version': version,
        'compat_metadata': json.loads(compat), 'publication_sources': json.loads(sources), 'visibility_role': role,
    }) == command


if __name__ == '__main__':
    print(json.dumps(asdict(record(*opened(sys.argv[1], session='restarted')))))
