#!/bin/sh
# Probe which Claude Code hook events accept type=mcp_tool, and whether they wait for a slow server.
# Disposable project, synthetic server, real login (one haiku call per run, nothing persisted:
# --no-session-persistence). Reads the debug log; never touches the Nexus store.
# usage: claude_mcp_tool_hooks.sh /path/to/python-with-mcp SERVER_DELAY_SECONDS
set -eu
PY="${1:?python with the mcp package}"; DELAY="${2:-3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/proj/.claude"; cd "$W/proj"; git init -q
cat > .mcp.json <<JSON
{"mcpServers":{"probe":{"command":"$PY","args":["$HERE/probe_server.py"],"env":{"PROBE_LOG":"$W/log.jsonl","PROBE_DELAY":"$DELAY"}}}}
JSON
cat > .claude/settings.json <<'JSON'
{"hooks":{
 "SessionStart":[{"hooks":[{"type":"mcp_tool","server":"probe","tool":"ping","input":{"session_id":"${session_id}","cwd":"${cwd}"}}]}],
 "UserPromptSubmit":[{"hooks":[{"type":"mcp_tool","server":"probe","tool":"ping","input":{"session_id":"${session_id}","cwd":"${cwd}","prompt":"${prompt}"}}]}],
 "PreToolUse":[{"matcher":"mcp__probe__other","hooks":[{"type":"mcp_tool","server":"probe","tool":"ping","input":{"tool_name":"${tool_name}"}}]}]},
 "enableAllProjectMcpServers":true}
JSON
claude -p "Call the mcp__probe__other tool once." --model haiku --no-session-persistence \
  --allowedTools mcp__probe__other --debug hooks,mcp --debug-file "$W/debug.log" </dev/null >/dev/null 2>&1 || true
echo "--- server log"; cat "$W/log.jsonl"
echo "--- hook lines"; grep -E "mcp_tool|Hook (SessionStart|UserPromptSubmit|PreToolUse)[^ ]* .*error" "$W/debug.log" | cut -c1-260
