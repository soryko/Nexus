"""Stage B bridge: a folder-enrolled repo's memory lives in the owner catalog, behind the fence.

The first host launch creates the repo's project space (recall only), each launch gets its own
grant, owner disable reaches open sessions, and status reports the effective mode. Every test
uses a temporary HOME, disposable checkouts and a real server process; no host instructions.
"""
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client, StdioServerParameters
from test_project_scope_v2 import ENTRY, repo, stand_in_runtime
from nexus_memory.transport.codex import WITHDRAWN_LINE

SERVER = Path(sys.executable).with_name('nexus-memory')


@pytest.fixture
def owner(tmp_path):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    work = (tmp_path / 'work').resolve()
    base = home / '.local/share/nexus-memory/project-scopes-v1'

    def run(*args, cwd=None):
        return subprocess.run([sys.executable, '-I', ENTRY, *map(str, args)], cwd=cwd or tmp_path, text=True,
                              capture_output=True, env={'HOME': str(home), 'PATH': os.environ['PATH']}, check=False)

    def status(checkout):
        result = run('status', checkout)
        return result.returncode, json.loads(result.stdout)

    def catalog(sql, *params):
        with closing(sqlite3.connect(base / 'catalog.sqlite3')) as db:
            return db.execute(sql, params).fetchall()

    work.mkdir()
    assert run('approve-folder', work).returncode == 0
    return SimpleNamespace(home=home, work=work, base=base, run=run, status=status, catalog=catalog,
                           runtime=stand_in_runtime(tmp_path / 'bin'), tmp=tmp_path)


def launched(result):
    assert result.returncode == 0, result.stderr
    args = result.stdout.split()
    return dict(zip(args[::2], args[1::2]))


def client(owner, checkout, role='claude'):
    return Client(StdioServerParameters(command=sys.executable, args=['-I', str(ENTRY), role, str(SERVER)], cwd=checkout,
                                        env={'HOME': str(owner.home), 'PATH': os.environ['PATH'], 'PWD': str(checkout)}))


async def call(session, tool, **arguments):
    result = await session.call_tool(tool, arguments)
    return result.structured_content if result.is_error else json.loads(result.content[0].text)


def test_first_opens_create_one_recall_only_space_and_a_grant_per_session(owner):
    api = repo(owner.work / 'api')
    procs = [subprocess.Popen([sys.executable, '-I', ENTRY, 'claude', owner.runtime], cwd=api, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={'HOME': str(owner.home), 'PATH': os.environ['PATH']}) for _ in range(6)]
    launches = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        launches.append(launched(subprocess.CompletedProcess(proc.args, proc.returncode, out, err)))
    assert len({a['--project-id'] for a in launches}) == 1
    assert {a['--catalog'] for a in launches} == {str(owner.base / 'catalog.sqlite3')}
    assert owner.catalog("SELECT kind,ceiling FROM spaces") == [('project', 'recall')]
    grants = [Path(a['--grant-file']) for a in launches]
    assert len(set(grants)) == 6
    for grant in grants:
        assert grant.stat().st_mode & 0o777 == 0o600
        assert json.loads(grant.read_text())['project_id'] == launches[0]['--project-id']
    sessions = owner.catalog("SELECT mode,role FROM sessions WHERE grant_id IS NOT NULL")
    assert sessions == [('read_only', 'root')] * 6
    assert not list((owner.base / 'sessions').glob('*.request'))      # request files are not left behind


def test_a_catalog_repo_is_refused_where_the_fence_does_not_reach(owner):
    api = repo(owner.work / 'api')
    launched(owner.run('claude', owner.runtime, cwd=api))
    assert owner.run('antigravity', owner.runtime, cwd=api).returncode != 0
    # An explicit scoped profile cannot redirect a catalog repo to another catalog or grant.
    other = owner.tmp / 'other-catalog'
    assert owner.run('codex', owner.runtime, '--catalog', other, '--grant-file', other, cwd=api).returncode != 0
    # In-process callers (the Antigravity gateway, Atlas) get no project unless they ask for the catalog.
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from project_scope import resolve, ScopeError\n'
            'try: resolve(__import__("pathlib").Path(sys.argv[2]))\n'
            'except ScopeError as e: print("refused", e)')
    probe = subprocess.run([sys.executable, '-I', '-c', code, str(ENTRY.parent), str(api)], check=False,
                           env={'HOME': str(owner.home), 'PATH': os.environ['PATH']}, capture_output=True, text=True)
    assert probe.stdout.startswith('refused'), probe.stderr


