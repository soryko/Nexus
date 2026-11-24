import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { loadRoute, replayCase } from './route.mjs'

const [smoke] = JSON.parse(readFileSync(new URL('./route-smoke.json', import.meta.url))).cases
const route = await loadRoute()

test('installed handler searches once and offers only a current pinned body', async () => {
  const result = await replayCase(route, smoke)
  assert.equal(result.complete, true)
  assert.deepEqual(result.calls, [
    { name: 'mcp__nexus__search', arguments: { query: '"inspect" OR "ceramic" OR "kiln"', advanced: true, limit: 4 } },
    ...smoke.responses[0].hits.slice(0, 2).map(arguments_ => ({ name: 'mcp__nexus__get', arguments: arguments_ })),
  ])
  assert.deepEqual(result.candidate_ids, ['fixture-quartz'])
  assert.match(result.injection, /Quartz tile cooled slowly/)
  assert.doesNotMatch(result.injection, /Retired glaze|fixture-amber|fixture-cobalt/)
  assert.deepEqual(await replayCase(route, smoke), result)
})

test('missing search reply differs from observed empty search and skipped query', async () => {
  const missing = await replayCase(route, { ...smoke, responses: [] })
  assert.equal(missing.complete, false)
  assert.equal(missing.receipt.failed, true)
  const empty = await replayCase(route, { ...smoke, responses: [{ hits: [] }] })
  assert.equal(empty.complete, true)
  assert.equal(empty.receipt.hitCount, 0)
  const skip = await replayCase(route, { ...smoke, prompt: 'ok', responses: [] })
  assert.deepEqual(skip.calls, [])
  assert.equal(skip.complete, true)
  assert.equal(skip.receipt, null)
})
