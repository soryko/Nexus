# Test-only Host activity probe

Run from the Nexus repository root with Node 24 and a trusted DSH source checkout with existing workspace dependencies. No install, rebuild, or profile patch is needed.

```sh
node benchmarks/dsh_capture/host_probe/run.mjs /path/to/deepseek-harness
node --test benchmarks/dsh_capture/activity/*.test.mjs
```

The launcher starts a separate child for each case, with its own temporary cwd and an allowlisted environment (including disposable HOME, DSH_HOME, and TMPDIR). Each child has a 30-second timeout. The launcher asserts its directory is still empty after the child exits and then removes it. Each child mounts the real DSH Cordis services, AgentRegistry, AgentLoop, and in-memory SessionStore; only the LLM is a one-shot in-process mock. No production profile or store is loaded or changed. No credential loader, external provider adapter, persistence plugin, MCP, Nexus, capture writer, or HTTP/UI server is mounted. There is no Save tool, RPC, or approval route. This is an offline test against a trusted checkout, not an OS sandbox, shipped CLI/replay profile, or GUI test.

The adapter observes the post-commit `session/event` feed. It enrolls one fresh root bound to the exact live Agent, Session object, and disposable cwd. Admission requires its own committed start, the enrolled same-turn user-message ID, and a matching local committed completed end at the log tail. The adapter does not read message content, append to the Session, or forward raw events. It exposes a test-only decision (sequence and outcome), separate from the activity projection. The only admitted classification is the fixture's no-candidate control; no draft body exists.

## Evidence

On Node v24.14.0 against DSH checkout HEAD `f3803c96974ad4a94195fb97e5c637e9c2a3afe3`, the probe exited 0 with one JSON line per case:

| Case | Committed DSH `turn/end` | Adapter decision | Activity |
| --- | --- | --- | --- |
| Valid fixture ID | `completed` | `accepted` | `no-candidate` / `no-durable-candidate`; no draft |
| In-flight user cancellation | `aborted` (`user`) | `non-completed` | No latest row or draft |
| Nonmatching fixture ID | `completed` | `no-matching-fixture-input` | No latest row or draft |

The cancellation calls the real `Agent.cancel({ kind: 'user' })` while the mock stream is blocked and checks that the stream's signal aborts. The mismatched case submits the same normal fixture prompt but enrolls a different ID; the real turn still completes. In every case, an independent event listener sees exactly one published end and the session log contains that same end (turn 1, seq 11 in this run). Both negative decisions identify that end's sequence: these are adapter refusals, not missing runtime events. While the stream is blocked there is no end, no decision, and no activity. Disposal clears the decision and activity.

Every case used one mock model call, zero global/root/model-admitted tool schemas, and no committed tool call or result. The session log is in memory; no durable capture writer was connected, and no files were left under the isolated cwd/HOME/DSH_HOME/TMPDIR (`disposableEntries: 0` for each case).

## Limits

Fixture provenance is not human identity authentication. The probe does not exercise child agents, a separate failure end, resumed/replayed sessions, a ready draft, dismissal, timers, live profile activation, real inference, or production storage/export. The empty-directory check is not a system-wide I/O audit. The probe proves neither private draft ingress nor independent approval, recovery binding, or authorization for any capture write. The [Host activation gate](<../../../docs/measurements/2026-11-19-dsh-capture-host-gate.md>) is outside this probe.
