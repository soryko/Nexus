# Disposable capture activity projection spike

Question: can the pilot plan's [§3B / §6 fallback](../../../docs/superpowers/plans/2026-11-19-dsh-nexus-capture-pilot.md#b-surface-one-lesson-at-turn-completion) show useful turn outcomes and one pending draft using **only** synthetic metadata? Yes, given a trusted adapter that supplies the facts below. This does not prove that DSH can accept private proposal bodies or that humans alone can approve writes.

From the repository root:

- Tests: `node --test benchmarks/dsh_capture/activity/*.test.mjs`
- Interactive synthetic transitions: `node benchmarks/dsh_capture/activity/demo.mjs`

## Normalized contract

One in-memory state per enrolled root-session instance and checkout/config generation. Start with `initialState()`; call `reduceActivity(state, event)` and expose only `projectActivity(state)`.

| Event (exact fields) | Accepted values |
| --- | --- |
| `{type: 'turn/end', seq, turn, live, current, committed, userRoot, inherited, completion, result, reason}` | Nonnegative safe-integer `seq` and `turn`, each strictly increasing among accepted end events. Require `live === true`, `current === true`, `committed === true`, `userRoot === true`, `inherited === false`. A completed turn takes one pair below. Normalize a known unsuccessful end to `completion: 'failed'` or `'cancelled'` with null result and reason; it advances only private watermarks. |
| `{type: 'draft/end', targetSeq, live, current, reason}` | Require `live === true` and `current === true`. `targetSeq` must be the original completion sequence of the pending draft in this instance. `ttl` or `replaced` → `expired`; `dismissed` → `dismissed`. First valid terminal transition wins. |

Completed-turn status/reason pairs:

- `no-candidate` / `no-durable-candidate`
- `draft-ready` / `metadata-only`
- `blocked` / one of `evidence-unverified`, `privacy-rejected`, `scope-unavailable`

The view contains `latest` (last projected turn outcome) and `draft` (a single pending or terminal draft slot). Each is null or exactly `{turn, status, reason}`. A newer ready draft atomically replaces the old slot; ordinary no-candidate or blocked turns do not replace it. If a lifecycle event ends the same draft shown in `latest`, both rows reflect the terminal status. There is no history, and retained size is constant. Malformed or ineligible events are silent no-ops. No body, evidence text, IDs from proposals, exceptions, or paths enter the reducer; declared values are primitives and unexpected fields are rejected rather than redacted.

## Adapter obligations (no adapter implemented)

1. **Bind and isolate.** Bind state and every input to the same live session object/identity, owner, canonical enrolled checkout/worktree, and config generation. Route only to that instance and check at delivery as well as staging. Reset/remove state, pending timers, and queued signals on restart, teardown, disable, or scope/generation change; never hydrate/replay this state. The reducer cannot distinguish a misrouted event with colliding ordinals or verify caller-supplied flags.
2. **Use the live commit seam.** The existing DSH Nexus `hooks-core.js` fold resets per-turn fields on `turn/start`, ignores `event.seq < inheritedEventCount`, detects children via `header.parentSession` or positive `header.delegationDepth`, and records the terminal turn/sequence. In `hooks.js` the `session/event` listener checks `ctx.agents.get(session.id)?.session === session`, then matches `state.ended.seq === event.seq` for `turn/end`. The fixture in `tests/hooks.test.mjs` folds before listener publication, verifies no capture until committed completion, and rejects nested publication. Match both `event.data.turn` and `event.seq` to the current completed turn after commit; the only live success is `event.data.reason.kind === 'completed'`. Do not infer completion from tool results or assistant output. The existing content-bearing fold is not the activity schema or a private handoff.
3. **Prove origin and order.** Explicitly verify a genuine human-initiated root turn and non-inherited local start/origin/evidence/end, not just a parentless session or a post-commit callback. Reject workers, generated continuations, fork/inherited rows, replay/backfill, stale completions and mismatched turn/checkout; absence of provenance is not proof. Use only verified Host-owned ordinals; serialize end events in strictly increasing turn and commit-sequence order, emitting at most one normalized classification per end. Do not reclassify a rejected or unsuccessful end as ready. A lifecycle callback cannot create a draft, and nothing should append to the Session during publication or wait on this spike on the response path.
4. **Derive readiness without text ingress.** For this fallback, use disposable synthetic signals only. A future adapter would have to verify source identity, allowed evidence type, committed same-turn/root membership, evidence relevance, checkout, bounds, and sensitivity before emitting ready; an explicit Remember must not create a second draft. Construct a fresh object of only the exact keys above; never spread raw events, source metadata, tool arguments, bodies, transcripts, paths, destination strings, or exception text. Use only enumerated reason codes, including in diagnostics. The canary test checks this local rejection/projection boundary, not Host logs, exports, or model context.
5. **Own draft lifetime.** Serialize transitions for the bound draft. In a future adapter, start the 30-minute timer on ready; invalidate at/past the deadline before serving stale ready state, not only when a timer happens to fire. Resolve dismissal to the owner/session and exact original completion sequence; drop delayed, replayed or mismatched callbacks. New ready supersedes the prior slot; cancel old timers and remove state on restart rather than recovering it. The reducer reads no clock and cannot authenticate dismissal.

## Stop here

No registration, activation, plugin or configuration changes, tool/RPC/UI binding, real proposal ingress, Save, approval, Nexus access, durable payload, or recovery. The demo is a local stdin menu only. Do not deploy it or point it at DSH events. A verified private body ingress, independent approval authority and isolated recovery binding remain separate §6 gates.
