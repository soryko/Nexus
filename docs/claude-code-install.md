# Installing the Nexus hooks plugin in Claude Code (including the Claude desktop Code tab)

Hooks run only in Claude Code, which the desktop app's **Code** tab embeds. The desktop **Chat** tab has no
hooks (a `.mcpb` extension would give it the tools only; that is a separate piece of work).

What you get: on each session start in an enrolled project a one-line cue; on each prompt an **ID-only**
recall (memory IDs, never stored text); and a task guard before Nexus tool calls. In a project that is not
enrolled, one line per session and then silence. Nothing here enrolls a project or writes a memory.

**Everything below is run by you.** It changes live client configuration, so none of it is done for you.
Steps 1–2 only add files; steps 3–5 change what Claude launches. Each has a rollback.

> **Pilot note.** The user-scope `nexus-memory` entry is also what the `p1-claude-auto` pilot observes.
> Changing it ends that measurement segment. Record the change in the pilot log before step 4 if you care.

## Prerequisites (already true after v0.1.0a3 is released)

- A checkout at tag `v0.1.0a3` or later (`git describe --tags`).
- The project you want hooks in is enrolled: `"$nexus_python" -I "$scope_bin/launcher.py" enroll "$main_checkout"`.

## 1. Build the new runtime (adds a directory; touches nothing live)

```bash
nexus_runtime="$HOME/.local/share/nexus-memory/venvs/0.1.0a3"
python3 tools/install.py --venv "$nexus_runtime"
"$nexus_runtime/bin/nexus-memory-check"        # expect: PASS: 9/9 checks
```

## 2. Install the launcher (one file; back it up first)

The new command is `claude-hooks`. The existing `claude` command is unchanged, so anything still using
`launcher.py claude <old runtime>` keeps working.

```bash
scope_bin="$HOME/.local/share/nexus-memory/project-scopes-v1/bin"
cp -p "$scope_bin/launcher.py" "$HOME/.local/share/nexus-memory/project-scopes-v1/launcher.py.before-claude-hooks"
cp integrations/project_scope/launcher.py "$scope_bin/.launcher.py.new" && chmod 600 "$scope_bin/.launcher.py.new" \
  && mv "$scope_bin/.launcher.py.new" "$scope_bin/launcher.py"
python3 tools/check_claude_code_setup.py --runtime "$nexus_runtime/bin/nexus-memory" --launcher "$scope_bin/launcher.py"
```

Expect four `PASS` lines. Rollback: copy the `.before-claude-hooks` file back.

## 3. Note what Claude launches today (read-only), and keep a copy of the file it lives in

```bash
claude mcp get nexus-memory        # save this output; carry any extra arguments or env (such as --expected-project) into step 4
claude mcp list                    # shows each server's health
cp -p ~/.claude.json ~/.claude.json.before-claude-hooks      # the rollback that cannot lose a detail
```

`claude mcp get` is a summary; the copied file is the exact record. A project's own `.mcp.json` that defines a
server named `nexus-memory` takes precedence over the user entry in that project: check the enrolled projects
for one before expecting step 4 to apply there.

## 4. Point the user-scope entry at the hook-enabled launch

```bash
claude mcp remove nexus-memory --scope user
claude mcp add --scope user nexus-memory -- "$nexus_runtime/bin/python" -I "$scope_bin/launcher.py" claude-hooks "$nexus_runtime/bin/nexus-memory"
claude mcp get nexus-memory
claude mcp list                    # nexus-memory should show as connected
```

The interpreter is the runtime's own, by absolute path, so the entry does not depend on the `PATH` the desktop app
launches it with. Between `remove` and `add` there is no entry: do it when no session is starting. Rollback: remove
the entry and re-add the command saved in step 3, or restore `~/.claude.json.before-claude-hooks` while Claude is closed.

## 5. Install and trust the plugin

```bash
claude plugin marketplace add "$PWD"            # this repository is the marketplace (.claude-plugin/marketplace.json)
claude plugin install nexus-memory@nexus-local
```

Restart Claude (the Code tab: start a new session). If a hook-trust prompt appears, read it and approve it
yourself; `/hooks` lists the plugin's hooks.

## Verify

Run `claude mcp list` once from an enrolled project and once from a directory that is not enrolled: the server
must show as connected in both (in the second it is the no-op hook server, and says so in its first reply).

In a new session inside an enrolled project: `/nexus-memory:status` reports the hooks are active, and a prompt
that matches a stored memory shows IDs in the injected context. In an unenrolled project you see exactly one
"not enrolled" line per session. The status command prints only; it never enrolls.

## Keep in mind

- Step 5 registers this checkout as a marketplace in place. Installed plugin files are, as far as I know (unobserved), copied into Claude's
  plugin cache, but moving or deleting this checkout breaks `claude plugin marketplace update`.
- Replacing the launcher is atomic (rename). Servers already running keep the old file; new sessions use the new one.
- Codex uses `launcher.py codex`, which this change does not touch.

## What is not verified

Whether Claude desktop's Code tab shares `~/.claude` with the `claude` command line used above, and the exact
wording of its trust prompt for plugin hooks, have not been observed; `docs/hook-engine-phase1-interactive-checks.md`
is the checklist for recording both in a throwaway configuration first. A recorded failure of the `mcp_tool` hook
at `SessionStart` is why that one hook is a plain command.
