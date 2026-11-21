#!/usr/bin/env node
/**
 * agy-node entrypoint for Nexus Memory's Antigravity MCP server.
 *
 * Launches mcp_gateway.py inside a verified Python >= 3.12 + SQLite >= 3.51.3
 * environment. If no qualifying runtime is available, stays connected over stdio
 * as a zero-dependency diagnostic MCP server reporting `runtime_unavailable`
 * instead of silently closing the transport.
 */

import { spawn } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import { resolveRuntime } from './runtime.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = process.env.PLUGIN_ROOT || resolve(__dirname, '..');
const PLUGIN_DATA = process.env.PLUGIN_DATA || '';

function startDegradedMcpServer(runtimeResult) {
  const diagnostic = {
    scope_state: 'runtime_unavailable',
    durable_storage: 'unavailable',
    message: `${runtimeResult.reason}. ${runtimeResult.fixHint}`,
    project_id: null,
    recorded_device: null,
    observed_device: null,
    recover_command: null,
    enroll_command: null,
    service_version: '0.2.0',
    capture_policy: 'explicit',
    probes: (runtimeResult.probes || []).slice(0, 8),
  };

  const statusTool = {
    name: 'status',
    title: 'Nexus Memory Status (Runtime Unavailable)',
    description: 'Report why the Nexus Memory Python/SQLite runtime could not start and how to fix it.',
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    },
    inputSchema: {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
  };

  const send = (obj) => {
    process.stdout.write(`${JSON.stringify(obj)}\n`);
  };

  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      return;
    }
    if (!msg || typeof msg !== 'object') return;
    const { id, method, params } = msg;
    if (id === undefined || id === null) return;

    if (method === 'initialize') {
      send({
        jsonrpc: '2.0',
        id,
        result: {
          protocolVersion: params?.protocolVersion || '2024-11-05',
          capabilities: { tools: {} },
          serverInfo: { name: 'nexus-memory', version: '0.2.0' },
          instructions: 'Nexus Memory Python/SQLite runtime is unavailable. Call `status` for diagnostic and installation details.',
        },
      });
      return;
    }

    if (method === 'ping') {
      send({ jsonrpc: '2.0', id, result: {} });
      return;
    }

    if (method === 'tools/list') {
      send({ jsonrpc: '2.0', id, result: { tools: [statusTool] } });
      return;
    }

    if (method === 'tools/call') {
      if (params?.name === 'status') {
        send({
          jsonrpc: '2.0',
          id,
          result: {
            isError: false,
            structuredContent: diagnostic,
            content: [{ type: 'text', text: JSON.stringify(diagnostic) }],
          },
        });
      } else {
        send({
          jsonrpc: '2.0',
          id,
          result: {
            isError: true,
            content: [{ type: 'text', text: `runtime_unavailable: ${diagnostic.message}` }],
          },
        });
      }
      return;
    }

    if (method === 'resources/list') {
      send({ jsonrpc: '2.0', id, result: { resources: [] } });
      return;
    }

    send({
      jsonrpc: '2.0',
      id,
      error: { code: -32601, message: `Method not found: ${method}` },
    });
  });
}

function main() {
  const cmd = process.argv[2];
  if (cmd !== 'mcp') {
    process.stderr.write('Usage: launch.mjs mcp [expected-project]\n');
    process.exit(2);
  }

  const runtime = resolveRuntime({
    autoBootstrap: true,
    pluginRoot: PLUGIN_ROOT,
    pluginData: PLUGIN_DATA,
  });

  if (!runtime.ok) {
    process.stderr.write(`nexus-memory degraded startup: ${runtime.reason}\n`);
    startDegradedMcpServer(runtime);
    return;
  }

  const gateway = join(__dirname, 'mcp_gateway.py');
  const args = ['-I', '-B', gateway, ...process.argv.slice(3)];
  const child = spawn(runtime.python, args, {
    stdio: 'inherit',
    env: process.env,
    windowsHide: true,
  });

  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, () => {
      if (!child.killed) child.kill(sig);
    });
  }

  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code ?? 1);
  });
}

main();
