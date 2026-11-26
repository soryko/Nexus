"""Stage B repo ceiling: recall-only by default, owner opt-in to maintain, fence-checked disable,
and read-only delegated sessions. Every catalog and store here is disposable."""
from contextlib import closing
import sqlite3
import threading
from time import monotonic

import pytest

from nexus_memory.domain.errors import (GrantRevoked, IdempotencyConflict, PublicationBusy, SpaceNotMounted,
                                        SpaceReadOnly)
from nexus_memory.spaces.catalog import _SCHEMA, Catalog
from test_authority import broker, exact, op, publish
from test_publication_fence import child, counts


def catalog(tmp_path, kind='project'):
    cat = Catalog(tmp_path / 'catalog')
    space = cat.create_space('P', kind, principal_id='alice', operation=op('p'))
    return cat, space


def grant(cat, space, mode, key, role='root'):
    session = cat.launch_context('alice', operation=op('launch-'+key), role=role)
    return cat.bind_session(session, 'alice', space.space_id, alias='here', mode=mode, operation=op('bind-'+key))


def ceiling(cat, space, value, key):
    return cat.set_ceiling(space.space_id, value, principal_id='alice', operation=op('ceiling-'+key))


def read(cat, g, space, payload=b'recalled text'):
    fence, queue = broker(cat)
    return fence.publish(g, queue.reserve(1024, monotonic()+2), source_space_ids=(space.space_id,),
                         destination_space_id=None, validate_sources=lambda _: None, commit=lambda _: payload), queue


def test_a_project_space_starts_recall_only(tmp_path):
    cat, space = catalog(tmp_path)
    with pytest.raises(SpaceReadOnly):
        grant(cat, space, 'read_write', 'w')
    reader = grant(cat, space, 'read_only', 'r')
    env, queue = read(cat, reader, space)
    assert queue.drain(env.publication_id) == b'recalled text'
    # Other kinds keep Stage A behaviour.
    research = cat.create_space('R', 'research', principal_id='alice', operation=op('r'))
    assert grant(cat, research, 'read_write', 'rw')


def test_maintain_is_an_explicit_opt_in_and_lowering_it_narrows_writers_to_reads(tmp_path):
    cat, space = catalog(tmp_path)
    assert ceiling(cat, space, 'maintain', 'up') == 2
    writer = grant(cat, space, 'read_write', 'w')
    reader = grant(cat, space, 'read_only', 'r')
    assert publish(cat, writer, space, exact(cat, writer, space))
    assert ceiling(cat, space, 'recall', 'down') == 3
    with pytest.raises(SpaceReadOnly):
        publish(cat, writer, space, lambda _: pytest.fail('write ran after the ceiling was lowered'))
    for session in (writer, reader):                           # both sessions keep reading
        env, queue = read(cat, session, space)
        assert queue.drain(env.publication_id)
    with pytest.raises(SpaceReadOnly):
        grant(cat, space, 'read_write', 'w2')
    # Raising the ceiling again does not hand write back to a narrowed session.
    ceiling(cat, space, 'maintain', 'up-again')
    with pytest.raises(SpaceReadOnly):
        publish(cat, writer, space, lambda _: pytest.fail('narrowed session wrote after re-raise'))


def test_off_revokes_every_grant_and_refuses_new_ones(tmp_path):
    cat, space = catalog(tmp_path)
    reader = grant(cat, space, 'read_only', 'r')
    ceiling(cat, space, 'off', 'off')
    with pytest.raises(GrantRevoked):
        read(cat, reader, space)
    with pytest.raises(SpaceNotMounted):
        grant(cat, space, 'read_only', 'r2')
    # Re-enabling is a new generation; the old grant stays revoked and nothing replays.
    assert ceiling(cat, space, 'recall', 'on') == 3
    with pytest.raises(GrantRevoked):
        read(cat, reader, space)
    fresh = grant(cat, space, 'read_only', 'r3')
    env, queue = read(cat, fresh, space)
    assert queue.drain(env.publication_id)


def test_a_delegated_session_never_holds_a_writable_grant(tmp_path):
    cat, space = catalog(tmp_path)
    ceiling(cat, space, 'maintain', 'up')
    with pytest.raises(SpaceReadOnly):
        grant(cat, space, 'read_write', 'sub', role='delegated')
    sub = grant(cat, space, 'read_only', 'sub-r', role='delegated')
    with pytest.raises(SpaceReadOnly):
        publish(cat, sub, space, lambda _: pytest.fail('delegated write ran'))
    env, queue = read(cat, sub, space)
    assert queue.drain(env.publication_id)


