"""Registry version 2: folder approval, lazy enrollment, denial, moves and private groups.

Every test uses a temporary HOME and disposable checkouts; nothing reads the owner's registry.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / 'integrations/project_scope'
ENTRY = SCOPE / 'launcher.py'


def git(cwd, *args):
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True)


def repo(path):
    path.mkdir(parents=True)
    git(path, 'init', '-q')
    git(path, '-c', 'user.name=f', '-c', 'user.email=f@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'f')
    return path.resolve()


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Load this file by path: other suites put stubs or the plugin copy in sys.modules['project_scope'].
    spec = importlib.util.spec_from_file_location('project_scope_under_test', SCOPE / 'project_scope.py')
    scope = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, scope)   # dataclasses look the module up
    spec.loader.exec_module(scope)
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HOME', str(home))

    def launch(cwd, *args, **extra):
        e = {'HOME': str(home), 'PATH': os.environ['PATH'], **extra}
        return subprocess.run([sys.executable, '-I', ENTRY, *map(str, args)], cwd=cwd, env=e,
                              capture_output=True, text=True)

    def registry():
        return json.loads((home / '.local/share/nexus-memory/project-scopes-v1/registry.json').read_text())

    return SimpleNamespace(scope=scope, home=home, launch=launch, registry=registry, tmp=tmp_path.resolve(),
                           runtime=stand_in_runtime(tmp_path / 'bin'))


def stand_in_runtime(bin_dir):
    """A server that prints its arguments, beside the real admin command of this build."""
    bin_dir.mkdir()
    server = bin_dir / 'nexus-memory'
    server.write_text('#!/bin/sh\necho "$@"\n')
    admin = bin_dir / 'nexus-memory-admin'
    admin.write_text(f'#!/bin/sh\nexec {sys.executable} -I -c '
                     '"import sys; from nexus_memory.spaces.admin import main; sys.exit(main())" "$@"\n')
    for path in (server, admin):
        path.chmod(0o700)
    return server


def project_of(result):
    assert result.returncode == 0, result.stderr
    args = result.stdout.split()
    flag = '--project-id' if '--project-id' in args else '--namespace'
    return args[args.index(flag) + 1]


def host(env, cwd):
    return env.launch(cwd, 'claude', env.runtime)


def test_a_repo_under_an_approved_folder_enrolls_on_first_open(env):
    work = env.tmp / 'work'
    inside, outside = repo(work / 'api'), repo(env.tmp / 'elsewhere' / 'api')
    assert host(env, inside).returncode != 0          # nothing approved yet: no memory
    assert env.launch(inside, 'approve-folder', work).returncode == 0
    assert env.registry()['version'] == 2
    assert env.registry()['projects'] == {}           # approval alone enrolls nothing
    first = project_of(host(env, inside))
    assert project_of(host(env, inside / '.')) == first
    assert host(env, outside).returncode != 0
    entry, = env.registry()['projects'].values()
    assert entry['origin'] == 'folder' and entry['id'] == first
    status = json.loads(env.launch(outside, 'status', outside).stdout)
    assert status['state'] == 'unenrolled'


def test_containment_needs_both_the_worktree_and_its_main_checkout(env):
    work = env.tmp / 'work'
    main = repo(work / 'main')
    away = env.tmp / 'away-tree'
    near = work / 'near-tree'
    git(main, 'worktree', 'add', '-q', '--detach', away)
    git(main, 'worktree', 'add', '-q', '--detach', near)
    env.launch(main, 'approve-folder', work)
    assert host(env, away).returncode != 0            # worktree outside the folder
    assert env.registry()['projects'] == {}
    linked = project_of(host(env, near))
    assert project_of(host(env, main)) == linked      # the linked worktree shares the main checkout's project
    # A main checkout outside the folder cannot be enrolled through a worktree inside it.
    other = repo(env.tmp / 'outside-main')
    inner = work / 'inner-tree'
    git(other, 'worktree', 'add', '-q', '--detach', inner)
    assert host(env, inner).returncode != 0


def test_a_symlink_inside_an_approved_folder_does_not_enroll_its_target(env):
    work = env.tmp / 'work'
    work.mkdir()
    target = repo(env.tmp / 'private' / 'secret')
    (work / 'link').symlink_to(target)
    env.launch(work, 'approve-folder', work)
    assert host(env, work / 'link').returncode != 0
    assert env.registry()['projects'] == {}


def test_two_hosts_opening_at_once_get_one_registration(env):
    work = env.tmp / 'work'
    api = repo(work / 'api')
    env.launch(api, 'approve-folder', work)
    procs = [subprocess.Popen([sys.executable, '-I', ENTRY, 'claude', env.runtime], cwd=api,
                              env={'HOME': str(env.home), 'PATH': os.environ['PATH']},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
    ids = set()
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        args = out.split()
        ids.add(args[args.index('--project-id') + 1])
    assert len(ids) == 1
    assert len(env.registry()['projects']) == 1


def test_disable_overrides_folders_and_enrollment_and_keeps_the_identity(env):
    work = env.tmp / 'work'
    api = repo(work / 'api')
    explicit = repo(env.tmp / 'explicit')
    env.launch(api, 'approve-folder', work)
    first = project_of(host(env, api))
    assert env.launch(explicit, 'enroll', explicit).returncode == 0
    for checkout in (api, explicit):
        assert env.launch(checkout, 'disable', checkout).returncode == 0
        assert host(env, checkout).returncode != 0
        assert json.loads(env.launch(checkout, 'status', checkout).stdout)['state'] == 'disabled'
    refused = env.launch(explicit, 'enroll', explicit)
    assert refused.returncode != 0 and 'disabled' in refused.stderr
    assert env.launch(api, 'enable', api).returncode == 0
    assert project_of(host(env, api)) == first        # re-enabling resumes the same project
    # Denial of a repo that was never opened also blocks its first open.
    fresh = repo(work / 'fresh')
    env.launch(fresh, 'disable', fresh)
    assert host(env, fresh).returncode != 0
    assert len(env.registry()['projects']) == 2


def test_revoking_a_folder_stops_only_the_repos_it_enrolled(env):
    work = env.tmp / 'work'
    lazy, explicit = repo(work / 'lazy'), repo(work / 'explicit')
    env.launch(work, 'approve-folder', work)
    first = project_of(host(env, lazy))
    env.launch(explicit, 'enroll', explicit)
    assert env.launch(work, 'revoke-folder', work).returncode == 0
    assert host(env, lazy).returncode != 0
    assert host(env, explicit).returncode == 0
    env.launch(work, 'approve-folder', work)
    assert project_of(host(env, lazy)) == first       # the entry and its store were kept


def test_explicit_enroll_upgrades_a_folder_entry(env):
    work = env.tmp / 'work'
    api = repo(work / 'api')
    env.launch(work, 'approve-folder', work)
    first = project_of(host(env, api))
    assert env.launch(api, 'enroll', api).stdout.strip() == first
    env.launch(work, 'revoke-folder', work)
    assert project_of(host(env, api)) == first


def test_a_moved_checkout_is_refused_until_the_owner_recovers_it(env):
    old = repo(env.tmp / 'old' / 'api')
    project_id = env.launch(old, 'enroll', old).stdout.strip()
    base = env.home / '.local/share/nexus-memory/project-scopes-v1'
    (base / (project_id + '.sqlite3')).touch(mode=0o600)
    new = env.tmp / 'new' / 'api'
    new.parent.mkdir()
    old.rename(new)
    refused = env.launch(new, 'enroll', new)
    assert refused.returncode != 0 and 'moved' in refused.stderr
    assert host(env, new).returncode != 0
    assert json.loads(env.launch(new, 'status', new).stdout)['state'] == 'moved'
    assert env.launch(new, 'recover', new, '--expected-project-id', '00000000-0000-4000-8000-000000000000').returncode != 0
    assert env.launch(new, 'recover', new, '--expected-project-id', project_id).stdout.strip() == project_id
    assert project_of(host(env, new)) == project_id
    assert list(env.registry()['projects']) == [str(new / '.git')]


def test_a_moved_checkout_under_an_approved_folder_is_not_enrolled_again(env):
    work = env.tmp / 'work'
    api = repo(work / 'api')
    env.launch(work, 'approve-folder', work)
    first = project_of(host(env, api))
    api.rename(work / 'renamed')
    assert host(env, work / 'renamed').returncode != 0
    assert [e['id'] for e in env.registry()['projects'].values()] == [first]


def test_registry_stays_version_1_without_version_2_state(env):
    api = repo(env.tmp / 'api')
    env.launch(api, 'enroll', api)
    data = env.registry()
    assert data['version'] == 1 and set(data) == {'version', 'projects'}
    assert 'origin' not in next(iter(data['projects'].values()))
    env.launch(api, 'disable', api)
    assert env.registry()['version'] == 2
    env.launch(api, 'enable', api)
    assert env.registry()['version'] == 1


def test_version_mismatches_fail_closed(env):
    scope = env.scope
    api = repo(env.tmp / 'api')
    scope.enroll(api)
    base = scope.private_base()
    path = base / 'registry.json'
    data = json.loads(path.read_text())
    for bad in ({**data, 'version': 3}, {**data, 'folders': {}},
                {**data, 'version': 2, 'projects': {k: {**v, 'origin': 'host'} for k, v in data['projects'].items()}}):
        path.write_text(json.dumps(bad))
        with pytest.raises(scope.ScopeError):
            scope.read_registry(base)


def test_a_version_1_registry_cannot_carry_a_catalog_store(env):
    api = repo(env.tmp / 'api')
    project_id = env.launch(api, 'enroll', api).stdout.strip()
    path = env.home / '.local/share/nexus-memory/project-scopes-v1/registry.json'
    data = env.registry()
    assert data['version'] == 1 and project_of(host(env, api)) == project_id   # the control: it launches
    data['projects'][str(api / '.git')]['store'] = 'catalog'
    path.write_text(json.dumps(data))
    assert host(env, api).returncode != 0
    data['version'] = 2
    data['projects'][str(api / '.git')]['store'] = 'elsewhere'
    path.write_text(json.dumps(data))
    assert host(env, api).returncode != 0


@pytest.mark.parametrize('layout', ['bare', 'submodule', 'separate'])
def test_unsupported_layouts_are_named(env, layout):
    target = env.tmp / 'target'
    if layout == 'bare':
        subprocess.run(['git', 'init', '-q', '--bare', target], check=True)
    elif layout == 'separate':
        subprocess.run(['git', 'init', '-q', '--separate-git-dir', env.tmp / 'gitdir', target], check=True)
    else:
        sub = repo(env.tmp / 'sub')
        outer = repo(env.tmp / 'outer')
        git(outer, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', sub, 'child')
        target = outer / 'child'
    status = json.loads(env.launch(env.tmp, 'status', target).stdout)
    assert status['state'] == 'invalid'
    assert {'bare': 'bare', 'submodule': 'submodule', 'separate': 'separate Git directory'}[layout] in status['message']


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS letter-case canonicalization')
def test_a_case_variant_path_resolves_to_the_same_project(env):
    api = repo(env.tmp / 'CaseRepo')
    variant = Path(str(api).replace('CaseRepo', 'caserepo'))
    if not variant.exists():
        pytest.skip('case-sensitive volume')
    first = env.launch(api, 'enroll', api).stdout.strip()
    assert project_of(host(env, variant)) == first
    assert env.launch(variant, 'enroll', variant).stdout.strip() == first
    assert len(env.registry()['projects']) == 1
    # A folder approved through a case variant still covers the canonical path.
    work = env.tmp / 'Work'
    inside = repo(work / 'api')
    env.launch(env.tmp, 'approve-folder', env.tmp / 'work')
    assert host(env, inside).returncode == 0


def test_private_group_rule(env, monkeypatch):
    scope = env.scope
    uid = os.getuid()
    user = SimpleNamespace(pw_name='dev', pw_uid=uid, pw_gid=4242)
    others = [user, SimpleNamespace(pw_name='ops', pw_uid=uid + 1, pw_gid=100)]
    groups = {4242: SimpleNamespace(gr_name='dev', gr_mem=[]), 100: SimpleNamespace(gr_name='users', gr_mem=['dev', 'ops'])}
    monkeypatch.setattr(scope.sys, 'platform', 'linux')
    monkeypatch.setattr(scope.pwd, 'getpwuid', lambda _: user)
    monkeypatch.setattr(scope.pwd, 'getpwall', lambda: others)
    monkeypatch.setattr(scope.grp, 'getgrgid', lambda gid: groups[gid])

    def st(mode, gid, owner=uid):
        return SimpleNamespace(st_mode=mode, st_gid=gid, st_uid=owner)

    assert not scope.writable_by_others(st(0o40775, 4242))        # umask 002, private group
    assert scope.writable_by_others(st(0o40775, 100))             # shared group
    groups[4243] = SimpleNamespace(gr_name='dev', gr_mem=[])
    assert scope.writable_by_others(st(0o40775, 4243))            # not the owner's primary group
    assert scope.writable_by_others(st(0o40777, 4242))            # world write
    assert scope.writable_by_others(st(0o40775, 4242, owner=uid + 1))
    groups[4242].gr_mem = ['dev', 'ops']                           # a second member
    assert scope.writable_by_others(st(0o40775, 4242))
    groups[4242].gr_mem = []
    others.append(SimpleNamespace(pw_name='svc', pw_uid=uid + 2, pw_gid=4242))   # another primary user
    assert scope.writable_by_others(st(0o40775, 4242))
    monkeypatch.setattr(scope.sys, 'platform', 'darwin')
    others.pop()
    assert scope.writable_by_others(st(0o40775, 4242))            # Linux only


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux umask 002 behaviour')
def test_umask_002_checkout_follows_the_private_group_rule(env):
    old = os.umask(0o002)
    try:
        api = repo(env.tmp / 'u002' / 'api')
    finally:
        os.umask(old)
    common = os.stat(api / '.git')
    assert common.st_mode & 0o020
    result = env.launch(api, 'enroll', api)
    assert (result.returncode == 0) == env.scope.private_group(common), result.stderr


def test_the_antigravity_plugin_carries_the_same_registry_module():
    vendored = ROOT / 'integrations/antigravity-plugin/scripts/project_scope.py'
    assert vendored.read_bytes() == (SCOPE / 'project_scope.py').read_bytes()
