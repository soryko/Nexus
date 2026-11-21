"""All catalogs, grants and destination stores here are disposable."""
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
import json
from time import monotonic

import pytest

from nexus_memory.domain.errors import FlowDenied, SpaceReadOnly, PublicationBusy, PublicationUnknown
from nexus_memory.domain.models import MemoryInput
from nexus_memory.domain.spaces import MemoryRef, OperationRef
from nexus_memory.memory import MemoryService
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.storage import SQLiteRepository
from nexus_memory.storage.repository import WriteContext


def op(key):
    return OperationRef('installation', key)


def setup(path):
    cat = Catalog(path / 'catalog')
    a = cat.create_space('A', 'research', principal_id='alice', operation=op('a'))
    session = cat.launch_context('alice', operation=op('launch'))
    grant = cat.bind_session(session, 'alice', a.space_id, alias='here', mode='read_write', operation=op('bind'))
    return cat, grant, a


def broker(cat):
    from nexus_memory.spaces.authority import AuthorityFence
    from nexus_memory.spaces.queue import PublicationQueue
    queue = PublicationQueue(cat)
    return AuthorityFence(cat, queue), queue


def exact(cat, grant, space, transform=lambda prepared: prepared):
    with closing(sqlite3.connect(cat.path)) as db:
        path, = db.execute('SELECT locator FROM stores WHERE store_id=?', (space.store_id,)).fetchone()
    repo = SQLiteRepository(path, owner_schema5=True)
    svc = MemoryService(repo, space.scope)
    ctx = WriteContext(space.space_id, op('record'), 'alice', None, grant.session_id, None, None, None, None, None)
    prepared = svc.prepare_record(MemoryInput('synthetic payload'), 'record', context=ctx)
    prepared = transform(prepared)
    return lambda admission: json.dumps(asdict(admission.exact(repo, prepared)), sort_keys=True).encode()


def publish(cat, grant, space, callback, *, size=2048, deadline=None, source_space_ids=()):
    fence, queue = broker(cat)
    slot = queue.reserve(size, deadline if deadline is not None else monotonic()+2)
    env = fence.publish(grant, slot, source_space_ids=source_space_ids, destination_space_id=space.space_id,
                        validate_sources=lambda admission: None, commit=callback)
    return queue.drain(env.publication_id)


@pytest.mark.parametrize('mode', ['read_only', 'mixed'])
def test_all_mutations_obey_flow(tmp_path, mode):
    cat, grant, space = setup(tmp_path)
    if mode == 'mixed':
        space = cat.create_space('B', 'research', principal_id='alice', operation=op('b'))
    fresh = cat.launch_context('alice', operation=op('fresh'))
    allowed = cat.bind_session(fresh, 'alice', space.space_id, alias='here', mode='read_write', operation=op('fresh-bind'))
    assert publish(cat, allowed, space, exact(cat, allowed, space))
    current = cat.switch_session(grant.session_id, 'alice', space.space_id, alias='here',
                                 mode='read_write' if mode=='mixed' else mode, operation=op('switch'))
    # Includes A04's already-prepared operation replay (same stream/key).
    for callback in (exact(cat, current, space), lambda _: pytest.fail('generic mutation ran')):
        with pytest.raises(FlowDenied if mode=='mixed' else SpaceReadOnly):
            publish(cat, current, space, callback)


@pytest.mark.parametrize('sizes', [[1]*64, [1024*1024]])
def test_queue_full_prevents_mutation(tmp_path, sizes):
    cat, grant, space = setup(tmp_path)
    fence, queue = broker(cat)
    slots = [queue.reserve(size, monotonic()+2) for size in sizes]
    try:
        with pytest.raises(PublicationBusy):
            publish(cat, grant, space, lambda _: pytest.fail('effect without reservation'))
    finally:
        for slot in slots:
            queue.cancel(slot)


def test_destination_wait_uses_remaining_deadline(tmp_path):
    cat, grant, space = setup(tmp_path)
    cb = exact(cat, grant, space)
    with closing(sqlite3.connect(next(tmp_path.glob('space-*.sqlite')))) as db:
        db.execute('BEGIN IMMEDIATE')
        start = monotonic()
        with pytest.raises(PublicationBusy):
            publish(cat, grant, space, cb, deadline=start+0.15)
        assert monotonic()-start < 1
        assert db.execute('SELECT count(*) FROM operation_keys').fetchone() == (0,)


def test_catalog_and_destination_share_one_budget(tmp_path, monkeypatch):
    from nexus_memory.spaces import authority
    cat, grant, space = setup(tmp_path)
    fence, queue = broker(cat)
    slot = queue.reserve(16, monotonic()+60)
    assert 0 < slot.deadline-monotonic() <= 2
    # Consume budget deterministically after catalog BEGIN; inspect the real destination timeout.
    def validate(_):
        monkeypatch.setattr(authority, 'monotonic', lambda: slot.deadline-0.080)
    def commit(admission):
        def apply(db):
            ms, = db.execute('PRAGMA busy_timeout').fetchone()
            assert 1 <= ms <= 81
            return b'ok'
        return admission.mutate(apply, lambda _: None)
    env = fence.publish(grant, slot, source_space_ids=(), destination_space_id=space.space_id,
                        validate_sources=validate, commit=commit)
    assert queue.drain(env.publication_id) == b'ok'


