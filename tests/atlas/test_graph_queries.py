"""Exercise a bound broker, never an owner store."""
import json

import pytest

from graph_case import batch, op
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.storage.sqlite import SQLiteRepository
from nexus_memory.transport.broker import Broker


def mounted(tmp_path):
    cat = Catalog(tmp_path/'catalog')
    space = cat.create_space('visible', 'research', principal_id='local', operation=op('visible'))
    cat.create_space('hidden', 'research', principal_id='local', operation=op('hidden'))
    session = cat.launch_context('local', operation=op('session'))
    grant = cat.bind_session(session,'local',space.space_id,alias='here',mode='read_write',operation=op('grant'))
    _, path = cat._resolve(grant,None,False)
    SQLiteRepository(path,owner_schema6=True)
    return Broker(cat,grant)


def test_permitted_pack_and_exact_revision_only(tmp_path):
    b = mounted(tmp_path)
    graph = json.loads(b.drain(b.dispatch('curate', {'batch':json.loads(batch().to_json()),'operation_key':'seed'})))
    ref = graph['assertion_revisions'][0]
    pack = json.loads(b.drain(b.dispatch('recall', {'query':{'text':'Synthetic'}})))
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    q = GraphAtlasQueries([b]); sid = b.binding.space_id
    assert [s['id'] for s in q.spaces()] == [sid]
    assert q.pack(sid,pack['pack_id']) == pack
    assert q.assertion(sid, ref['assertion_id'],ref['revision_id'])['assertion']['ref'] == ref
    with pytest.raises(KeyError): q.pack('hidden',pack['pack_id'])
    b.fence.revoke(b.grant.grant_id,expected_epoch=b.grant.epoch,operation=op('revoke'))
    assert q.spaces() == []
    with pytest.raises(PermissionError): q.pack(sid,pack['pack_id'])


def mounted_lease(tmp_path):
    from types import SimpleNamespace
    from fixtures.v2.lease_case import lease_case
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    from nexus_memory.transport.broker import Broker
    case = lease_case(tmp_path)
    cat = Catalog(tmp_path/'catalog')
    # Complete the fixture's sentinel partition inventory before owner registration.
    import sqlite3
    with sqlite3.connect(case.repo.path) as db:
        db.execute('INSERT INTO space_partitions VALUES(?,?,?,?)',(case.binding.store_id,'unmounted','local','hidden-sentinel'))
    cat.register_store(case.repo.path, expected_store_id=case.binding.store_id,operation=op('register'))
    session = cat.launch_context('local',operation=op('session'))
    sid = case.binding.space_id
    grant = cat.bind_session(session,'local',sid,alias='here',mode='read_only',operation=op('mount'))
    b = Broker(cat,grant,checkout=SimpleNamespace(repository=case.bound,resolved_commit=case.R))
    handle = json.loads(b.drain(b.dispatch('recall',{'query':{'anchors':['renew_lease'],'intent':'compound'}})))['pack_id']
    return case, b, handle


def test_pinned_supported_path_from_enrolled_fixture(tmp_path):
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    case, b, handle = mounted_lease(tmp_path)
    sid = b.binding.space_id
    q = GraphAtlasQueries([b]); pack = q.pack(sid,handle)
    edges = pack['bundles'][0]['path']
    assert [edge['step']['direction'] for edge in edges] == ['forward','forward','inverse','forward']
    assert [edge['claim']['revision_id'] for edge in edges] == [case.refs[k].revision_id for k in ('implements','motivation','regression','definition')]
    assert 'synthetic sentinel X' not in json.dumps(pack)
    assert q.spaces() == [dict(id=sid,label=case.binding.scope.namespace,mode='read_only',default=False)]
