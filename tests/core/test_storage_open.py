import sqlite3
from contextlib import closing

import pytest

from nexus_memory.storage import SQLiteRepository


@pytest.mark.parametrize(('index', 'history'), [('exact', 'none'), ('stem', 'all'), ('dual', 'window3')])
def test_current_open_does_not_need_writer_lock(tmp_path, index, history):
    path = tmp_path / 'store.db'
    SQLiteRepository(path, index, history)
    with closing(sqlite3.connect(path)) as writer:
        # No timing threshold: a second writer cannot succeed until this block ends.
        writer.execute('BEGIN IMMEDIATE')
        reopened = SQLiteRepository(path, index, history)
        with closing(reopened._connect()) as db:
            assert db.execute('PRAGMA journal_mode').fetchone() == ('wal',)
            assert db.execute('PRAGMA synchronous').fetchone() == (2,)  # FULL


def test_current_profiles_still_repair_missing_historical_index(tmp_path):
    path = tmp_path / 'store.db'
    SQLiteRepository(path, 'stem', 'all')
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute('DROP TABLE revision_fts')
        SQLiteRepository(path, 'stem', 'all')
        sql, = db.execute("SELECT sql FROM sqlite_master WHERE name='revision_fts'").fetchone()
        assert 'porter unicode61' in sql
