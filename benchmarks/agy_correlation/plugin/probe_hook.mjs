#!/usr/bin/env node
// Lifecycle hook for the correlation probe: records the event's key paths and identifier
// values, then answers with the event's neutral output so the host behaves as if no hook ran.

import { readFileSync } from 'node:fs';
import { envIds, idValues, keyPaths, write } from './probe_log.mjs';

const NEUTRAL = {
  PreToolUse: '{"decision":"allow"}\n',
  PostToolUse: '{}\n',
  Stop: '{}\n',
};

const event = process.argv[2] || 'unknown';
let payload = null;
try {
  const raw = readFileSync(0, 'utf-8');
  payload = raw.trim() ? JSON.parse(raw) : null;
} catch {
  payload = null;
}

const toolCall = payload && typeof payload.toolCall === 'object' ? payload.toolCall : null;
const args = toolCall && typeof toolCall.args === 'object' ? toolCall.args : null;
write({
  source: 'hook',
  event,
  parsed: payload !== null,
  keys: keyPaths(payload),
  ids: idValues(payload),
  tool: toolCall ? {
    id: typeof toolCall.id === 'string' ? toolCall.id : null,
    name: typeof toolCall.name === 'string' ? toolCall.name : null,
    // Server and tool names identify the target; they are routing metadata, not content.
    server: args && typeof args.ServerName === 'string' ? args.ServerName : null,
    target: args && typeof args.ToolName === 'string' ? args.ToolName : null,
  } : null,
  workspaces: Array.isArray(payload?.workspacePaths) ? payload.workspacePaths.length : null,
  env: envIds(),
});

process.stdout.write(NEUTRAL[event] ?? '{"injectSteps":[]}\n');
