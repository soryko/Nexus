# Hook engine, Phase 0

Branch `feat/hook-engine-phase0` (on `a2ded1b`). Prerequisites for running Nexus as a plugin with
hooks on Claude Code and Codex. Nothing here touches the `p1-claude-auto` pilot.

Provenance labels: **observed** = I ran it and read the output; **tested** = covered by a test in
this tree; **unverified** = not run, with the reason and the exact procedure.

## 1. Host-neutral hook tools (done, tested)

`src/nexus_memory/transport/codex.py` now registers one engine under two sets of names. Both sets
call the same handler functions, so they share one session table.

| Host-neutral | Alias kept for the installed Codex adapter |
|---|---|
| `nexus_session_start` | `codex_session_start` |
| `nexus_recall` | `codex_recall` |
| `nexus_pre_tool` | `task_pre_tool_use` |

Guarantees preserved, each asserted in `tests/transport/test_hook_engine_names.py` through both
names, and by the unchanged `test_codex_integration.py` suite:

- recall returns at most three memory/revision ID pairs and never stored prose;
- `nexus_pre_tool` denies follow-up searches unless the prompt starts with `Nexus explore:`;
- admitted IDs are per session and reset on each prompt (and on session start);
- every tool is gated to the enrolled checkout; another checkout gets `{}`;
- an alias call and a new-name call see the same dedupe and admission state.

`LIFECYCLE_TOOLS` (all six names) is denied when the model tries to call a lifecycle tool itself.

One test fixture changed: the stub in `test_old_lookup_cannot_reopen_task_access_after_a_new_prompt`
keyed handlers by `fn.__name__`; it now keys by the registered `name=`. No assertion changed.

Because the alias names and the `[[hooks.*]]` definitions in `docs/codex-desktop.md` are
untouched, an installed Codex adapter sees byte-identical hook definitions.

## 2. Launch flag (done, tested) and the launcher change it needs

`--hook-project-root` is the host-neutral spelling of `--codex-project-root` (same `dest`, so
both work). Semantics are the old flag's: it registers the hook tools, binds the launch cwd
inside the named repository or its worktrees, and sets the bound checkout so reference
verification is available. It is still mutually exclusive with `--repo`. Tested in
`tests/transport/test_hook_project_root_flag.py` for both spellings: hook tools listed,
`status.repository_id` set and `verification` not `unavailable`, the `--repo` combination
refused (exit 2), an unrelated launch cwd refused (exit 1).

### Required change in `integrations/project_scope/launcher.py` (not applied; PR #17 branch)

Today, for every host except Codex, the launcher passes `--repo <checkout>`, so Claude never
gets the hook tools. In `feat/project-scope-integration`, `launcher.py`:

```python
        if args.command == 'codex':
            options.extend(['--codex-project-root', str(project.common.parent)])
        else:
            options.extend(['--repo', str(project.checkout)])
```

needs to become:

```python
        if args.command in ('codex', 'claude'):
            options.extend(['--hook-project-root', str(project.common.parent)])
        else:  # antigravity has no Nexus hooks
            options.extend(['--repo', str(project.checkout)])
```

(`codex` may keep `--codex-project-root` until the runtime floor is known to include the new
flag; both are accepted by this build.) Notes for whoever applies it:

- The launcher `chdir`s to `project.checkout` before `execv`, and `resolve_checkout` binds the
  *cwd's* checkout, so Claude's bound checkout is the same worktree as with `--repo`.
- Claude gains the hook tools only; nothing is enabled until a hook definition calls them.
- `--repo-id` is not combinable with the hook flag unless `--repo` is also given; the launcher
  passes none, so this does not arise.
- A runtime older than this build rejects the flag (argparse exit 2). Reinstall the runtime
  before switching a Claude entry, or the entry will fail to start. The existing 41 `p1-claude-auto`
  entries are not affected by anything on this branch.

## 3. `via: hook|model` in the usage log (not in this tree; validated patch)

The usage log (`transport/usage.py`, `UsageMiddleware`, the reporters) is not in this tree. It
exists only on `feat/server-instructions-usage-log` (worktree `/Users/soko/Cerebros/nexus-cue`,
format version 2). I did not port ~8,000 lines of unmerged work into this branch.

Two findings from reading it:

1. `KNOWN_TOOLS` lists only the seven model-facing tools; any other name is logged as
   `"unknown"`. Hook calls would therefore be logged as `unknown`, not under their own names.
