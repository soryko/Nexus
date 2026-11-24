import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { init, apply } from './projection.js';

const root = { id: 'root-id', isSeeded: false };
const key = (seq, id = root.id) => 'dsh-memory:' + createHash('sha256').update(`${id}:${seq}`).digest('hex');
const user = { role: 'user', source: { kind: 'user' }, get content() { throw Error('body read'); } };
const tool = turn => ({ turn, message: { isError: false, get content() { throw Error('tool body read'); } } });
const notice = (seq, override) => ({ inserted: [{ role: 'user', get content() { throw Error('notice body read'); },
  source: { kind: 'nexus-memory', form: 'notice', operation: 'capture', outcome: 'success',
    memoryId: 'mem-private', revisionId: 'rev-private', key: key(seq), budgetTurn: 91, ...override } }] });
function log(header = root, inherited = 0) {
  let state = init(header, inherited), seq = 0;
  return { get state() { return state; }, push(type, data) { state = apply(state, { seq, type, data }); return seq++; } };
}

test('a committed retry points to the original user turn; metadata-only view and checkpoint', () => {
  const l = log();
  l.push('turn/start', { turn: 1 });
  const original = l.push('user/message', user);
  l.push('turn/end', { turn: 1 });
  l.push('turn/start', { turn: 91 });
  const oldView = l.state.view;
  l.push('tool/result', tool(91));
  assert.equal(l.state.view, oldView);
  const receiptSeq = l.push('agent/inbox/spliced', notice(original));
  assert.deepEqual(l.state.view, { turns: { 1: [{ seq: receiptSeq }] } });
  const confirmed = l.state;
  l.push('agent/inbox/spliced', notice(original));
  assert.equal(l.state, confirmed);
  assert.doesNotMatch(JSON.stringify(l.state), /mem-private|rev-private/);
});

test('only singleton capture-success notices with valid IDs and an eligible session-bound origin count', () => {
  const l = log();
  const outside = l.push('user/message', user);
  l.push('turn/start', { turn: 2 });
  const faux = l.push('user/message', { role: 'user', source: { kind: 'nexus-memory' } });
  const failed = l.push('tool/result', { ...tool(2), error: {} });
  const valid = l.push('tool/result', tool(2));
  const prior = l.state;
  for (const n of [notice(outside), notice(faux), notice(failed), notice(valid, { key: key(valid, 'foreign') }),
    notice(valid, { outcome: 'prepared' }), notice(valid, { operation: 'recall' }), notice(valid, { revisionId: '' }),
    { inserted: [...notice(valid).inserted, ...notice(valid).inserted] }]) l.push('agent/inbox/spliced', n);
  assert.equal(l.state, prior);
  const seq = l.push('agent/inbox/spliced', notice(valid));
  assert.deepEqual(l.state.view, { turns: { 2: [{ seq }] } });
});

test('fork, inherited, seeded, and child sessions never gain cues', () => {
  for (const [extra, inherited] of [[{}, 1], [{ parentSession: 'p' }, 0], [{ origin: 'subagent' }, 0],
    [{ delegationDepth: 1 }, 0], [{ isSeeded: true }, 0]]) {
    const l = log({ ...root, ...extra }, inherited), initial = l.state;
    l.push('turn/start', { turn: 1 });
    l.push('agent/inbox/spliced', notice(l.push('user/message', user)));
    assert.equal(l.state, initial);
  }
});

test('origin lookup and projected turns and receipts stay bounded', () => {
  const l = log();
  for (let i = 0; i < 257; i++) l.push('tool/result', tool(1));
  l.push('agent/inbox/spliced', notice(0));
  assert.equal(l.state.origins.length, 256);
  assert.deepEqual(l.state.view, { turns: {} });
  for (let turn = 1; turn <= 33; turn++) {
    for (let j = 0; j < 9; j++) l.push('agent/inbox/spliced', notice(l.push('tool/result', tool(turn))));
  }
  const turns = l.state.view.turns;
  assert.equal(Object.keys(turns).length, 32);
  assert.equal(turns[1], undefined);
  assert.ok(Object.values(turns).every(rows => rows.length === 8));
});
