---
name: nexus-atlas
description: Open and navigate Nexus Studio or the read-only Nexus Memory Atlas to inspect live content-free Activity & Health telemetry, browse revision chains, and verify Git references at HEAD.
---

# Nexus Atlas & Studio Guide

Nexus exposes two complementary read-only inspection surfaces (neither mutates memories or `.git`):

1. **Nexus Studio Sidecar (`nexus-studio`)**: Persistent side-pane workbench with three tabs:
   - **Activity & Health**: Live reverse-chronological feed from `<store>.activity.sqlite3` (`session_start`, `recall`, MCP read/write outcomes, scope/runtime errors) with strict allowlist privacy (no prompt prose, queries, memory bodies, or paths).
   - **Memory Atlas (Read-Only)**: Lexical BM25 search, kind filters, full revision text, immutable revision chain + diff inspection, related/neighborhood links, on-demand **Verify at HEAD**, **Open File** in Antigravity's aux pane, and **Attach to Next Prompt**.
   - **Automations & Setup**: Python/SQLite runtime floor checks, explicit-only capture status, APFS drift diagnostics, and setup status for the optional weekly read-only audit automation.
2. **Embedded MCP App (`open_atlas`)**: Side-pane and full-page fallback view rendered inside MCP hosts when calling `open_atlas` (or opening Nexus Atlas from the `+` menu / sidebar).

## Navigation Rules

- Call `open_atlas` on `nexus-memory_nexus-memory` when the user asks to browse or open the Memory Atlas directly in conversation/sidebar.
- Emit `[Nexus Studio](sidecar://nexus-memory/nexus-studio/)` only if the `Nexus Studio` sidecar is installed, enabled, and running.
- Never claim stored text is current until Git references have been verified (`compare_at_head`) or checked against workspace files.
