"""The Antigravity prompt hook shows the user one count line, and only the user: the model gets the candidates
through its own step and never the line."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

INTEGRATION = Path(__file__).resolve().parents[2] / 'integrations' / 'project_scope'
HOOK = INTEGRATION / 'antigravity_hook.py'
LAUNCHER = INTEGRATION / 'launcher.py'

SEED = """
import os, sys
os.umask(0o077)  # as the server does; resolve() refuses a store others can read
sys.path.insert(0, sys.argv[1])
from project_scope import host_project
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
project = host_project('antigravity', [sys.argv[2]])
service = MemoryService(SQLiteRepository(project.database), Scope(project.id, 'local'))
for n, text in enumerate(sys.argv[3:]):
    service.record(MemoryInput(content=text, kind='decision'), f'seed-{n}')
print(project.database)
"""


def git(cwd, *args):
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True,
                   env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})


@pytest.fixture
def world(tmp_path):
    home = tmp_path / 'home'
    plugin = home / '.gemini/config/plugins/nexus-memory'
    plugin.mkdir(parents=True)
    root = tmp_path / 'alpha'
    root.mkdir()
    git(root, 'init', '-q')
    env = {'HOME': str(home), 'PATH': os.environ['PATH']}
    subprocess.run([sys.executable, '-I', LAUNCHER, 'enroll', root], cwd=root, env=env, check=True, capture_output=True)

    def seed(*texts):
        done = subprocess.run([sys.executable, '-c', SEED, INTEGRATION, root, *texts], cwd=plugin, env=env,
                              capture_output=True, text=True, check=True)
        return Path(done.stdout.strip())

    def recall(prompt, conversation='conv-1'):
        body = {'workspacePaths': [str(root)], 'conversationId': conversation, 'lastUserInput': prompt}
        done = subprocess.run([sys.executable, '-I', HOOK, 'recall'], cwd=plugin, env=env, input=json.dumps(body),
                              capture_output=True, text=True, check=True)
        return json.loads(done.stdout)['injectSteps']

    return type('W', (), {'seed': staticmethod(seed), 'recall': staticmethod(recall)})


def shown(steps):
    """The user-only lines: system messages that send the model nothing and render markdown to the user."""
    lines = []
    for step in steps:
        message = step.get('systemMessage')
        if isinstance(message, dict) and message.get('metadata', {}).get('user_markdown'):
            assert message['systemMessage'] == ''
            lines.append(message['metadata']['user_markdown'])
    return lines


def test_recall_offers_candidates_to_the_model_and_a_count_to_the_user(world):
    world.seed('quasarflux secret-body-0', 'quasarflux secret-body-1', 'lens assembly uses a copper collar')
    steps = world.recall('Explain quasarflux please')
    assert shown(steps) == ['Nexus: 2 memory candidates offered for this prompt.']
    model = [step['ephemeralMessage'] for step in steps if 'ephemeralMessage' in step]
    assert len(model) == 1 and model[0].count('(id: ') == 2
    # The count line carries no stored text.
    assert 'secret-body' not in shown(steps)[0]

    assert shown(world.recall('How is the lens assembly secured?')) == [
        'Nexus: 1 memory candidate offered for this prompt.']
    # Nothing new, nothing found, and a prompt too short to search are all silent.
    assert world.recall('Explain quasarflux please') == []
    assert world.recall('zzzxxyynomatch anywhere') == []
    assert world.recall('ok') == []


def test_a_failed_recall_tells_the_user_and_not_the_model(world):
    database = world.seed('quasarflux secret-body-0')
    database.write_bytes(b'not a database' * 512)
    steps = world.recall('Explain quasarflux please')
    assert shown(steps) == ['Nexus recall unavailable; continue without automatic memory context.']
    assert not any('ephemeralMessage' in step for step in steps)
