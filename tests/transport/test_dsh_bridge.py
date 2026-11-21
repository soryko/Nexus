"""NDJSON executable contract with a real, disposable catalog grant."""
from contextlib import closing
import json
import os
import select
from pathlib import Path
import sqlite3
import subprocess
import sys

from graph_case import op
from test_v2_broker import make_broker


def launch_args(tmp_path, broker, grant=None):
    ticket = tmp_path / 'ticket'
    ticket.write_text(json.dumps({'grant_id': (grant or broker.grant).grant_id}))
    ticket.chmod(0o600)
    exe = Path(sys.executable).with_name('nexus-memory-bridge')
    return dict(args=[exe, '--catalog', broker.catalog.path, '--grant-file', ticket],
                cwd=tmp_path, env={'HOME': str(tmp_path), 'PATH': os.defpath,
                                   'PYTHONDONTWRITEBYTECODE': '1'})


def frame(method, params, **extra):
    return json.dumps(dict(v=1, id='i', method=method, params=params, **extra),
                      ensure_ascii=False).encode() + b'\n'


def test_installed_bridge(tmp_path):
    broker = make_broker(tmp_path)
    with subprocess.Popen(**launch_args(tmp_path, broker),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
        def call(method, params, **extra):
            child.stdin.write(frame(method, params, **extra)); child.stdin.flush()
            assert select.select([child.stdout], [], [], 10)[0], 'bridge response timed out'
            return json.loads(child.stdout.readline())
        request = dict(content=' \t雪 🧠\r\n ',category='preference',source='user',entities=['雪'],importance=3)
        key = dict(operation_key='same')
        try:
            assert call('capabilities',{})['result']['remember']
            assert not call('remember',request)['ok']
            assert call('reconcile',request,**key)['result'] == {'state':'absent_at_snapshot'}
            receipt = call('remember',request,**key)['result']
            assert call('remember',request,**key)['result'] == receipt
            assert call('reconcile',request,**key)['result'] == {'state':'committed','receipt':receipt}
            assert call('get',{'memory_id':receipt['memory_id']})['result']['content'] == request['content']
            assert not call('get',{'memory_id':receipt['memory_id'],'space':'elsewhere'})['ok']
            with sqlite3.connect(broker.path) as db:
                meta, = db.execute('select compat_metadata_json from revision_provenance').fetchone()
                assert json.loads(meta) == {k:v for k,v in request.items() if k!='content'}
            broker.fence.revoke(broker.grant.grant_id,expected_epoch=broker.grant.epoch,operation=op('revoke'))
            assert call('get',{'memory_id':receipt['memory_id']})['error']['code'] == 'grant_revoked'
            assert call('remember',request,operation_key='other')['error']['code'] == 'grant_revoked'
            for revoked_key in ('same', 'other'):
                assert call('reconcile',request,operation_key=revoked_key)['result'] == {'state':'unknown'}
        finally:
            child.stdin.close()


def test_raw_frames_and_byte_limit(tmp_path):
    args = launch_args(tmp_path, make_broker(tmp_path))
    limit = 1024 * 1024  # Wire bytes, including the newline.
    good = frame('capabilities', {})
    at_limit = b' ' * (limit - len(good)) + good
    ambiguous = b'{"v":1,"id":"i","method":"status","method":"capabilities","params":{}}\n'
    run = subprocess.run(**args, input=b'not-json\n\xff\n' + ambiguous + at_limit,
                         capture_output=True, timeout=10, check=True)
    malformed, utf8, duplicate, accepted = map(json.loads, run.stdout.splitlines())
    for reply in (malformed, utf8, duplicate):
        assert reply['ok'] is False and reply['error']['code'] == 'invalid_input'
    assert accepted['result']['remember'] is True and run.stderr == b''
    for invalid in (b' ' + at_limit, good[:-1]):
        run = subprocess.run(**args, input=invalid, capture_output=True, timeout=10)
        assert (run.returncode, run.stdout, run.stderr) == (2, b'', b'')


def test_restart_reconciles_without_resubmission(tmp_path):
    broker = make_broker(tmp_path)
    args = launch_args(tmp_path, broker)
    params = {'content': ' \te\u0301 雪 🧠\r\n '}
    key = {'operation_key': 'synthetic'}
    # Lose delivery, not the commit. The new process cannot consult the old reply.
    subprocess.run(**args, input=frame('remember', params, **key), stdout=subprocess.DEVNULL,
                   check=True, timeout=10)
    with closing(sqlite3.connect(broker.path)) as db:
        version = db.execute('pragma data_version').fetchone()
        run = subprocess.run(**args, input=frame('reconcile', params, **key)
                             + frame('reconcile', {'content':'different'}, **key)
                             + frame('reconcile', params, operation_key='never-sent'),
                             capture_output=True, check=True, timeout=10)
        committed, conflict, absent = map(json.loads, run.stdout.splitlines())
        assert committed['result']['state'] == 'committed'
        assert conflict['error']['code'] == 'idempotency_conflict'
        assert absent['result'] == {'state':'absent_at_snapshot'}
        assert db.execute('pragma data_version').fetchone() == version
        retry = subprocess.run(**args, input=frame('remember', params, **key),
                               capture_output=True, check=True, timeout=10)
        assert json.loads(retry.stdout)['result'] == committed['result']['receipt']


def test_read_only_grant_reconciliation_never_changes_store(tmp_path):
    broker = make_broker(tmp_path)
    session = broker.catalog.launch_context('local', operation=op('reader-context'))
    reader = broker.catalog.bind_session(session, 'local', broker.binding.space_id,
                                         alias='here', mode='read_only', operation=op('reader-bind'))
    params = {'content': 'synthetic'}
    key = {'operation_key': 'unseen'}
    query = frame('reconcile', params, **key)
    with closing(sqlite3.connect(broker.path)) as db:
        version = db.execute('pragma data_version').fetchone()
        run = subprocess.run(**launch_args(tmp_path, broker, reader), capture_output=True,
                             input=frame('capabilities', {}) + query + frame('remember', params, **key) + query,
                             check=True, timeout=10)
        capability, before, write, after = map(json.loads, run.stdout.splitlines())
        assert capability['result']['remember'] is False
        assert before['result'] == after['result'] == {'state': 'absent_at_snapshot'}
        assert not write['ok']
        assert db.execute('pragma data_version').fetchone() == version


def test_native_receipt_requires_current_viewer_authority(tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    import pytest
    from nexus_memory.atlas.graph_queries import GraphAtlasQueries
    from nexus_memory.atlas.server import AtlasServer
    from test_v2_broker import call

    b = make_broker(tmp_path)
    r = call(b, 'record', content='first', idempotency_key='one')
    call(b, 'revise', memory_id=r['memory_id'], expected_revision_id=r['revision_id'], content='second', idempotency_key='two')
    with AtlasServer(graph=GraphAtlasQueries([b])) as atlas:
        thread = threading.Thread(target=atlas.serve_forever, daemon=True); thread.start()
        try:
            url = f"{atlas.public_address}api/v2/memories/{r['memory_id']}/revisions/{r['revision_id']}?space={b.binding.space_id}"
            with pytest.raises(HTTPError) as failure:
                urlopen(url,timeout=3)
            assert failure.value.code == 401
            req = Request(url, headers={'Cookie':atlas.exchange(atlas.launch_secret).split(';')[0]})
            with urlopen(req,timeout=3) as res:
                data = json.load(res)
                assert data['content'] == 'first' and data['references'] == []
            b.fence.revoke(b.grant.grant_id,expected_epoch=b.grant.epoch,operation=op('revoke'))
            with pytest.raises(HTTPError) as failure:
                urlopen(req,timeout=3)
            assert failure.value.code == 403
        finally:
            atlas.shutdown(); thread.join()
