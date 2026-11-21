#!/usr/bin/env node
/**
 * agy-node entrypoint for Antigravity SessionStart and PreInvocation hooks.
 * Never auto-bootstraps venvs inside the 5s hook timeout budget; fails open with
 * `{"injectSteps":[]}` if no verified runtime is available yet.
 */

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveRuntime } from './runtime.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = process.env.PLUGIN_ROOT || resolve(__dirname, '..');
const PLUGIN_DATA = process.env.PLUGIN_DATA || '';

function main() {
  const mode = process.argv[2];
  let stdinText = '';
  try {
    stdinText = readFileSync(0, 'utf-8');
  } catch {
    stdinText = '';
  }

  if (mode !== 'start' && mode !== 'recall') {
    process.stdout.write('{"injectSteps":[]}\n');
    return;
  }

  const runtime = resolveRuntime({
    autoBootstrap: false,
    pluginRoot: PLUGIN_ROOT,
    pluginData: PLUGIN_DATA,
  });

  if (!runtime.ok) {
    process.stdout.write('{"injectSteps":[]}\n');
    return;
  }

  const hookScript = join(__dirname, 'hooks.py');
  const res = spawnSync(runtime.python, ['-I', '-B', hookScript, mode], {
    input: stdinText,
    encoding: 'utf-8',
    timeout: 4500,
    windowsHide: true,
    env: process.env,
  });

  if (res.status === 0 && (res.stdout || '').trim()) {
    process.stdout.write(res.stdout);
    return;
  }
  process.stdout.write('{"injectSteps":[]}\n');
}

main();
