---
name: nexus-setup
description: Diagnose and guide Nexus Memory setup issues: Python>=3.12 and SQLite>=3.51.3 runtime floors, explicit repository enrollment, macOS APFS st_dev drift recovery, and read-only scope verification.
---

# Nexus Memory Setup & Diagnostics

Nexus Memory enforces **fail-closed scope isolation** and **strict runtime floors** (`Python >= 3.12` and linked `SQLite >= 3.51.3`). Never bypass registry checks or auto-enroll a repository silently.

## Step 1: Check Current Status

Call `status` on `nexus-memory_nexus-memory` (or inspect the top Health Strip / Setup tab in Nexus Studio if enabled).

Interpret `scope_state` (when unavailable) or `durable_storage`:

| State | Meaning | Required Owner Action |
| :--- | :--- | :--- |
| `durable_storage: "available"` | Workspace is enrolled and verified. | Ready to use. |
| `device_drift` | Checkout matches registered `.git` `inode` and `birth_time`, but macOS APFS renumbered `st_dev` after an OS update or volume remount. | Ask the user to run the exact printed `recover` command in their terminal after confirming it is the same checkout. |
| `unenrolled` | Valid Git checkout with no entry in `~/.local/share/nexus-memory/project-scopes-v1/registry.json`. | Ask the user whether they want to enroll explicitly from the main checkout root. |
| `identity_changed` | Path exists in registry, but `inode` or `birth_time` changed (repo recloned or replaced). | Inspect carefully before any manual registry change; do not force-overwrite unknowingly. |
| `runtime_unavailable` | No Python `>= 3.12` with linked `SQLite >= 3.51.3` and `nexus-memory` package was found. | Install qualifying Python + `uv` (e.g. `brew install python@3.14 uv`) and build a non-editable venv with `tools/install.py --venv ...`. |

## Owner Terminal Commands

Run these from the main Git checkout (never from a linked worktree when enrolling):

```bash
# Enroll a repository explicitly
python3 -I ~/.gemini/config/plugins/nexus-memory/scripts/launcher.py enroll /path/to/repo

# Confirm APFS device-only drift (use exact UUID shown by status/Nexus Studio)
python3 -I ~/.gemini/config/plugins/nexus-memory/scripts/launcher.py recover /path/to/repo --expected-project-id <UUID>
```

After changing registry state or upgrading runtime venvs, use **Refresh** in Nexus Studio or refresh MCP servers in Antigravity settings.
