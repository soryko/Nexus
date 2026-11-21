"""005 acceptance tests. Only frozen schemas and disposable synthetic stores."""
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import UUID

import pytest
from test_migration_b2a import _v3_database

from nexus_memory.domain.errors import UnsupportedSchema
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

NEW = ('store_identity', 'space_partitions', 'revision_provenance', 'operation_provenance', 'operation_keys')
CONFIG = Scope('a', 'empty')
PEER_DIGEST = '52d0121a085ec530be10a46cfa22d6eec7704662b9ffbb724d736414b5323901'
DIGEST = 'a29cd383f1d4ad2c03a7f6f37fc60400eb1dca7670f230205d0e7440272b477b'


def legacy(path):
    _v3_database(path, 1)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SQLiteRepository._script('004_repositories.sql'))
        db.execute('PRAGMA user_version=4')
        db.execute("INSERT INTO memories VALUES('a','hidden','m-0','r-0',1)")
        db.execute("INSERT INTO memories VALUES('a','peer','m-0','r-1',0)")
        db.execute("INSERT INTO blobs(namespace,actor,digest,body) VALUES('a','peer',x'01',?)", ('e\u0301\r\n雪'.encode(),))
        for rev, parent in [('r-0', None), ('r-1', 'r-0')]:
            db.execute("INSERT INTO revisions VALUES('a','peer','m-0',?,?,2,'decision','[]',NULL,NULL,'2026')", (rev, parent))
        for seq, actor in [(2, 'peer'), (3, 'receipt-only')]:
            digest, version = (PEER_DIGEST, 2) if actor == 'peer' else (DIGEST, 1)
            db.execute("INSERT INTO receipts VALUES('a',?,'key-0',?,'m-0','r-0',?,?,'record',?)",
                       (actor, digest, f'op-{seq}', seq, version))
        db.execute("INSERT INTO repositories VALUES('elsewhere','peer','repo','sha1',NULL,'2026')")


def snapshot(db, tables):
    return {t: sorted(db.execute(f'SELECT * FROM "{t}"'), key=repr) for t in tables}


@pytest.mark.parametrize('existing', [False, True])
def test_default_open_stops_at_schema4(tmp_path, existing):
    path = tmp_path / 'store'
    if existing:
        legacy(path)
    SQLiteRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (4,)
        assert not set(NEW).intersection(t for t, in db.execute('SELECT name FROM sqlite_master'))


