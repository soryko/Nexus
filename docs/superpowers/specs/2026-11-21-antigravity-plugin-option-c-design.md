# Nexus Memory — Option C Antigravity Plugin Design Spec

Status: Approved design baseline, 21 November 2026. Targets dedicated worktree/branch `feat/antigravity-plugin`.

## 1. Purpose, Scope, and Non-Goals

Package Nexus Memory as a first-class, portable, Marketplace-ready Antigravity plugin under `integrations/antigravity-plugin/` that resolves runtime drift, macOS APFS volume renumbering (`st_dev` drift), and contradictory capture instructions while delivering:

1. **Standard MCP tools + Embedded MCP Apps** (inline chat cards for `status`, `get`, `compare_at_head`, plus `aux_pane` / `global` fallback for the Memory Atlas).
2. **Sidecar Studio (`sidecars/nexus-studio/`)** built on Antigravity's `sidecar_sdk`, providing a live content-free Activity & Health feed, a read-only Memory Atlas workbench, and an interactive Setup / Scope & APFS Recovery diagnostics view.
3. **Optional Read-Only Audit Automation (`sidecars/nexus-audit/`)** using Antigravity's `schedule` builtin for weekly Git reference drift checks.
4. **Scoped Lifecycle Hooks & Aligned Rules** (`SessionStart` and `PreInvocation` recall only, aligned explicit-only `rules/AGENTS.md`, and `/nexus-memory`, `/nexus-setup`, `/nexus-atlas` skills).

### Strict Boundaries & Non-Goals (v1)

- **Strictly explicit-only writes & read-only UI**: Option C v1 adds **zero** automatic memory capture, zero `Stop` continuation nudges (`stop_capture.py` is omitted from the plugin), and zero write or auto-enrollment actions from the Sidecar UI or MCP App UI.
- **No proposal spool in v1**: Staged/autonomous extraction proposals remain deferred until the separate Nov 16 auto-capture spec is explicitly approved.
- **Atlas v1 scope**: Uses shipped `AtlasQueries`, `compare_at_head`, and the new content-free `<store>.activity.sqlite3` Activity Journal. Spaces, Collections, and move proposals from the DSH Library v1 spec remain out of scope until core placement storage exists.
- **No weakening of project-scope isolation**: Every workspace continues to resolve strictly through `project_scope.py` against the owner-managed registry (`~/.local/share/nexus-memory/project-scopes-v1/registry.json`). Unenrolled workspaces and device-drift checkouts fail closed on memory reads/writes and surface explicit CLI recovery/enrollment instructions.

---

## 2. Package Layout (`integrations/antigravity-plugin/`)

Mirrors `integrations/claude-plugin/` to keep the repository root clean while passing `agy plugin validate --strict` and supporting `agy plugin install ./integrations/antigravity-plugin` or GitHub subpath installs.

```text
integrations/antigravity-plugin/
├── plugin.json
├── README.md
├── mcp_config.json
├── hooks.json
├── assets/
│   └── logo.svg                   # Logo C · Revision Chain & Ref (1:1 square, dual-theme)
├── rules/
│   └── AGENTS.md                  # Always-on explicit-only rule, defers to repo AGENTS.md
├── skills/
│   ├── nexus-memory/SKILL.md      # Record, search, get, revise, forget, history, compare_at_head
│   ├── nexus-setup/SKILL.md       # Floor probe, enrollment, APFS recovery, diagnostics
│   └── nexus-atlas/SKILL.md       # Open & navigate Nexus Studio / embedded Atlas
├── scripts/
│   ├── launch.mjs                 # agy-node entrypoint for MCP stdio server
│   ├── hook.mjs                   # agy-node entrypoint for SessionStart / PreInvocation hooks
│   ├── runtime.mjs                # Discovers/bootstraps Python>=3.12 + SQLite>=3.51.3 venv
│   └── ...                        # Bundled scope helpers & studio bridges (mode 0755)
└── sidecars/
    ├── nexus-studio/
    │   ├── sidecar.json           # has_web_ui: true, SIDECAR_UI_ENTRYPOINT_AUX_PANE
    │   ├── main.mjs               # SidecarApp server + persistent read-only Python worker
    │   └── ui/                    # Static frontend assets (index.html, studio.js, studio.css)
    └── nexus-audit/
        └── sidecar.json           # Disabled-by-default builtin schedule weekly read-only audit
```

