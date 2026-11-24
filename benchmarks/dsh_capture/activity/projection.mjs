// Disposable §3B / §6 spike. No Host imports, registration, I/O, or durable state.
import { types } from 'node:util'

const blockedReasons = new Set(['evidence-unverified', 'privacy-rejected', 'scope-unavailable'])
const turnKeys = ['type', 'seq', 'turn', 'live', 'current', 'committed', 'userRoot', 'inherited', 'completion', 'result', 'reason']
const draftKeys = ['type', 'targetSeq', 'live', 'current', 'reason']
const ordinal = n => Number.isSafeInteger(n) && n >= 0
const row = (turn, status, reason) => Object.freeze({ turn, status, reason })

// Do not execute getters, proxies, or coercions; do not inspect extra values.
function exact(event, keys) {
  if (Reflect.ownKeys(event).length !== keys.length) return null
  const fields = Object.create(null)
  for (const key of keys) {
    const field = Object.getOwnPropertyDescriptor(event, key)
    if (!field || !Object.hasOwn(field, 'value')) return null
    fields[key] = field.value
  }
  return fields
}

export function initialState() {
  return Object.freeze({ lastSeq: -1, lastTurn: -1, latest: null, draftSeq: -1, draft: null })
}

export function reduceActivity(state, event) {
  if (event === null || typeof event !== 'object' || types.isProxy(event)) return state
  const proto = Object.getPrototypeOf(event)
  if (proto !== Object.prototype && proto !== null) return state
  switch (Object.getOwnPropertyDescriptor(event, 'type')?.value) {
    case 'turn/end': {
      const e = exact(event, turnKeys)
      if (!e || e.live !== true || e.current !== true || e.committed !== true || e.userRoot !== true
        || e.inherited !== false || !ordinal(e.seq) || e.seq <= state.lastSeq
        || !ordinal(e.turn) || e.turn <= state.lastTurn) return state
      if (e.completion === 'failed' || e.completion === 'cancelled') {
        if (e.result !== null || e.reason !== null) return state
        return Object.freeze({ ...state, lastSeq: e.seq, lastTurn: e.turn })
      }
      if (e.completion !== 'completed') return state
      const valid = (e.result === 'no-candidate' && e.reason === 'no-durable-candidate')
        || (e.result === 'draft-ready' && e.reason === 'metadata-only')
        || (e.result === 'blocked' && blockedReasons.has(e.reason))
      if (!valid) return state
      const latest = row(e.turn, e.result, e.reason)
      return Object.freeze({ lastSeq: e.seq, lastTurn: e.turn, latest,
        draftSeq: e.result === 'draft-ready' ? e.seq : state.draftSeq,
        draft: e.result === 'draft-ready' ? latest : state.draft })
    }
    case 'draft/end': {
      const e = exact(event, draftKeys)
      if (!e || e.live !== true || e.current !== true || !ordinal(e.targetSeq)
        || e.targetSeq !== state.draftSeq || state.draft?.status !== 'draft-ready') return state
      const status = e.reason === 'ttl' || e.reason === 'replaced' ? 'expired'
        : e.reason === 'dismissed' ? 'dismissed' : null
      if (!status) return state
      const draft = row(state.draft.turn, status, e.reason)
      return Object.freeze({ ...state, latest: state.latest === state.draft ? draft : state.latest, draft })
    }
    default: return state
  }
}

// Only for state returned by this module. Do not hydrate state from external data.
export function projectActivity(state) {
  return Object.freeze({ latest: state.latest, draft: state.draft })
}
