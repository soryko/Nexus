"""Owner CLI contract; all identities, stores and credentials are disposable."""
import json
import sqlite3
from contextlib import closing

import pytest


@pytest.fixture
def owner(tmp_path, capsys):
    from nexus_memory.spaces import admin
    catalog = tmp_path / 'catalog'
    def run(command, key=None, request=None, *args, error=None):
        argv = command.split() + ['--catalog', str(catalog)]
        if key:
            argv += ['--operation-key', key]
        if request is not None:
            path = tmp_path / 'request'
            path.write_text(json.dumps(request) if isinstance(request, dict) else request)
            argv += ['--request', str(path)]
        status = admin.main(argv + list(map(str, args)))
        output = json.loads(capsys.readouterr().out)
        if error:
            assert status and output == {'error': error}
        else:
            assert not status, output
        return output
    run('catalog init', 'init')
    space = run('space create', 'create', {'name':'test','kind':'research'})
    run('store migrate', 'migrate', {'store_id':space['store_id']})
    return run, space, tmp_path


def test_admin_create_register_and_curate_from_json(owner):
    run, space, root = owner
    sid = space['space_id']
    assert run('space create', 'create', {'kind':'research','name':'test'}) == space
    store, = root.glob('space-*.sqlite')
    assert run('space register', 'reg', {'path':str(store)})['spaces'] == [space]
    from nexus_memory.spaces.migration import backup_store
    copy = root / 'copy'
    backup_store(store, copy)
    run('space register','duplicate',{'path':str(copy)},error='storage_integrity')
    run('store migrate','missing',{'store_id':'unknown'},error='storage_integrity')
    assert len(run('space list')['spaces']) == 1
    run('graph curate','bad','{',error='invalid_input')
    run('graph curate','big',' '*(2**20+1),error='invalid_input')
    def authorized(action,key,body):
        out = root / key
        preview = {'family':'graph '+action,'operation_key':key,'space_id':sid,'request':body,'exposed_space_ids':[sid]}
        issued = run('grant issue','issue-'+key,preview,'--out',out)
        assert out.stat().st_mode & 0o777 == 0o600
        assert json.loads(out.read_text())['secret'] not in json.dumps(issued)
        return ('graph '+action,key,body,'--space-id',sid,'--grant-file',out)
    endpoint = {'kind':'entity','entity_id':'a'}
    draft = {'subject':endpoint,'object':endpoint,'predicate':'CALLS'}
    args = authorized('curate','add',{'entities':[{'temp_id':'a','kind':'symbol','qualified_key':'a'}], 'assertions':[draft]})
    args[-1].chmod(0o644)
    run(*args,error='invalid_input')
    args[-1].chmod(0o600)
    saved = run(*args)
    assert run(*args) == saved
    ref, = saved['assertion_revisions']
    endpoint['entity_id'] = saved['id_mappings']['a']
    correction = {'assertion_id':ref['assertion_id'],'expected_revision_id':ref['revision_id'],'replacement':draft}
    edit = authorized('correct','edit',correction)
    run(*edit[:2],dict(correction,expected_revision_id='unapproved'),*edit[3:],error='flow_denied')
    revised = run(*edit)
    assert revised['assertion_revisions'][0]['revision_id'] != ref['revision_id']
    run(*authorized('correct','stale',correction),error='revision_conflict')
    change = {'action':'alias','expected_generation':0,'memberships':['alternate'],'target':endpoint['entity_id']}
    run(*authorized('resolve','alias',change))
    run(*authorized('resolve','old-generation',change),error='resolution_conflict')
    # Attach evidence to the returned revision, never a moving assertion or memory head.
    import hashlib
    from nexus_memory.domain.models import MemoryInput, Scope
    from nexus_memory.storage import SQLiteRepository
    exact = SQLiteRepository(store,owner_schema6=True)
    scope = Scope(**space['scope'])
    record = exact.record(scope,MemoryInput('note'),'save','synthetic')
    evidence = {'target':ref['revision_id'],'role':'context','source':{'kind':'memory','ref':{
        'space_id':sid,'memory_id':record.memory_id,'revision_id':record.revision_id}},
        'span':{'start':0,'end':4,'sha256':hashlib.sha256(b'note').hexdigest()}}
    association = run(*authorized('curate','attach',{'evidence':[evidence]}))
    assert association['operation_id'] != record.operation_id
    exact.revise(scope,record.memory_id,record.revision_id,MemoryInput('new note'),'revise','synthetic revision')
    run(*authorized('curate','old-source',{'evidence':[evidence]}),error='invalid_evidence')
    with closing(sqlite3.connect(store)) as db:
        assert db.execute('select count(*) from graph_assertion_revisions').fetchone() == (2,)
        assert db.execute('select distinct json_extract(content,\'$.origin_kind\') from graph_assertion_revisions').fetchall() == [('human_asserted',)]