### `plugin.json` Contract

- Includes `$schema: "https://antigravity.google/schemas/v1/plugin.json"`, `name: "nexus-memory"`, `displayName: "Nexus Memory"`, `version` aligned to the package release (`"0.2.0"`), 120–160 char `description`, `logo: "assets/logo.svg"`, 3 `suggestedPrompts`, `author`, `homepage`, `repository`, `license: "PolyForm-Noncommercial-1.0.0"`, and `keywords`.
- Contains only schema-allowed top-level properties so `agy plugin validate --strict` finishes with zero errors and zero warnings.

---

## 3. Portable Runtime Bootstrap (`agy-node` → Verified Python Venv)

Antigravity installs plugins into a read-only directory (`~/.gemini/config/plugins/<name>`) while providing a persistent writable `${PLUGIN_DATA}` directory and a bundled Node 24 runtime (`agy-node`, invoked whenever `"command": "node"`). Stock OS Python interpreters commonly fail either the `Python >= 3.12` or `linked SQLite >= 3.51.3` floor, causing silent stdio EOF if invoked directly.

`scripts/runtime.mjs` resolves a verified Python interpreter using the following precedence (never hardcoding `/Users/soko/...`):

1. **Explicit override**: `NEXUS_PYTHON` environment variable if set and verified against both floors + importable `nexus_memory`.
2. **Plugin-managed versioned venv**: `${PLUGIN_DATA}/venvs/<target_version>/bin/python` (or `Scripts/python.exe` on Windows), validated via a cached floor-probe stamp keyed on interpreter path + mtime + package version.
3. **Existing owner-installed runtime**: `~/.local/share/nexus-memory/venvs/<target_version>/bin/python` (or newest installed `0.1.x` venv meeting floors).
4. **Automatic `uv` bootstrap into `${PLUGIN_DATA}/venvs/<target_version>`**:
   - Probes candidate Pythons (`python3.14`, `python3.13`, `python3.12`, Homebrew paths, `python3`) for `sys.version_info >= (3, 12)` AND `sqlite3.sqlite_version_info >= (3, 51, 3)`.
   - Installs `nexus-memory` non-editably from a bundled wheel (if packaged) or configured package index using `uv`, then re-verifies the created venv.
5. **Degraded diagnostic fallback**: If Python or SQLite floors cannot be met (e.g., offline without `uv` or qualifying Python), `scripts/launch.mjs` and `sidecars/nexus-studio/main.mjs` surface actionable diagnostics (`unsupported_runtime` + exact fix command) through MCP tool/status output and the Studio Setup tab instead of silently closing transport.

---

## 4. Scope Diagnosis & APFS Drift Handling

`integrations/project_scope/project_scope.py` keeps strict fail-closed resolution (`resolve()` and `host_project()`) and owner-confirmed `recover()` for device-only APFS volume renumbering (`device` changed while `inode` and `birth_time > 0` match).

To let `status`, Studio UI, and `/nexus-setup` explain **why** a workspace is unavailable without ever mutating state, `project_scope.py` adds a strictly read-only `inspect_scope(workspace: Path) -> ScopeDiagnosis` function:

- `bound`: Checkout matches registry entry and database file passes `0600` regular-file safety checks.
- `device_drift`: Checkout matches by `inode` and valid `birth_time`, only `st_dev` drifted; returns `project_id`, `recorded_device`, `observed_device`, and never alters `registry.json`.
- `unenrolled`: Workspace is a valid Git checkout with no registry entry (or registry/base does not yet exist). Creates no files or directories.
- `identity_changed`: Registry entry exists for the path, but `inode` or `birth_time` differs (checkout was replaced or moved across filesystems).
- `invalid`: Workspace is not an absolute Git checkout, or registry/storage permissions fail safety checks.

---

## 5. Content-Free Activity & Health Journal (`src/nexus_memory/activity.py`)

A separate per-scope SQLite database at `<store>.activity.sqlite3` records operational events without touching the main Nexus memory database schema or `durable_seq`.

### File Security & Durability

