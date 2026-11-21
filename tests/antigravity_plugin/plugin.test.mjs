import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import readline from 'node:readline';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  compareVersionTuples,
  parseVersionTuple,
  resolveRuntime,
} from '../../integrations/antigravity-plugin/scripts/runtime.mjs';
import { createStudioApp } from '../../integrations/antigravity-plugin/sidecars/nexus-studio/main.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const PLUGIN_ROOT = join(REPO_ROOT, 'integrations', 'antigravity-plugin');
const LAUNCH_JS = join(PLUGIN_ROOT, 'scripts', 'launch.mjs');
const HOOK_JS = join(PLUGIN_ROOT, 'scripts', 'hook.mjs');
const WORKTREE_PYTHON = join(REPO_ROOT, '.venv', 'bin', 'python');
const SIDECAR_SDK = join(
  process.env.HOME || '',
  '.gemini',
  'antigravity',
  'lib',
  'node_modules',
  'sidecar_sdk',
  'index.mjs',
);

test('version helpers and explicit NEXUS_PYTHON override work accurately', () => {
  assert.equal(compareVersionTuples(parseVersionTuple('3.54.1'), [3, 51, 3]), 1);
  assert.equal(compareVersionTuples(parseVersionTuple('3.50.4'), [3, 51, 3]), -1);

  const bad = resolveRuntime({
    autoBootstrap: false,
    pluginRoot: PLUGIN_ROOT,
    env: { ...process.env, NEXUS_PYTHON: '/nonexistent/python' },
  });
  assert.equal(bad.ok, false);
  assert.equal(bad.source, 'NEXUS_PYTHON');

  if (existsSync(WORKTREE_PYTHON)) {
    const good = resolveRuntime({
      autoBootstrap: false,
      pluginRoot: PLUGIN_ROOT,
      env: { ...process.env, NEXUS_PYTHON: WORKTREE_PYTHON },
    });
    assert.equal(good.ok, true);
    assert.equal(good.source, 'NEXUS_PYTHON');
    assert.equal(good.info.hasPackage, true);
  }
});

test('launch.mjs serves degraded diagnostic MCP server when runtime is unavailable', async () => {
  const proc = spawn(process.execPath, [LAUNCH_JS, 'mcp'], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, NEXUS_PYTHON: '/nonexistent/python', PLUGIN_ROOT },
  });

  try {
    const rl = readline.createInterface({ input: proc.stdout, crlfDelay: Infinity });
    const nextMessage = () =>
      new Promise((resolveMsg, rejectMsg) => {
        const timer = setTimeout(() => rejectMsg(new Error('timeout waiting for MCP response')), 4000);
        rl.once('line', (line) => {
          clearTimeout(timer);
          resolveMsg(JSON.parse(line));
        });
      });

    proc.stdin.write(
      `${JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '1' } },
      })}\n`,
    );
    const initRes = await nextMessage();
    assert.equal(initRes.id, 1);
    assert.equal(initRes.result.serverInfo.name, 'nexus-memory');

    proc.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' })}\n`);
    proc.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' })}\n`);
    const listRes = await nextMessage();
    assert.deepEqual(
      listRes.result.tools.map((t) => t.name),
      ['status'],
    );

    proc.stdin.write(
      `${JSON.stringify({
        jsonrpc: '2.0',
        id: 3,
        method: 'tools/call',
        params: { name: 'status', arguments: {} },
      })}\n`,
    );
    const statusRes = await nextMessage();
    assert.equal(statusRes.result.isError, false);
    assert.equal(statusRes.result.structuredContent.scope_state, 'runtime_unavailable');
    assert.equal(statusRes.result.structuredContent.durable_storage, 'unavailable');

    proc.stdin.write(
      `${JSON.stringify({
        jsonrpc: '2.0',
        id: 4,
        method: 'tools/call',
        params: { name: 'search', arguments: { query: 'test' } },
      })}\n`,
    );
    const searchRes = await nextMessage();
    assert.equal(searchRes.result.isError, true);
    rl.close();
  } finally {
    proc.kill('SIGTERM');
  }
});

test('hook.mjs fails open cleanly when runtime is unavailable', () => {
  const res = spawnSync(process.execPath, [HOOK_JS, 'recall'], {
    input: JSON.stringify({ workspacePaths: ['/tmp'], lastUserInput: 'explain decision' }),
    encoding: 'utf-8',
    env: { ...process.env, NEXUS_PYTHON: '/nonexistent/python', PLUGIN_ROOT },
  });
  assert.equal(res.status, 0);
  assert.deepEqual(JSON.parse(res.stdout), { injectSteps: [] });
});

