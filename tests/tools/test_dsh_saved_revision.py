"""Disposable MCP contract for the saved-revision inspector; no DSH profile needed."""
import asyncio
import json
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters


def test_saved_revision_is_exact_and_inspection_does_not_write(tmp_path):
    server = StdioServerParameters(
        command=sys.executable,
        args=['-B', '-m', 'nexus_memory', '--db', str(tmp_path / 'disposable.db'),
              '--namespace', 'inspector-test', '--actor', 'synthetic', '--no-instructions'],
        cwd=tmp_path, env={'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')},
    )

    async def inspect():
        async with Client(server) as client:
            async def call(name, **args):
                result = await client.call_tool(name, args)
                assert not result.is_error, result.content
                return result.structured_content

            original = '  Original café\n\t</aside>\n'
            saved = await call('record', content=original, idempotency_key='fixture-save',
                               source_uri='dsh-session:disposable-inspector-session/event/1')
            mid, rid = saved['memory_id'], saved['revision_id']
            newer = await call('revise', memory_id=mid, expected_revision_id=rid,
                               content='Changed later.', idempotency_key='fixture-edit')
            before = await call('status')
            proc = await asyncio.create_subprocess_exec(
                'node', str(Path(__file__).with_name('dsh_inspector_bridge.mjs')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(10):
                    proc.stdin.write(json.dumps(saved).encode() + b'\n')
                    request = json.loads(await proc.stdout.readline())
                    assert request == {'name': 'mcp__nexus__get', 'args': {
                        'memory_id': mid, 'revision_id': rid}}
                    historical = await call('get', **request['args'])
                    proc.stdin.write(json.dumps(historical).encode() + b'\n')
                    proc.stdin.close()
                    displayed = json.loads(await proc.stdout.readline())
                    assert displayed['status'] == 'ok' and displayed['content'] == original
                    assert displayed['isCurrent'] is False
                    assert displayed['originSeq'] == 1 and displayed['turn'] == 1
                    assert await proc.wait() == 0
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            assert historical['content'] == original
            assert historical['revision_id'] == rid
            assert historical['current_revision_id'] == newer['revision_id'] != rid
            after = await call('status')
            for key in ('active_memories', 'revisions', 'latest_durable_seq'):
                assert after[key] == before[key]

    anyio.run(inspect)