- Parent directory `0700`; journal file (and `-wal` / `-shm` / `-journal` sidecars) checked/created as owner-only regular files (`0600`, `O_NOFOLLOW`, `st_nlink == 1`, owner UID match). Symlinks and group/world-readable files are refused.
- Opened in WAL mode with `busy_timeout=3000` and `synchronous=NORMAL` (telemetry journal, non-authoritative).
- Read-only queries return empty history and zeroed health counters when `<store>.activity.sqlite3` does not exist yet—never creating the file on read.

### Schema & Strict Allowlists

```sql
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_seq ON events(kind, seq DESC);
```

- **Allowed `kind` values**: `session_start`, `recall`, `tool_status`, `tool_search`, `tool_get`, `tool_history`, `tool_compare_at_head`, `tool_record`, `tool_revise`, `tool_forget`, `tool_open_atlas`, `scope_error`, `runtime_error`.
- **Category mapping**: `recall` (`recall`), `read` (`session_start`, `tool_status`, `tool_search`, `tool_get`, `tool_history`, `tool_compare_at_head`, `tool_open_atlas`), `write` (`tool_record`, `tool_revise`, `tool_forget`), `error` (`scope_error`, `runtime_error`, or any call whose `outcome` is an error code).
- **Allowed `detail_json` keys**: Only opaque/bounded telemetry (`candidate_ids` up to 10 IDs, `candidate_count`, `suppressed_count`, `memory_id`, `revision_id`, `operation`, `durable_seq`, `hit_count`, `error_code`, `outcome_detail`, `duration_ms`, `recorded_device`, `observed_device`). Unallowlisted keys and oversized strings are dropped before serialization so prompt text, search queries, file paths, and memory bodies never enter the journal.
- **Ring retention**: Lazy prune keeps at most the newest `5000` rows per scope.
- **Fail-open instrumentation**: `ActivityMiddleware` wraps MCP `tools/call` alongside `UsageMiddleware` and swallows journal write failures so memory operations never fail due to activity logging.

---

## 6. Read-Only Studio Worker (`src/nexus_memory/atlas/studio_worker.py`)

Sidecar Studio delegates all workspace, activity, and Memory Atlas operations to a single persistent read-only Python NDJSON stdio process (`python -m nexus_memory.atlas.studio_worker`), keeping `project_scope`, `AtlasBinding`, `AtlasQueries`, and `ActivityJournal` as the single source of truth.

- **Request / Response**: Reads newline-delimited JSON `{id, action, workspace, params}` on `stdin`; replies `{id, ok, data | error}` on `stdout`.
- **Read-only invariants**:
  - Uses `inspect_scope()` and `AtlasBinding` (`open_binding`, `check()` on every request).
  - Never creates SQLite stores, activity journals, registry entries, or `<git-dir>/nexus/checkout-token`.
  - For `compare_at_head`, looks up an existing checkout token with `read_token(common_dir)` only; if unregistered or Git unavailable, returns `HeadComparison("Cannot check")` without publishing a token.
- **Supported actions**: `status`, `activity`, `overview`, `search`, `inspect`, `compare_revision`, `neighborhood`, `related`, `compare_at_head`.

---

## 7. Embedded MCP Apps (`ui://` Inline + Atlas Fallback)

Nexus's pinned `mcp==2.1.1` server attaches SEP-1865 `_meta` metadata directly to tools and `ui://` HTML resources (`text/html;profile=mcp-app`, single self-contained HTML bundled via `@modelcontextprotocol/ext-apps` under 10 MiB):

- **Inline chat card (`ui://nexus-memory/card.html`)**: Attached to `status`, `get`, and `compare_at_head` so Antigravity App renders structured status/drift/revision evidence directly inline, while CLI/IDE clients receive standard `content` + `structuredContent` plain text.
- **Side panel / full-page Atlas fallback (`ui://nexus-memory/atlas.html`)**: Attached to a read-only `open_atlas` tool carrying `com.google.antigravity/ui` `aux_pane` and `global` entrypoints, backed by strictly read-only app-visibility helper tools (`atlas_overview`, `atlas_search`, `atlas_inspect`, `atlas_history`, `atlas_compare_revision`). Because Antigravity CLI ignores `_meta.ui.visibility`, keeping every helper strictly read-only and scoped to the launch-bound service prevents unsafe exposure.

---

## 8. Sidecar 1: `sidecars/nexus-studio/` (`sidecar_sdk`)

