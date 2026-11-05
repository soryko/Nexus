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
    assert manifest["name"] == "nexus"
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


FAKE_SCOPE = '''
from pathlib import Path
class ScopeError(ValueError): pass
CALLS = Path(__file__).with_name("calls.log")
def resolve(workspace):
    CALLS.write_text(CALLS.read_text() + "resolve\\n" if CALLS.exists() else "resolve\\n")
    if str(workspace).endswith("enrolled"): return object()
    if str(workspace).endswith("broken"): raise OSError("boom")
    raise ScopeError("no")
def enroll(workspace): raise AssertionError("a hook must never enrol")
'''


def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "project_scope.py").write_text(FAKE_SCOPE)
    return bin_dir


def run_script(name: str, tmp_path: Path, cwd: str, bin_dir: Path | None) -> str:
    import os
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_SCOPE_BIN"}
    env["NEXUS_SCOPE_BIN"] = str(bin_dir or tmp_path / "missing")
    env["CLAUDE_PROJECT_DIR"] = cwd
    return subprocess.run([sys.executable, "-I", str(PLUGIN / "hooks" / name)],
                          input=json.dumps({"cwd": cwd}), capture_output=True, text=True, check=True,
                          env=env, timeout=10).stdout


def test_session_start_prints_the_cue_only_for_an_enrolled_project(tmp_path: Path) -> None:
    bin_dir = fake_bin(tmp_path)
    out = json.loads(run_script("session_start.py", tmp_path, "/x/enrolled", bin_dir))
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"] == SESSION_CUE
    for cwd in ("/x/other", "/x/broken"):
        assert run_script("session_start.py", tmp_path, cwd, bin_dir) == ""
    assert run_script("session_start.py", tmp_path, "/x/enrolled", None) == ""  # resolver absent: unknown, silent
    assert run_script("session_start.py", tmp_path, "relative", bin_dir) == ""


def test_status_reports_three_states_and_never_enrols(tmp_path: Path) -> None:
    bin_dir = fake_bin(tmp_path)
    assert "ENROLLED" in run_script("status.py", tmp_path, "/x/enrolled", bin_dir) and "NOT" not in run_script("status.py", tmp_path, "/x/enrolled", bin_dir)
    assert "NOT ENROLLED" in run_script("status.py", tmp_path, "/x/other", bin_dir)
    assert "UNKNOWN" in run_script("status.py", tmp_path, "/x/broken", bin_dir)
    assert "UNKNOWN" in run_script("status.py", tmp_path, "/x/enrolled", None)
    for script in (PLUGIN / "hooks").glob("*.py"):
        assert "enroll(" not in script.read_text().replace("never enrol", "")


def test_skill_and_command_carry_the_policy_and_cannot_enrol() -> None:
    skill = (PLUGIN / "skills" / "nexus-memory" / "SKILL.md").read_text()
    for needle in ("IDs only", "Fetch only admitted IDs", "Nexus explore:", "untrusted", "Save only when the user asks"):
        assert needle in skill
    command = (PLUGIN / "commands" / "status.md").read_text()
    assert "allowed-tools: Bash(python3 -I:*)" in command
    assert "mcp__" not in command and "launcher" not in command
