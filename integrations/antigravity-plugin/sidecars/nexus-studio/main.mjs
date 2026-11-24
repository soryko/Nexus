#!/usr/bin/env node
/**
 * Nexus Studio sidecar server (`sidecars/nexus-studio/main.mjs`).
 *
 * Hosts static read-only UI assets over GET with strict CSP headers and exposes
 * read-only Activity & Memory Atlas endpoints strictly over POST so `sidecar_sdk`
 * enforces `X-Sidecar-Token` authentication on every data request.
 */

import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import readline from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolveRuntime } from '../../scripts/runtime.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI_DIR = join(__dirname, 'ui');
const PLUGIN_ROOT = resolve(__dirname, '..', '..');

const CSP_HEADER =
  "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'";

const WORKER_ACTIONS = new Map([
  ['/api/status', 'status'],
  ['/api/activity', 'activity'],
  ['/api/overview', 'overview'],
  ['/api/search', 'search'],
  ['/api/inspect', 'inspect'],
  ['/api/history', 'history'],
  ['/api/compare-revision', 'compare_revision'],
  ['/api/neighborhood', 'neighborhood'],
  ['/api/related', 'related'],
  ['/api/compare-at-head', 'compare_at_head'],
]);

async function loadSidecarSdk() {
  try {
    return await import('sidecar_sdk');
  } catch {
    const candidates = [
      process.env.SIDECAR_SDK_PATH,
      join(homedir(), '.gemini', 'antigravity', 'lib', 'node_modules', 'sidecar_sdk', 'index.mjs'),
    ].filter(Boolean);
    for (const candidate of candidates) {
      if (existsSync(candidate)) {
        return await import(pathToFileURL(candidate).href);
      }
    }
    throw new Error('Could not locate Antigravity sidecar_sdk module.');
  }
}

class StudioWorkerClient {
  constructor({ pluginRoot, pluginData, env }) {
    this.pluginRoot = pluginRoot;
    this.pluginData = pluginData;
    this.env = env;
    this.runtime = null;
    this.proc = null;
    this.rl = null;
    this.nextSeq = 1;
    this.pending = new Map();
    this.closed = false;
  }

  invalidateRuntime() {
    this.runtime = null;
    this.stopProcess(new Error('runtime refreshed'));
  }

  ensureRuntime({ autoBootstrap = true } = {}) {
    if (!this.runtime || (!this.runtime.ok && autoBootstrap)) {
      this.runtime = resolveRuntime({
        autoBootstrap,
        pluginRoot: this.pluginRoot,
        pluginData: this.pluginData,
        env: this.env,
      });
    }
    return this.runtime;
  }

  ensureProcess() {
    if (this.closed) {
      return null;
    }
    const rt = this.ensureRuntime({ autoBootstrap: true });
    if (!rt.ok) {
      return null;
    }
    if (this.proc && !this.proc.killed && this.proc.exitCode === null) {
      return this.proc;
    }

    const scopeDir = join(this.pluginRoot, 'scripts');
    const workerEnv = {
      ...this.env,
      NEXUS_PROJECT_SCOPE_DIR: scopeDir,
      PYTHONUNBUFFERED: '1',
    };
    const proc = spawn(rt.python, ['-I', '-B', '-m', 'nexus_memory.atlas.studio_worker'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: workerEnv,
      windowsHide: true,
    });
    const rl = readline.createInterface({ input: proc.stdout, crlfDelay: Infinity });
    rl.on('line', (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let msg;
      try {
        msg = JSON.parse(trimmed);
      } catch {
        return;
      }
      const id = msg?.id;
      if (id == null || !this.pending.has(id)) return;
      const entry = this.pending.get(id);
      this.pending.delete(id);
      clearTimeout(entry.timer);
      entry.resolve(msg);
    });

    const failPending = (reason) => {
      for (const [id, entry] of this.pending.entries()) {
        clearTimeout(entry.timer);
        entry.reject(new Error(reason));
        this.pending.delete(id);
      }
    };

    proc.on('exit', () => {
      if (this.proc === proc) {
        this.proc = null;
        this.rl = null;
      }
      failPending('studio worker exited');
    });
    proc.on('error', (err) => {
      if (this.proc === proc) {
        this.proc = null;
        this.rl = null;
      }
      failPending(err?.message || 'studio worker spawn error');
    });

    this.proc = proc;
    this.rl = rl;
    return proc;
  }

