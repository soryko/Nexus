# Antigravity hook ↔ MCP request correlation probe

PR #28 is blocked until the gateway can tell which MCP request belongs to which `PreToolUse`
hook. Today a request consumes whichever workspace lease is pending, so a call whose hook never
ran (timeout, crash) can take another call's lease and act on that project's store. Closing that
needs an identifier the host puts in **both** places. Unit tests cannot establish what Antigravity
sends; this probe records it from a real host session.

## What it is

`plugin/` is a standalone diagnostic plugin, separate from Nexus. It never touches a memory store.

- `probe_hook.mjs` runs on `SessionStart`, `PreInvocation`, `PreToolUse`, `PostToolUse` and `Stop`.
  It answers with each event's neutral output (`{"decision":"allow"}` for `PreToolUse`).
- `probe_server.mjs` is a minimal MCP stdio server with one tool, `probe_echo(label)`.
- Both append to one JSON-lines log: `${PLUGIN_DATA}/correlation-probe.jsonl` (the tool's reply
  prints the exact path), or `$NEXUS_PROBE_LOG` if set.

**What is logged:** payload key names; values only under id-shaped keys (`id`, `…Id`, `…_id`,
`conversation…`, `session…`, `trajectory…`); the target server and tool names; the JSON-RPC id,
method, `_meta` and parameter key names of each MCP request; the initialize client info and
capability names; process ids, parent ids and timestamps; environment variables whose names
mention session, conversation, Antigravity, Gemini, plugin, workspace or MCP, with secret-looking
names (`KEY`, `TOKEN`, `SECRET`, `AUTH`, …) listed but never valued.

**Never logged:** prompt text, tool arguments (other than the probe's own short label), memory
content. `tests/benchmarks/test_agy_correlation_probe.py` checks that a prompt, a tool argument and
a token placed in the inputs do not reach the log. Read the log before sharing it anyway: paths and
session ids are in it.

## Run it (owner)

The plan authorizes testing in disposable profiles, not changing the live one, so use a throwaway
`HOME`. You will need to sign in inside it; nothing here copies credentials. Each step below is one
short model turn.

```bash
export PROBE_HOME="$(mktemp -d)"
export PROBE_REPO=/Users/soko/Cerebros/nexus-antigravity-plugin
HOME="$PROBE_HOME" agy plugin validate --strict "$PROBE_REPO/benchmarks/agy_correlation/plugin"
HOME="$PROBE_HOME" agy plugin install "$PROBE_REPO/benchmarks/agy_correlation/plugin"
```

1. **One conversation, several calls.** In an empty folder, run
   `HOME="$PROBE_HOME" agy -p "Call the probe_echo tool three times, one after another, with labels a1, a2 and a3. Report each reply."`
   and approve the tool when asked.
2. **Two conversations at once.** In two terminals, in two different folders, start the same prompt
   at the same time with labels `b1 b2 b3` and `c1 c2 c3`. This shows whether one MCP server serves
   both and whether a candidate key differs between conversations.
3. **The surface that matters.** Lease mode exists for Antigravity surfaces whose global plugin MCP
   client does not advertise `roots`. If that is the IDE rather than the CLI, repeat step 1 there
   with the probe installed in that disposable profile. The log's `initialize` record shows whether
   `roots` was advertised.

Then analyze (the tool reply names the log path):

```bash
python3 -I "$PROBE_REPO/benchmarks/agy_correlation/analyze.py" /path/to/correlation-probe.jsonl
```

Finally `HOME="$PROBE_HOME" agy plugin uninstall nexus-correlation-probe` and delete `$PROBE_HOME`.

## Reading the result

- `per_call_key_found`: some hook value (for example `toolCall.id`) equals a value in the matching
  request (for example `_meta.<key>`) for every call, one to one. The gateway can require it.
- `session_key_found`: an id from the hook payload (for example `conversationId`) also reaches the
  server process (environment or initialize). Binding per conversation is possible if step 2 shows
  it differs between conversations and each conversation has its own server process.
- `no_shared_identifier`: nothing ties them. The lease design cannot be made safe from the host's
  current signals; PR #28 stays blocked and the alternatives are roots-only or `--workspace`.
- `shared_env_leads` are variables both processes inherit with the same value. On their own they say
  nothing about which conversation a request came from.

One session is a lead, not a host contract. Confirm a key across both steps and after restarting
the host before building on it, and record the host version (`agy --version`).