test('Nexus Studio enforces POST-only + X-Sidecar-Token auth and serves real seeded read-only views', async (t) => {
  if (!existsSync(WORKTREE_PYTHON) || !existsSync(SIDECAR_SDK)) {
    t.skip('worktree venv or Antigravity sidecar_sdk unavailable');
    return;
  }

  const sandbox = mkdtempSync(join(tmpdir(), 'nx-studio-node-'));
  const home = join(sandbox, 'home');
  const repo = join(sandbox, 'repo');
  mkdirSync(home, { mode: 0o700, recursive: true });
  mkdirSync(repo, { recursive: true });

  const gitEnv = {
    ...process.env,
    HOME: home,
    GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_CONFIG_NOSYSTEM: '1',
  };
  execFileSync('git', ['-C', repo, 'init', '-q'], { env: gitEnv });
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'Test'], { env: gitEnv });
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.invalid'], { env: gitEnv });
  writeFileSync(join(repo, 'app.py'), 'print("nexus")\n');
  execFileSync('git', ['-C', repo, 'add', 'app.py'], { env: gitEnv });
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'init'], { env: gitEnv });

  // Enroll repo and seed one memory + one activity event using Python in the sandbox HOME
  const seedScript = `
import os, sys
from pathlib import Path
os.umask(0o077)
sys.path.insert(0, sys.argv[1])
import project_scope
from nexus_memory.activity import ActivityJournal
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

repo = Path(sys.argv[2]).resolve()
pid = project_scope.enroll(repo)
proj = project_scope.resolve(repo)
store = SQLiteRepository(proj.database)
scope = Scope(pid, "local")
binding, verifier = bind_repository(store, scope, repo, None, GitCli())
svc = MemoryService(store, scope, binding, verifier)
r1 = svc.record(MemoryInput("STUDIO_SECRET_SENTINEL decision", kind="decision", tags=("studio",), references=(ReferenceInput("app.py"),)), "seed-studio-1")
ActivityJournal(ActivityJournal.path_for_store(proj.database)).record_event("recall", "offered", session_id="s1", detail={"candidate_ids": [r1.memory_id], "candidate_count": 1})
print(r1.memory_id)
`;
  const seededId = execFileSync(WORKTREE_PYTHON, ['-I', '-B', '-c', seedScript, join(PLUGIN_ROOT, 'scripts'), repo], {
    env: gitEnv,
    encoding: 'utf-8',
  }).trim();

  const token = 'test-sidecar-secret-token';
  const prevEnv = {
    HOME: process.env.HOME,
    NEXUS_PYTHON: process.env.NEXUS_PYTHON,
    ANTIGRAVITY_LS_ADDRESS: process.env.ANTIGRAVITY_LS_ADDRESS,
    ANTIGRAVITY_CSRF_TOKEN: process.env.ANTIGRAVITY_CSRF_TOKEN,
    ANTIGRAVITY_SIDECAR_WEB_PORT: process.env.ANTIGRAVITY_SIDECAR_WEB_PORT,
    ANTIGRAVITY_SIDECAR_UI_TOKEN: process.env.ANTIGRAVITY_SIDECAR_UI_TOKEN,
    SIDECAR_SDK_PATH: process.env.SIDECAR_SDK_PATH,
  };

  Object.assign(process.env, {
    HOME: home,
    NEXUS_PYTHON: WORKTREE_PYTHON,
    ANTIGRAVITY_LS_ADDRESS: '127.0.0.1:59999',
    ANTIGRAVITY_CSRF_TOKEN: 'csrf-test',
    ANTIGRAVITY_SIDECAR_WEB_PORT: '0',
    ANTIGRAVITY_SIDECAR_UI_TOKEN: token,
    SIDECAR_SDK_PATH: SIDECAR_SDK,
  });

  const studio = await createStudioApp({
    pluginRoot: PLUGIN_ROOT,
    pluginData: join(sandbox, 'plugin-data'),
    env: { ...process.env },
  });

  try {
    const port = studio.server.address().port;
    const base = `http://127.0.0.1:${port}`;

    // Static GET has strict CSP & zero stored memory text
    const homeRes = await fetch(`${base}/`);
    assert.equal(homeRes.status, 200);
    assert.match(homeRes.headers.get('content-security-policy') || '', /default-src 'none'/);
    const html = await homeRes.text();
    assert.equal(html.includes('STUDIO_SECRET_SENTINEL'), false);

    // GET /api/status refused with 405 (POST-only enforcement)
    const getApiRes = await fetch(`${base}/api/status`);
    assert.equal(getApiRes.status, 405);

    // Unauthenticated POST /api/status refused with 401
    const unauthPostRes = await fetch(`${base}/api/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace: [repo] }),
    });
    assert.equal(unauthPostRes.status, 401);

    // Authenticated POST /api/status succeeds
    const statusRes = await fetch(`${base}/api/status`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Sidecar-Token': token,
      },
      body: JSON.stringify({ workspace: [`file://${repo}`] }),
    });
    assert.equal(statusRes.status, 200);
    const statusJson = await statusRes.json();
    assert.equal(statusJson.ok, true);
    assert.equal(statusJson.data.state, 'bound');
    assert.equal(statusJson.data.overview.total, 1);
    assert.equal(statusJson.data.activity_health.counts_by_category.recall, 1);

    // Authenticated POST /api/inspect & /api/compare-at-head
    const inspRes = await fetch(`${base}/api/inspect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Sidecar-Token': token },
      body: JSON.stringify({ workspace: [repo], params: { memory_id: seededId } }),
    });
    const inspJson = await inspRes.json();
    assert.equal(inspJson.ok, true);
    assert.equal(inspJson.data.content, 'STUDIO_SECRET_SENTINEL decision');

    const cmpRes = await fetch(`${base}/api/compare-at-head`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Sidecar-Token': token },
      body: JSON.stringify({
        workspace: [repo],
        params: { memory_id: seededId, revision_id: inspJson.data.revision_id, reference_ordinal: 0 },
      }),
    });
    const cmpJson = await cmpRes.json();
    assert.equal(cmpJson.ok, true);
    assert.equal(cmpJson.data.outcome, 'Matches at HEAD');

    // Unenrolled sibling workspace stays unenrolled without touching disk
    const unenrolled = join(sandbox, 'unenrolled');
    mkdirSync(unenrolled);
    execFileSync('git', ['-C', unenrolled, 'init', '-q'], { env: gitEnv });
    const unRes = await fetch(`${base}/api/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Sidecar-Token': token },
      body: JSON.stringify({ workspace: [unenrolled] }),
    });
    const unJson = await unRes.json();
    assert.equal(unJson.ok, true);
    assert.equal(unJson.data.state, 'unenrolled');
  } finally {
    await studio.close();
    for (const [k, v] of Object.entries(prevEnv)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    rmSync(sandbox, { recursive: true, force: true });
  }
});
