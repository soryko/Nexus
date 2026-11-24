#!/usr/bin/env node
// Minimal MCP stdio server for the correlation probe. It exposes one tool, probe_echo, and
// records each request's JSON-RPC id, method, _meta and parameter key paths (never argument
// text other than the probe's own short label), plus this process's identity at start-up.

import { createInterface } from 'node:readline';
import { envIds, idValues, keyPaths, logPath, write } from './probe_log.mjs';

const SERVER_INFO = { name: 'nexus-correlation-probe', version: '0.1.0' };
const TOOL = {
  name: 'probe_echo',
  description: 'Diagnostic: returns its label. Call it when asked to run the correlation probe.',
  inputSchema: {
    type: 'object',
    properties: { label: { type: 'string', maxLength: 64, description: 'Short label for this call, e.g. a1' } },
    required: ['label'],
  },
};

write({ source: 'server', event: 'start', argv: process.argv.slice(2), cwd: process.cwd(), env: envIds() });

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function handle(message) {
  const params = message.params && typeof message.params === 'object' ? message.params : {};
  const label = typeof params.arguments?.label === 'string' ? params.arguments.label.slice(0, 64) : null;
  write({
    source: 'server',
    event: 'request',
    method: message.method ?? null,
    rpc_id: message.id ?? null,
    meta: params._meta ?? null,
    keys: keyPaths(params),
    ids: idValues(params),
    tool: message.method === 'tools/call' ? { name: params.name ?? null, label } : null,
    client: message.method === 'initialize' ? { info: params.clientInfo ?? null, capabilities: keyPaths(params.capabilities ?? {}) } : null,
  });

  if (message.id === undefined || message.id === null) return; // notification
  switch (message.method) {
    case 'initialize':
      send({ jsonrpc: '2.0', id: message.id, result: {
        protocolVersion: params.protocolVersion || '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: SERVER_INFO,
      } });
      return;
    case 'ping':
      send({ jsonrpc: '2.0', id: message.id, result: {} });
      return;
    case 'tools/list':
      send({ jsonrpc: '2.0', id: message.id, result: { tools: [TOOL] } });
      return;
    case 'tools/call':
      if (params.name !== TOOL.name) {
        send({ jsonrpc: '2.0', id: message.id, error: { code: -32602, message: `unknown tool ${params.name}` } });
        return;
      }
      send({ jsonrpc: '2.0', id: message.id, result: {
        content: [{ type: 'text', text: `probe ok ${label ?? ''} (pid ${process.pid}); log: ${logPath()}` }],
      } });
      return;
    default:
      send({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: `method not found: ${message.method}` } });
  }
}

createInterface({ input: process.stdin }).on('line', (line) => {
  if (!line.trim()) return;
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    write({ source: 'server', event: 'unparsable_line', length: line.length });
    return;
  }
  handle(message);
});
process.stdin.on('end', () => write({ source: 'server', event: 'stdin_end' }));
