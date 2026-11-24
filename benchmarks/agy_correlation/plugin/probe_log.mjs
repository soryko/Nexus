// Shared, content-free logging for the correlation probe.
//
// The probe exists to answer one question: is there an identifier that appears both in an
// Antigravity PreToolUse hook payload and in the MCP tools/call request for the same tool call?
// So it records key paths everywhere, but values only for keys whose names look like
// identifiers, and never for keys that carry text (prompts, tool arguments, content).

import { appendFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// A value is recorded only under a strictly id-shaped key, or a loosely id-like key that does not
// also look like text (so "requestText" or "sessionSummary" are never valued).
const STRICT_ID_KEY = /(^id$|_id$|[a-z]Id$|ID$|^uuid$|Uuid$)/;
const LOOSE_ID_KEY = /(session|conversation|trajectory|cascade|invocation|request|step|turn|correlation|trace|span)/i;
const TEXT_KEY = /(input|prompt|content|text|message|body|query|description|instruction|args|arguments|summary|title|label|note|reason|name)/i;

function isIdKey(k) {
  return STRICT_ID_KEY.test(k) || (LOOSE_ID_KEY.test(k) && !TEXT_KEY.test(k));
}
const ENV_NAME = /(SESSION|CONVERSATION|TRAJECTORY|CASCADE|AGY|ANTIGRAVITY|GEMINI|PLUGIN|WORKSPACE|MCP|TRACE)/i;
const ENV_SECRET = /(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|COOKIE)/i;
const MAX_VALUE = 256;
const MAX_DEPTH = 8;

export function logPath() {
  if (process.env.NEXUS_PROBE_LOG) return process.env.NEXUS_PROBE_LOG;
  const dir = process.env.PLUGIN_DATA || join(tmpdir(), 'nexus-correlation-probe');
  return join(dir, 'correlation-probe.jsonl');
}

export function write(record) {
  const path = logPath();
  try {
    mkdirSync(join(path, '..'), { recursive: true, mode: 0o700 });
    appendFileSync(path, `${JSON.stringify({ t: Date.now(), hr: process.hrtime.bigint().toString(), pid: process.pid, ppid: process.ppid, ...record })}\n`, { mode: 0o600 });
  } catch {
    // A diagnostic must never break the host.
  }
}

/** Every key path in `value`, e.g. "toolCall.args.ServerName". Values are not included. */
export function keyPaths(value, prefix = '', depth = 0, out = []) {
  if (depth > MAX_DEPTH || value === null || typeof value !== 'object') return out;
  for (const [k, v] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${k}` : k;
    out.push(path);
    keyPaths(v, path, depth + 1, out);
  }
  return out;
}

/** Scalar values whose key names look like identifiers, keyed by path. Text-bearing keys are skipped whole. */
export function idValues(value, prefix = '', depth = 0, out = {}) {
  if (depth > MAX_DEPTH || value === null || typeof value !== 'object') return out;
  for (const [k, v] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (TEXT_KEY.test(k) && !STRICT_ID_KEY.test(k)) continue;
    if (v !== null && typeof v === 'object') {
      idValues(v, path, depth + 1, out);
    } else if (isIdKey(k) && (typeof v === 'string' || typeof v === 'number')) {
      out[path] = String(v).slice(0, MAX_VALUE);
    }
  }
  return out;
}

/** Environment variables that could carry a session or host identity; secret-looking names are named but never valued. */
export function envIds() {
  const out = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (!ENV_NAME.test(name)) continue;
    out[name] = ENV_SECRET.test(name) ? '<withheld>' : String(value).slice(0, MAX_VALUE);
  }
  return out;
}
