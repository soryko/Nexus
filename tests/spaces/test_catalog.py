"""A03 owner operations against disposable files only."""
from contextlib import closing
import sqlite3

import pytest

from nexus_memory.domain.errors import IdempotencyConflict, StorageIntegrityError, UnsupportedSchema
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.memory import MemoryService
from nexus_memory.spaces.migration import backup_store
from nexus_memory.storage import SQLiteRepository


def op(key):
    return OperationRef('owner-install', key)


def test_register_retry_keeps_allocated_ids(tmp_path, monkeypatch):
    from nexus_memory.spaces.catalog import Catalog
    path = tmp_path / 'legacy'
    for actor in ('one', 'two'):
        MemoryService(SQLiteRepository(path), Scope('test', actor)).record(MemoryInput(actor), 'k')
    cat = Catalog(tmp_path / 'catalog')
    with monkeypatch.context() as patch:
        def crash(*args):
            raise RuntimeError('before catalog commit')
        patch.setattr(cat, '_install_bindings', crash)
        with pytest.raises(RuntimeError, match='before catalog commit'):
            cat.register_store(path, operation=op('import'))
    with closing(sqlite3.connect(path)) as db:
        allocated = set(db.execute('SELECT store_id,space_id,actor FROM space_partitions'))
    cat = Catalog(tmp_path / 'catalog')
    backup_store(path, tmp_path / 'partial-copy')
    with pytest.raises(StorageIntegrityError, match='duplicate'):
        cat.register_store(tmp_path / 'partial-copy', operation=op('copy'))
    bindings = cat.register_store(path, operation=op('import'))
    assert {b.scope.actor for b in bindings} == {'one', 'two'}
    assert {(b.store_id, b.space_id, b.scope.actor) for b in bindings} == allocated
    assert cat.register_store(path, operation=op('import')) == bindings
    with pytest.raises(IdempotencyConflict):
        cat.register_store(path, expected_store_id='different', operation=op('import'))


@pytest.mark.parametrize('fault', ['duplicate', 'replacement', 'retired', 'future'])
def test_registration_refuses_changed_store(tmp_path, fault):
    from nexus_memory.spaces.catalog import Catalog
    cat = Catalog(tmp_path / 'catalog')
    path = tmp_path / 'one'
    SQLiteRepository(path, owner_schema5=True, configured_scope=Scope('test','one'))
    original, = cat.register_store(path, operation=op('first'))
    if fault == 'duplicate':
        backup_store(path, tmp_path / 'copy')
        path = tmp_path / 'copy'
    if fault == 'replacement':
        backup_store(path, tmp_path / 'replacement')
        (tmp_path / 'replacement').replace(path)
    if fault == 'retired':
        cat.retire_store(original.store_id, operation=op('retire'))
    if fault == 'future':
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA user_version=99')
    with pytest.raises(UnsupportedSchema if fault == 'future' else StorageIntegrityError):
        cat.register_store(path, operation=op('again'))
    with closing(sqlite3.connect(tmp_path / 'catalog')) as db:
        assert db.execute('SELECT locator FROM stores WHERE store_id=?',(original.store_id,)).fetchone() == (str(tmp_path / 'one'),)


def test_new_space_has_distinct_file_and_scope(tmp_path):
    from nexus_memory.spaces.catalog import Catalog
    cat = Catalog(tmp_path / 'catalog')
    def create(key):
        return cat.create_space('Same title', 'project', principal_id='owner', operation=op(key))
    a, b = create('a'), create('b')
    assert a == create('a') and a.store_id != b.store_id
    with closing(sqlite3.connect(tmp_path / 'catalog')) as db:
        locations = dict(db.execute('SELECT store_id,locator FROM stores'))
        assert locations[a.store_id] != locations[b.store_id]
        for binding in (a,b):
            assert binding.scope == Scope('space:' + binding.space_id, 'space')
            with closing(sqlite3.connect(locations[binding.store_id])) as store:
                assert store.execute('SELECT store_id,space_id FROM space_partitions').fetchall() == [(binding.store_id,binding.space_id)]
