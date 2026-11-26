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
            refused = await call(first, 'record', idempotency_key='k1', content='synthetic kestrel decision')
            assert refused == {'code': 'space_read_only', 'retryable': False}      # recall only by default
        assert owner.status(api)[1]['mode'] == 'Recall only'
        assert owner.run('mode', api, 'maintain').returncode == 0
        assert owner.status(api)[1]['mode'] == 'Recall + maintain'
        async with client(owner, api) as writer, client(owner, api) as reader:
            saved = await call(writer, 'record', idempotency_key='k2', content='synthetic kestrel decision')
            assert 'memory_id' in saved
            assert (await call(reader, 'search', query='kestrel'))['hits']
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