2. The reporters (`tools/summarize_usage.py`, `summarize_pilot.py`) count by tool name
   (`get`, `history`, `search`, writes), so hook tool names would not be counted as consults even
   without a field. `via` makes that explicit rather than accidental.

`docs/patches/usage-log-via.patch` adds `HOOK_TOOLS` (all six names), splits `MODEL_TOOLS` out of
`KNOWN_TOOLS`, writes `"via": "hook"|"model"` on every `call` line, and adds
`tests/transport/test_usage_via.py` (15 parametrised cases). Evidence:

- `git apply --check` against `/Users/soko/Cerebros/nexus-cue`: applies cleanly (observed).
- Applied to a `git archive` copy of that branch: the new test plus
  `tests/transport/test_mcp_instructions_usage.py` and `tests/tools` all pass, 181 passed (observed).
- Not applied to the cue worktree, and `FORMAT_VERSION` is left at 2 because `via` is an
  additive field; whether the readers should reject a line that lacks it is the cue branch's call.

Limit: `via` is inferred from the tool name server-side. A host cannot lie about it, but a model
that calls `nexus_recall` itself would be logged `via: hook`. `nexus_pre_tool` denies that call
for the model path when the PreToolUse hook is installed, so the hook must be installed for
the field to be trustworthy.

## 4. Host behaviour probes

Scripts: `tools/probes/claude_mcp_tool_hooks.sh`, `tools/probes/probe_server.py` (synthetic MCP server that logs process start, serving
and each call with timestamps; optional start delay via `PROBE_DELAY`) and
`tools/probes/claude_plugin_naming.sh`. All use `mktemp` directories, a disposable
`CLAUDE_CONFIG_DIR`, synthetic data, and never open the real store or the real Claude profile.

### 4a. Claude Code: how a plugin's MCP server is named (observed)

Claude Code 2.1.316, `--plugin-dir` plugin `nexusprobe` declaring server `nexus-memory`:

```
servers: ['plugin:nexusprobe:nexus-memory']
tools:   ['mcp__plugin_nexusprobe_nexus-memory__other', 'mcp__plugin_nexusprobe_nexus-memory__ping']
```

So a PreToolUse matcher must target `mcp__plugin_<plugin>_<server>__<tool>`, not
`mcp__nexus_memory__<tool>`. **Consequence for the engine:** `nexus_pre_tool` splits the tool
name on `__` and accepts only server `nexus_memory`, `nexus-memory` or `nexus_recall`; for the
plugin form the server part is `plugin_nexusprobe_nexus-memory`, so it falls through to `{}`,
i.e. **allowed, not gated**. That is fail-open for a plugin launch. It is pinned as a strict
`xfail` (`test_plugin_launched_server_name_is_gated`); Phase 1 must widen the match, ideally to
the exact name the plugin will have, and delete the marker. I did not widen it now because the
plugin name is not decided and a loose pattern (`plugin_*`) would also gate other plugins' tools.

Also observed: the run ended at the init message with "Not logged in" (disposable profile has no
credentials), so no model call was made and nothing was spent.

### 4b. Claude Code: `mcp_tool` hooks (observed, authenticated run, 2.1.316)

Ran with the real login in a disposable project (`tools/probes/claude_mcp_tool_hooks.sh`; one
haiku call per run, `--no-session-persistence`, synthetic server only). The user's own plugin
hooks also fired in these runs; they are unrelated to the results.

- **SessionStart: `mcp_tool` is not available.** Claude logged `mcp_tool hook skipped — mcp_tool
  hooks are not available for the 'SessionStart' hook event (no MCP client context)` and the
  hook failed with status 1. The probe server was only started afterwards, so the question
  "does it work on a not-yet-connected server" has the answer **no, it never runs at
  SessionStart at all**. The SessionStart delivery on Claude must be a `command` hook.
  Recorded answers to the three questions, from the first run (`claude -p`, delay 3 s): the
  SessionStart `ping` **did not run** (so it did not run after `serving`, which was logged ~3.9 s
  later), and `PROBE-CONTEXT-OK` **did not reach the model**: asked for the token, the model
  answered `NONE`. The `mcp_tool` hooks in the same runs were the only `ping`s that ran.
- **UserPromptSubmit and PreToolUse: `mcp_tool` works.** The hook calls the tool with
  `${session_id}`, `${cwd}`, `${prompt}`, `${tool_name}` substituted. With a server that took
  ~4 s to connect (`PROBE_DELAY=3`), the UserPromptSubmit hook logged `waiting up to 30000ms …
  for MCP server 'probe' to finish connecting` and then ran; the first prompt is therefore not
  lost to a slow server start, up to that 30 s limit. With no delay it ran immediately.
