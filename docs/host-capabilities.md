# Stage A: what the hosts actually did

Fresh macOS arm64 observations on the A13 working tree based on `8b3ea5b`. These are not claims about a user's existing configuration.

* **Codex CLI 0.169.0:** a fresh `codex app-server --stdio` initialized an ephemeral read-only thread. `mcpServer/tool/call` on the configured stdio server returned the verified release path, all three semantic steps and file proof, with exact source revisions and span digests. This was an actual host tool call, not a model choosing to recall, not a model answer, and not a native hook certification.
* **Claude Code 2.1.318:** `claude mcp add --scope local` and `claude mcp get` reported the fixture connected in a fresh home/project. This verifies registration and health only. No observed Claude recall result; **receiving-harness certification pending**.
* **DSH 0.2.1-rc.5 / Cordis 4.0.5:** the external packed consumer and real disposable Profile/Loader ran. The Loader co-mounted two restricted Sources, read their separate fixtures and denied writes. This is not a production writable-audience audit or a full DSH save turn. In the canonical path drill the exact save comes from a **bridge protocol client**, not a DSH user session. Full writable host acceptance remains pending.

`uv run python tools/probe_memory_v2_hosts.py` repeats the first two observations on a Mac with those CLIs and `sandbox-exec`. It generates a new temporary enrollment, HOME, Claude configuration and CODEX_HOME. Network access is denied to the host probes; no turn/start or inference request. The emitted JSON retains only version, outcome, fixture IDs/digests, not raw host diagnostics. Linux CI runs the independent protocol gate; it does not claim this host result.

The ordinary MCP acceptance test uses the Python MCP client and fresh server processes. It is useful protocol evidence even where neither receiving host exists, but a passing test never upgrades a host to certified.
