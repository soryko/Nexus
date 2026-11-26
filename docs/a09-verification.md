# A09 lead review — base 5b8d242

Uncommitted working tree. Tests used local SDK transports, disposable catalogs, stores and Git checkouts, and synthetic host commands. No host certification or deployment is claimed.

**Red:** `uv run pytest -q tests/transport/test_v2_broker.py tests/transport/test_host_control.py --tb=short` before implementation: exit 1, six failures (missing broker, host control and v2 MCP factory). Intermediate runs exposed fixture DTO field names, macOS Unix socket path length, and a noncanonical UTC timestamp rejected by the retrieval context.

**Green:** named plan gate, exit 0: **32 passed, 1 xfailed** (24.62 s). Both E2E server variables selected this checkout's `.venv/bin/nexus-memory`; integration and installed plugin overrides were unset. Existing expected failure: plugin-prefixed Claude MCP names in the optional **legacy** pre-tool gate. This is not used for v2 authority. `uv run pytest -q tests/graph tests/spaces`: **108 passed** (5.49 s). `git diff --check`: exit 0.

```sh
uv run pytest -q tests/transport/test_v2_broker.py tests/transport/test_host_control.py tests/transport/test_codex_integration.py tests/transport/test_claude_plugin.py tests/transport/test_project_isolation_e2e.py tests/transport/test_hook_engine_names.py
```

**Evidence:** a genuine admitted result drained through the SDK client, digest/substitution rejection, one catalog publication per successful route including duplicate exact write, real curation and CAS correction, retrieval and repeated pack opens, fixed busy/revoked errors, private command invocation and wrong-capability refusal, two-call cap, byte charging and expiry, generic stdio resume and an enrolled linked-worktree project launch. No change to the default legacy hook manifest. No observer writes.

**Activation contract:** both `--catalog` and `--grant-file` are required. The owner-only JSON ticket names `grant_id`; project launches also require a matching `project_id` and an existing registered checkout identity. The grant selects the sole mounted schema-6 partition. An explicit Claude native installation selects `hooks-v2.json` instead of the legacy hook manifest. Its launcher must coordinate a fresh private Unix socket path and capability between MCP and hook processes, select the installed hook interpreter and supply a reliable turn ID. Absent private support, use generic explicit operations. No model tool registers a context or changes the allowance.

**Unverified:** live host event fields, actual installation/launcher capability distribution, Linux execution, native reconnect/turn continuity across a process restart, performance under load, and end-user delivery/acknowledgement. Allowances and pack handles are process-local. A command fixture demonstrates ingress, not a certified Claude/Codex recipe. No live profiles, credentials, external models, merge, commit or push were used.
