# Nexus Memory for Antigravity

Local, append-only project memory backed by SQLite, lexical BM25 retrieval, content-free operational telemetry, and cryptographic Git reference verification.

## Features

- **Fail-Closed Workspace Isolation**: Every project scope is resolved through `project_scope.py` against the owner-managed registry at `~/.local/share/nexus-memory/project-scopes-v1/registry.json`. Unenrolled workspaces and macOS APFS volume renumberings (`st_dev` drift) fail closed without silently creating or rebinding databases.
- **Portable Runtime Discovery (`agy-node`)**: Automatically locates or bootstraps a verified Python `>= 3.12` + linked SQLite `>= 3.51.3` environment via `uv`, or surfaces structured diagnostic guidance through `status` and Nexus Studio if floors are not yet met.
- **Embedded MCP Apps (`ui://`)**: Inline cards for `status`, `get`, and `compare_at_head`, plus a fallback `open_atlas` view (`Nexus Atlas`) in the side pane or sidebar.
- **Nexus Studio Sidecar (`sidecars/nexus-studio/`)**: Read-only auxiliary pane showing live content-free Activity & Health telemetry (`<store>.activity.sqlite3`), the full Memory Atlas (search, revision history, diffs, Git reference checks at `HEAD`), and Setup / APFS Drift diagnostics.
- **Optional Scheduled Audit (`sidecars/nexus-audit/`)**: Weekly read-only cron check verifying store binding and Git reference currency at `HEAD` (ships disabled until reviewed and enabled).

## Privacy & Capture Policy

- **Explicit-only writes**: No autonomous background memory extraction and no `Stop` continuation hook. Save, revise, or forget records only when explicitly requested.
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
5. On Antigravity surfaces whose global plugin MCP client does not advertise `roots` (`host_missing_roots`), Nexus Studio and hooks still receive workspace paths natively. To also pin MCP tools to one enrolled repository, add a workspace-scoped `.agents/mcp_config.json`:
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