def test_post_destination_failure_is_unknown(tmp_path):
    cat, grant, space = setup(tmp_path)
    cb = exact(cat, grant, space)
    def fail_after(admission):
        cb(admission)
        raise RuntimeError('serialization failed')
    with pytest.raises(PublicationUnknown):
        publish(cat, grant, space, fail_after)
    assert publish(cat, grant, space, exact(cat, grant, space))
    with closing(sqlite3.connect(next(tmp_path.glob('space-*.sqlite')))) as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone() == (1,)


def test_changed_prepared_payload_is_rejected_under_fence(tmp_path):
    from nexus_memory.domain.errors import InvalidInput
    cat, grant, space = setup(tmp_path)
    callback = exact(cat, grant, space, lambda p: replace(p, item=MemoryInput('changed after prepare')))
    with pytest.raises(InvalidInput):
        publish(cat, grant, space, callback)


@pytest.fixture
def cited_write(tmp_path):
    cat, grant, space = setup(tmp_path)
    path = next(tmp_path.glob('space-*.sqlite'))
    repo = SQLiteRepository(path, owner_schema5=True)
    svc = MemoryService(repo, space.scope)
    source = svc.record(MemoryInput('source'), 'seed')
    ref = MemoryRef(space.space_id, source.memory_id, source.revision_id)
    ctx = WriteContext(space.space_id, op('derived'), 'alice', session_id=grant.session_id,
                       publication_sources=(ref,))
    return cat, grant, space, path, repo, svc, ref, ctx


def receipt_bytes(repo, prepared):
    return lambda admission: json.dumps(asdict(admission.exact(repo, prepared)), sort_keys=True).encode()


@pytest.mark.parametrize('change', ['superseded', 'tombstoned'])
def test_nonadmitted_receipt_survives_source_change(cited_write, change):
    from nexus_memory.domain.errors import GrantRevoked, MemoryNotFound, RevisionConflict
    cat, grant, space, path, repo, svc, ref, ctx = cited_write
    item = MemoryInput('derived')
    prepared = svc.prepare_record(item, 'derived', ctx)
    cb = receipt_bytes(repo, prepared)
    fence, queue = broker(cat)
    slot = queue.reserve(0, monotonic()+2)
    with pytest.raises(PublicationUnknown):
        fence.publish(grant, slot, source_space_ids=(space.space_id,), destination_space_id=space.space_id,
                      validate_sources=lambda _: None, commit=cb)
    with pytest.raises(PublicationBusy):
        queue.drain(slot.publication_id)
    if change == 'superseded':
        svc.revise(ref.memory_id, ref.revision_id, MemoryInput('corrected'), 'change')
    else:
        svc.forget(ref.memory_id, ref.revision_id, 'change')

    fresh = svc.prepare_record(item, 'new', replace(ctx, operation=op('new')))
    def retry(callback):
        return publish(cat, grant, space, callback, source_space_ids=(ref.space_id,))

    with pytest.raises(RevisionConflict if change == 'superseded' else MemoryNotFound):
        retry(receipt_bytes(repo, fresh))
    receipt = repo.receipt_for_operation(space.scope, ctx.operation, prepared.command_digest)
    assert json.loads(retry(cb)) == asdict(receipt)
    fence.revoke(grant.grant_id, expected_epoch=grant.epoch, operation=op('revoke'))
    with pytest.raises(GrantRevoked):
        retry(cb)
    with closing(sqlite3.connect(cat.path)) as db:
        assert db.execute('SELECT count(*) FROM publications').fetchone() == (1,)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM operation_keys').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM receipts').fetchone() == (3,)  # seed, derived, change


def test_ambiguous_commit_reconciles_receipt_not_source_head(cited_write, monkeypatch):
    cat, grant, space, path, repo, svc, ref, ctx = cited_write
    # The successful revision itself supersedes the cited head before reconciliation.
    prepared = svc.prepare_revise(ref.memory_id, ref.revision_id, MemoryInput('corrected'), 'derived', ctx)
    connect = sqlite3.connect
    commits = []

    class LostAcknowledgement(sqlite3.Connection):
        def commit(self):
            super().commit()
            commits.append(True)
            raise sqlite3.OperationalError('lost acknowledgement')

    def connect_with_fault(database, **kwargs):
        if database == path.as_uri()+'?mode=rw':
            kwargs['factory'] = LostAcknowledgement
        return connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_with_fault)
    payload = publish(cat, grant, space, receipt_bytes(repo, prepared), source_space_ids=(ref.space_id,))
    assert commits == [True]
    assert json.loads(payload) == asdict(repo.receipt_for_operation(space.scope, ctx.operation, prepared.command_digest))
    with closing(connect(path)) as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone() == (2,)