def test_owner_creates_schema5_and_legacy_open_refuses_it(tmp_path):
    path = tmp_path / 'owner'
    SQLiteRepository(path, owner_schema5=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (5,)
        before = snapshot(db, NEW)
        assert len(before['store_identity']) == 1
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone() == ('delete',)
        for opener in (SQLiteRepository, SQLiteRepository.open_readonly):
            with pytest.raises(UnsupportedSchema):
                opener(path)
        assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert db.execute('PRAGMA user_version').fetchone() == (5,)
        assert snapshot(db, NEW) == before


def test_configured_scope_is_not_an_upgrade_request(tmp_path):
    path = tmp_path / 'store'
    with pytest.raises(ValueError, match='owner_schema5'):
        SQLiteRepository(path, configured_scope=CONFIG)
    assert not path.exists()


def test_005_preserves_legacy_rows_and_bytes(tmp_path):
    path = tmp_path / 'legacy'
    legacy(path)
    with closing(sqlite3.connect(path)) as db:
        tables = [t for t, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = snapshot(db, tables)
        SQLiteRepository(path, owner_schema5=True, configured_scope=CONFIG)
        assert db.execute('PRAGMA user_version').fetchone() == (5,)
        assert snapshot(db, tables) == before
        from nexus_memory.spaces.migration import inventory_partitions
        scopes = inventory_partitions(db, CONFIG)
        rows = snapshot(db, NEW)
        mapping = db.execute('SELECT namespace,actor,space_id FROM space_partitions').fetchall()
        expected = {('a', a) for a in ('empty', 'hidden', 'local', 'peer', 'receipt-only')} | {('elsewhere','peer')}
        assert {(s.namespace,s.actor) for s in scopes} == {(n,a) for n,a,_ in mapping} == expected
        assert len({UUID(i) for _,_,i in mapping}) == len(mapping) == len(expected)
        assert len(rows['revision_provenance']) == len(before['revisions'])
        assert len(rows['operation_provenance']) == len(before['receipts'])
        for table in ('revision_provenance', 'operation_provenance'):
            assert db.execute(f'SELECT DISTINCT principal_id,agent_id FROM {table}').fetchall() == [(None,None)]
        assert not rows['operation_keys']
        reopened = SQLiteRepository(path, owner_schema5=True, configured_scope=CONFIG)
        for actor, item, op in (
            ('receipt-only', MemoryInput('payments retry policy', kind='decision', tags=['payments']), 'op-3'),
            ('peer', MemoryInput('e\u0301\r\n雪', kind='decision', references=(ReferenceInput('x', 'HEAD'),)), 'op-2'),
        ):
            assert MemoryService(reopened, Scope('a', actor)).record(item, 'key-0').operation_id == op
        assert snapshot(db, NEW) == rows
        assert snapshot(db, tables) == before


@pytest.mark.parametrize('boundary', [*(f'CREATE TABLE {t}' for t in NEW),
    *(f'INSERT INTO {t}' for t in NEW[:-1]), 'PRAGMA user_version = 5'])
def test_interrupted_005_rolls_back(tmp_path, monkeypatch, boundary):
    path = tmp_path / 'legacy'
    legacy(path)

    class Interrupt(sqlite3.Connection):
        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if sql.strip().startswith(boundary):
                raise RuntimeError(boundary)
            return result

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteRepository, '_connect', lambda self: sqlite3.connect(path, factory=Interrupt, isolation_level=None))
        with pytest.raises(RuntimeError, match=boundary):
            SQLiteRepository(path, owner_schema5=True)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone() == (4,)
        assert not set(NEW).intersection(t for t, in db.execute('SELECT name FROM sqlite_master'))
        SQLiteRepository(path, owner_schema5=True)
        assert db.execute('SELECT count(*) FROM store_identity').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM space_partitions').fetchone() == (5,)


def test_backup_is_restorable_with_wal(tmp_path):
    from nexus_memory.spaces.migration import backup_store
    path, backup = tmp_path / 'live-synthetic', tmp_path / 'restore'
    legacy(path)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES('a','local','wal','{}')")
        writer.commit()
        assert Path(str(path) + '-wal').stat().st_size > 32
        manifest = backup_store(path, backup)
    assert manifest.schema_version == 4 and manifest.store_id is None
    assert manifest.last_durable_seq == 2 and manifest.counts['outbox'] == 2
    assert manifest.integrity_check == ('ok',) and not manifest.foreign_key_check
    SQLiteRepository(path, owner_schema5=True)
    code = '''
import sqlite3, sys, json
from dataclasses import asdict
from nexus_memory.spaces.migration import store_manifest
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("SELECT operation_id FROM outbox WHERE durable_seq=2").fetchone() == ('wal',)
    print(json.dumps(asdict(store_manifest(db))))
'''
    reopened = subprocess.check_output([sys.executable, '-c', code, str(backup)], text=True)
    assert json.loads(reopened) == json.loads(json.dumps(asdict(manifest)))
    with pytest.raises(FileExistsError):
        backup_store(path, backup)
    latest = backup_store(path, tmp_path / 'upgraded-copy')
    with closing(sqlite3.connect(path)) as db:
        assert str(UUID(latest.store_id)) == db.execute('SELECT store_id FROM store_identity').fetchone()[0]
    assert latest.row_hashes['blobs'] == manifest.row_hashes['blobs']