def test_recalled_text_admitted_before_disable_is_not_delivered(tmp_path):
    cat, space = catalog(tmp_path)
    reader = grant(cat, space, 'read_only', 'r')
    env, queue = read(cat, reader, space)
    ceiling(cat, space, 'off', 'off')
    with pytest.raises(PublicationBusy):
        queue.drain(env.publication_id)
    # The refused read releases its queue slot rather than holding it for the life of the process.
    from nexus_memory.spaces.queue import _entries
    assert env.publication_id not in _entries


def test_a_write_waiting_for_admission_is_denied_after_disable(tmp_path):
    cat, space = catalog(tmp_path)
    ceiling(cat, space, 'maintain', 'up')
    writer = grant(cat, space, 'read_write', 'w')
    with child(cat, writer, space, 'before') as (_, go, recv):
        ceiling(cat, space, 'off', 'off')
        go()
        assert recv() == 'denied'
    assert counts(cat) == (0, 0)


def test_disable_waits_for_a_write_already_inside_the_fence(tmp_path):
    cat, space = catalog(tmp_path)
    ceiling(cat, space, 'maintain', 'up')
    writer = grant(cat, space, 'read_write', 'w')
    done = []
    with child(cat, writer, space, 'locked') as (_, go, recv):
        go()
        assert recv() == 'locked'                              # the child holds the catalog lock
        thread = threading.Thread(target=lambda: done.append(ceiling(cat, space, 'off', 'off')))
        thread.start()
        thread.join(0.5)
        assert not done                                        # disable is not acknowledged yet
        go()
        assert recv() == 'admitted'
        thread.join(10)
        assert done == [3]
        go()
        assert b'revision_id' in recv()                        # its receipt still drains
    assert counts(cat) == (1, 1)                               # committed before the acknowledgement
    with pytest.raises(GrantRevoked):
        publish(cat, writer, space, exact(cat, writer, space))


def test_set_ceiling_is_an_exact_retry(tmp_path):
    cat, space = catalog(tmp_path)
    assert ceiling(cat, space, 'maintain', 'k') == 2
    assert ceiling(cat, space, 'maintain', 'k') == 2
    with pytest.raises(IdempotencyConflict):
        ceiling(cat, space, 'off', 'k')
    with pytest.raises(SpaceNotMounted):
        cat.set_ceiling(space.space_id, 'off', principal_id='mallory', operation=op('steal'))


def test_a_version_2_catalog_upgrades_keeping_stage_a_spaces_writable(tmp_path):
    path = tmp_path / 'catalog'
    with closing(sqlite3.connect(path)) as db, db:
        for statement in _SCHEMA.strip().rstrip(';').split(';'):
            db.execute(statement)
        db.execute('CREATE TABLE publications(publication_id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions, '
                   'grant_id TEXT NOT NULL, epoch INTEGER NOT NULL, digest TEXT NOT NULL, enqueued_at TEXT NOT NULL)')
        db.execute("INSERT INTO stores VALUES('s','/nowhere',NULL,NULL,'active')")
        db.execute("INSERT INTO spaces VALUES('sp','s','ns','actor','t','project','alice',1)")
        db.execute("INSERT INTO sessions(session,owner,stream,epoch) VALUES('x','alice','w',0)")
        db.execute('PRAGMA user_version=2')
    Catalog(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (3,)
        assert db.execute('SELECT ceiling,ceiling_generation FROM spaces').fetchall() == [('maintain', 1)]
        assert db.execute('SELECT role FROM sessions').fetchall() == [('root',)]


def test_the_fence_reads_the_ceiling_itself(tmp_path):
    """set_ceiling revokes grants, so this lowers the ceiling out of band to prove the fence's own check."""
    cat, space = catalog(tmp_path)
    ceiling(cat, space, 'maintain', 'up')
    writer = grant(cat, space, 'read_write', 'w')
    with closing(sqlite3.connect(cat.path)) as db, db:
        db.execute("UPDATE spaces SET ceiling='recall'")
    with pytest.raises(SpaceReadOnly):
        publish(cat, writer, space, lambda _: pytest.fail('write ran above the ceiling'))
    with closing(sqlite3.connect(cat.path)) as db, db:
        db.execute("UPDATE spaces SET ceiling='off'")
    with pytest.raises(SpaceNotMounted):
        read(cat, writer, space)
