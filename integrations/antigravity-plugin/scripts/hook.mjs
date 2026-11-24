#!/usr/bin/env node
/**
 * agy-node entrypoint for Antigravity lifecycle hooks (SessionStart, PreInvocation,
 * PreToolUse, PostToolUse, Stop).
 * Never auto-bootstraps venvs inside the 5s hook timeout budget; fails open with
 * mode-appropriate safe JSON if no verified runtime is available yet.
 */

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveRuntime } from './runtime.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = process.env.PLUGIN_ROOT || resolve(__dirname, '..');
const PLUGIN_DATA = process.env.PLUGIN_DATA || '';
const VALID_MODES = new Set(['start', 'recall', 'pre-tool', 'post-tool', 'stop']);

function fallbackOutput(mode) {
  if (mode === 'pre-tool') {
    return '{"decision":"allow"}\n';
  }
  if (mode === 'post-tool' || mode === 'stop') {
    return '{}\n';
  }
  return '{"injectSteps":[]}\n';
}

function main() {
  const mode = process.argv[2];
  let stdinText = '';
  try {
    stdinText = readFileSync(0, 'utf-8');
  } catch {
    stdinText = '';
  }

  if (!VALID_MODES.has(mode)) {
    process.stdout.write(fallbackOutput(mode));
    return;
  }

  const runtime = resolveRuntime({
    autoBootstrap: false,
    pluginRoot: PLUGIN_ROOT,
    pluginData: PLUGIN_DATA,
  });

  if (!runtime.ok) {
    process.stdout.write(fallbackOutput(mode));
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
  process.stdout.write(fallbackOutput(mode));
}

main();
