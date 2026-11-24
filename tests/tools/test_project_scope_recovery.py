"""Owner recovery and read-only scope inspection use a temporary HOME; never repair the actual registry."""
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest


SCOPE = Path(__file__).resolve().parents[2] / 'integrations/project_scope'


@pytest.fixture
def drift(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCOPE))
    import project_scope as scope

    home, root = tmp_path / 'home', tmp_path / 'main'
    home.mkdir(mode=0o700)
    root.mkdir()
    root = root.resolve()
    monkeypatch.setenv('HOME', str(home))
    subprocess.run(['git', 'init', '-q', root], check=True)
    observed = scope.identity(root / '.git')
    # Exercise the positive path even on filesystems without native birth time.
    if observed['birth_time'] is None:
        observed['birth_time'] = 123456.25
        monkeypatch.setattr(scope, 'identity', lambda _: observed.copy())
    project_id = scope.enroll(root)
    base = scope.private_base()
    registry = base / 'registry.json'
    data = json.loads(registry.read_bytes())
    data['projects'][str(root / '.git')]['device'] += 1
    registry.write_text(json.dumps(data))
    db = base / (project_id + '.sqlite3')
    db.touch(mode=0o600)
    db.write_bytes(b'private fixture')
    return scope, root, project_id, registry, db


def test_recover_changes_only_device(drift):
    scope, root, project_id, registry, db = drift
    expected = json.loads(registry.read_bytes())
    expected['projects'][str(root / '.git')]['device'] = (root / '.git').stat().st_dev
    with pytest.raises(scope.ScopeError):
        scope.resolve(root)
    with pytest.raises(scope.ScopeError):
        scope.enroll(root)
    assert scope.recover(root, project_id) == project_id
    assert json.loads(registry.read_bytes()) == expected
    assert scope.resolve(root).database == db
    assert db.read_bytes() == b'private fixture'
    with pytest.raises(scope.ScopeError):  # matched device does not authorize a rewrite
        scope.recover(root, project_id)


@pytest.mark.parametrize('problem', ['wrong-id', 'inode', 'birth-time', 'missing-store',
                                     'unsafe-store', 'unsafe-wal', 'subdir', 'linked-checkout'])
def test_recovery_refuses_without_mutation(drift, problem):
    scope, root, project_id, registry, db = drift
    if problem == 'wrong-id':
        project_id = str(uuid4())
    elif problem in ('inode', 'birth-time'):
        data = json.loads(registry.read_bytes())
        data['projects'][str(root / '.git')][problem.replace('-', '_')] += 1
        registry.write_text(json.dumps(data))
    elif problem == 'missing-store':
        db.unlink()
    elif problem == 'unsafe-store':
        db.chmod(0o644)
    elif problem == 'unsafe-wal':
        Path(str(db) + '-wal').symlink_to(db)
    elif problem == 'subdir':
        root = root / 'subdir'
        root.mkdir()
    elif problem == 'linked-checkout':
        linked = root.parent / 'linked'
        subprocess.run(['git', '-C', root, '-c', 'user.name=test', '-c', 'user.email=test@test',
                        'commit', '--allow-empty', '-qm', 'test'], check=True)
        subprocess.run(['git', '-C', root, 'worktree', 'add', '-q', '--detach', linked], check=True)
        root = linked
    before = registry.read_bytes()
    with pytest.raises((scope.ScopeError, FileNotFoundError)):
        scope.recover(root, project_id)
    assert registry.read_bytes() == before
    if problem == 'missing-store':
        assert not db.exists()


@pytest.mark.parametrize('invalid', [None, 0, -1, float('inf'), float('nan'), True, '123'])
def test_recovery_requires_meaningful_birth_time(drift, invalid):
    scope, *_ = drift
    previous = {'device': 2, 'inode': 1, 'birth_time': invalid}
    assert not scope.device_only_drift(previous, dict(previous, device=3))


def test_recovery_cli(drift):
    _, root, project_id, registry, _ = drift
    command = [sys.executable, '-I', SCOPE / 'launcher.py', 'recover', root]
    before = registry.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode == 2
    assert registry.read_bytes() == before
    result = subprocess.run([*command, '--expected-project-id', project_id], capture_output=True, text=True)
    if getattr((root / '.git').stat(), 'st_birthtime', None) is None:
        assert result.returncode == 1
        assert registry.read_bytes() == before
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == project_id


def test_inspect_scope_classifies_all_states_without_mutation(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCOPE))
    import project_scope as scope

    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HOME', str(home))

    plain_dir = tmp_path / 'plain'
    plain_dir.mkdir()
    invalid = scope.inspect_scope(plain_dir)
    assert invalid.state == 'invalid'
    assert not (home / '.local').exists()

    repo = tmp_path / 'repo'
    repo.mkdir()
    repo = repo.resolve()
    subprocess.run(['git', 'init', '-q', repo], check=True)
    unenrolled_no_base = scope.inspect_scope(repo)
    assert unenrolled_no_base.state == 'unenrolled'
    assert unenrolled_no_base.checkout == repo
    assert not (home / '.local').exists()

    observed = scope.identity(repo / '.git')
    if observed['birth_time'] is None:
        observed['birth_time'] = 123456.25
        monkeypatch.setattr(scope, 'identity', lambda _: observed.copy())

    other = tmp_path / 'other'
    other.mkdir()
    subprocess.run(['git', 'init', '-q', other], check=True)
    scope.enroll(other)
    unenrolled = scope.inspect_scope(repo)
    assert unenrolled.state == 'unenrolled'

    pid = scope.enroll(repo)
    bound_diag = scope.inspect_scope(repo)
    assert bound_diag.state == 'bound'
    assert bound_diag.project_id == pid
    assert bound_diag.database == scope.private_base() / f'{pid}.sqlite3'

    registry_path = scope.private_base() / 'registry.json'
    reg = json.loads(registry_path.read_bytes())
    old_dev = reg['projects'][str(repo / '.git')]['device']
    reg['projects'][str(repo / '.git')]['device'] = old_dev + 2
    registry_path.write_text(json.dumps(reg))

    drift_diag = scope.inspect_scope(repo)
    assert drift_diag.state == 'device_drift'
    assert drift_diag.project_id == pid
    assert drift_diag.recorded_device == old_dev + 2
    assert drift_diag.observed_device == old_dev

    reg['projects'][str(repo / '.git')]['inode'] += 1
    registry_path.write_text(json.dumps(reg))
    changed_diag = scope.inspect_scope(repo)
    assert changed_diag.state == 'identity_changed'
    assert changed_diag.project_id == pid