def test_an_explicit_enroll_keeps_the_version_1_layout(owner):
    legacy = repo(owner.tmp / 'elsewhere' / 'tool')
    project = owner.run('enroll', legacy).stdout.strip()
    args = launched(owner.run('claude', owner.runtime, cwd=legacy))
    assert args['--namespace'] == project and '--catalog' not in args
    code, report = owner.status(legacy)
    assert code == 0 and report['store'] == 'legacy'


def test_recall_only_maintain_disable_and_enable_against_a_real_server(owner):
    api = repo(owner.work / 'api')

    async def scenario():
        async with client(owner, api) as first:
            assert (await call(first, 'status'))['lifecycle'] == 'generic_explicit'
            assert not list((owner.base / 'sessions').glob('*.grant'))           # consumed once read
            refused = await call(first, 'record', idempotency_key='k1', content='synthetic kestrel decision')
            assert refused == {'code': 'space_read_only', 'retryable': False}      # recall only by default
        assert owner.status(api)[1]['mode'] == 'Recall only'
        assert owner.run('mode', api, 'maintain').returncode == 0
        assert owner.status(api)[1]['mode'] == 'Recall + maintain'
        async with client(owner, api) as writer, client(owner, api) as reader:
            saved = await call(writer, 'record', idempotency_key='k2', content='synthetic kestrel decision')
            assert 'memory_id' in saved
            assert (await call(reader, 'search', query='kestrel'))['hits']
            # Lowering to recall narrows the writer without a restart: it keeps reading, and cannot write.
            assert owner.run('mode', api, 'recall').returncode == 0
            assert (await call(writer, 'search', query='kestrel'))['hits']
            narrowed = await call(writer, 'record', idempotency_key='k2b', content='after lowering')
            assert narrowed == {'code': 'space_read_only', 'retryable': False}
            disabled = owner.run('disable', api)
            assert disabled.returncode == 0, disabled.stderr
            # Both open sessions lose access at once, without a restart.
            assert (await call(reader, 'search', query='kestrel'))['code'] == 'grant_revoked'
            assert (await call(writer, 'record', idempotency_key='k3', content='after disable'))['code'] == 'grant_revoked'
        code, report = owner.status(api)
        assert code == 3 and report['mode'] == 'Off'
        assert owner.run('claude', owner.runtime, cwd=api).returncode != 0
        assert owner.run('enable', api).returncode == 0
        assert owner.status(api)[1]['mode'] == 'Recall only'                  # never back to maintain
        async with client(owner, api) as again:
            assert (await call(again, 'search', query='kestrel'))['hits']        # history survives
            refused = await call(again, 'record', idempotency_key='k4', content='synthetic')
            assert refused == {'code': 'space_read_only', 'retryable': False}
    anyio.run(scenario)


def test_status_names_the_effective_mode(owner):
    api = repo(owner.work / 'api')
    code, report = owner.status(api)
    assert code == 3 and report['state'] == 'eligible' and report['mode'] == 'Recall only (on first open)'
    launched(owner.run('claude', owner.runtime, cwd=api))
    code, report = owner.status(api)
    assert code == 0 and report['store'] == 'catalog' and report['mode'] == 'Recall only'
    assert owner.run('mode', owner.tmp, 'maintain').returncode != 0          # not a repo
    # A ceiling turned off in the catalog alone (an interrupted disable) still reads as off.
    with closing(sqlite3.connect(owner.base / 'catalog.sqlite3')) as db, db:
        db.execute("UPDATE spaces SET ceiling='off'")
    code, report = owner.status(api)
    assert code == 3 and report['mode'] == 'Off'
    assert owner.run('claude', owner.runtime, cwd=api).returncode != 0


def test_disable_still_refuses_new_sessions_when_the_catalog_is_unreachable(owner):
    api = repo(owner.work / 'api')
    launched(owner.run('claude', owner.runtime, cwd=api))
    catalog = owner.base / 'catalog.sqlite3'
    catalog.chmod(0o644)                                  # the admin refuses a catalog others can read
    result = owner.run('disable', api)
    assert result.returncode == 1 and 'open sessions were not stopped' in result.stderr
    assert owner.status(api)[1]['state'] == 'disabled'
    catalog.chmod(0o600)
    assert owner.run('disable', api).returncode == 0      # a retry reaches the catalog
    assert owner.catalog("SELECT ceiling FROM spaces") == [('off',)]