def test_publication_approval_cannot_be_reused_with_new_operation(owner, monkeypatch):
    from nexus_memory.spaces import admin
    run, space, root = owner
    sid, out = space['space_id'], root / 'approval'
    now = [1000]
    monkeypatch.setattr(admin,'_now',lambda: now[0])
    preview = {'family':'graph curate','operation_key':'add','space_id':sid,'exposed_space_ids':[sid],'request':{}}
    run('grant issue','issue',preview,'--out',out)
    opts = ('--space-id',sid,'--grant-file',out)
    run('graph curate','other',{},*opts,error='flow_denied')
    run('graph curate','add',{'entities':[]},*opts,error='flow_denied')
    with monkeypatch.context() as patch:
        def crash(*_): raise RuntimeError('injected crash')
        patch.setattr(admin,'_record_graph_receipt',crash)
        with pytest.raises(RuntimeError,match='injected crash'):
            run('graph curate','add',{},*opts)
    now[0] += 120
    receipt = run('graph curate','add',{},*opts)
    assert receipt == run('graph curate','add',{},*opts)
    assert receipt['graph_event_seq'] == 1
    preview['operation_key'] = 'expired'
    run('grant issue','issue2',preview,'--out',root/'second')
    now[0] += 120
    run('graph curate','expired',{},'--space-id',sid,'--grant-file',root/'second',error='flow_denied')
    with closing(sqlite3.connect(next(root.glob('space-*.sqlite')))) as db:
        assert db.execute('select count(*) from graph_events').fetchone() == (1,)


