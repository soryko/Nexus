import sys, time, json, os
LOG = os.environ["PROBE_LOG"]
def log(**k):
    with open(LOG, "a") as f: f.write(json.dumps({"t": round(time.time(), 3), **k}) + "\n")
log(ev="process_start", delay=float(os.environ.get("PROBE_DELAY", "0")))
time.sleep(float(os.environ.get("PROBE_DELAY", "0")))
from mcp.server import MCPServer
s = MCPServer("probe")
@s.tool()
def ping(session_id: str | None = None, cwd: str | None = None, prompt: str | None = None, tool_name: str | None = None) -> dict:
    log(ev="ping", session_id=session_id, cwd=cwd, tool_name=tool_name)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "PROBE-CONTEXT-OK"}}
@s.tool()
def other() -> str:
    log(ev="other"); return "x"
log(ev="serving")
s.run()