def test_a_delegated_session_reads_only_even_when_the_repo_is_maintained(owner, tmp_path):
    api = repo(owner.work / 'api')
    args = launched(owner.run('claude', owner.runtime, cwd=api))
    owner.run('mode', api, 'maintain')
    request = tmp_path / 'request'
    request.write_text(json.dumps({'project_id': args['--project-id'], 'checkout': str(api), 'host': 'dsh',
                                   'role': 'delegated'}))
    out = tmp_path / 'grant'
    admin = subprocess.run([owner.runtime.with_name('nexus-memory-admin'), 'project', 'open', '--catalog',
                            args['--catalog'], '--request', request, '--operation-key', 'sub', '--out', out],
                           env={'HOME': str(owner.home), 'PATH': os.environ['PATH']}, capture_output=True, text=True,
                           check=False)
    assert json.loads(admin.stdout)['mode'] == 'read_only', admin.stdout
    assert owner.catalog("SELECT mode FROM sessions WHERE role='delegated'") == [('read_only',)]


def hooks_client(owner, checkout, role='claude-hooks'):
    return Client(StdioServerParameters(command=sys.executable, args=['-I', str(ENTRY), role, str(SERVER)], cwd=checkout,
                                        env={'HOME': str(owner.home), 'PATH': os.environ['PATH'], 'PWD': str(checkout),
                                             'NEXUS_CAPTURE_POLICY': 'explicit'}))


async def hook(session, tool, checkout, **arguments):
    result = await session.call_tool(tool, {'cwd': str(checkout), 'session_id': 's1', **arguments})
    assert not result.is_error, result.content
    return result.structured_content


PROMPT = 'what did we decide about {} and the staging path?'


def note(term):
    return f'Decision on {term}: staging retention window. The {term} staging path stays bounded.'


def test_hook_recall_reads_through_the_fence_and_stops_on_disable(owner):
    api, web = repo(owner.work / 'api'), repo(owner.work / 'web')

    async def save(checkout, term):
        assert owner.run('claude', owner.runtime, cwd=checkout).returncode == 0     # first open creates the space
        assert owner.run('mode', checkout, 'maintain').returncode == 0
        async with client(owner, checkout) as writer:
            saved = await call(writer, 'record', idempotency_key='k-' + term, content=note(term))
        assert owner.run('mode', checkout, 'recall').returncode == 0
        return saved['memory_id']

    async def scenario():
        mine = await save(api, 'kestrelx')
        theirs = await save(web, 'osprayx')
        async with hooks_client(owner, api) as session:
            cue = await hook(session, 'nexus_session_start', api)
            assert 'recall-only' in cue['hookSpecificOutput']['additionalContext']
            offered = await hook(session, 'nexus_recall', api, prompt=PROMPT.format('kestrelx'))
            context = offered['hookSpecificOutput']['additionalContext']
            assert f'memory_id={mine}' in context and 'retention window' not in context   # identifiers, never stored text
            # The other repo's memory is never offered here, even for its own distinctive term.
            other = await hook(session, 'nexus_recall', api, prompt=PROMPT.format('osprayx'))
            assert theirs not in json.dumps(other)
            assert owner.run('disable', api).returncode == 0
            withdrawn = await hook(session, 'nexus_recall', api, prompt=PROMPT.format('kestrelx'))
            assert withdrawn == {'systemMessage': WITHDRAWN_LINE}
            assert await hook(session, 'nexus_recall', api, prompt=PROMPT.format('kestrelx')) == {}  # said once
        async with hooks_client(owner, web, role='codex') as codex:
            offered = await hook(codex, 'codex_recall', web, prompt=PROMPT.format('osprayx'))
            assert f'memory_id={theirs}' in offered['hookSpecificOutput']['additionalContext']
            assert mine not in json.dumps(await hook(codex, 'codex_recall', web, prompt=PROMPT.format('kestrelx')))
    anyio.run(scenario)