def enroll_fixture(root, checkout, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    home = root / 'home'
    home.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv('HOME', str(home))
    launcher = Path(__file__).resolve().parents[2] / 'integrations/project_scope/launcher.py'
    subprocess.run([sys.executable, '-I', launcher, 'enroll', checkout], check=True, capture_output=True)


def test_owner_enrollment_restore_revocation_and_unsupported(owner, monkeypatch):
    import subprocess
    from nexus_memory.spaces.migration import backup_store
    run, _, root = owner
    checkout = root / 'checkout'
    subprocess.run(['git','init','-q',checkout],check=True)
    enroll_fixture(root, checkout, monkeypatch)
    binding = run('space create','project',{'name':'example','kind':'project','repo':str(checkout)})
    assert len(binding['repository_bindings']) == 1
    run('store migrate','project-schema',{'store_id':binding['store_id']})
    sid = binding['space_id']
    store = root / ('space-'+sid+'.sqlite')
    copy = root/'snapshot'
    backup_store(store,copy)
    restore = {'store_id':binding['store_id'],'path':str(copy)}
    assert run('store restore','restore',restore) == run('store restore','restore',restore)
    preview = {'family':'graph curate','operation_key':'apply','space_id':sid,'exposed_space_ids':[sid],'request':{}}
    out = root/'grant'
    out.write_text('keep')
    out.chmod(0o644)
    run('grant issue','issue',preview,'--out',out,error='invalid_input')
    assert out.read_text() == 'keep'
    with closing(sqlite3.connect(root/'catalog')) as db:
        assert db.execute('select count(*) from admin_approvals').fetchone() == (0,)
    private = root/'private'
    grant = run('grant issue','issue-ok',preview,'--out',private)
    assert grant == run('grant issue','issue-ok',preview,'--out',private)
    run('grant revoke','revoke',{'grant_id':grant['grant_id']})
    run('graph curate','apply',{},'--space-id',sid,'--grant-file',private,error='flow_denied')
    from nexus_memory.transport.harnesses.generic import resume
    from nexus_memory.domain.errors import GrantRevoked
    ticket = root/'ticket'
    mount = {'principal_id':'local','space_id':sid,'receiving_context':'test-host-context','mode':'read_only'}
    handle = run('grant issue','mount',mount,'--out',ticket)
    assert json.loads(ticket.read_text())['grant_id'] not in json.dumps(handle)
    assert resume(root/'catalog',ticket).binding.space_id == sid
    run('grant revoke','unmount',{'grant_id':handle['grant_id']})
    with pytest.raises(GrantRevoked):
        resume(root/'catalog',ticket)
    run('projection status',error='unsupported_capability')
    run('proposal review','later',{},error='unsupported_capability')


def test_approval_revoke_and_receipt_commit_together(owner, monkeypatch):
    from nexus_memory.spaces.catalog import Catalog
    run, space, root = owner
    sid = space['space_id']
    grant = run('grant issue', 'issue', {'family':'graph curate', 'operation_key':'write',
        'space_id':sid, 'exposed_space_ids':[sid], 'request':{}}, '--out', root/'grant')
    request = {'grant_id': grant['grant_id']}
    with monkeypatch.context() as patch:
        def interrupt(*args):
            raise RuntimeError('receipt interrupted')
        patch.setattr(Catalog, '_finish', staticmethod(interrupt))
        with pytest.raises(RuntimeError, match='receipt interrupted'):
            run('grant revoke', 'revoke', request)
    with closing(sqlite3.connect(root/'catalog')) as db:
        assert db.execute('SELECT revoked FROM admin_approvals').fetchone() == (0,)
        assert not db.execute("SELECT receipt FROM operations WHERE key='revoke' AND receipt IS NOT NULL").fetchall()
    receipt = run('grant revoke', 'revoke', request)
    assert receipt == {'revoked':grant['grant_id']} == run('grant revoke', 'revoke', request)
    with closing(sqlite3.connect(root/'catalog')) as db:
        assert db.execute('SELECT revoked FROM admin_approvals').fetchone() == (1,)
        assert json.loads(db.execute("SELECT receipt FROM operations WHERE key='revoke'").fetchone()[0]) == receipt


@pytest.mark.parametrize('collision', ['file', 'symlink', 'hardlink', 'sidecar'])
def test_unexpected_backup_refused_before_migration(owner, monkeypatch, collision):
    import os
    from nexus_memory.spaces import admin
    run, _, root = owner
    new = run('space create','new',{'name':'new','kind':'research'})
    store = root / ('space-'+new['space_id']+'.sqlite')
    before = store.read_bytes()
    req = {'store_id':new['store_id']}
    with monkeypatch.context() as patch:
        def interrupt(*args): raise RuntimeError('before backup')
        patch.setattr(admin, 'backup_store', interrupt)
        with pytest.raises(RuntimeError, match='before backup'):
            run('store migrate','upgrade',req)
    with closing(sqlite3.connect(root/'catalog')) as db:
        allocation, = db.execute("SELECT allocation FROM operations WHERE key='upgrade'").fetchone()
    backup = root / ('backup-'+allocation+'.sqlite')
    other = root/'unrelated'
    other.write_bytes(before)
    if collision == 'file': backup.write_bytes(before)
    elif collision == 'symlink': backup.symlink_to(other)
    elif collision == 'hardlink': os.link(other, backup)
    else: backup.with_name(backup.name+'-wal').write_text('unrelated')
    run('store migrate','upgrade',req,error='storage_integrity')
    assert store.read_bytes() == before == other.read_bytes()


def test_migration_requires_observed_schema_and_replays_verified_backup(owner, monkeypatch):
    from nexus_memory.spaces import admin
    run, _, root = owner
    new = run('space create','new',{'name':'new','kind':'research'})
    req = {'store_id':new['store_id']}
    with monkeypatch.context() as patch:
        patch.setattr(admin,'SQLiteRepository',lambda *a, **kw: None)
        run('store migrate','upgrade',req,error='storage_integrity')
    result = run('store migrate','upgrade',req)
    with closing(sqlite3.connect(root/('space-'+new['space_id']+'.sqlite'))) as db:
        assert result['schema_version'] == db.execute('PRAGMA user_version').fetchone()[0] == 6
    assert run('store migrate','upgrade',req) == result


def test_project_create_uses_enrolled_worktree_identity(owner, monkeypatch):
    import subprocess
    run, _, root = owner
    main = root/'main'
    subprocess.run(['git','init','-q',main],check=True)
    enroll_fixture(root, main, monkeypatch)
    subprocess.run(['git','-C',main,'-c','user.name=test','-c','user.email=test@example.invalid',
                    'commit','-q','--allow-empty','-m','fixture'],check=True)
    linked = root/'linked'
    subprocess.run(['git','-C',main,'worktree','add','-q','--detach',linked],check=True)
    def create(path, **kw):
        return run('space create',path.name,{'name':'project','kind':'project','repo':str(path)}, **kw)
    assert create(linked)['repository_bindings']
    misplaced = root/'misplaced'
    misplaced.mkdir()
    (misplaced/'.git').write_bytes((linked/'.git').read_bytes())
    create(misplaced,error='invalid_input')
    relocated = root/'relocated'
    assert main.resolve() == root.resolve()/'main' and not relocated.exists()
    main.rename(relocated)
    create(relocated,error='invalid_input')
