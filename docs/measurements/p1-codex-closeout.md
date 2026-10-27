# P1 closeout: the `p1-codex` segment

A segment of [the P1 usage pilot](../pilot-p1.md), closed on 2026-10-27 as **inconclusive**.
Nothing was ever captured, so the question the pilot asks — can context captured in one
session be found, checked and applied in a later one — never had an opportunity to be
answered. This account is sanitized: it carries no session files, no memory bodies, no
private paths and no real project identifiers.

## Configuration

| | |
| --- | --- |
| Client | Codex Desktop; the manifest pins engine 0.160.1 |
| Runtime | Nexus 0.1.0a2, a normal (non-editable) installation |
| Consultation policy | MCP tools only: no lifecycle hooks, no per-prompt recall, no consultation instructions |
| Routine | [`docs/daily-use.md`](../daily-use.md) at `f60967a` |
| Scope | one participant, one project (private alias `project-b`), one unchanged `(database, namespace, actor)` |
| Reporter | `tools/summarize_pilot.py` at `54a99b2`; exit 0, `protocol_deviations: []`; output in [`p1-codex-summary-as-run.json`](p1-codex-summary-as-run.json) |

The project is a coordination workspace whose work is largely delegated to subagents. A
sibling segment, `p1-claude-code`, runs Claude Code against a separate database; it has not
started and is never aggregated with this one. An earlier segment, `p1-first-use`, closed
without starting.

## Duration and stop reason

Started 2026-10-07T08:26:19Z. Stopped by the **fourteen-day limit** at 2026-10-21T08:26:19Z,
with 2 of 10 sessions recorded. No early stop was declared.

## Recorded sessions

| | s01 (day 1) | s02 (day 6) |
| --- | --- | --- |
| state / task outcome | completed / completed | completed / completed |
| consultation | `not_attempted` | `attempted` — one search, zero hits |
| captures | 0 | 0 |
| impact | `not_used` | `unknown` |
| friction | `connection` | `no_relevant_hit` |

In s01 the Nexus tools were not yet in the client's tool list when the agent first looked
for them, so no consultation was made (see the startup race below). In s02 the search
returned nothing because the store was empty; no other result was possible. No reuse was
claimed, so there was nothing to review against §7.

## Coverage the log does not show

Measured at closeout from the client's own session files and the store's write log:

- **Store:** 0 memories, 0 revisions and an empty write log, for the whole window and from
  every thread that could reach it.
- **Logging:** 25 top-level threads started in the project inside the window, plus s01's own
  thread, begun three minutes before the clock during the setup exchange. 2 were recorded.
  Whether the other 24 were eligible sessions was not assessed, and no retrospective rows
  were added: none of them captured anything either, so they could not change the result.
- **Subagents:** 266 further threads ran in the project. Two called a Nexus tool; neither
  wrote.
- **Activity:** no top-level thread started in the project after day 7.
- **Client drift:** 23 of the 25 threads ran pre-release 0.162.0 builds, not the pinned
  0.160.1. The reporter validates the manifest, not each session's client build, so this
  is not among its flagged deviations.

## Friction, separated

| Kind | Observed |
| --- | --- |
| Setup / connection | 1 session (s01) |
| Capture effort | none — no capture was attempted |
| Retrieval miss | 1 session (s02), against an empty store, so not evidence about retrieval |
| Stale content, verification effort, correction conflict | none |
| Logging burden | not recorded as friction, yet 2 of 26 top-level threads were logged |

## Limits

- The pilot's own limits apply: one participant, no control arm, no blinding, self-report.
- With nothing captured, these records say nothing about retrieval quality, ranking or reuse.
- Task outcomes and impact were recorded by the agent, not rated by the participant.
- **Startup race.** Codex exposes an MCP server's tools to the model only once that server has
  finished starting. During setup, two immediate headless prompts saw no Nexus tools, and the
  same prompt after a 15-second wait succeeded. A session's first request can therefore miss
  Nexus, as s01's did.
- Other context sources were available to the client — a hosted memory service, the
  client's own memory, and the project's files — and both recorded sessions say they drew on
  them. That is an observation about competing sources, not a measured effect.

## Next action

**Close as inconclusive** (§10). What was missing: any captured content, logging for most
threads, and a stable client build. Nothing here justifies a retrieval or ranking change,
and nothing here argues for automatic capture.