  async call(action, workspace, params = {}) {
    const proc = this.ensureProcess();
    if (!proc) {
      const rt = this.ensureRuntime({ autoBootstrap: false });
      if (action === 'status') {
        return {
          ok: true,
          data: {
            state: 'runtime_unavailable',
            message: `${rt.reason}. ${rt.fixHint}`,
            service_version: '0.2.0',
            python_version: null,
            sqlite_version: null,
            project_id: null,
            store_initialized: false,
            recorded_device: null,
            observed_device: null,
            recover_command: null,
            probes: (rt.probes || []).slice(0, 8),
            fix_hint: rt.fixHint,
            overview: { total: 0, counts: [], generation: 0 },
            activity_health: {
              total_events: 0,
              events_today: 0,
              errors_24h: 0,
              last_event_ts: null,
              last_error_ts: null,
              last_error_code: null,
              counts_by_category: { recall: 0, read: 0, write: 0, error: 0 },
            },
          },
        };
      }
      return {
        ok: false,
        error: {
          code: 'runtime_unavailable',
          message: rt.reason || 'Nexus Python/SQLite runtime is unavailable',
        },
      };
    }

    const id = this.nextSeq++;
    const payload = JSON.stringify({ id, action, workspace: workspace ?? null, params: params ?? {} });

    return await new Promise((resolvePromise) => {
      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          this.stopProcess(new Error('studio worker timeout'));
          resolvePromise({
            ok: false,
            error: { code: 'worker_timeout', message: `Action ${action} timed out` },
          });
        }
      }, 10000);

      this.pending.set(id, {
        timer,
        resolve: resolvePromise,
        reject: (err) => {
          resolvePromise({
            ok: false,
            error: { code: 'worker_unavailable', message: err?.message || 'studio worker failed' },
          });
        },
      });

      try {
        proc.stdin.write(`${payload}\n`);
      } catch (err) {
        this.pending.delete(id);
        clearTimeout(timer);
        resolvePromise({
          ok: false,
          error: { code: 'worker_write_error', message: err?.message || 'failed to write to worker' },
        });
      }
    });
  }

  stopProcess(err) {
    if (this.rl) {
      try {
        this.rl.close();
      } catch {
        // ignore
      }
      this.rl = null;
    }
    if (this.proc) {
      try {
        this.proc.kill('SIGTERM');
      } catch {
        // ignore
      }
      this.proc = null;
    }
    for (const [id, entry] of this.pending.entries()) {
      clearTimeout(entry.timer);
      entry.reject(err || new Error('studio worker stopped'));
      this.pending.delete(id);
    }
  }

  close() {
    this.closed = true;
    this.stopProcess(new Error('studio worker closed'));
  }
}

function staticResponse(filePath, contentType) {
  const body = readFileSync(filePath, 'utf-8');
  return {
    status: 200,
    contentType,
    body,
    headers: {
      'Content-Security-Policy': CSP_HEADER,
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-store',
    },
  };
}

export async function createStudioApp({
  pluginRoot = process.env.PLUGIN_ROOT || PLUGIN_ROOT,
  pluginData = process.env.PLUGIN_DATA || '',
  env = process.env,
} = {}) {
  const { SidecarApp, Response } = await loadSidecarSdk();
  const app = new SidecarApp();
  const worker = new StudioWorkerClient({ pluginRoot, pluginData, env });

  const makePage = (filePath, contentType) => () => {
    const res = staticResponse(filePath, contentType);
    return new Response(res.body, {
      status: res.status,
      contentType: res.contentType,
      headers: res.headers,
    });
  };

  app.page('/', makePage(join(UI_DIR, 'index.html'), 'text/html; charset=utf-8'));
  app.page('/studio.css', makePage(join(UI_DIR, 'studio.css'), 'text/css; charset=utf-8'));
  app.page('/studio.js', makePage(join(UI_DIR, 'studio.js'), 'application/javascript; charset=utf-8'));
  app.page('/logo.svg', makePage(join(pluginRoot, 'assets', 'logo.svg'), 'image/svg+xml; charset=utf-8'));

  for (const [route, action] of WORKER_ACTIONS.entries()) {
    app.api(
      route,
      async (body = {}) => {
        const workspace = body && Object.prototype.hasOwnProperty.call(body, 'workspace') ? body.workspace : null;
        let params = {};
        if (body?.params && typeof body.params === 'object') {
          params = body.params;
        } else if (body && typeof body === 'object') {
          params = Object.fromEntries(Object.entries(body).filter(([k]) => k !== 'workspace' && k !== 'params'));
        }
        return await worker.call(action, workspace, params);
      },
      'POST',
    );
  }

  app.api(
    '/api/retry-runtime',
    async (body = {}) => {
      worker.invalidateRuntime();
      worker.ensureRuntime({ autoBootstrap: true });
      const workspace = body && Object.prototype.hasOwnProperty.call(body, 'workspace') ? body.workspace : null;
      return await worker.call('status', workspace, {});
    },
    'POST',
  );

  const server = await app.run();
  const close = async () => {
    worker.close();
    await app.close();
  };
  return { app, server, worker, close };
}

const isDirectEntry =
  Boolean(process.argv[1]) && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectEntry) {
  const studio = await createStudioApp();
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, async () => {
      await studio.close();
      process.exit(0);
    });
  }
}
