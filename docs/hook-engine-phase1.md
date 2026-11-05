# Hook engine, Phase 1: the Claude Code read-path plugin

Branch `feat/claude-hooks-plugin` (from `feat/hook-engine-phase0`). Nothing is pushed. No change to
`~/.claude.json`, `~/.claude/settings.json`, the plugin cache, the installed launcher, the
`nexus-memory` checkout, or anything of `p1-claude-auto`. The `p1-claude-hooks` segment is not
registered (Phase 2).

Labels: **measured** / **observed** = I ran it; **tested** = covered by a test in this tree;
**estimated** = derived, not measured; **unverified** = not run, with why.

## What was built

`integrations/claude-plugin/` (plugin name `nexus`; hooks only; no bundled server, no launcher copy):

| Event | Hook | Does |
|---|---|---|
| SessionStart (`startup|resume|clear|compact`) | `command` → `hooks/session_start.py` | Prints the fixed cue (the same text as `SESSION_CUE` in the engine; a test keeps them identical) **only for an enrolled project**, decided read-only by the installed project-scope resolver. Unenrolled or undeterminable: prints nothing. |
| UserPromptSubmit | `mcp_tool` → `nexus_recall` | ID-only recall. |
| PreToolUse, matcher `mcp__nexus-memory__.*` | `mcp_tool` → `nexus_pre_tool` | Task guard. Inputs `session_id`, `cwd`, `tool_name`, `tool_input`. |

All hooks have `timeout: 2`. Also: `skills/nexus-memory/SKILL.md` (consultation policy) and
`commands/status.md`, print-only.

**The command is `/nexus:status`.** Claude namespaces plugin commands and skills by plugin name (observed in the init
`slash_commands`, with the earlier name `nexus-memory:status`). The plugin is named `nexus` on your instruction. This
name is the plugin's only; the MCP server entry the hooks address is still `nexus-memory`, and so are the tool names
`mcp__nexus-memory__*`. The skill keeps its directory name, so it is `nexus:nexus-memory`.

`/nexus:status` replies with one of three lines. "Hooks not trusted" is inferred, not
detected: enrolled by the resolver but the SessionStart cue is absent from the model's context.
Claude exposes no API for plugin-hook trust to a command, so that inference is the only evidence
available. It never enrols (a test asserts no script calls `enroll(`).

## Step 4: can a plugin's `mcp_tool` hook reach a `nexus-memory` server in user or project scope?

**Yes (observed, 2.1.316).** The hook's `server` is resolved by name against connected servers
whatever their scope:

- project scope (`.mcp.json`, `enableAllProjectMcpServers`): connected; recall returned IDs, the guard
  denied and allowed correctly;
- dynamic scope (`--mcp-config`, used by the matrix): the same;
- user scope: run in an enrolled project with the real `nexus-memory` entry (installed runtime
  0.1.0a2, which has no hook tools). The hook reached the connected server and got its
  `invalid_input: invalid tool arguments`, which is exactly what that runtime returns for an unknown tool
  (reproduced against a disposable store). The hook therefore reached the user-scope server. No
  memory was read: the tool does not exist there.

So no alternative is needed for reach. Two things *are* needed, both outside this repo:
(a) the user-scope entry's server must have the hook tools, i.e. the launcher must pass
`--hook-project-root` for `claude` (Phase 0 §2); (b) an unenrolled project must still have a
connected server, or every prompt logs a hook error (next section).

