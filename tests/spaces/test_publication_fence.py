"""Spawned publisher exchanges pipe acknowledgements; no scheduler sleeps."""
import multiprocessing
import sqlite3
from contextlib import closing, contextmanager
from time import monotonic

import pytest

from nexus_memory.domain.errors import AuthorityBusy, GrantRevoked, PublicationBusy
from test_authority import setup as make_catalog, broker, exact, op, publish


def worker(pipe, cat, grant, space, phase):
    fence, queue = broker(cat)
    write = exact(cat, grant, space)
    def barrier(name):
        pipe.send(name)
        assert pipe.poll(12) and pipe.recv() == 'continue'
    def commit(admission):
        if phase == 'locked':
            barrier('locked')
        payload = write(admission)
        if phase == 'committed':
            barrier('committed')
        return payload
    barrier('ready')
    try:
        env = fence.publish(grant, queue.reserve(2048, monotonic()+20), source_space_ids=(),
                            destination_space_id=space.space_id, validate_sources=lambda _: None, commit=commit)
    except GrantRevoked:
        pipe.send('denied')
        return
    barrier('admitted')
    pipe.send(queue.drain(env.publication_id))


@contextmanager
def child(cat, grant, space, phase):
    ctx = multiprocessing.get_context('spawn')
    parent, other = ctx.Pipe()
    proc = ctx.Process(target=worker, args=(other, cat, grant, space, phase))
    proc.start()
    other.close()
    def recv():
        assert parent.poll(10), 'publisher did not acknowledge'
        return parent.recv()
    try:
        assert recv() == 'ready'
        yield proc, lambda: parent.send('continue'), recv
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()


def revoke(fence, grant):
    return fence.revoke(grant.grant_id, expected_epoch=grant.epoch, operation=op('revoke'))


def counts(cat):
    with closing(sqlite3.connect(cat.path)) as db:
        receipts, = db.execute('SELECT count(*) FROM publications').fetchone()
    with closing(sqlite3.connect(next(cat.path.parent.glob('space-*.sqlite')))) as db:
        return receipts, db.execute('SELECT count(*) FROM revisions').fetchone()[0]


def test_revoke_before_admission_denies_commit_and_queue(tmp_path):
    cat, grant, space = make_catalog(tmp_path)
    fence, _ = broker(cat)
    with child(cat, grant, space, 'before') as (_, go, recv):
        assert revoke(fence, grant) == grant.epoch+1
        go()
        assert recv() == 'denied'
    assert counts(cat) == (0, 0)


def test_revoke_after_admission_allows_old_drain_only(tmp_path):
    cat, grant, space = make_catalog(tmp_path)
    fence, _ = broker(cat)
    with child(cat, grant, space, 'after') as (_, go, recv):
        go()
        assert recv() == 'admitted'
        assert revoke(fence, grant) == grant.epoch+1
        with pytest.raises(GrantRevoked):
            publish(cat, grant, space, exact(cat, grant, space))
        go()
        assert b'revision_id' in recv()
    assert counts(cat) == (1, 1)


def test_killed_publisher_releases_fence(tmp_path):
    cat, grant, space = make_catalog(tmp_path)
    fence, _ = broker(cat)
    with child(cat, grant, space, 'locked') as (proc, go, recv):
        go()
        assert recv() == 'locked'
        start = monotonic()
        with pytest.raises(AuthorityBusy):
            revoke(fence, grant)
        assert monotonic()-start < 3
        proc.kill()
        proc.join(5)
        assert revoke(fence, grant) == grant.epoch+1
    assert counts(cat) == (0, 0)


def test_crash_between_store_and_catalog_recovers_one_effect(tmp_path):
    cat, grant, space = make_catalog(tmp_path)
    with child(cat, grant, space, 'committed') as (proc, go, recv):
        go()
        assert recv() == 'committed'
        proc.kill()
        proc.join(5)
    assert counts(cat) == (0, 1)
    assert b'revision_id' in publish(cat, grant, space, exact(cat, grant, space))
    assert counts(cat) == (1, 1)


def test_uncommitted_or_digest_mismatched_payload_never_drains(tmp_path, monkeypatch):
    cat, grant, space = make_catalog(tmp_path)
    fence, queue = broker(cat)
    stage = queue.stage
    def inspect(slot, payload):
        env = stage(slot, payload)
        with pytest.raises(PublicationBusy):
            queue.drain(env.publication_id)
        return env
    monkeypatch.setattr(queue, 'stage', inspect)
    env = fence.publish(grant, queue.reserve(10, monotonic()+2), source_space_ids=(space.space_id,),
                        destination_space_id=None, validate_sources=lambda _: None, commit=lambda _: b'secret')
    with closing(sqlite3.connect(cat.path)) as db, db:
        db.execute("UPDATE publications SET digest='wrong'")
    with pytest.raises(PublicationBusy):
        queue.drain(env.publication_id)
    with closing(sqlite3.connect(cat.path)) as db, db:
        db.execute('UPDATE publications SET digest=?', (env.response_digest,))
    assert queue.drain(env.publication_id) == b'secret'
