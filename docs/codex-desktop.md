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
workflow fallback. `codex_recall` selects up to eight nontrivial lexical terms,
searches at most three current records, and returns only canonical memory/revision
UUIDs. Stored prose, tags, paths, and the user prompt are never interpolated into
developer context. The agent fetches useful candidates through `get` as tool data.

The stopword heuristic is not a relevance classifier and has no measured retrieval
advantage. No useful terms or no matches yields no context. The same revision is
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
