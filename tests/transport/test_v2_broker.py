"""Exercise only fixture grants and disposable memory stores."""
from dataclasses import replace
from hashlib import sha256
import json
import sqlite3
from contextlib import closing
from time import monotonic

import anyio
from mcp import Client
import pytest

from graph_case import batch, op
from nexus_memory.domain.errors import PublicationBusy, OperationFailure
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.storage.sqlite import SQLiteRepository


def make_broker(tmp_path):
    from nexus_memory.transport.broker import Broker
    cat = Catalog(tmp_path/'catalog')
    binding = cat.create_space('test', 'research', principal_id='local', operation=op('space'))
    session = cat.launch_context('local', operation=op('context'))
    grant = cat.bind_session(session, 'local', binding.space_id, alias='here', mode='read_write', operation=op('bind'))
    _, path = cat._resolve(grant, None, False)
    SQLiteRepository(path, owner_schema6=True)
    return Broker(cat, grant)


def call(b, name, **args):
    env = b.dispatch(name,args)
    assert not isinstance(env,OperationFailure), (name,env)
    return json.loads(b.drain(env))


def test_every_v2_content_route_requires_admission(tmp_path):
    b = make_broker(tmp_path)
    with closing(sqlite3.connect(b.catalog.path)) as db:
        def admitted(name, **args):
            before, = db.execute('select count(*) from publications').fetchone()
            value = call(b,name,**args)
            assert db.execute('select count(*) from publications').fetchone()[0] == before+1
            return value
        args = dict(content='synthetic',idempotency_key='one')
        rec = admitted('record',**args)
        assert admitted('record',**args) == rec
        target = dict(memory_id=rec['memory_id'])
        admitted('get',**target); admitted('search',query='synthetic'); admitted('history',**target)
        rev = admitted('revise',**target,expected_revision_id=rec['revision_id'],content='two',idempotency_key='two')
        admitted('forget',**target,expected_revision_id=rev['revision_id'],idempotency_key='three')
        admitted('status')
        graph = admitted('curate',batch=json.loads(batch().to_json()),operation_key='graph')
        ref = graph['assertion_revisions'][0]
        draft = json.loads(batch().assertions[0].to_json())
        for side in ['subject','object']:
            draft[side]['entity_id'] = graph['id_mappings'][draft[side]['entity_id']]
        admitted('correct',assertion_id=ref['assertion_id'],expected_revision_id=ref['revision_id'],replacement=draft,operation_key='correct')
        pack = admitted('recall',query={'text':'synthetic'})
        admitted('traverse',query={'anchors':['a'],'pattern':[{'predicate':'CALLS','direction':'forward'}]})
        for name in ['explain','read_pack']:
            assert admitted(name,pack_id=pack['pack_id']) == pack


def test_control_errors_contain_no_source_data(tmp_path):
    b = make_broker(tmp_path)
    slot = b.queue.reserve(2**20,monotonic()+2)
    try:
        assert b.dispatch('status',{}).for_model() == {'code':'publication_busy','retryable':True}
    finally:
        b.queue.cancel(slot)
    with closing(sqlite3.connect(b.catalog.path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert b.dispatch('status',{}).for_model() == {'code':'authority_busy','retryable':True}
    b.fence.revoke(b.grant.grant_id,expected_epoch=b.grant.epoch,operation=op('revoke'))
    assert b.dispatch('status',{}).for_model() == {'code':'grant_revoked','retryable':False}


def test_real_mcp_serializes_only_admitted_bytes(tmp_path, monkeypatch):
    from nexus_memory.transport.mcp_server import create_v2_server
    b = make_broker(tmp_path)
    admitted = b.dispatch('status',{})
    monkeypatch.setattr(b,'dispatch',lambda *_:admitted)
    async def scenario():
        async with Client(create_v2_server(b)) as client:
            result = await client.call_tool('status',{})
            assert result.content[0].text.encode() == admitted.payload
            assert result.structured_content is None and not result.is_error
    anyio.run(scenario)
    with pytest.raises(Exception,match='digest'):
        replace(admitted,response_digest='0'*64)
    # A second real admission, owned by the same queue.
    monkeypatch.undo()
    env = b.dispatch('status',{})
    forged = replace(env,payload=b'{}',response_digest=sha256(b'{}').hexdigest())
    with pytest.raises(PublicationBusy):
        b.drain(forged)
    assert b.drain(env) == env.payload


def test_enrolled_launcher_v2_verifies_linked_checkout(tmp_path):
    import os
    import sys
    import subprocess
    from pathlib import Path
    from test_project_isolation_e2e import _repo, _git, _env, _client, LAUNCHER
    from nexus_memory.domain.models import Scope
    from nexus_memory.git import GitCli, bind_repository
    from nexus_memory.transport.harnesses.generic import register
    home = tmp_path/'home'; home.mkdir(mode=0o700)
    checkout = _repo(tmp_path/'repo')
    linked = tmp_path/'linked'; _git(checkout,'worktree','add','--detach',str(linked))
    project = subprocess.check_output([sys.executable,'-I',LAUNCHER,'enroll',checkout],cwd=checkout,
                                      env=_env(home,checkout),text=True).strip()
    path = tmp_path/'store'; scope = Scope('fixture','local')
    store = SQLiteRepository(path,owner_schema6=True,configured_scope=scope)
    bind_repository(store,scope,checkout,None,GitCli())
    catalog = Catalog(tmp_path/'catalog')
    binding, = catalog.register_store(path,operation=op('register'))
    grant = register(catalog,'local',binding.space_id,stream='fixture-launch',receiving_context='ctx')
    ticket = tmp_path/'ticket'; ticket.write_text(json.dumps({'grant_id':grant.grant_id,'project_id':project})); ticket.chmod(0o600)
    async def scenario():
        async with _client(home,linked,'codex',Path(sys.executable).with_name('nexus-memory'),
                           '--catalog',str(catalog.path),'--grant-file',str(ticket)) as client:
            status = await client.call_tool('status',{})
            assert not status.is_error and json.loads(status.content[0].text)['lifecycle']=='generic_explicit'
    anyio.run(scenario)


def test_generic_stdio_profile_and_grant_resume(tmp_path):
    import os
    import sys
    from mcp import StdioServerParameters
    from nexus_memory.transport.harnesses.generic import register
    b = make_broker(tmp_path)
    ctx = dict(stream='fixture-launcher',receiving_context='fixture-context')
    grant = register(b.catalog,'local',b.binding.space_id,**ctx)
    assert register(b.catalog,'local',b.binding.space_id,**ctx) == grant
    ticket = tmp_path/'ticket'
    ticket.write_text(json.dumps({'grant_id':grant.grant_id}))
    ticket.chmod(0o600)
    spec = StdioServerParameters(command=sys.executable,args=['-I','-m','nexus_memory','--catalog',str(b.catalog.path),
        '--grant-file',str(ticket)],cwd=tmp_path,env={'HOME':str(tmp_path),'PATH':os.defpath})
    async def scenario():
        async with Client(spec) as client:
            response = await client.call_tool('status',{})
            assert not response.is_error and json.loads(response.content[0].text)['lifecycle']=='generic_explicit'
            denied = await client.call_tool('record',{'idempotency_key':'k','content':'synthetic'})
            assert denied.structured_content == {'code':'space_read_only','retryable':False}
    anyio.run(scenario)
