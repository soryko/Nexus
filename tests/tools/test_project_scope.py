"""Isolation controls for the host launcher and automatic project recall."""
import json
import os
from pathlib import Path
import subprocess
import sys



ENTRY = Path(__file__).resolve().parents[2] / 'integrations' / 'project_scope' / 'launcher.py'


def launch(cwd, home, *args, **extra):
    env = {'HOME': str(home), 'PATH': os.environ['PATH'], **extra}
    return subprocess.run([sys.executable, '-I', ENTRY, *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def git(cwd, *args):
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True)


def test_registration_only_admits_its_verified_worktrees(tmp_path):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    a, b = [tmp_path / x / 'api' for x in ('a', 'b')]
    for p in (a, b):
        p.mkdir(parents=True)
        git(p, 'init', '-q')
    git(a, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-m', 'fixture')
    def check(cwd, *args, **env):
        return launch(cwd, home, *args, **env)
    assert check(a, 'enroll', a).returncode == 0
    def probe(cwd, **env):
        return check(cwd, 'claude', '/bin/echo', **env)
    resolved = probe(a)
    assert resolved.returncode == 0, resolved.stderr
    assert '--namespace api ' not in resolved.stdout
    assert probe(b).returncode != 0
    assert probe(a, CLAUDE_PROJECT_DIR=str(b)).returncode != 0
    linked = tmp_path / 'linked-tree'
    git(a, 'worktree', 'add', '-q', '--detach', linked)
    linked_result = probe(linked)
    assert linked_result.returncode == 0
    assert resolved.stdout.split('--repo')[0] == linked_result.stdout.split('--repo')[0]
    fake = tmp_path / 'fake'
    fake.mkdir()
    (fake / '.git').write_bytes((linked / '.git').read_bytes())
    assert probe(fake).returncode != 0
    (fake / '.git').write_text('gitdir: ' + str(a / '.git') + '\n')
    assert probe(fake).returncode != 0
    assert check(b, 'enroll', b).returncode == 0
    assert probe(a).stdout.split('--repo')[0] != probe(b).stdout.split('--repo')[0]
    assert check(a, 'codex', '/bin/echo', '--expected-project', b).returncode != 0
    # A replacement at the same path cannot inherit the old enrollment.
    (a / '.git').rename(a / '.prior-git')
    git(a, 'init', '-q')
    assert probe(a).returncode != 0
    assert check(a, 'enroll', a).returncode != 0


def test_registry_permission_and_duplicate_ids_fail_closed(tmp_path):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    a, b = tmp_path / 'a', tmp_path / 'b'
    for p in (a, b):
        p.mkdir()
        git(p, 'init', '-q')
        result = launch(p, home, 'enroll', p)
        assert result.returncode == 0, result.stderr
    registry = home / '.local/share/nexus-memory/project-scopes-v1/registry.json'
    registry.chmod(0o644)
    assert launch(a, home, 'claude', '/bin/echo').returncode != 0
    registry.chmod(0o600)
    data = json.loads(registry.read_text())
    entries = list(data['projects'].values())
    entries[1]['id'] = entries[0]['id']
    registry.write_text(json.dumps(data))
    assert launch(a, home, 'claude', '/bin/echo').returncode != 0


def test_antigravity_hook_uses_single_host_workspace_at_plugin_root(tmp_path):
    home = tmp_path / 'home'
    plugin = home / '.gemini/config/plugins/nexus-memory'
    plugin.mkdir(parents=True)
    a, b = (tmp_path / name for name in ('alpha', 'beta'))
    for root in (a, b):
        root.mkdir()
        git(root, 'init', '-q')
        assert launch(root, home, 'enroll', root).returncode == 0
    def event(paths, **extra):
        result = subprocess.run(
            [sys.executable, '-I', ENTRY.with_name('antigravity_hook.py'), 'start'],
            cwd=plugin, env={'HOME': str(home), 'PATH': os.environ['PATH'], **extra},
            input=json.dumps({'workspacePaths': [str(p) for p in paths]}), capture_output=True,
            text=True, check=True)
        return json.loads(result.stdout)['injectSteps']
    assert event([a])
    assert event([a], PWD=str(a))
    assert event([a], PWD='/')
    assert not event([a, b])
    assert not event([a], PWD=str(b))
    assert not event([a], AGY_WORKSPACE=str(b))
    assert not event([tmp_path / 'missing' / 'alpha'])


def test_antigravity_hook_stop_capture(tmp_path):
    home = tmp_path / 'home'
    plugin = home / '.gemini/config/plugins/nexus-memory'
    plugin.mkdir(parents=True)
    root = tmp_path / 'alpha'
    root.mkdir()
    git(root, 'init', '-q')
    (root / 'file.txt').write_text('initial')
    git(root, 'add', 'file.txt')
    git(root, 'commit', '-q', '-m', 'initial')
    assert launch(root, home, 'enroll', root).returncode == 0

    transcript = tmp_path / 'transcript.jsonl'

    def write_transcript(steps):
        with transcript.open('w') as f:
            for s in steps:
                f.write(json.dumps(s) + '\n')

    def stop_event(payload_extra=None, **extra):
        payload = {
            'workspacePaths': [str(root)],
            'conversationId': 'conv-123',
            'transcriptPath': str(transcript),
            'terminationReason': 'NO_TOOL_CALL',
            'fullyIdle': True,
        }
        if payload_extra:
            payload.update(payload_extra)
        result = subprocess.run(
            [sys.executable, '-I', ENTRY.with_name('antigravity_hook.py'), 'stop'],
            cwd=plugin, env={'HOME': str(home), 'PATH': os.environ['PATH'], **extra},
            input=json.dumps(payload), capture_output=True,
            text=True, check=True)
        return json.loads(result.stdout)

    # 1. Non-NO_TOOL_CALL returns empty
    write_transcript([
        {'step_index': 1, 'type': 'USER_INPUT', 'content': 'fix a bug'},
        {'step_index': 2, 'type': 'PLANNER_RESPONSE', 'tool_calls': [{'name': 'edit_file', 'args': {}}]}
    ])
    assert stop_event({'terminationReason': 'ERROR'}) == {}

    # 2. Tool edit triggers nudge
    res = stop_event()
    assert res.get('decision') == 'continue'
    assert 'Nexus Memory Check' in res.get('reason', '')

    # 3. Repeat on same turn suppressed
    assert stop_event() == {}

    # 4. New turn with NO_MEMORY suppressed
    write_transcript([
        {'step_index': 3, 'type': 'USER_INPUT', 'content': 'minor tweak'},
        {'step_index': 4, 'type': 'PLANNER_RESPONSE', 'content': 'Done. NO_MEMORY', 'tool_calls': [{'name': 'edit_file', 'args': {}}]}
    ])
    assert stop_event() == {}

    # 5. New turn where record was already called suppressed
    write_transcript([
        {'step_index': 5, 'type': 'USER_INPUT', 'content': 'remember this decision'},
        {'step_index': 6, 'type': 'PLANNER_RESPONSE', 'tool_calls': [
            {'name': 'call_mcp_tool', 'args': {'ServerName': 'nexus-memory_nexus-memory', 'ToolName': 'record'}}
        ]}
    ])
    assert stop_event() == {}

