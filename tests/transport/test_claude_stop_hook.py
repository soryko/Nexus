"""The Claude plugin's Stop check: one nudge to record durable knowledge, never a loop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

STOP = Path(__file__).parents[2] / "integrations" / "claude-plugin" / "hooks" / "stop.py"

FAKE_SCOPE = '''
from pathlib import Path
class ScopeError(ValueError): pass
class Project:
    def __init__(self, checkout, database):
        self.checkout, self.database = Path(checkout), Path(database)
def resolve(workspace):
    if str(workspace).endswith("enrolled"):
        root = Path(__file__).parent.parent
        return Project(root / "repo", root / "store" / "memory.sqlite3")
    raise ScopeError("no")
def enroll(workspace): raise AssertionError("a hook must never enrol")
'''


@pytest.fixture
def world(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "project_scope.py").write_text(FAKE_SCOPE)
    (tmp_path / "store").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    git = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=git)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True, env=git)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True, env=git)
    return tmp_path


def prompt(text: str, uuid: str = "p1") -> dict:
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}


def tool(name: str, uuid: str = "a1") -> dict:
    return {"type": "assistant", "uuid": uuid,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": uuid, "name": name, "input": {}}]}}


def result(uuid: str = "r1") -> dict:
    return {"type": "user", "uuid": uuid,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": "ok"}]}}


def said(text: str, uuid: str = "s1") -> dict:
    return {"type": "assistant", "uuid": uuid, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def stop(world: Path, entries: list[dict], *, session: str = "s", cwd: str = "/x/enrolled",
         active: bool = False, **env: str) -> dict:
    transcript = world / f"{session}.jsonl"
    transcript.write_text("".join(json.dumps(e) + "\n" for e in entries))
    environment = {k: v for k, v in os.environ.items() if k not in ("NEXUS_SCOPE_BIN", "NEXUS_CAPTURE_POLICY")}
    environment.update({"NEXUS_SCOPE_BIN": str(world / "bin"), **env})
    event = {"session_id": session, "transcript_path": str(transcript), "cwd": cwd,
             "hook_event_name": "Stop", "stop_hook_active": active}
    out = subprocess.run([sys.executable, "-I", str(STOP)], input=json.dumps(event), capture_output=True,
                         text=True, check=True, env=environment, timeout=20).stdout
    return json.loads(out) if out.strip() else {}


EDIT_TURN = [prompt("Fix the cache key"), tool("Edit"), result(), said("Done.")]


def test_an_editing_turn_is_nudged_once_with_head_evidence(world: Path) -> None:
    out = stop(world, EDIT_TURN)
    assert out["decision"] == "block"
    assert "NO_MEMORY" in out["reason"] and "HEAD=" in out["reason"] and "space" in out["reason"]
    assert stop(world, EDIT_TURN) == {}  # the same prompt is never nudged twice
    state = json.loads((world / "store" / "memory.claude_stop_nudge.json").read_text())
    assert state == {"s": {"turns": ["p1"]}}
    assert oct((world / "store" / "memory.claude_stop_nudge.json").stat().st_mode & 0o777) == "0o600"


def test_a_hook_driven_continuation_is_never_nudged(world: Path) -> None:
    assert stop(world, EDIT_TURN, active=True) == {}
    assert stop(world, EDIT_TURN)["decision"] == "block"  # control: the same turn without the flag


@pytest.mark.parametrize("name", ["mcp__nexus-memory__record", "mcp__nexus-memory__revise",
                                  "mcp__plugin_nexus-memory_nexus-memory__record"])
def test_a_turn_that_already_wrote_to_nexus_is_not_nudged(world: Path, name: str) -> None:
    assert stop(world, [*EDIT_TURN, tool(name, "a2")]) == {}


def test_a_no_memory_reply_ends_the_check(world: Path) -> None:
    assert stop(world, [*EDIT_TURN, said("NO_MEMORY", "s2")]) == {}


def test_explicit_policy_and_unenrolled_projects_are_silent(world: Path) -> None:
    assert stop(world, EDIT_TURN, NEXUS_CAPTURE_POLICY="explicit") == {}
    assert stop(world, EDIT_TURN, cwd="/x/other") == {}
    assert stop(world, EDIT_TURN, cwd="relative") == {}


def test_a_stated_decision_is_nudged_without_any_tool(world: Path) -> None:
    out = stop(world, [prompt("From now on we use UTC for every cron job"), said("Understood.")])
    assert out["decision"] == "block"


def test_a_plain_question_is_not_nudged(world: Path) -> None:
    assert stop(world, [prompt("What does this function return?"), said("A list.")]) == {}


def test_a_command_counts_only_when_the_checkout_has_changes(world: Path) -> None:
    turn = [prompt("Run the tests"), tool("Bash"), result(), said("All green.")]
    assert stop(world, turn) == {}
    (world / "repo" / "a.txt").write_text("changed\n")
    assert stop(world, turn)["decision"] == "block"


def test_a_turn_ending_on_a_question_to_the_user_is_not_nudged(world: Path) -> None:
    assert stop(world, [prompt("Plan the migration"), tool("AskUserQuestion"), said("Which one?")]) == {}


def test_the_latest_prompt_is_the_turn_not_a_tool_result(world: Path) -> None:
    earlier = [prompt("Fix it", "p0"), tool("Edit", "a0"), result("r0")]
    assert stop(world, [*earlier, prompt("Thanks, what is HEAD?", "p1"), said("abc")]) == {}


def test_harness_injected_user_entries_are_not_prompts(world: Path) -> None:
    meta = {**prompt("Caveat: local command output", "m1"), "isMeta": True}
    summary = {**prompt("This session is being continued from a previous conversation", "c1"), "isCompactSummary": True}
    # The real prompt stays the turn, so its Edit still counts and it is nudged once.
    assert stop(world, [*EDIT_TURN, meta, summary])["decision"] == "block"
    assert stop(world, [*EDIT_TURN, meta, summary]) == {}


def test_at_most_three_nudges_per_session(world: Path) -> None:
    nudged = [stop(world, [prompt(f"Fix bug {n}", f"p{n}"), tool("Edit")]) for n in range(5)]
    assert [bool(out) for out in nudged] == [True, True, True, False, False]
    assert stop(world, [prompt("Fix bug 9", "p9"), tool("Edit")], session="other")["decision"] == "block"


def test_malformed_input_never_breaks_a_stop(world: Path) -> None:
    for payload in ("", "not json", "[]", json.dumps({"session_id": "s"}),
                    json.dumps({"session_id": "s", "transcript_path": str(world / "missing.jsonl"), "cwd": "/x/enrolled"})):
        run = subprocess.run([sys.executable, "-I", str(STOP)], input=payload, capture_output=True, text=True,
                             env={**os.environ, "NEXUS_SCOPE_BIN": str(world / "bin")}, timeout=20)
        assert run.returncode == 0 and run.stdout == ""
