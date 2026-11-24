// Runs only in the launcher's isolated child. Uses actual DSH source services.
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { Context } from '@deepseek-ai/cordis'
import AgentLoop from '@deepseek-ai/dsh-agent-loop'
import { mountAgentLoopTestDependencies } from '@deepseek-ai/dsh-agent-loop-testkit'
import { createUserMessage, LlmAdapter, type GenerateOptions, type StreamChunk } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'
import { observeFixtureTurn } from './adapter.mjs'

const cwd = process.cwd()
assert.equal(process.env.HOME, cwd)
assert.equal(process.env.DSH_HOME, cwd)
const scenario = process.argv[2]
assert.ok(scenario === 'valid' || scenario === 'cancelled' || scenario === 'wrong-input-id')
const cancelled = scenario === 'cancelled'
const route = 'capture-probe-local'
const empty = { latest: null, draft: null }
let calls = 0
let toolSchemas = -1
let release!: () => void
const resume = new Promise<void>(resolve => { release = resolve })
let entered!: (signal: AbortSignal) => void
const atModel = new Promise<AbortSignal>(resolve => { entered = resolve })

class FixtureModel extends LlmAdapter {
  override async resolveModel(provider: string, model: string) {
    return { provider, id: model, name: model }
  }

  override async * stream(options: GenerateOptions): AsyncIterable<StreamChunk> {
    calls++
    toolSchemas = options.tools?.length ?? 0
    assert.ok(options.signal, 'model invocation must be cancellable')
    entered(options.signal)
    await resume
    if (options.signal.aborted) return
    const text = 'Synthetic reply.'
    yield { type: 'block-start', index: 0, blockType: 'text' }
    yield { type: 'text-delta', index: 0, text }
    yield { type: 'block-end', index: 0, block: { type: 'text', text } }
    yield { type: 'finish', reason: { kind: 'stop' } }
  }
}

const ctx = new Context()
try {
  await mountAgentLoopTestDependencies(ctx, { workingDirectory: true })
  await ctx.plugin(AgentLoop, { agents: [] })
  await ctx.plugin({
    inject: ['llm'],
    apply(context: Context) { context.llm.registerAdapter([route], new FixtureModel()) },
  })
  const { agent } = await ctx.agents.create({
    sessionId: SessionId(randomUUID()),
    agentOptions: { provider: route, model: route },
    meta: { cwd },
  })
  assert.equal(ctx.agents.roots().length, 1)
  assert.equal(ctx.agents.roots()[0], agent)
  assert.deepEqual(ctx.tools.schemas(), [])
  assert.deepEqual(ctx.tools.schemas(agent), [])
  const prompt = createUserMessage({ source: { kind: 'user' }, content: [
    { type: 'text', text: 'Reply with the synthetic greeting.' },
  ] })
  const expectedInputId = scenario === 'wrong-input-id' ? randomUUID() : prompt.id
  const endNotifications: number[] = []
  const removeTap = ctx.on('session/event', (source, event) => {
    if (source === agent.session && event.type === 'turn/end') endNotifications.push(event.seq)
  })
  const probe = observeFixtureTurn(ctx, agent, cwd, expectedInputId)
  try {
    assert.deepEqual(probe.read(), empty)
    agent.followup(prompt)
    const idle = agent.whenIdle()
    const signal = await Promise.race([atModel, idle.then(() => {
      throw new Error('fixture turn ended before the model gate')
    })])
    assert.deepEqual(probe.read(), empty, 'no projection before the mock finishes')
    assert.deepEqual(endNotifications, [], 'runtime has not emitted an end yet')
    assert.equal(probe.readDecision(), null, 'no adapter decision before the end')
    assert.equal(signal.aborted, false)
    if (cancelled) {
      agent.cancel({ kind: 'user' })
      assert.equal(signal.aborted, true, 'Agent.cancel must abort the in-flight stream')
    }
    release()
    await idle
    assert.equal(calls, 1)
    assert.equal(toolSchemas, 0)
    const events = agent.session.ownEvents()
    const ends = events.filter(event => event.type === 'turn/end')
    assert.equal(ends.length, 1, 'runtime must commit an end, even for a refused turn')
    const end = ends[0]!
    assert.deepEqual(endNotifications, [end.seq], 'runtime must publish the committed end')
    assert.equal(end.data.turn, 1)
    assert.deepEqual(end.data.reason, cancelled
      ? { kind: 'aborted', reason: { kind: 'user' } }
      : { kind: 'completed' })
    const starts = events.filter(event => event.type === 'turn/start')
    const inputs = events.filter(event => event.type === 'user/message' && event.data.source.kind === 'user')
    assert.deepEqual(starts.map(event => event.data.turn), [1])
    assert.deepEqual(inputs.map(event => event.data.id), [prompt.id])
    assert.ok(starts[0]!.seq < inputs[0]!.seq && inputs[0]!.seq < end.seq)
    const fixtureInputMatches = inputs[0]!.data.id === expectedInputId
    assert.equal(fixtureInputMatches, scenario !== 'wrong-input-id')
    assert.equal(events.filter(event => event.type === 'assistant/message').length, cancelled ? 0 : 1)
    assert.equal(events.some(event => event.type === 'tool/call' || event.type === 'tool/result'), false)
    const activity = probe.read()
    const decision = probe.readDecision()
    assert.deepEqual(decision, { seq: end.seq, outcome: cancelled ? 'non-completed'
      : scenario === 'wrong-input-id' ? 'no-matching-fixture-input' : 'accepted' })
    assert.deepEqual(activity, scenario === 'valid' ? {
      latest: { turn: 1, status: 'no-candidate', reason: 'no-durable-candidate' },
      draft: null,
    } : empty)
    probe.dispose()
    assert.deepEqual(probe.read(), empty)
    assert.equal(probe.readDecision(), null)
    console.log(JSON.stringify({ scenario, end: { turn: end.data.turn, seq: end.seq, reason: end.data.reason },
      endNotifications: endNotifications.length, fixtureInputMatches, decision, mockCalls: calls,
      toolSchemas, toolCalls: 0, activity, clearedOnDispose: true }))
  } finally {
    release()
    probe.dispose()
    removeTap()
  }
} finally {
  await ctx.fiber.dispose()
}
