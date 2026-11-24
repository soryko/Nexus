"""The correlation probe's own scripts, driven over stdio exactly as a host would, plus its analyzer."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "benchmarks" / "agy_correlation"
PLUGIN = ROOT / "plugin"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required to run the probe scripts")


def _analyzer():
    spec = importlib.util.spec_from_file_location("agy_correlation_analyze", ROOT / "analyze.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _env(log: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGY", "ANTIGRAVITY", "PLUGIN"))}
    env.update({"NEXUS_PROBE_LOG": str(log), **extra})
    return env


def _hook(log: Path, event: str, payload: dict, **env: str) -> str:
    result = subprocess.run(
        [NODE, str(PLUGIN / "probe_hook.mjs"), event],
        input=json.dumps(payload), capture_output=True, text=True, timeout=10, env=_env(log, **env), check=True,
    )
    return result.stdout


def _server(log: Path, messages: list[dict], **env: str) -> list[dict]:
    result = subprocess.run(
        [NODE, str(PLUGIN / "probe_server.mjs")],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, timeout=10, env=_env(log, **env), check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _pre_payload(tool_call_id: str, conversation: str = "conv-1") -> dict:
    return {
        "conversationId": conversation,
        "workspacePaths": ["/tmp/ws"],
        "lastUserInput": "PRIVATE PROMPT TEXT",
        "toolCall": {
            "id": tool_call_id,
            "name": "call_mcp_tool",
            "args": {"ServerName": '"nexus-correlation-probe_correlation-probe"', "ToolName": '"probe_echo"', "label": "PRIVATE ARG"},
        },
    }


def _session(log: Path, call_meta: list[dict | None], **server_env: str) -> list[dict]:
    messages: list[dict] = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "host", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ]
    for index, meta in enumerate(call_meta):
        params: dict = {"name": "probe_echo", "arguments": {"label": f"c{index}"}}
        if meta is not None:
            params["_meta"] = meta
        messages.append({"jsonrpc": "2.0", "id": 10 + index, "method": "tools/call", "params": params})
    return _server(log, messages, **server_env)


def test_server_speaks_enough_mcp_for_a_host(tmp_path: Path) -> None:
    replies = _session(tmp_path / "log.jsonl", [None])
    by_id = {r["id"]: r for r in replies}
    assert by_id[0]["result"]["serverInfo"]["name"] == "nexus-correlation-probe"
    assert [t["name"] for t in by_id[1]["result"]["tools"]] == ["probe_echo"]
    assert "probe ok c0" in by_id[10]["result"]["content"][0]["text"]
    assert _hook(tmp_path / "log.jsonl", "PreToolUse", _pre_payload("x")) == '{"decision":"allow"}\n'
    assert _hook(tmp_path / "log.jsonl", "PostToolUse", _pre_payload("x")) == "{}\n"


def test_per_call_key_is_found_when_meta_carries_the_hook_tool_call_id(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    for tc in ("tc-a", "tc-b"):
        _hook(log, "PreToolUse", _pre_payload(tc))
    _session(log, [{"toolCallId": "tc-a"}, {"toolCallId": "tc-b"}])
    report = _analyzer().analyze(_analyzer().load(log))
    assert report["verdict"] == "per_call_key_found"
    key = next(k for k in report["per_call_keys"] if k["covers_every_call"])
    assert (key["hook_path"], key["request_path"]) == ("hook.toolCall.id", "request._meta.toolCallId")


def test_session_key_is_found_when_the_server_environment_names_the_conversation(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    _hook(log, "PreToolUse", _pre_payload("tc-a", conversation="conv-42"))
    _session(log, [None], AGY_CONVERSATION_ID="conv-42")
    report = _analyzer().analyze(_analyzer().load(log))
    assert report["verdict"] == "session_key_found"
    assert {"hook_path": "hook.conversationId", "server_path": "server.env.AGY_CONVERSATION_ID"} in report["session_keys"]


def test_negative_control_finds_nothing_when_no_identifier_is_shared(tmp_path: Path) -> None:
    # The JSON-RPC id is the host's private counter and may coincide with anything numeric, so it
    # never counts as a per-call key on its own.
    log = tmp_path / "log.jsonl"
    _hook(log, "PreToolUse", _pre_payload("10"))
    _session(log, [{"progressToken": "p-1"}])
    report = _analyzer().analyze(_analyzer().load(log))
    assert report["verdict"] == "no_shared_identifier"


def test_log_never_holds_prompt_or_argument_text(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    _hook(log, "PreToolUse", _pre_payload("tc-a"))
    _hook(log, "PreInvocation", {"conversationId": "c", "lastUserInput": "PRIVATE PROMPT TEXT", "requestText": "PRIVATE"})
    _session(log, [{"toolCallId": "tc-a"}], AGY_API_TOKEN="PRIVATE-TOKEN")
    text = log.read_text()
    assert "PRIVATE" not in text
    assert "AGY_API_TOKEN" in text  # named, never valued
