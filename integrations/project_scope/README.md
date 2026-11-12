# Local project-scoped Nexus readers

The launcher binds ordinary MCP reads and writes to an owner-enrolled Git checkout. Codex, Claude and Antigravity can share the same private project database. The owner can enroll a separately cloned repository independently. Linked worktrees share their main checkout's registration and verify references against the active worktree.

Install the Python entry points from this directory in an owner-only directory at `~/.local/share/nexus-memory/project-scopes-v1/bin/`. Invoke them with an installed Nexus interpreter in isolated mode (`python -I`). Install `launcher.py`, `project_scope.py`, `antigravity_hook.py`, and `antigravity_mcp.py` together. Files under `antigravity/` are wrappers for the local plugin's existing launcher and hook paths. Keep the installed entry points outside the checkouts being evaluated. The registry and project databases belong to the OS user. Local filesystem access by another process running as that user is outside the MCP isolation boundary.

## Enroll and connect

Enrollment is an explicit local owner operation. Supply the absolute main checkout, then configure the host to invoke the installed launcher with the appropriate Nexus executable. Use the installed Codex-hook executable for Codex. For example, with shell variables pointing to absolute installed paths:

```sh
"$nexus_python" -I "$scope_bin/launcher.py" enroll "$main_checkout"
"$nexus_python" -I "$scope_bin/launcher.py" codex "$codex_nexus" --expected-project "$main_checkout"
"$nexus_python" -I "$scope_bin/launcher.py" claude "$standard_nexus"
```

When a verified project connects for the first time, it gets a new database under `project-scopes-v1/`. The registry binds a random identifier to the trusted local Git common directory and its filesystem identity. Replacing or moving a checkout needs owner attention. An unknown or ambiguous project has no fallback to a shared database. The Claude pilot and DSH founder profile each explicitly configure their own shared database. Those stores are separate from the retired default endpoint.

### Automatic task reads

The Nexus checkout's Codex configuration pairs `codex_recall` on prompt submission with `task_pre_tool_use` before Nexus tool calls. Both hooks use the same running adapter. In a task session the model can fetch the current version of an admitted decision. If no decision qualifies, the guard declines follow-up searches before they return excerpts. To deliberately search more broadly or inspect history, start your prompt with `Nexus explore:`. Existing Codex sessions need a reconnect after changing the runtime. Review the new hook in Codex's native trust prompt.

The Claude handoff test uses the same hooks in a per-invocation task profile with strict MCP configuration. The registered Claude pilot has its own configuration and database. The [handoff receipt](../../docs/measurements/2026-11-04-recall-distractor-handoff.json) records the guarded tasks, earlier failed trials, and deliberate broad searches.

## Open the read-only viewer

`launcher.py atlas <absolute main checkout> [--no-browser]` starts the Memory Atlas viewer for an enrolled checkout. It
needs the installed Nexus interpreter (run with `python -I`), never enrolls, migrates or writes, and prints the address
without its launch secret; the secret is in a private `launch.html` (mode 0600) that is deleted once used. It binds
`127.0.0.1` only. It shows recorded memories, revisions and cited files, and no agent activity. Replacing or moving the
checkout, replacing or retiring the store, or removing the enrollment stops the viewer from serving data until it is
relaunched. See the top-level README for the privacy boundary and measured timings.

Installing it means copying this directory's `launcher.py` over the installed one; the other three entry points are
unchanged by this feature. Back the old file up first. The existing `codex`, `claude` and `antigravity` commands behave
identically (the same exec arguments and working directory were compared between the old and new file).

## Antigravity host context

A lifecycle hook launched in the installed plugin directory validates the single workspace in the host event. A stdio server launched in that neutral directory asks the client for its MCP roots before admitting a tool call. An empty initial root allows discovery of the tool schemas. A memory operation requires one local, enrolled root. Once bound, a connection that observes a different or ambiguous root requires reconnection. No last-used project lookup is performed. A stored excerpt or tool argument cannot select a project.

### Antigravity Stop hook (capture nudge)

`antigravity_hook.py stop` runs when an Antigravity turn ends (`terminationReason` `NO_TOOL_CALL`, fully idle, no error)
and answers the host's Stop contract. If the turn edited a file, ran a command in a repository with uncommitted
changes, or the prompt reads like a decision, it returns `{"decision": "continue", "reason": ...}`, which asks the agent
to record a durable fact or reply `NO_MEMORY`. It does this at most once per turn and three times per session, and never
after a `record`/`revise` call or a `NO_MEMORY` reply. **This asks the agent to save without the user having asked**, which
differs from the rule that memory is written only on request; decide whether to install it with that in mind. The
reason text carries the HEAD commit and a count of changed files, never file names. A project that is not enrolled,
or any failure, answers `{}` and lets the agent stop. The hook reads only a regular `.jsonl` transcript named by the host.

## Retire a vulnerable shared endpoint

After installing the new readers, run the maintenance command directly as the store owner. It operates on a local Nexus v4 SQLite store and checks that active read transactions can drain. Supply a private recovery directory and retain its contents.

```sh
"$nexus_python" -I tools/retire_legacy_store.py --database "$old_db" --archive-dir "$private_recovery"
```

The command checkpoints WAL, locks the original database, makes a verified private copy, and commits a retirement marker on the original inode. This prevents old processes, including one that opened the file before cutover but had yet to read, from using the original Nexus tables. If the database is busy, let the active operations finish and retry. On success the command prints the recovery file. Reconnect MCP sessions opened before the cutover so they load the new launcher. Repeating it checks the recorded checksum. Keep the marker at the old pathname: removing it would allow a stale application to create a new shared store.

The recovery file contains all original partitions, revisions, evidence and receipts. Inspect it offline using SQLite read-only mode. Restore selected records only after the owner establishes their project. Previously delivered text may still exist in host sessions or backups.

## Verification

Set `NEXUS_E2E_SERVER` to an installed standard Nexus executable and `NEXUS_E2E_CODEX_SERVER` to its Codex-hook counterpart. Set `NEXUS_E2E_INTEGRATION_DIR` to verify an installed copy of the launchers. The transport tests create disposable homes, checkouts, worktrees, and databases.

```sh
python -m pytest -q tests/tools/test_project_scope.py tests/tools/test_retire_legacy_store.py tests/transport/test_project_isolation_e2e.py
```