def test_revoking_a_folder_ends_only_its_repos_open_sessions(owner):
    api = repo(owner.work / 'api')
    other_folder = (owner.tmp / 'other').resolve()
    kept = repo(other_folder / 'kept')
    assert owner.run('approve-folder', other_folder).returncode == 0

    async def scenario():
        async with client(owner, api) as gone, client(owner, kept) as stays:
            assert 'hits' in await call(gone, 'search', query='anything')
            revoked = owner.run('revoke-folder', owner.work)
            assert revoked.returncode == 0, revoked.stderr
            assert (await call(gone, 'search', query='anything'))['code'] == 'grant_revoked'
            assert 'hits' in await call(stays, 'search', query='anything')
        assert owner.run('claude', owner.runtime, cwd=api).returncode != 0
        # A retry after the folder is gone still sweeps, and says the folder was not approved.
        again = owner.run('revoke-folder', owner.work)
        assert again.returncode == 1 and 'not approved' in again.stderr
        assert owner.run('approve-folder', owner.work).returncode == 0
        async with client(owner, api) as back:                 # the space and its ceiling are unchanged
            assert 'hits' in await call(back, 'search', query='anything')
        assert owner.status(api)[1]['mode'] == 'Recall only'
    anyio.run(scenario)


@pytest.mark.skipif(getattr(os.stat('/'), 'st_birthtime', None) is None, reason='a move needs a birth time')
def test_a_moved_catalog_repo_is_recovered_with_its_memory(owner):
    api = repo(owner.work / 'api')
    project_id = launched(owner.run('claude', owner.runtime, cwd=api))['--project-id']
    moved = owner.work / 'renamed'
    api.rename(moved)
    assert owner.status(moved)[1]['state'] == 'moved'
    recovered = owner.run('recover', moved, '--expected-project-id', project_id)
    assert recovered.returncode == 0, recovered.stderr
    assert launched(owner.run('claude', owner.runtime, cwd=moved))['--project-id'] == project_id
    assert owner.catalog('SELECT count(*) FROM spaces') == [(1,)]          # the same space, not a new one


BRIDGE = Path(sys.executable).with_name('nexus-memory-bridge')


class Bridge:
    """A DSH session's bridge, started through the launcher exactly as the writer host starts it."""

    def __init__(self, owner, checkout, role='dsh'):
        self.proc = subprocess.Popen([sys.executable, '-I', str(ENTRY), role, str(BRIDGE)], cwd=checkout,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env={'HOME': str(owner.home), 'PATH': os.environ['PATH'], 'PWD': str(checkout)})

    def call(self, method, params, **extra):
        frame = dict(v=1, id='i', method=method, params=params, **extra)
        self.proc.stdin.write(json.dumps(frame).encode() + b'\n')
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read().decode()
        return json.loads(line)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


def test_dsh_launch_binds_the_bridge_to_the_verified_project(owner):
    api = repo(owner.work / 'api')
    result = owner.run('dsh', owner.runtime, cwd=api)
    assert result.returncode == 0, result.stderr
    args = result.stdout.split()
    assert args[args.index('--repo') + 1] == str(api) and '--consume-grant-file' in args
    assert Path(args[args.index('--grant-file') + 1]).parent == owner.base / 'sessions'
    assert owner.catalog("SELECT role,mode FROM sessions") == [('root', 'read_only')]       # recall only by default


def test_dsh_root_writes_only_under_maintain_and_a_worker_never_does(owner):
    api = repo(owner.work / 'api')
    assert owner.run('dsh', owner.runtime, cwd=api).returncode == 0          # first open creates the space
    recall = Bridge(owner, api)
    try:
        assert recall.call('capabilities', {})['result']['remember'] is False
    finally:
        recall.close()
    assert owner.run('mode', api, 'maintain').returncode == 0
    unread = set((owner.base / 'sessions').glob('*.grant'))                  # the stand-in runtime reads none
    root, worker = Bridge(owner, api), Bridge(owner, api, 'dsh-worker')
    try:
        assert root.call('capabilities', {})['result']['remember'] is True
        assert worker.call('capabilities', {})['result']['remember'] is False
        receipt = root.call('remember', {'content': 'DSH phase 2 fixture'}, operation_key='k1')['result']
        assert worker.call('get', {'memory_id': receipt['memory_id']})['result']['content'] == 'DSH phase 2 fixture'
        assert not worker.call('remember', {'content': 'worker write'}, operation_key='k2')['ok']
    finally:
        root.close()
        worker.close()
    assert set((owner.base / 'sessions').glob('*.grant')) == unread         # each bridge consumed its grant
    assert owner.catalog("SELECT role,mode FROM sessions WHERE role='delegated'") == [('delegated', 'read_only')]


def test_dsh_refuses_a_repo_outside_the_catalog(owner, tmp_path):
    outside = repo(tmp_path / 'elsewhere')
    assert owner.run('dsh', owner.runtime, cwd=outside).returncode != 0