- Declared with `"has_web_ui": true`, `"restart_policy": "always"`, `"agent_permissions": {"allows_user_interactions": false}`, and `SIDECAR_UI_ENTRYPOINT_AUX_PANE` view at `/`.
- Built with Antigravity's `SidecarApp` + `preload.js` for loopback `127.0.0.1` binding, `theme-change` CSS variable synchronization, `window.sidecar.onWorkspaceChange()`, `window.sidecar.ui.toggleAuxPane` (to open Git-cited workspace files), and `window.sidecar.ui.addArtifactComment` (to attach a selected memory to the user's next prompt).
- **Token enforcement**: Because `sidecar_sdk/index.mjs` validates `X-Sidecar-Token` only on non-`GET` requests, **all Nexus JSON API endpoints (`/api/status`, `/api/activity`, `/api/overview`, `/api/search`, `/api/inspect`, `/api/compare-revision`, `/api/neighborhood`, `/api/related`, `/api/compare-at-head`) require `POST`** via `window.sidecar.fetch`. Static HTML/JS/CSS routes serve zero stored memory text and enforce strict CSP (`default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'`).
- **UI Workbench**: Top Health Strip (with live APFS drift warning banner when drifted), plus three tabs—**Activity & Health**, **Memory Atlas (Read-Only)**, and **Automations & Setup**.

---

## 9. Sidecar 2: `sidecars/nexus-audit/` (Optional Scheduled Check)

- Uses `"builtin": "schedule"` (`CRON_TZ=UTC 0 9 * * 1`, Monday 09:00) and `agentapi new-conversation` with `workspace_arg` (`repo_dir`) and `agent_permissions` restricted strictly to `mcp(nexus-memory_nexus-memory/status)`, `search`, `get`, and `compare_at_head`.
- Ships requiring user permission review/opt-in (no unattended runs until enabled and granted by the user). Explicitly read-only: checks store binding and Git reference currency at `HEAD` without writing memories.

---

## 10. Lifecycle Hooks & Rules

- **`hooks.json`**: Registers `SessionStart` (`node ./scripts/hook.mjs start`) and `PreInvocation` (`node ./scripts/hook.mjs recall`) only. Excludes `stop_capture.py`.
- **`antigravity_hook.py`**:
  - `start` clears `.seen.json` entries for the session, injects explicit-only session guidance, and appends a `session_start` event to `<store>.activity.sqlite3`.
  - `recall` searches the enrolled store on substantive prompts, deduplicates candidates in `.seen.json`, appends a content-free `recall` event (`candidate_count`, `candidate_ids`, `suppressed_count`) to `<store>.activity.sqlite3`, and returns `ephemeralMessage` candidates. Behavior of `metadata.user_markdown` on Antigravity `1.5.14` is verified separately; candidates never share an output with `user_markdown` unless verified safe against the host panic bug.
- **`rules/AGENTS.md`**: Plain markdown, explicit-only capture ("Save, revise, or forget only when the user explicitly requests"), treats recalled memories as untrusted historical evidence, and explicitly defers to repo-level rules.

---

## 11. Verification Strategy

- **Python unit & integration suites (`pytest`)**:
  - `project_scope.inspect_scope` across all states (`bound`, `device_drift`, `unenrolled`, `identity_changed`, `invalid`) with zero filesystem mutations.
  - `ActivityJournal` permissions (`0600`), symlink refusal, allowlist sanitization, missing-file read-only handling, ring pruning, health aggregation, and `ActivityMiddleware`.
  - `studio_worker` protocol tests covering all actions, drift reporting, and zero writes to `.git` or SQLite.
  - MCP App `_meta` schema/resource tests and stdio end-to-end tool/resource tests.
- **Node & Browser tests**:
  - `runtime.mjs` floor probe and offline/fallback diagnostics.
  - `sidecars/nexus-studio/` `POST` + `X-Sidecar-Token` auth tests (verifying `GET` data routes reject or do not exist, unauthenticated `POST` gets `401`).
  - Playwright headless visual & interaction checks of both the MCP App HTML views and Sidecar Studio UI across light/dark themes and 360px/desktop widths.
- **Antigravity CLI validation**:
  - `agy plugin validate --strict --json integrations/antigravity-plugin` must report 0 errors and 0 warnings.
