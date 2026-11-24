// Synthetic terminal demo; input is only a menu selector, never an event field.
import { createInterface } from 'node:readline'
import { initialState, projectActivity, reduceActivity } from './projection.mjs'

let state = initialState()
let seq = 0
const controls = createInterface({ input: process.stdin, terminal: false })
function render() {
  if (process.stdout.isTTY) console.clear()
  console.log(JSON.stringify(projectActivity(state), null, 2))
  console.log('Enter: n = no candidate, r = ready, b = blocked, t = expire, d = dismiss, x = restart, q = quit')
}
render()
for await (const key of controls) {
  if (key === 'q') break
  if (key === 'x') state = initialState()
  if (key === 'n' || key === 'r' || key === 'b') {
    const [result, reason] = key === 'n' ? ['no-candidate', 'no-durable-candidate']
      : key === 'r' ? ['draft-ready', 'metadata-only'] : ['blocked', 'evidence-unverified']
    state = reduceActivity(state, { type: 'turn/end', seq, turn: seq++, live: true, current: true,
      committed: true, userRoot: true, inherited: false, completion: 'completed', result, reason })
  }
  if (key === 't' || key === 'd') {
    state = reduceActivity(state, { type: 'draft/end', targetSeq: state.draftSeq, live: true, current: true,
      reason: key === 't' ? 'ttl' : 'dismissed' })
  }
  render()
}
