"""The neutral Antigravity gateway re-reads the client's roots on every call and stays refused once it has
seen a different or ambiguous root. A harmless roots notification must not refuse the connection."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import anyio
import pytest

INTEGRATION = Path(__file__).resolve().parents[2] / 'integrations' / 'project_scope'


def load(name):
    if str(INTEGRATION) not in sys.path:
        sys.path.insert(0, str(INTEGRATION))
    return importlib.import_module(name)


def repo(path):
    path.mkdir(parents=True)
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    for args in (['init', '-q'], ['-c', 'user.name=t', '-c', 'user.email=t@x', 'commit', '-q', '--allow-empty', '-m', 'i']):
        subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, env=env)
    return path.resolve()


class Session:
    def __init__(self):
        self.roots = []
        self.fail = False

    def check_client_capability(self, capability):
        return True

    async def list_roots(self):
        if self.fail:
            raise RuntimeError('workspace manager unavailable')
        return SimpleNamespace(roots=[SimpleNamespace(uri=Path(r).as_uri()) for r in self.roots])


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    previous = os.umask(0o077)                 # the real entry points do this; stores must be owner-only
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HOME', str(home))
    plugin = home / '.gemini/config/plugins/nexus-memory'
    plugin.mkdir(parents=True)
    monkeypatch.chdir(plugin)                  # the host starts the neutral server in the plugin directory
    monkeypatch.setenv('PWD', str(plugin))
    scope = load('project_scope')
    a, b = repo(tmp_path / 'a'), repo(tmp_path / 'b')
    scope.enroll(a)
    scope.enroll(b)
    module = load('antigravity_mcp')
    guard, session = module.GuardedService(None), Session()

    async def call(method='tools/call'):
        async def call_next(ctx):
            return 'admitted'
        result = await guard.authorize(SimpleNamespace(method=method, session=session), call_next)
        return 'admitted' if result == 'admitted' else ('denied' if getattr(result, 'is_error', False) else result)

    yield SimpleNamespace(guard=guard, session=session, a=a, b=b, call=lambda *x: anyio.run(call, *x))
    os.umask(previous)


def test_a_bound_connection_is_admitted_for_its_own_root(gateway):
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'admitted'
    assert gateway.call() == 'admitted'


def test_an_ambiguous_root_after_binding_requires_reconnection(gateway):
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'admitted'
    gateway.session.roots = [gateway.a, gateway.b]
    assert gateway.call() == 'denied'
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'denied'          # recovery is not trusted: the README says reconnect


def test_a_host_failure_after_binding_requires_reconnection(gateway):
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'admitted'
    gateway.session.fail = True
    assert gateway.call() == 'denied'
    gateway.session.fail = False
    assert gateway.call() == 'denied'


def test_a_switch_to_another_project_requires_reconnection(gateway):
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'admitted'
    gateway.session.roots = [gateway.b]
    assert gateway.call() == 'denied'
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'denied'


def test_a_roots_notification_alone_does_not_refuse_the_connection(gateway):
    gateway.session.roots = [gateway.a]
    assert gateway.call() == 'admitted'
    gateway.call('notifications/roots/list_changed')
    assert gateway.call() == 'admitted'         # the roots are the same: the next call re-checks and is fine
