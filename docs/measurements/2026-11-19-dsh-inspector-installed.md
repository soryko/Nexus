# Installed-browser saved-revision test — PASS

[Open inspector](2026-11-19-dsh-inspector-installed-open.png) · [Returned to chat](2026-11-19-dsh-inspector-installed-return.png) · [Run receipt and byte hashes](2026-11-19-dsh-inspector-installed.json)

Chrome clicked **Saved memory** beside the originating chat message. No Nexus call was observed between fixture setup and the click. The click made exactly one successful `mcp__nexus__get`, with the original revision ID, after a separate revision had become current. The rendered text matched the original string exactly, including leading spaces, a tab, the accented character, literal angle brackets and the final newline. Both revision IDs matched the store receipts. **Return to turn** collapsed the inspector and restored keyboard focus to the same cue in the visible chat.

**Real components:** packed inspector installed via `dsh plugin --profile web add`; byte equality of installed Host, Client, descriptor and projection against the workspace; checkout-built DSH CLI/Host and Web bundle; real Chrome, authenticated owner HTTP gateway, DSH session and turn-tail projection, native tool executor, stdio MCP bridge and disposable Nexus SQLite database. **Fixture:** a test-only startup overlay creates one synthetic root session, appends its turn and capture-success notice, and seeds/revises the test record through DSH’s MCP tools. No model, organic capture, live store or production profile was exercised. The observer neither implements the RPC nor substitutes a tool result.

From the Nexus root with `.venv` installed, a built DSH checkout, Chrome, Node, npm and pnpm available:

```sh
DSH_CHECKOUT=/Users/soko/Repos/deepseek-harness \
  .venv/bin/python integrations/dsh-inspector/tests/run-installed.py
```

[Launcher](../../integrations/dsh-inspector/tests/run-installed.py) · [Browser assertions](../../integrations/dsh-inspector/tests/installed-browser.mjs) · [Synthetic turn fixture](../../integrations/dsh-inspector/tests/installed-fixture.mjs). Installation needs package registry access in a fresh temp home. The host and browser receive an allowlisted environment, and the test uses a separate loopback port (not 3080). The launcher terminates the host process group, confirms the listener is closed, and removes the temporary home, profile and store on exit.

During development, the original client failed the gateway's strict input codec validation at boot ([first gate](2026-11-19-dsh-inspector-initial-gate.png)); the subsequent checks caught a missing client namespace injection and a strict lookup-provider symbol mismatch. The passing receipt identifies the tested corrected bytes, not the earlier builds.
