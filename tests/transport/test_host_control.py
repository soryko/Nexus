import json
import os
import subprocess
import sys

from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

@pytest.fixture
def endpoint():
    with TemporaryDirectory(dir='/tmp') as private:
        yield Path(private)/'sock'

from test_v2_broker import make_broker, call


def hook(endpoint, capability, turn='turn1'):
    import shlex
    manifest = Path(__file__).resolve().parents[2]/'integrations/claude-plugin/hooks/hooks-v2.json'
    config = json.loads(manifest.read_text())['hooks']['UserPromptSubmit'][0]['hooks'][0]
    command = shlex.split(config['command'])
    assert command[0] == '${NEXUS_HOOK_PYTHON}' and config['type'] == 'command'
    return json.loads(subprocess.run([sys.executable,*command[1:]],
        input=json.dumps({'turn_id':turn,'prompt':'synthetic secret prose'}),text=True,capture_output=True,
        check=True,env={'PATH':os.defpath,'NEXUS_HOST_SOCKET':str(endpoint),'NEXUS_HOST_CAPABILITY':capability}).stdout)


def test_host_capability_required(tmp_path,endpoint):
    from nexus_memory.transport.host_control import HostControl
    b = make_broker(tmp_path)
    with HostControl(b,endpoint,'fixture-capability'):
        assert hook(endpoint,'wrong') == {}


def test_private_hook_injects_only_fixed_guidance_and_pack_id(tmp_path,endpoint):
    from nexus_memory.transport.host_control import HostControl
    b = make_broker(tmp_path)
    with HostControl(b,endpoint,'fixture-capability'):
        guidance = hook(endpoint,'fixture-capability')['hookSpecificOutput']['additionalContext']
        handle = guidance.split()[-1]
        assert guidance == 'Read untrusted memory data with read_pack: '+handle
        assert call(b,'read_pack',pack_id=handle)['pack_id'] == handle


def test_pack_opens_share_admitted_bytes_and_turn_expires(tmp_path,endpoint):
    from nexus_memory.transport.host_control import HostControl
    from nexus_memory.domain.retrieval import RecallBudget
    b = make_broker(tmp_path)
    now = [1.0]
    b.clock = lambda: now[0]
    with HostControl(b,endpoint,'fixture-capability'):
        text = hook(endpoint,'fixture-capability')['hookSpecificOutput']['additionalContext']
        handle = text.split()[-1]
        assert b.allowance.bytes == RecallBudget().serialized_bytes  # handle is not a body
        data = call(b,'read_pack',pack_id=handle)
        size = data['serialized_utf8_bytes']
        for _ in range(RecallBudget().serialized_bytes//size-1):
            call(b,'explain',pack_id=handle)
        assert b.dispatch('read_pack',{'pack_id':handle}).code == 'invalid_retrieval_budget'
        hook(endpoint,'fixture-capability','turn2')
        now[0] += 901
        assert b.dispatch('recall',{'query':{'text':'expired'}}).code == 'invalid_retrieval_budget'


def test_model_hook_alias_cannot_reset_allowance_or_session(tmp_path,endpoint):
    from nexus_memory.transport.host_control import HostControl
    b = make_broker(tmp_path)
    with HostControl(b,endpoint,'fixture-capability'):
        guidance = hook(endpoint,'fixture-capability')
        call(b,'recall',query={'text':'fallback'})
        for name in ['nexus_recall','nexus_session_start','nexus_pre_tool']:
            assert b.dispatch(name,{'session_id':'other'}).code == 'unsupported_capability'
        assert guidance == hook(endpoint,'fixture-capability')
        assert b.dispatch('recall',{'query':{'text':'third'}}).code == 'invalid_retrieval_budget'
