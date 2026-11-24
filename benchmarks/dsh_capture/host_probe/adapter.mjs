// Test-only one-shot Host observer. No tools, RPC, storage, or body ingress.
import { initialState, projectActivity, reduceActivity } from '../activity/projection.mjs'

export function observeFixtureTurn(ctx, agent, cwd, expectedInputId) {
  const session = agent.session
  const firstSeq = session.seq
  const isBoundRoot = () => ctx.agents.get(agent.id) === agent
    && agent.session === session && session.header.cwd === cwd
    && session.header.isSeeded === false && session.inheritedEventCount === 0
    && session.header.parentSession === undefined && session.header.origin === undefined
    && (session.header.delegationDepth ?? 0) === 0
  if (!isBoundRoot() || firstSeq !== session.firstLifecycleSeq) {
    throw new Error('fixture observer requires a fresh bound root')
  }

  let state = initialState()
  let start = null
  let inputSeq = null
  let sealed = false
  let disposed = false
  let decision = null
  const remove = ctx.on('session/event', (source, event) => {
    if (sealed || source !== session || !isBoundRoot()) return
    const { type, seq } = event
    if (type !== 'turn/start' && type !== 'user/message' && type !== 'turn/end') return
    if (seq < firstSeq || !session.isOwnSeq(seq) || session.eventAt(seq) !== event) return
    if (type === 'turn/start') {
      if (start !== null) sealed = true
      else start = { turn: event.data.turn, seq }
    } else if (type === 'user/message') {
      if (start !== null && seq > start.seq && event.data.id === expectedInputId
        && event.data.source.kind === 'user') inputSeq = seq
    } else {
      sealed = true // Never reclassify, including on failed or cancelled ends.
      const { turn, reason } = event.data
      const outcome = start === null || turn !== start.turn || seq !== session.seq - 1
        ? 'invalid-turn-boundary'
        : reason.kind !== 'completed' ? 'non-completed'
        : inputSeq === null || seq <= inputSeq ? 'no-matching-fixture-input'
        : 'accepted'
      decision = { seq, outcome }
      if (outcome !== 'accepted') return
      state = reduceActivity(state, {
        type: 'turn/end', seq, turn,
        live: true, current: true, committed: true, userRoot: true, inherited: false,
        completion: 'completed', result: 'no-candidate', reason: 'no-durable-candidate',
      })
    }
  })

  return {
    readDecision: () => decision && { ...decision },
    read: () => projectActivity(isBoundRoot() ? state : initialState()),
    dispose() {
      if (disposed) return
      disposed = true
      sealed = true
      state = initialState()
      decision = null
      start = null
      inputSeq = null
      remove()
    },
  }
}
