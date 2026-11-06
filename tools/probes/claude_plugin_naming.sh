#!/bin/sh
# Read-only probe: how does Claude Code name a plugin's MCP server and its tools?
# Disposable CLAUDE_CONFIG_DIR and project, synthetic server, no model call (the run ends at
# "Not logged in" after the init message). Never touches the real profile or store.
# usage: claude_plugin_naming.sh /path/to/python-with-mcp
set -eu
PY="${1:?python with the mcp package}"
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/plug/.claude-plugin" "$W/proj" "$W/cfg"
echo '{"name":"nexusprobe","version":"0.0.1","description":"probe"}' > "$W/plug/.claude-plugin/plugin.json"
cat > "$W/plug/.mcp.json" <<JSON
{"mcpServers":{"nexus-memory":{"command":"$PY","args":["$HERE/probe_server.py"],"env":{"PROBE_LOG":"$W/log.jsonl"}}}}
JSON
cd "$W/proj" && git init -q
CLAUDE_CONFIG_DIR="$W/cfg" claude -p hi --plugin-dir "$W/plug" --output-format stream-json --verbose </dev/null 2>/dev/null |
  "$PY" -c '
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e.get("type") == "system" and e.get("subtype") == "init":
        print("servers:", [s["name"] for s in e["mcp_servers"]])
        print("tools:  ", [t for t in e["tools"] if t.startswith("mcp__")])'
