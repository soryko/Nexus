# Nexus Memory for Antigravity

Local, append-only project memory backed by SQLite, lexical BM25 retrieval, content-free operational telemetry, and cryptographic Git reference verification.

## Features

- **Fail-Closed Workspace Isolation**: Every project scope is resolved through `project_scope.py` against the owner-managed registry at `~/.local/share/nexus-memory/project-scopes-v1/registry.json`. Unenrolled workspaces and macOS APFS volume renumberings (`st_dev` drift) fail closed without silently creating or rebinding databases.
- **Portable Runtime Discovery (`agy-node`)**: Automatically locates or bootstraps a verified Python `>= 3.12` + linked SQLite `>= 3.51.3` environment via `uv`, or surfaces structured diagnostic guidance through `status` and Nexus Studio if floors are not yet met.
- **Embedded MCP Apps (`ui://`)**: Inline cards for `status`, `get`, and `compare_at_head`, plus a fallback `open_atlas` view (`Nexus Atlas`) in the side pane or sidebar.
- **Nexus Studio Sidecar (`sidecars/nexus-studio/`)**: Read-only auxiliary pane showing live content-free Activity & Health telemetry (`<store>.activity.sqlite3`), the full Memory Atlas (search, revision history, diffs, Git reference checks at `HEAD`), and Setup / APFS Drift diagnostics.
- **Optional Scheduled Audit (`sidecars/nexus-audit/`)**: Weekly read-only cron check verifying store binding and Git reference currency at `HEAD` (ships disabled until reviewed and enabled).

- **Catalog repos (registry version 2)**: a repo enrolled through an approved folder keeps its memory in the owner catalog. The gateway enrolls it on its first attested open and mints a grant for each project. A subagent (its conversation differs from the root conversation) gets a read-only grant. Every call goes through the catalog's fence. Supported tools: `record`, `revise`, `forget`, `get`, `history`, `search`, `status`. Refused for now: `spaces`, `compare_at_head` and the Atlas app. After a disable the gateway refuses calls without poisoning the connection, and after a re-enable or an owner's `mode maintain` it mints a fresh grant on its own. Hooks read through a one-time read-only grant. A recall-only repo gets recall-only guidance and never a Stop nudge.

## Privacy & Capture Policy

- **Autonomous + `Stop`-nudge capture (`forget` stays explicit-only)**: The agent proactively records or revises verified architectural decisions, non-obvious constraints, procedures, and root-caused failures (checking `search` first), backed by a bounded `Stop`-hook nudge on edit/decision turns (`NEXUS_CAPTURE_POLICY=explicit` restores explicit-only mode). `forget` always requires explicit user request.
- **Host-attested workspace binding**: On Antigravity surfaces whose global plugin MCP client omits MCP `roots`, `PreToolUse` / `PostToolUse` hooks write a 10-second `0600` host workspace lease so MCP calls resolve the active enrolled checkout fail-closed without per-repo config.
- **Read-only UI**: Nexus Studio and MCP App views never write memories, enroll repositories, or mutate `.git`.
- **Content-free telemetry**: `<store>.activity.sqlite3` logs only allowlisted event kinds, opaque UUIDs, counts, and error codes—never prompt text, queries, memory bodies, or filesystem paths.

## Quick Setup

1. Ensure Python `>= 3.12` with linked SQLite `>= 3.51.3` and `uv` are installed on the machine (for macOS Homebrew: `brew install python@3.14 uv`).
2. Install the non-editable Nexus runtime or build into `~/.local/share/nexus-memory/venvs/`:
   ```bash
   python3 tools/install.py --venv "$HOME/.local/share/nexus-memory/venvs/0.2.0"
   ```
3. Explicitly enroll each repository from its main checkout root:
   ```bash
   python3 -I scripts/launcher.py enroll /path/to/repo
   ```
4. If macOS APFS volume renumbering triggers `device_drift`, run the exact recovery command printed by `status` or Nexus Studio:
   ```bash
   python3 -I scripts/launcher.py recover /path/to/repo --expected-project-id <UUID>
   ```
5. On Antigravity surfaces whose global plugin MCP client does not advertise `roots` and does not invoke `PreToolUse` hooks (`host_missing_roots`), Nexus Studio and hooks still receive workspace paths natively. To pin MCP tools directly to one enrolled repository, add a workspace-scoped `.agents/mcp_config.json`:
   ```json
   {
     "mcpServers": {
       "nexus-memory": {
         "command": "node",
         "args": ["~/.gemini/config/plugins/nexus-memory/scripts/launch.mjs", "mcp", "--workspace", "/abs/path/to/repo"]
       }
     }
   }
   ```
   (Expand `~` to your absolute `$HOME` path.)
