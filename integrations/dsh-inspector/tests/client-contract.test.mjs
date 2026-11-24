import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// No DSH install needed: a stale standalone browser codec is a boot blocker,
// even if the Host contract parses correctly in isolation.
test('packaged client embeds the canonical host descriptor factory', () => {
  assert.doesNotThrow(() => execFileSync(process.execPath, [
    fileURLToPath(new URL('../build-client.mjs', import.meta.url)), '--check',
  ]));
});