**The strict xfail for plugin-launched server names stays.** The plugin bundles no server, so Nexus
tools keep the name `mcp__nexus-memory__<tool>` (the user's entry), which `nexus_pre_tool` already
recognises, and the plugin matcher uses it. `mcp__plugin_<plugin>_<server>__…` would only occur if the plugin
shipped its own `.mcp.json`; it does not (a test asserts that).

## Step 3: unenrolled or ambiguous → one visible line, then silence

Observed problem: when no server is connected (what the launcher does for an unenrolled project, it
exits 1), the `mcp_tool` hook fails with `MCP server 'nexus-memory' not connected` (outcome `error`,
non-blocking) on **every** prompt. The smallest fix that gives "one line, then silence":

1. Engine: `--hook-unenrolled` starts a server with only `nexus_session_start`, `nexus_recall` and
   `nexus_pre_tool`. It opens no store and has no memory tools. The first recall of a session returns
   `{"systemMessage": "Nexus: this project is not enrolled, so its memory hooks are off here."}`;
   everything after returns `{}`. Refuses `--db/--repo/--repo-id/--hook-project-root`. Tested.
2. Launcher (patch below, **not applied**): on a `ScopeError` for `claude`, exec the runtime with
   `--hook-unenrolled` instead of exiting.

No hook ever enrols. Cost of this design: **every** `ScopeError` now looks like "not enrolled", including
misconfiguration. That hid a real bug during this work (my harness set no `PWD`, the launcher's
PWD cross-check refused, and every server silently became the no-op server, so my first matrix run
passed vacuously). The patch therefore keeps the failure visible only through this one line; if you
want misconfiguration distinguishable, give the launcher a stderr line or a distinct message.

### Launcher patch (documented, not applied): `docs/patches/launcher-hook-root.patch`

Against `integrations/project_scope/launcher.py` at `5bd3a97` (PR #17 head, `feat/project-scope-integration`):

```diff
diff --git a/integrations/project_scope/launcher.py b/integrations/project_scope/launcher.py
index 3fed123..e8f14e1 100644
--- a/integrations/project_scope/launcher.py
+++ b/integrations/project_scope/launcher.py
@@ -37,12 +37,22 @@ def main():
             if args.expected_project is not None:
                 command.append(str(args.expected_project))
             os.execv(python, command)
-        project = host_project(args.command)
-        if args.expected_project is not None and resolve(args.expected_project).id != project.id:
-            raise ScopeError('host entry is bound to a different enrolled project')
+        try:
+            project = host_project(args.command)
+            if args.expected_project is not None and resolve(args.expected_project).id != project.id:
+                raise ScopeError('host entry is bound to a different enrolled project')
+        except ScopeError:
+            if args.command != 'claude':
+                raise
+            # Unenrolled or ambiguous: serve only no-op hook tools (no store, no memory tools) so the
+            # plugin's hooks stay connected and say one line instead of failing on every prompt.
+            os.chdir(Path.home())
+            os.execv(args.runtime, [str(args.runtime), '--namespace', 'unenrolled', '--hook-unenrolled'])
         options = ['--db', str(project.database), '--namespace', project.id, '--actor', 'local']
         if args.command == 'codex':
             options.extend(['--codex-project-root', str(project.common.parent)])
+        elif args.command == 'claude':
+            options.extend(['--hook-project-root', str(project.common.parent)])
         else:
             options.extend(['--repo', str(project.checkout)])
         os.chdir(project.checkout)
```

Checked: `git apply` succeeds on the PR #17 file (test `test_the_launcher_patch_still_applies_to_the_pr17_launcher`).
Installing it also needs the user-scope entry's runtime to be a build with `--hook-project-root` and
`--hook-unenrolled` (this branch). The installed 0.1.0a2 runtime rejects both flags and would fail to start.

## Step 5: scope matrix (disposable HOME, synthetic data, real login, haiku)

Harness `tools/probes/claude_matrix.py`; sanitized output `docs/measurements/claude-hooks-phase1-matrix-as-run.json`.
Setup: throwaway HOME with a registry; projects A, B, D, E enrolled with 3 synthetic memories each
(distinct markers, same subject word), C unenrolled, a linked worktree of A. Launcher = PR #17 copy + the
patch. **Pass criterion: zero wrong-project disclosures. Result: 0.** Foreign markers were searched in every hook output and reply.

| Scenario | Observed |
|---|---|
| cold start, enrolled A | server connected; cue delivered; recall returned 3 ID pairs, no stored prose in any hook output; guard denied 4 follow-up reads, allowed 3 admitted gets; no B marker |
| enrolled B | IDs; no A marker |
| linked worktree of A | IDs from A's store (session in the worktree launches its own server); no B marker |
| resume (`--resume`) | server connected; cue delivered again at resume |
| compaction (`/compact`) | `SessionStart:compact` fired and the cue was delivered again |
| unenrolled C, 3 prompts | **exactly 1** visible Nexus line, no cue, 0 hook errors, 3 prompts completed, no store created |
| store unavailable (db file mode 0) | first run: server `failed`, hook error `not connected` on every prompt. After the follow-up commit (`--hook-project-root` falls back to the hook-only server with its own line): server connected, **1** visible line over 2 prompts, 0 hook errors, prompts complete, no disclosure |
| write-denied (store dir 0500, files 0400) | first run: server `failed` (SQLite cannot open its WAL). After the follow-up: server connected, 0 hook errors, no IDs, no disclosure |
| wrong-project, direct to A's server | cwd of B, of unenrolled C, `/` and home: all `{}`; guard decision for a foreign cwd: deny |

Notes:
- Calls with another checkout's `cwd` on A's server return `{}`, and a linked worktree's cwd is also refused
  by A's server (it is a different checkout); a session in that worktree has its own server, as the worktree row shows.
- The first matrix run reported 0 disclosures while every server was the no-op server. The harness
  now asserts `recall_has_ids` per scenario; the numbers above are from the corrected run.

## Step 6: latency and injected context

Measured, 25 prompts in one session (A), client-observed `hook_started` → `hook_response`:

| | p50 | p95 | max | n |
|---|---|---|---|---|
| Nexus UserPromptSubmit `mcp_tool` (`nexus_recall`) | 11 ms | 35 ms | 69 ms | 25 |
| Nexus SessionStart `command` hook | 93 ms | | | 1 |
| The user's other UserPromptSubmit hooks (Supermemory and other plugins) | 400 ms | 3,228 ms | | 25 |

- Injected context (measured in characters): SessionStart cue 669 chars, once per session start/resume/compact; recall 380 chars on the
  15 of 25 prompts that had candidates, 0 on the others; 5,700 chars over the 25 prompts.
- Token cost (**estimated**, characters ÷ 4, not tokenised): ≈ 95 tokens per recall, ≈ 170 for the cue,
  ≈ 1,400 over the 25-prompt run. I did not measure true tokens: usage figures are cache-dependent and I had no tokenizer.
- Supermemory also fired in every one of these sessions (its SessionStart context, UserPromptSubmit recall and a PreToolUse
  `recalling:` message), and in this environment its recall failed on the network. Its hooks are the slow ones above; Nexus's
  added latency is two orders of magnitude lower. Hook classification (ours vs theirs) is by output content, not by a source field.

## Not verified

- **Hook timeout of 2 s on `mcp_tool`**: not shown to be honoured. A server that took 6 s to start did not exercise it, because in
  `-p` stream mode Claude waited for the connection before the first prompt. Whether a prompt typed in the interactive UI before the
  server has connected waits (earlier probe: up to 30 s) or fails open is unverified and needs the TUI.
- **PreToolUse fail-open** when the server is down: the model has no Nexus tool in that case, so the hook never fires; not exercised.
  UserPromptSubmit fail-open (error outcome, prompt proceeds) is observed.
- **Rendering**: the unenrolled `systemMessage` appears once in the event stream; how the interactive UI renders it, and whether the
  per-prompt hook errors in the store-unavailable case are shown to the user, is unverified (no TUI).
- **`/clear`**: not exercised. Per the decision, recall dedupe is not reset on `/clear`; SessionStart `clear` re-sends the cue only.
- **Codex re-trust (4c)** remains unverified (interactive trust UI).
- Whether `/nexus:status` shows "hooks not trusted" correctly when a plugin is disabled was not run; its logic is an inference (above).
- Hook trust for the plugin itself (user approval of plugin hooks) was not observed: `--plugin-dir` sessions ran them without a prompt.

## Decisions needed

1. ~~Store-unavailable noise~~ **Done on your instruction** (runtime-side, no further launcher change): when a hook-enabled launch
   cannot start its store (or the checkout check fails) the runtime still serves the three hook tools, says
   "Nexus: memory could not start for this project, so its memory hooks are off here." once per session, and is silent after. A launch
   without `--hook-project-root` still exits 1. Side effect: an enrolled project still gets the SessionStart cue in that state (the
   cue is decided by enrolment, not by store health); the cue already says to continue if Nexus is unavailable.
2. ~~Plugin name / command~~ **Done on your instruction**: the plugin is `nexus` (`/nexus:status`).
3. **Who applies** `docs/patches/launcher-hook-root.patch` on PR #17, and when the user-scope entry moves to a runtime with the new flags.
