# Cross-host plan, Phase 2: DSH isolation and first real save (27 November 2026)

Phase 2 of [the cross-host plan](../plans/cross-host-project-memory.md) §5 has an entry gate and an exit gate:
- **Entry:** the first real writable DSH save, in a disposable profile.
- **Exit:** simultaneous unrelated sessions stay isolated, and session switch, old receipt, worker write attempt and missing root behave as specified.

The owner closed Phase 2 on 27 November on macOS evidence and chose to start Phase 3 without a Linux replay. PR #31 stacks on #30, and CI runs only on PRs to `main`, so nothing in this record ran on Linux.

## What was built

- **DSH launcher hosts (`bbe3fab`).** The project launcher gains `dsh` and `dsh-worker`. Each resolves the repo from the working directory and mints a one-time catalog grant: root for `dsh`, delegated (always read-only) for `dsh-worker`. It then execs `nexus-memory-bridge` with `--project-id`, `--repo` and `--consume-grant-file`.
- **The writer host, `dsh-mnemon-provider-nexus/writer-host` (`040c15a` → `1ecb391`).** DSH's provider host exposes no profile inventory, so this Cordis plugin is the audited embedding owner that `forAuditedHost` asks for.
  - Recall goes through mnemon, on a read-only Source bound to a `dsh-worker` grant. Every mutation through mnemon is refused.
  - Saving goes only through the host's own `nexus_remember` tool, on a root grant. The tool reads the session header of the agent actually executing the call and refuses any subagent origin, parent or fork lineage, or delegation depth.
  - The host vouches only for one workspace, one exposure and a pinned loader inventory.

## Entry gate: two real saves

Both runs used a disposable `DSH_HOME` and a disposable Nexus home, on the installed DSH 0.2.2-alpha.2 with `dsh-mnemon` 0.5.50 and Memory Spaces 0.5.31. The owner ran each headless DeepSeek turn with their own key.

1. **22:05 UTC, writer host at `30cb4bc`.** The save landed, but it was written by mnemon's delegated child session. That child has `origin: subagent`, `delegationDepth: 1`, and runs its own deepseek-flash model. Root `mnemon_remember` always delegates the save this way. The host's root check looked up the agent named in the Source request, and that request did not identify the child, so the write got through. This violated the plan's rule that workers never write, and it cost two model runs. `1ecb391` replaced the write path.
2. **22:37 UTC, writer host at `1ecb391`.** The root session (depth 0) called `nexus_remember` once, and no child session was spawned. The fresh project space holds exactly one memory, `305043c2…`, and one revision, `7e06cfe1…`, with the exact body. The receipt matches the store, the grant file was consumed and the journal is empty. **This run meets the entry gate.**

## Exit gate: `tests/writer-exit.spec.ts` (`e2d8794`)

Every scenario boots the writer host under the real DSH Loader against a disposable Nexus home. No model is called. All five pass on macOS, Node 22.23.4. The pinned 22.19.0 is not installed locally.

| Criterion | Scenario | Result |
|---|---|---|
| Simultaneous unrelated sessions | Two processes in two repos wait on a shared barrier and save at the same moment. | Each sees only its own record. A request naming the other workspace is refused. |
| Session switch | The process leaves its workspace after a save, then returns. | Saves are refused away from the workspace and stay refused after the return. A session whose cwd is another workspace is refused too (`writer-host.spec.ts`). |
| Old receipt | An earlier process left an unresolved journal entry. | Reconciled read-only and never re-sent. The entry is kept for inspection. |
| Owner disable on an open session | Disable the repo while a session holds open grants, then re-enable it. | Saves get `grant_revoked`. The open read session delivers no text: it found the record before the disable and returns none after. Re-enabling does not revive that session. |
| Worker write attempt | Subagent, mnemon's delegated writer, fork, and a root session of another workspace. | All refused (`writer-host.spec.ts`). |
| Missing root | The configured workspace does not exist. | DSH boots without Nexus. |

**Mutation checks.** Removing each of the six writer-host guards fails `test:writer`. The missing-root test is the exception: it passes with or without the host's early return, because Cordis already contains a plugin that throws. The test states the behaviour, not the code path.

## Known gaps carried into Phase 3

- No real DSH turn has run a recall through mnemon. Both real turns only saved.
- A search on a disabled repo returns an empty result rather than an error, because mnemon swallows `grant_revoked`. No text leaks, but the owner gets no reason.
- No Linux run of any of this. It is first exercised when the stack reaches `main`, or in a later replay.
