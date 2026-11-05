# Phase 1 interactive checks (about 30 minutes, at a keyboard)

These are the items `docs/hook-engine-phase1.md` lists as **not verified** because they need a real
Claude Code or Codex terminal. Everything runs in a disposable world: a throwaway `HOME`, a patched
**copy** of the PR #17 launcher, synthetic memories, and (for the trust checks) throwaway
`CLAUDE_CONFIG_DIR` / `CODEX_HOME`. Nothing here uses the installed launcher, `~/.claude.json`,
`~/.claude/settings.json`, `~/.codex` config, or any real store.

Two things from the earlier list are already settled and need no terminal: `/nexus:status` works
(plugin renamed to `nexus`) and prints "hooks not trusted or not running" when the plugin's hooks are
empty (observed with `bin/claude-nohooks-A.sh`, section 6).

## Results (tick and paste)

| # | Check | Min | Outcome (one line) | Done |
|---|---|---|---|---|
| 0 | Setup | 2 | | [ ] |
| 1 | 2 s `mcp_tool` timeout when a prompt is typed before the server connects | 4 | | [ ] |
| 2 | PreToolUse fail-open with the server down | 4 | | [ ] |
| 3 | How the unenrolled `systemMessage` and per-prompt hook errors render | 4 | | [ ] |
| 4 | `/clear`: recall dedupe persists, cue re-sent | 3 | | [ ] |
| 5 | Plugin hook trust prompt (throwaway config dir, needs a login) | 6 | | [ ] |
| 6 | Disabling the plugin: what happens to `/nexus:status` and the hooks | 2 | | [ ] |
| 7 | Codex re-trust after the hook definition changes (4c) | 8 | | [ ] |
| 8 | Cleanup | 2 | | [ ] |

Paste back the "Outcome" column. Nothing else needs to be sent; evidence stays in `$WORK`.

## 0. Setup

From the repo root (`/Users/soko/Cerebros/nexus-codex`, branch `feat/claude-hooks-plugin`):

```bash
export WORK="$(mktemp -d)/ia"
.venv/bin/python tools/probes/interactive_setup.py "$WORK"
export PATH="$WORK/bin:$PATH"
```

It prints the paths and creates `$WORK/bin/*.sh`. Each script `cd`s into the right disposable project, sets
`PWD`, passes `--strict-mcp-config` (so none of your real MCP servers start), loads the plugin from this
checkout with `--plugin-dir`, and writes a debug log to `$WORK/debug-<script>.log`. Extra arguments go to
`claude`. Your own plugins' hooks (Supermemory and others) still run; ignore their lines.

Helper: `grep-hooks.sh <script-name>` filters the debug log, e.g. `grep-hooks.sh claude-A.sh`.

Smoke test (one cheap model call, expect `Nexus: enrolled, hooks active.`):

```bash
claude-A.sh -p "/nexus:status" --no-session-persistence </dev/null
```

If that prints anything else, stop and report it; the rest depends on it.

## 1. The 2 s `mcp_tool` timeout when a prompt is typed before the server connects

Why: in `-p` mode Claude waited for the server before the first prompt, so the timeout was never exercised.
Here the server needs about 6 s to start (`bin/claude-slow-A.sh`), and you type immediately.

```bash
claude-slow-A.sh
```

1. As soon as the input box appears (within 2 s), type `Explain quasarflux` and press Enter.
2. Note how long the prompt sat before the model started answering (roughly: under 2 s, about 6 s, or longer).
3. `/exit`, then: `grep-hooks.sh claude-slow-A.sh | grep -i "UserPromptSubmit\|waiting up to\|not connected\|timed out"`.

Outcomes:

| You see | Meaning |
|---|---|
| Log has `mcp_tool hook for UserPromptSubmit is waiting up to 30000ms … for MCP server` and then `calling nexus-memory/nexus_recall`, prompt delayed ~6 s | **The 2 s timeout does not cap the connection wait** (wait up to 30 s). Cold start costs the first prompt a delay. |
| Hook error `timed out` / `not connected` after about 2 s, prompt proceeds without Nexus context | Timeout honoured; hook **fails open**. First prompt gets no recall (acceptable). |
| Error and the prompt is blocked/never answered | **Fail-closed. Report immediately.** |
| Server was already connected when you typed (log shows no waiting line) | Inconclusive; repeat and type faster. |

Result line: `1: <waits ~N s | times out at ~2 s, fails open | blocked | inconclusive>`

## 2. PreToolUse fail-open with the server down

Why: if the server dies, the guard must not wedge the session. `search` is denied by the guard when the
server is up and no candidate is admitted, which makes a down server distinguishable from a deny.

