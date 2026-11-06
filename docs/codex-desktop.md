# Codex Desktop integration

Nexus can serve Codex Desktop through the same local stdio MCP configuration used
by the Codex CLI. This integration is local-computer only; it does not deploy a
remote service or change other memory providers.

## Runtime and scope

Run `tools/install.py` if needed, then `.venv/bin/python tools/check_install.py`.
Python >= 3.12, linked SQLite >= 3.51.3, FTS5, and Git are required for the full
integration. Use an absolute installed executable path, a private database outside
the checkout, and stable namespace and actor values. Both namespace **and actor**
partition memories. Back up an existing database through SQLite's online backup
API before first launch with an updated server.

The opt-in `--codex-project-root /absolute/canonical/repo` option validates the
server's launch cwd against that repository's common Git directory, then binds
reference verification to the **active checkout**. Do not configure MCP `cwd` to
the main checkout: let Codex supply the task directory. Wrong projects and fresh
clones fail before storage is opened. Linked worktrees share the scope but verify
their own HEAD. The normal seven-tool server is unchanged without this option.

Install this host-specific configuration in the trusted project's
`.codex/config.toml` (replace example paths):

```toml
[mcp_servers.nexus_memory]
command = "/absolute/nexus-memory/.venv/bin/nexus-memory"
args = ["--namespace", "nexus-memory", "--actor", "local",
        "--db", "/private/data/memory.sqlite3",
        "--codex-project-root", "/absolute/project"]
startup_timeout_sec = 10
tool_timeout_sec = 10
required = false
```

Keep host paths out of shared Git configuration. A new worktree must receive the
project configuration through the normal local setup; Git does not copy ignored
files. Do not install a global fixed-project endpoint in unrelated projects.

## Hooks

Add these to the same file. Review the exact definitions in Codex's hook trust UI
(or `/hooks` in the CLI). Installing the file does not itself grant hook trust.
Never manufacture `trusted_hash` entries. A project must also be trusted for its
configuration to load.

```toml
[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"
[[hooks.SessionStart.hooks]]
type = "mcp_tool"
server = "nexus_memory"
tool = "codex_session_start"
input = { cwd = "${cwd}", session_id = "${session_id}" }
timeout = 2
statusMessage = "Preparing Nexus project memory"

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "mcp_tool"
server = "nexus_memory"
tool = "codex_recall"
input = { cwd = "${cwd}", session_id = "${session_id}", prompt = "${prompt}" }
timeout = 2
statusMessage = "Checking Nexus memory candidates"
```

`codex_session_start` provides fixed workflow guidance and resets per-session
deduplication. SessionStart can precede MCP readiness; `AGENTS.md` provides the
workflow fallback. `codex_recall` searches once with up to 32 prompt terms
and inspects excerpts from at most 24 current lexical hits. It prioritizes
identifiers and paths even when they appear late. Explicit identifiers and short
topic phrases such as “for cedar deliveries,” must match. A trailing qualifier
such as “on cedar deliveries” must match the whole subject when excerpts
identify deliveries as a stored topic. Longer prompts also
need evidence about the requested operation. The hook compares the combination
of matching words across candidates, abstains on conflicting equal matches, and
returns at most three canonical memory/revision UUIDs. A direct single-term
lookup still returns exact matches. Stored prose, tags, paths, and the user
prompt stay out of developer context. The agent fetches a candidate with
`get`, checks both subject and operation, and stops looking once the task is
answered. A rejected lexical shortlist produces a fixed advisory with no record
details. A lookup with no hits, an invalid scope, or a repeated qualified result
adds no context.

This lexical filter can miss paraphrases or evidence outside the search
excerpt. A result establishes overlap, not applicability. Manual search remains
available when the hook abstains. The same revision is
suppressed within a session until reset/eviction; another session or a new revision
can recall it again. Deduplication is bounded in process memory (128 sessions, 128
revisions each), and is not durable. A restart may repeat a candidate.

Hooks do not write memory, parse transcripts, make model calls, or send data to an
external service. MCP initialization can create/migrate storage and register a Git
checkout token; perform that preflight deliberately. Recall errors are advisory.
No Stop or permission-approval hook is installed. Existing global/plugin hooks run
alongside these hooks; adding Nexus does not disable Supermemory or make other
providers local-only.

## Validation and rollback

1. Run `.venv/bin/python -m pytest -q tests/transport/test_codex_integration.py`.
2. Verify the effective project entry with `codex mcp get nexus_memory --json`.
3. Start a fresh Desktop chat in this project. Confirm the server is available and
   review any hook trust request; inspect `status` for verification availability.
4. Save an explicitly authorized canary, retrieve it in another fresh chat, and
   exercise prompt recall, resume and compaction. Tombstone the canary afterward.
5. Check another repository cannot start this endpoint. In a worktree, references
   must resolve its commit rather than the main checkout's commit.

Local MCP round trips do not prove that the Desktop UI loaded or executed hooks.
Record that distinction when reporting installation. `status` does not expose the
database path, namespace or actor; inspect the effective launch configuration for
those values. Do not inspect private memory bodies merely to diagnose connectivity.

Rollback: remove only the `nexus_memory` MCP table and the two Nexus hook entries,
or disable them through Codex. Preserve the SQLite database and backups. Restore
the previous configuration backup if this was its only change.

References: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Codex hooks](https://learn.chatgpt.com/docs/hooks).

## Optional task-directed Claude sessions

For a task reader, point the `nexus_memory` MCP alias at the ordinary scoped
server and `nexus_recall` at the hook-enabled server for the same enrolled
checkout, database and actor. The recipe matches exactly those two aliases.
Use `--strict-mcp-config` to exclude the separately configured global alias
`nexus-memory` and any other MCP route to the store. If you deliberately use
the hyphenated name, change the matcher to include it. Use these per-invocation settings. Both
hooks must use the same running `nexus_recall` connection.

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{
      "type": "mcp_tool", "server": "nexus_recall", "tool": "codex_recall",
      "input": {"cwd": "${cwd}", "session_id": "${session_id}", "prompt": "${prompt}"}
    }]}],
    "PreToolUse": [{"matcher": "^mcp__(nexus_memory|nexus_recall)__.*$", "hooks": [{
      "type": "mcp_tool", "server": "nexus_recall", "tool": "task_pre_tool_use",
      "input": {"cwd": "${cwd}", "session_id": "${session_id}",
                "tool_name": "${tool_name}", "tool_input": "${tool_input}"}
    }]}]
  }
}
```

From the enrolled checkout, pass these settings and the two scoped MCP entries
using `claude --restricted --strict-mcp-config --mcp-config /path/to/servers.json
--settings /path/to/task-hooks.json` on a single command line. Keep the reader's
usual tool permissions. A successful guard returns no decision, so host
permissions still govern the call. Confirm a successful hook response in the
native trace. Claude can continue when an MCP hook is unreachable or returns an
error. [Claude hook reference](https://code.claude.com/docs/en/hooks#mcp-tool-hook-fields)

Each prompt replaces its session's admitted IDs. Use `get` with only an admitted
memory ID to read its live head. Unrelated, historical and broader discovery
reads are declined for that prompt. To deliberately inspect history or search
more broadly, start a fresh user prompt with the exact prefix `Nexus explore:`.
Model-initiated lifecycle calls are declined even during exploration. The guard
retains only the mode and IDs in memory for up to 128 sessions. It performs no
memory-store reads or writes. Clients that omit this PreToolUse hook retain the
ordinary Nexus tool semantics.
