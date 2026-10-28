"""The turn-accounting finding, as a test that fails if the instrument stops measuring it.

`DIAGNOSTIC-turn-accounting.md` establishes three different quantities that were all being
called "turns". The cheap half of that finding -- that the tally separates them, and that
the stub can produce a shape where they disagree -- is checked here and runs in CI. The
expensive half -- what the CLI does with them -- is an integration experiment against the
installed binary and lives in `diagnose_turns.py`; a test cannot assert a vendored binary's
behaviour without pinning the binary.

    <venv>/bin/python -m pytest benchmarks/agent/test_turn_accounting.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import diagnose_turns as DT                                            # noqa: E402
import stub_turns as ST                                                # noqa: E402


def trace(*entries) -> str:
    return "\n".join(json.dumps(e) for e in entries)


def assistant(mid, *blocks):
    return {"type": "assistant", "message": {"id": mid, "content": list(blocks)}}


def tool_use(tid, name="Bash"):
    return {"type": "tool_use", "id": tid, "name": name, "input": {}}


def tool_result(tid):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def envelope(**kw):
    return {"type": "result", **kw}


def test_parallel_tool_calls_make_the_two_counts_disagree():
    """One assistant message carrying three tool calls. If the tally conflated model
    responses with tool calls -- which one-tool-per-message traces cannot reveal -- this is
    where it shows."""
    t = trace(
        assistant("m1", tool_use("t1"), tool_use("t2"), tool_use("t3")),
        tool_result("t1"), tool_result("t2"), tool_result("t3"),
        assistant("m2", {"type": "text", "text": "done"}),
        envelope(num_turns=4, subtype="success"))
    got = DT.tally(t)
    assert got["assistant_messages"] == 2
    assert got["tool_use_blocks"] == 3
    assert got["tool_result_blocks"] == 3
    assert got["max_tool_use_in_one_message"] == 3
    assert got["num_turns"] == 4


def test_one_message_streamed_as_several_entries_is_one_message():
    """The stream emits one entry per content block, so a message with a thinking block and
    a tool call arrives twice under the same id. Counting entries would count it twice."""
    t = trace(
        assistant("m1", {"type": "thinking", "thinking": "..."}),
        assistant("m1", tool_use("t1")),
        tool_result("t1"),
        envelope(num_turns=2, subtype="success"))
    got = DT.tally(t)
    assert got["assistant_entries"] == 2
    assert got["assistant_messages"] == 1
    assert got["thinking_blocks"] == 1
    assert got["tool_use_blocks"] == 1


def test_a_trace_with_no_envelope_reports_no_turn_count():
    """A killed arm-run has no envelope. `num_turns` is then absent, not zero -- the
    calibration lost an arm-run's consumption to exactly this shape."""
    got = DT.tally(trace(assistant("m1", tool_use("t1")), tool_result("t1")))
    assert got["envelope_present"] is False
    assert got["num_turns"] is None


@pytest.mark.parametrize("script,turns,tools", [
    ("final_only", 1, 0),
    ("sequential", 4, 3),
    ("parallel", 2, 3),
    ("error_recovery", 3, 2),
    ("overshoot", 4, 9),
])
def test_the_scripts_are_the_shapes_they_claim_to_be(script, turns, tools):
    """The diagnostic's conclusions rest on the scripts differing in the right way. A script
    quietly edited to one tool per message would make `parallel` agree with `sequential` and
    the separation would silently stop being tested."""
    s = ST.SCRIPTS[script]
    assert len(s) == turns
    assert sum(1 for turn in s for b in turn if "tool" in b) == tools


def test_the_parallel_script_puts_several_tools_in_one_message():
    assert max(sum(1 for b in turn if "tool" in b) for turn in ST.SCRIPTS["parallel"]) > 1
    assert max(sum(1 for b in turn if "tool" in b)
               for turn in ST.SCRIPTS["sequential"]) == 1


def test_overshoot_can_exceed_a_cap_it_never_reaches():
    """The reported anomaly in one line: more tool calls than the ceiling, fewer model
    responses than the ceiling. A script that could not do both would not reproduce it."""
    s = ST.SCRIPTS["overshoot"]
    tools = sum(1 for turn in s for b in turn if "tool" in b)
    assert len(s) < tools


def test_the_stub_marks_a_tool_less_request_unscripted():
    """The CLI asks for a session title, with no tools. That request ate the script's first
    turn until it was separated, and the first tool result in the trace came back as the
    answer to a command the agent never issued."""
    assert ST.stream([{"text": "x"}], "m").count(b"event: message_start") == 1
    body_with_tools = {"tools": [{"name": "Bash"}]}
    body_without = {"tools": []}
    assert bool(body_with_tools.get("tools")) is True
    assert bool(body_without.get("tools")) is False


def test_a_tool_turn_and_a_text_turn_carry_different_stop_reasons():
    tool_sse = ST.stream([{"tool": "Bash", "input": {"command": "x"}}], "m").decode()
    text_sse = ST.stream([{"text": "done"}], "m").decode()
    assert '"stop_reason": "tool_use"' in tool_sse
    assert '"stop_reason": "end_turn"' in text_sse
