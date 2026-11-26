import pytest

from nexus_memory.domain.errors import GrantRevoked, SpaceNotMounted, SpaceReadOnly
from nexus_memory.domain.spaces import OperationRef


def op(key):
    return OperationRef('installation', key)


def test_switch_preserves_exposure(tmp_path):
    from nexus_memory.spaces.catalog import Catalog
    from nexus_memory.spaces.service import MountedMemoryService
    cat = Catalog(tmp_path / 'catalog')
    a, b = [cat.create_space(k, 'research', principal_id='alice', operation=op(k)) for k in ('a','b')]
    sid = cat.launch_context('alice', operation=op('launch'))
    def bind(session, space, key, principal='alice'):
        return cat.bind_session(session, principal, space.space_id, alias='here', mode='read_write', operation=op(key))
    grant = bind(sid,a,'first')
    mounted = MountedMemoryService(cat, grant)
    assert mounted.resolve(write=True).binding == a
    from dataclasses import replace
    from nexus_memory.spaces.service import BoundSpace
    from nexus_memory.domain.errors import InvalidInput
    reader = mounted.resolve('here').memory_service
    assert reader.status().schema_version == 5
    with pytest.raises(InvalidInput):
        BoundSpace(a, reader)
    with pytest.raises(GrantRevoked):
        MountedMemoryService(cat, replace(grant, principal_id='bob')).resolve()
    with pytest.raises(SpaceNotMounted):
        mounted.resolve('not-mounted')
    with pytest.raises(SpaceNotMounted):
        bind(sid,b,'second')
    assert mounted.resolve('here').binding == a
    with pytest.raises(GrantRevoked):
        bind('caller-selected',b,'fresh')
    other = cat.launch_context('bob', operation=op('bob'))
    with pytest.raises(SpaceNotMounted):
        bind(other,b,'inherit','bob')
    switched = cat.switch_session(sid,'alice',b.space_id,alias='next',mode='read_only',operation=op('switch'))
    assert switched.exposed_space_ids == {a.space_id,b.space_id}
    with pytest.raises(GrantRevoked):
        mounted.resolve('here')
    current = MountedMemoryService(cat, switched)
    with pytest.raises(SpaceReadOnly):
        current.resolve('next', write=True)
    assert current.resolve('next').memory_service.scope == b.scope


def test_restore_revokes_old_mount(tmp_path):
    from nexus_memory.spaces.catalog import Catalog
    from nexus_memory.spaces.migration import backup_store
    from nexus_memory.spaces.service import MountedMemoryService
    from nexus_memory.domain.errors import StorageIntegrityError
    from contextlib import closing
    import sqlite3
    cat = Catalog(tmp_path / 'catalog')
    a = cat.create_space('A','personal',principal_id='alice',operation=op('create'))
    with closing(sqlite3.connect(tmp_path / 'catalog')) as db:
        path, = db.execute('SELECT locator FROM stores').fetchone()
    copy = tmp_path / 'backup'
    backup_store(path, copy)
    sid = cat.launch_context('alice',operation=op('launch'))
    def bind(key):
        return cat.bind_session(sid,'alice',a.space_id,alias='a',mode='read_write',operation=op(key))
    old = MountedMemoryService(cat,bind('before'))
    bound = old.resolve('a')
    cat.begin_restore(a.store_id, operation=op('begin'))
    with pytest.raises(GrantRevoked):
        old.resolve('a')
    with pytest.raises(GrantRevoked):
        _ = bound.memory_service
    from nexus_memory.storage import SQLiteRepository
    wrong = tmp_path / 'different-store'
    SQLiteRepository(wrong, owner_schema5=True)
    with pytest.raises(StorageIntegrityError):
        cat.restore_store(a.store_id,wrong,operation=op('wrong'))
    with pytest.raises(StorageIntegrityError):
        bind('while-restoring')
    cat.restore_store(a.store_id,copy,operation=op('finish'))
    with pytest.raises(GrantRevoked):
        old.resolve('a')
    with pytest.raises(StorageIntegrityError):
        cat.register_store(path,operation=op('old-copy'))
    assert MountedMemoryService(cat,bind('after')).resolve('a').binding == a
