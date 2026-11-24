import assert from 'node:assert/strict'
import test from 'node:test'
import { initialState, projectActivity, reduceActivity } from './projection.mjs'

const noCandidate = (seq = 0, turn = seq) => ({ type: 'turn/end', seq, turn, live: true, current: true,
  committed: true, userRoot: true, inherited: false, completion: 'completed',
  result: 'no-candidate', reason: 'no-durable-candidate' })
const ready = (seq, turn) => ({ ...noCandidate(seq, turn), result: 'draft-ready', reason: 'metadata-only' })
const end = (targetSeq, reason) => ({ type: 'draft/end', targetSeq, reason, live: true, current: true })
const fold = (...events) => events.reduce(reduceActivity, initialState())

test('completed user-root turns show no candidate, metadata-only ready, or allowlisted blocked', () => {
  const none = { turn: 1, status: 'no-candidate', reason: 'no-durable-candidate' }
  const draft = { turn: 2, status: 'draft-ready', reason: 'metadata-only' }
  assert.deepEqual(projectActivity(fold(noCandidate(0, 1))), { latest: none, draft: null })
  assert.deepEqual(projectActivity(fold(ready(4, 2))), { latest: draft, draft })
  for (const reason of ['evidence-unverified', 'privacy-rejected', 'scope-unavailable']) {
    const blocked = { ...noCandidate(7, 3), result: 'blocked', reason }
    assert.deepEqual(projectActivity(fold(ready(4, 2), blocked)),
      { latest: { turn: 3, status: 'blocked', reason }, draft })
  }
})

test('children or generated turns, replay, inherited, stale, and uncommitted inputs are no-ops', () => {
  const initial = initialState()
  for (const gate of [{ userRoot: false }, { live: false }, { inherited: true }, { current: false },
    { committed: false }, { userRoot: undefined }, { committed: 'true' }, { type: 'turn/start' }]) {
    assert.equal(reduceActivity(initial, { ...ready(4, 2), ...gate }), initial)
  }
  const state = fold(ready(4, 2))
  for (const event of [ready(4, 2), ready(4, 3), ready(5, 2), ready(5, 1), ready(3, 3), ready(-1, 3),
    ready(9.5, 3), ready(10, Infinity), ready(Number.MAX_SAFE_INTEGER + 1, 3)]) {
    assert.equal(reduceActivity(state, event), state)
  }
})

test('failed and cancelled turns are not projected and cannot later become drafts for that turn', () => {
  for (const completion of ['failed', 'cancelled']) {
    const state = fold(ready(0, 0))
    assert.equal(reduceActivity(state, { ...ready(1, 1), completion }), state)
    const ended = reduceActivity(state, { ...noCandidate(1, 1), completion, result: null, reason: null })
    assert.deepEqual(projectActivity(ended), projectActivity(state))
    assert.equal(reduceActivity(ended, ready(2, 1)), ended)
    assert.equal(projectActivity(reduceActivity(ended, noCandidate(3, 2))).latest.turn, 2)
  }
})

test('expiry, replacement and dismissal are terminal and reference only the matching draft', () => {
  const state = fold(ready(4, 1))
  for (const [reason, status] of [['ttl', 'expired'], ['replaced', 'expired'], ['dismissed', 'dismissed']]) {
    const done = reduceActivity(state, end(4, reason))
    const row = { turn: 1, status, reason }
    assert.deepEqual(projectActivity(done), { latest: row, draft: row })
    assert.equal(reduceActivity(done, end(4, 'ttl')), done)
    assert.equal(reduceActivity(done, ready(4, 1)), done)
  }
  const replacement = reduceActivity(state, ready(5, 2))
  for (const event of [end(4, 'dismissed'), { ...end(5, 'dismissed'), live: false },
    { ...end(5, 'ttl'), current: false }, end(6, 'ttl')]) {
    assert.equal(reduceActivity(replacement, event), replacement)
  }
  const later = reduceActivity(replacement, noCandidate(6, 3))
  assert.deepEqual(projectActivity(reduceActivity(later, end(5, 'ttl'))), {
    latest: { turn: 3, status: 'no-candidate', reason: 'no-durable-candidate' },
    draft: { turn: 2, status: 'expired', reason: 'ttl' },
  })
  assert.equal(reduceActivity(initialState(), end(5, 'ttl')).draft, null)
})

test('rejects synthetic canary bodies, errors and unknown codes without retaining or echoing them', () => {
  const canary = 'SYNTHETIC_CAPTURE_CANARY_d391'
  const state = fold(ready(4, 1))
  for (const event of [{ ...ready(5, 2), body: canary }, { ...ready(5, 2), draft: { body: canary } },
    { ...ready(5, 2), [canary]: true }, { ...ready(5, 2), result: canary },
    { ...ready(5, 2), result: 'blocked', reason: canary }, { ...ready(5, 2), seq: canary },
    { ...ready(5, 2), turn: { toString() { throw Error(canary) } } }, { ...end(4, 'ttl'), error: canary },
    end(4, canary), { ...ready(5, 2), type: 'save' }, null]) {
    const next = reduceActivity(state, event)
    assert.equal(next === state, true, 'input unexpectedly changed projection')
    assert.equal(JSON.stringify(next).includes(canary), false)
    assert.equal(JSON.stringify(projectActivity(next)).includes(canary), false)
  }
  assert.deepEqual(Object.keys(state.draft), ['turn', 'status', 'reason'])
  assert.deepEqual(Object.keys(projectActivity(state).draft), ['turn', 'status', 'reason'])
  assert(Object.isFrozen(state.draft))
})

test('rejects accessors, proxies, inherited shapes and non-string extra keys', () => {
  const state = initialState()
  const fail = () => assert.fail('not safe to evaluate dynamic input')
  const accessor = Object.defineProperty(ready(), 'reason', { get: fail })
  const revocable = Proxy.revocable(ready(), {})
  revocable.revoke()
  for (const event of [accessor, Object.defineProperty(ready(), 'hidden', { value: 0 }),
    { ...ready(), [Symbol()]: 1 }, Object.create(ready()), new Proxy(ready(), { getPrototypeOf: fail }),
    revocable.proxy]) {
    assert.equal(reduceActivity(state, event), state)
  }
})

test('many turns retain constant-size state; restart starts empty and rejects replay', () => {
  let state = initialState()
  for (let n = 0; n < 10_000; n++) state = reduceActivity(state, n % 2 ? noCandidate(n) : ready(n))
  assert(JSON.stringify(state).length < 256)
  assert.equal(projectActivity(state).latest.turn, 9_999)
  assert.equal(projectActivity(state).draft.turn, 9_998)
  assert.deepEqual(projectActivity(fold({ ...ready(9_998), live: false }, end(9_998, 'ttl'))),
    { latest: null, draft: null })
})
