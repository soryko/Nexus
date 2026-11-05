"""The Claude Code plugin is hooks-only over an existing nexus-memory server."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from nexus_memory.transport.codex import SESSION_CUE

PLUGIN = Path(__file__).parents[2] / "integrations" / "claude-plugin"


def hooks() -> dict:
    return json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]


def test_manifest_names_the_plugin_and_bundles_no_server() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "nexus-memory"
    assert "mcpServers" not in manifest
    assert not (PLUGIN / ".mcp.json").exists()
    # The launcher is never copied into the plugin.
    assert not list(PLUGIN.rglob("launcher*")) and not list(PLUGIN.rglob("project_scope*"))


def test_hook_wiring_events_tools_inputs_and_timeouts() -> None:
    config = hooks()
    assert set(config) == {"SessionStart", "UserPromptSubmit", "PreToolUse"}
    every = [h for groups in config.values() for group in groups for h in group["hooks"]]
    assert all(h["timeout"] <= 2 for h in every)
    start, = config["SessionStart"][0]["hooks"]
    assert start["type"] == "command"  # mcp_tool is unavailable at SessionStart (observed, 2.1.316)
    recall, = config["UserPromptSubmit"][0]["hooks"]
    assert (recall["type"], recall["server"], recall["tool"]) == ("mcp_tool", "nexus-memory", "nexus_recall")
    assert set(recall["input"]) == {"session_id", "cwd", "prompt"}
    pre, = config["PreToolUse"][0]["hooks"]
    assert (pre["type"], pre["server"], pre["tool"]) == ("mcp_tool", "nexus-memory", "nexus_pre_tool")
    # Only listed inputs arrive; the engine denies without session_id and cwd.
    assert set(pre["input"]) == {"session_id", "cwd", "tool_name", "tool_input"}
    assert config["PreToolUse"][0]["matcher"] == "mcp__nexus-memory__.*"


def run_session_start(env: dict | None = None, cwd: str = "/") -> dict:
    out = subprocess.run([sys.executable, "-I", str(PLUGIN / "hooks" / "session_start.py")],
                         input=json.dumps({"cwd": cwd, "session_id": "s", "hook_event_name": "SessionStart"}),
                         capture_output=True, text=True, check=True, env=env, timeout=10).stdout
    return json.loads(out) if out.strip() else {}


def test_session_start_cue_is_the_engines_fixed_text() -> None:
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("session_start", PLUGIN / "hooks" / "session_start.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CUE == SESSION_CUE