```bash
claude-A.sh
```

1. Prompt `Explain quasarflux` (server up; the guard denies a follow-up `search`, which confirms it works). Answer or deny any tool prompts.
2. In another terminal: `kill-server.sh` (it kills only this WORKDIR's disposable servers and prints the PIDs; if it prints `none`, stop and report).
3. Back in Claude, run `/mcp`: `nexus-memory` should show failed/disconnected.
4. Prompt: `Call the mcp__nexus-memory__search tool with query quasarflux. Do not call any other tool.`
5. `/exit`, then `grep-hooks.sh claude-A.sh | grep -i "PreToolUse"`.

Outcomes:

| You see | Meaning |
|---|---|
| Hook error line (`not connected`) **and** the tool call is attempted and fails with an MCP/connection error | **Fail-open** (expected). |
| Tool call blocked with the guard's message ("Use the current prompt's admitted candidates…") | The guard is still answering; the server was not actually down. Redo step 2. |
| Tool call blocked with a hook-failure message and no attempt | **Fail-closed. Report immediately.** |
| The model cannot call the tool because Claude removed it when the server disconnected | Inconclusive for the hook; record "tool removed on disconnect" (that is itself safe). |

Result line: `2: <fail-open | fail-closed | tool removed on disconnect | inconclusive>`

## 3. How the unenrolled `systemMessage` and per-prompt hook errors render

**3a. Unenrolled project, the one visible line.**

```bash
claude-C.sh
```

Type three prompts: `hello`, `again`, `third`. Look at the screen after each.

| You see | Meaning |
|---|---|
| `Nexus: this project is not enrolled, so its memory hooks are off here.` once (after prompt 1) and nothing after | Matches the design. Note where it appears (inline, status area, transcript only). |
| Line repeated on every prompt | Dedupe not working in the TUI. Report. |
| Not visible at all | `systemMessage` is stream-only; note whether `Ctrl+O` (transcript) shows it. |

**3b. No server at all, the per-prompt hook error.** This is the failure the fallback modes exist to prevent.

```bash
claude-noserver-A.sh
```

Type `hello` and `again`. Record whether an error such as `UserPromptSubmit hook error … not connected` is shown to you on each prompt, only in `Ctrl+O`, or not at all. `/exit`.

Result line: `3: unenrolled line <once inline | once in status | transcript only | not visible>; no-server error <shown each prompt | transcript only | not shown>`

## 4. `/clear`: recall dedupe persists, the cue is re-sent

Decision recorded earlier: recall dedupe is not reset on `/clear`. The open question is whether Claude gives
a new `session_id` after `/clear`, which would make dedupe reset in practice.

```bash
claude-A.sh
```

1. Prompt `Explain quasarflux`. (Candidates arrive; answer or deny any tool prompts.)
2. Run `/clear`.
3. Prompt `Explain quasarflux` again.
4. `/exit`, then:

```bash
grep -c "memory_id=" "$WORK/debug-claude-A.sh.log"     # recall outputs that carried IDs
grep-hooks.sh claude-A.sh | grep "SessionStart:clear"   # cue re-sent on clear
```

| You see | Meaning |
|---|---|
| Count is **1** after both prompts and a `SessionStart:clear` line with the Nexus command hook (`session_start.py`) | Dedupe persisted across `/clear` (same `session_id`) and the cue was re-sent. As decided. |
| Count is **2** | `/clear` gives a new `session_id`; dedupe effectively resets. Not a bug, but the decision needs restating. |
| No `SessionStart:clear` line for `session_start.py` | The cue is **not** re-sent on `/clear`. Report. |

Result line: `4: IDs after /clear <not repeated | repeated>; cue re-sent <yes | no>`

## 5. Plugin hook trust prompt (throwaway config directory)

Why: `--plugin-dir` ran the hooks without any prompt. A marketplace-installed plugin may ask. This needs a
login inside a **throwaway** `CLAUDE_CONFIG_DIR` (`$WORK/cfg`), so your real config is untouched.

```bash
claude-throwaway-A.sh
```

1. If asked, pick a theme and run `/login` (a throwaway login; you will log out in cleanup).
2. `/plugin marketplace add $WORK/marketplace` (type the expanded path: `echo $WORK/marketplace`).
3. `/plugin install nexus@nexus-local`. **Write down every consent/trust/approval prompt verbatim.**
4. `/exit` and start `claude-throwaway-A.sh` again (hooks load at startup).
5. Run `/hooks`: are the three Nexus hooks listed? Prompt `Explain quasarflux`.
6. `/exit`; `grep-hooks.sh claude-throwaway-A.sh | grep -i "nexus"`.

| You see | Meaning |
|---|---|
| A prompt asking to trust/approve the plugin's hooks (on install or first run) | Trust is gated. Record its text and whether declining stops the hooks. |
| No prompt; hooks listed and `mcp_tool` calls in the log | Installed plugin hooks run without a separate approval. |
| Hooks listed but marked disabled/needs review, no calls in the log | Trust is required and was not given; record the exact state. |

Result line: `5: <trust prompt "<text>" | no prompt, hooks active | hooks need approval>`

## 6. Disabling the plugin

Continue in the throwaway config dir from section 5 (plugin installed):

1. `claude-throwaway-A.sh`, then `/nexus:status` (expect `Nexus: enrolled, hooks active.`).
2. `/plugin`, disable `nexus`, restart (`/exit`, rerun), then try `/nexus:status` and prompt `Explain quasarflux`.
3. `grep-hooks.sh claude-throwaway-A.sh | grep -ci "nexus_recall"` after the second start: calls should stop.

| You see | Meaning |
|---|---|
| `/nexus:status` is unknown and no `nexus_recall` calls | Disabled cleanly; the status command disappears with the hooks (so "hooks not trusted" can only be reported while the command exists). |
| `/nexus:status` still runs and says `hooks not trusted or not running` | Command stays while hooks are off. |
| `/nexus:status` still says `hooks active` after disable | **Wrong.** Report. |

The "hooks not trusted" wording itself is already observed: `claude-nohooks-A.sh -p "/nexus:status" --no-session-persistence </dev/null` prints it.

Result line: `6: after disable <command gone | command says not trusted | command says active>; hooks <stop | keep running>`

## 7. Codex re-trust after the hook definition changes (4c)

Codex 0.163.0. Throwaway `CODEX_HOME` (`$WORK/codex-home`), project `$WORK/codex-proj` with a
`.codex/config.toml` using the old tool names (`codex_recall`, `codex_session_start`) and a runtime that has the new hook tools. Never
edit `trusted_hash` values by hand.

1. `codex-run.sh login` (throwaway login; log out in cleanup).
2. `codex-run.sh`. Accept the project trust prompt for `$WORK/codex-proj` only. Open `/hooks`: two hooks need review. **Trust both through the UI.**
3. Prompt `Explain quasarflux`; confirm a hook ran (status message or `/hooks`). `/exit`.
4. Record the stored hashes (read-only): `grep -n -i "hash\|hooks" "$WORK/codex-home/config.toml" > "$WORK/codex-hashes-before.txt"; wc -l "$WORK/codex-hashes-before.txt"`.
5. Change one hook: `codex-edit-hook.sh` (swaps `codex_recall` → `nexus_recall`, `codex_session_start` → `nexus_session_start`).
6. `codex-run.sh`, open `/hooks`, prompt `Explain quasarflux`, `/exit`.
7. `grep -n -i "hash\|hooks" "$WORK/codex-home/config.toml" | diff "$WORK/codex-hashes-before.txt" - | head`.

| You see | Meaning |
|---|---|
| `/hooks` shows the changed hooks as needing review again; no hook ran before you trusted them | **Re-trust required** after a definition change. Phase 1 install notes must say so. |
| Hooks still trusted and the new definitions ran with no prompt | **Hash not enforced per definition. Report immediately (security-relevant).** |
| Hooks silently disabled, no prompt | Re-trust needed but not announced; record it. |
| `/hooks` unavailable in this build | Record the build; use the trust screen on launch instead. |

Result line: `7: after definition change <re-review required | ran untrusted | silently disabled | n/a>`

## 8. Cleanup

Check what changed in your real config **first**, then log out of both throwaway logins, stop leftovers and delete the world.

```bash
find ~/.claude.json ~/.claude/settings.json ~/.claude/plugins ~/.codex/config.toml -newer "$WORK/README.txt" 2>/dev/null
```

Expect no output. If `~/.claude.json` is listed, that is Claude's own bookkeeping from running at all; report anything
under `plugins/` or `settings.json`. Interactive sessions also write transcripts under `~/.claude/projects/` for the
disposable project paths (and `~/.claude/history`); that is ordinary runtime output, not config, and is harmless to leave.

```bash
# inside the throwaway Claude: /logout     then:
codex-run.sh logout
kill-server.sh
echo "$WORK"          # confirm it is the mktemp path you created in section 0, then:
rm -rf "$WORK"
```

`$WORK` holds the throwaway logins and every disposable store; nothing else needs deleting.
