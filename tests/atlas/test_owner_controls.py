import threading
import sqlite3
from contextlib import closing
from functools import partial

from test_http import Client
from test_graph_queries import mounted


def test_viewer_cookie_cannot_mutate(tmp_path):
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    from nexus_memory.atlas.owner import AtlasOwner
    from nexus_memory.atlas.server import AtlasServer
    b = mounted(tmp_path)
    server = AtlasServer(graph=GraphAtlasQueries([b]), owner=AtlasOwner(b.catalog.path))
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        c = Client(server); c.sign_in()
        path = '/api/v2/owner/curate'; origin = server.public_address.rstrip('/')
        assert c.request('POST',path,origin=origin,body={})[0] == 403
        status, headers, _ = c.request('POST','/session',origin=origin,body={'key':server.owner_key})
        assert status == 204 and 'HttpOnly' in headers['set-cookie'] and 'SameSite=Strict' in headers['set-cookie']
        c.cookie = headers['set-cookie'].split(';')[0]
        csrf = {'X-Atlas-CSRF': c.get_json('/api/v2/spaces')[1]['csrf']}
        def snapshot():
            with closing(sqlite3.connect(b.catalog.path)) as catalog, closing(sqlite3.connect(b.path)) as graph:
                return tuple(catalog.iterdump()), tuple(graph.iterdump())
        baseline = snapshot()
        for kw in [dict(origin='http://localhost:1',headers=csrf), dict(origin=origin,host='foreign',headers=csrf),dict(origin=origin)]:
            assert c.request('POST',path,body={},**kw)[0] == 403
        assert c.request('GET',path,headers=csrf)[0] == 405
        assert c.request('POST',path,origin=origin,headers={**csrf,'Content-Length':str(1048577)},body={})[0] == 413
        server.owner.expires = 0
        assert c.request('POST',path,origin=origin,headers=csrf,body={})[0] == 403
        assert snapshot() == baseline
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_owner_preview_binds_complete_command_and_pin(tmp_path):
    import json
    import pytest
    from graph_case import batch
    from nexus_memory.spaces.admin import initialize
    from nexus_memory.atlas.owner import AtlasOwner
    from nexus_memory.domain.errors import FlowDenied, RevisionConflict
    b = mounted(tmp_path); initialize(b.catalog)
    owner = AtlasOwner(b.catalog.path)
    body = dict(family='curate',space=b.binding.space_id,operation_key='one',request=json.loads(batch().to_json()))
    command = partial(owner.command,grant=b.grant)
    def preview(body): return command('preview',body)['approval']
    with pytest.raises(FlowDenied): command('curate',{**body,'operation_key':'other','approval':preview(body)})
    receipt = command('curate',{**body,'approval':preview(body)})
    draft = body['request']['assertions'][0]
    for side in ('subject','object'): draft[side]['entity_id'] = receipt['id_mappings'][draft[side]['entity_id']]
    ref = receipt['assertion_revisions'][0]
    correction = dict(family='correct',space=body['space'],operation_key='two',request=dict(assertion_id=ref['assertion_id'],expected_revision_id=ref['revision_id'],replacement=draft))
    command('correct',{**correction,'approval':preview(correction)})
    correction['operation_key']='three'
    with pytest.raises(RevisionConflict): command('correct',{**correction,'approval':preview(correction)})


def test_typed_scoped_failures(tmp_path, monkeypatch):
    import json
    from graph_case import batch, op
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    from nexus_memory.atlas.owner import AtlasOwner
    from nexus_memory.atlas.server import AtlasServer
    from nexus_memory.domain.errors import OperationFailure
    from nexus_memory.spaces.admin import initialize
    b = mounted(tmp_path); initialize(b.catalog)
    server = AtlasServer(graph=GraphAtlasQueries([b]), owner=AtlasOwner(b.catalog.path))
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        c = Client(server); origin = server.public_address.rstrip('/')
        _, h, _ = c.request('POST','/session',origin=origin,body={'key':server.owner_key})
        c.cookie = h['set-cookie'].split(';')[0]
        csrf = {'X-Atlas-CSRF': c.get_json('/api/v2/spaces')[1]['csrf']}
        sid = b.binding.space_id
        body = dict(family='curate', space=sid, operation_key='test', request=json.loads(batch().to_json()))
        def post(action, body):
            status, _, raw = c.request('POST','/api/v2/owner/'+action, origin=origin, headers=csrf, body=body)
            return status, json.loads(raw)
        status, preview = post('preview', body); assert status == 200
        token, digest, _ = server.owner.pending
        server.owner.pending = token, digest, 0
        assert post('curate', dict(body, approval=preview['approval'])) == (403, {'error':'approval_invalid'})
        path = '/api/v2/packs/absent?space='+sid
        assert c.get_json(path) == (409, {'error':'stale_pack'})
        with monkeypatch.context() as m:
            m.setattr(b, 'dispatch', lambda *a: OperationFailure('publication_busy', True))
            assert c.get_json(path) == (503, {'error':'request_unavailable'})
        b.fence.revoke(b.grant.grant_id, expected_epoch=b.grant.epoch, operation=op('revoke'))
        assert post('preview',body) == c.get_json(path) == (403, {'error':'scope_lost'})
    finally:
        server.shutdown(); server.server_close(); thread.join()
