"""Disposable, explicitly opted-in graph store."""
import sqlite3
from nexus_memory.domain.models import Scope
from nexus_memory.domain.spaces import SpaceBinding, OperationRef
from nexus_memory.domain.graph import CurationBatch, EntityDraft, AssertionDraft, EntityRef
from nexus_memory.storage.sqlite import SQLiteRepository


def case(tmp_path):
    from nexus_memory.graph.service import GraphService
    from nexus_memory.storage.graph_sqlite import GraphRepository
    path = tmp_path / 'synthetic.db'
    scope = Scope('synthetic', 'local')
    SQLiteRepository(path, owner_schema6=True, configured_scope=scope)
    with sqlite3.connect(path) as db:
        space, store = db.execute('SELECT space_id,store_id FROM space_partitions').fetchone()
    repo = GraphRepository(path)
    binding = SpaceBinding(space, store, scope, ())
    return GraphService(repo, binding), repo, binding


def op(key):
    return OperationRef('synthetic-stream', key)


def batch():
    return CurationBatch((EntityDraft('a','symbol','a'), EntityDraft('b','symbol','b')),
                         (AssertionDraft(EntityRef('a'),'CALLS',EntityRef('b'),temp_id='edge'),))
