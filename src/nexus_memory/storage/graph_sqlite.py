"""Non-initializing, partition-checked access to an explicit schema-6 file."""
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3

from nexus_memory.domain.errors import SpaceNotMounted, UnsupportedSchema
from nexus_memory.graph.repository import Snapshot


class GraphRepository:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def transaction(self, binding, *, write):
        mode = 'rw' if write else 'ro'
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=' + mode, uri=True)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if db.execute('PRAGMA user_version').fetchone()[0] != 6:
                raise UnsupportedSchema('graph profile required')
            if not db.execute('SELECT 1 FROM space_partitions WHERE space_id=? AND store_id=? AND namespace=? AND actor=?',
                              (binding.space_id,binding.store_id,binding.scope.namespace,binding.scope.actor)).fetchone():
                raise SpaceNotMounted('space not mounted')
            yield db

    @contextmanager
    def open_snapshot(self, binding):
        with self.transaction(binding, write=False) as db:
            yield Snapshot(db, binding)