- **The returned `hookEventName` is checked.** My probe returned `SessionStart` from every
  event and Claude rejected it: `Hook returned incorrect event name: expected 'UserPromptSubmit'
  but got 'SessionStart'`. The engine already emits the right name per event
  (`output("UserPromptSubmit", …)`); `nexus_session_start` must only be wired where its
  `SessionStart` name is valid.
- **Only the listed inputs arrive.** A PreToolUse hook that lists just `tool_name` gets
  `session_id: null` and `cwd: null`; `nexus_pre_tool` denies without both, so the hook input
  must list `session_id`, `cwd`, `tool_name` and `tool_input`.

Consequences for Phase 1: (1) on Claude, SessionStart is a `command` hook carrying the fixed
cue text (the text `nexus_session_start` returns is static); (2) it cannot call
`nexus_session_start` to reset the session table, which is acceptable because `nexus_recall`
resets the per-prompt grants itself and creates the session on demand, but dedupe memory
(`seen`) then persists across `/clear` for the same `session_id` — worth a decision;
(3) recall and the PreToolUse guard use `mcp_tool` as planned.

### 4c. Codex: re-trust after the hook hash changes (unverified)

Codex 0.163.0 persists hook trust per definition hash and has no non-interactive command to list
or re-check it (`codex hooks` forwards to the interactive CLI; only
`--dangerously-bypass-hook-trust` exists, which defeats the thing being measured).
`docs/codex-desktop.md` forbids manufacturing `trusted_hash` values, so I did not synthesise
one. Determining it needs the trust UI: trust the current hooks in a disposable `CODEX_HOME`,
edit one hook's `tool` string, relaunch, and see whether `/hooks` asks again. What is known: this
branch does not change any hook definition (section 1), so the installed adapter is not
expected to be re-prompted by Phase 0; switching hooks to the `nexus_*` names in Phase 1 **will**
change the hash, so plan for a re-trust step in the Codex install instructions regardless of
the outcome.

## 5. Draft: `p1-claude-auto` close-out note (NOT applied)

Nothing below has been written to any pilot file. Figures are the ones given in the task brief
and were not re-measured here; recompute them with the pilot's reporter before filing.

> **p1-claude-auto: closed, inconclusive-for-benefit, conclusive-for-cue.**
>
> *Question.* Does an automatic, instruction-only cue make Claude Code sessions consult Nexus
> memory without anyone asking?
>
> *Result.* Across 303 server instances in ~/Repos and its 40 nested repositories (41
> local-scope entries) there were 0 memory calls, 0 consults and 0 captures. At the 72-hour
> read the count was 150 instances with the same zeros. Zero calls over this many instances
> says the cue alone does not produce use here; it says nothing about whether memory would help
> if consulted, because no consult happened.
>
> *What this does and does not show.* Instances are server lifetimes, not conversations, and an
> instance with no call is only evidence of no use if the usage log was recording (the log
> shows its own gaps through `seq`, `start` and `stop` events; confirm none were lost before
> relying on the zeros). It does not distinguish "the model never considered Nexus" from
> "the model considered it and declined".
>
> *Decision.* Option (a): stop collecting on this segment and open a new segment,
> `p1-claude-hooks`, in which a SessionStart hook (and ID-only recall on prompt submit) supplies
> the cue and calls are attributed `via: hook` versus `via: model`, so hook activity is never
> counted as a consult. Enrolment is an explicit owner command per project, not automatic.
>
> *Preserve.* Leave the `p1-claude-auto` config, its 41 entries, store and reports exactly as they
> are; the close-out records a verdict, it does not delete or edit evidence. The reporter's
> refusal behaviour stays as is.
>
> *Open before filing.* (1) Recompute 303 / 0 / 0 / 0 with the reporter at a recorded commit
> (the version string no longer identifies the tree). (2) Decide whether the pilot's stop
> time is "now" or the last logged event. (3) The founder signs the close-out; it is not an
> automatic transition.

## Not done / follow-ups

- Apply the launcher change (section 2) on the PR #17 branch, and the usage patch (section 3) on
  the cue branch.
- Widen the `nexus_pre_tool` server match for the plugin name (section 4a) and drop the `xfail`.
- Run 4c in the Codex trust UI (interactive; not scriptable).
- Decide how Claude resets recall dedupe on `/clear` now that SessionStart cannot call the engine.
