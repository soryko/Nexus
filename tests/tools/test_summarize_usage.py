"""The usage-log reporter is the only thing an automatic pilot reads, so it is checked against
answers fixed in advance: a positive control (capture in one session, fetch in another), the
matching negative controls (same session; fetched before captured; a replayed write), the time
window, and the health signals that say whether a zero can be trusted. The last test runs the
real server twice and the reporter over what it wrote.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "tools" / "summarize_usage.py"


def _load():
    spec = importlib.util.spec_from_file_location("summarize_usage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["summarize_usage"] = module
    spec.loader.exec_module(module)
    return module


usage = _load()
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.seq: dict[str, int] = {}

    def event(self, instance: str, minutes: float, kind: str, **fields) -> None:
        self.seq[instance] = self.seq.get(instance, 0) + 1
        self.lines.append(json.dumps({
            "v": 1, "ts": (T0 + timedelta(minutes=minutes)).isoformat(), "instance": instance,
            "seq": self.seq[instance], "instructions": "on", "instructions_digest": "d1", "event": kind, **fields}))

    def call(self, instance: str, minutes: float, tool: str, outcome: str = "ok", **ids) -> None:
        self.event(instance, minutes, "call", tool=tool, outcome=outcome, ms=1,
                   client={"name": "claude-code", "version": "2.1"}, **ids)

    def session(self, instance: str, start: float, stop: float | None, calls=()) -> None:
        self.event(instance, start, "start")
        for args in calls:  # (minutes, tool, ids) or (minutes, tool, outcome, ids)
            minutes, tool, *rest = args
            outcome, ids = (rest[0], rest[1]) if len(rest) == 2 else ("ok", rest[0])
            self.call(instance, minutes, tool, outcome, **ids)
        if stop is not None:
            self.event(instance, stop, "stop", events_written=0, write_failures=0, reopens=0)

    def write(self, path: Path) -> Path:
        path.write_text("\n".join(self.lines) + "\n")
        return path


def _report(tmp_path: Path, log: Log, since: datetime = T0, until: datetime | None = None) -> dict:
    return usage.summarize(log.write(tmp_path / "usage.jsonl"), since, until)


def test_capture_in_one_session_fetched_in_another_is_one_reuse_candidate(tmp_path: Path) -> None:
    log = Log()
    log.session("A", 1, 5, [(2, "record", {"memory_id": "M", "revision_id": "R1", "operation_id": "op1"})])
    log.session("B", 10, 15, [(11, "search", {"hits": 1, "hit_ids": ["M"], "hit_ids_truncated": False}),
                              (12, "get", {"memory_id": "M"})])
    report = _report(tmp_path, log)
    assert (report["instances"], report["instances_consulted"], report["instances_captured"], report["records"]) == (2, 1, 1, 1)
    assert [(c["memory_id"], c["captured_by"], c["reused_by"], c["via"]) for c in report["reuse_candidates"]] == [
        ("M", "A", "B", "search_hit")]  # one candidate per memory and session, the first way it was reached


def test_the_negative_controls_produce_no_reuse(tmp_path: Path) -> None:
    log = Log()
    # Same session: captured and fetched by A.
    log.session("A", 1, 5, [(2, "record", {"memory_id": "M", "operation_id": "op1"}), (3, "get", {"memory_id": "M"})])
    # Fetched by B before C captured it (an id can only be reused after it exists).
    log.session("B", 6, 8, [(7, "get", {"memory_id": "N"})])
    log.session("C", 9, 12, [(10, "record", {"memory_id": "N", "operation_id": "op2"})])
    # A failed fetch is not reuse, and a replayed record (same operation_id) is one record.
    log.session("D", 13, 16, [(14, "get", "not_found", {}), (15, "record", {"memory_id": "P", "operation_id": "op3"}),
                              (15.5, "record", {"memory_id": "P", "operation_id": "op3"})])
    report = _report(tmp_path, log)
    assert report["reuse_candidates"] == []
    assert report["records"] == 3
    # Fetching a memory before it exists is impossible, so it is surfaced as a fault, not dropped.
    assert report["health"]["retrievals_before_capture"] == 1


def test_a_retrieval_in_the_same_instant_as_the_capture_is_still_reuse(tmp_path: Path) -> None:
    log = Log()
    log.event("A", 1, "start")
    log.event("B", 1, "start")
    log.call("A", 2, "record", memory_id="M", operation_id="op1")
    log.call("B", 2, "get", memory_id="M")  # same timestamp, different server
    report = _report(tmp_path, log)
    assert [(c["captured_by"], c["reused_by"]) for c in report["reuse_candidates"]] == [("A", "B")]
    assert report["health"]["retrievals_before_capture"] == 0


def test_a_replay_of_a_write_made_before_the_window_is_not_a_pilot_capture(tmp_path: Path) -> None:
    log = Log()
    log.session("before", -10, -5, [(-8, "record", {"memory_id": "M", "operation_id": "op1"})])
    log.session("replayer", 1, 4, [(2, "record", {"memory_id": "M", "operation_id": "op1"})])
    log.session("reader", 5, 8, [(6, "get", {"memory_id": "M"})])
    report = _report(tmp_path, log)
    assert (report["records"], report["instances_captured"], report["reuse_candidates"]) == (0, 0, [])


def test_calls_after_the_window_end_are_excluded_even_in_a_session_opened_inside_it(tmp_path: Path) -> None:
    log = Log()
    log.session("A", 1, 5, [(2, "record", {"memory_id": "M", "operation_id": "op1"})])
    log.session("B", 10, 90, [(20, "status", {}), (70, "get", {"memory_id": "M"})])
    report = _report(tmp_path, log, until=T0 + timedelta(minutes=60))
    assert report["instances"] == 2
    assert report["reuse_candidates"] == [] and report["instances_consulted"] == 0
    assert report["calls_by_tool_outcome"] == {"record:ok": 1, "status:ok": 1}


def test_a_replay_by_an_earlier_started_session_is_neither_a_capture_nor_an_origin(tmp_path: Path) -> None:
    log = Log()
    log.event("A", 1, "start")                      # A starts first...
    log.session("B", 2, 6, [(3, "record", {"memory_id": "M", "operation_id": "op1"})])  # ...B captures M
    log.call("A", 4, "record", memory_id="M", operation_id="op1")   # A replays B's operation
    log.call("A", 5, "get", memory_id="M")                           # and then fetches M
    log.event("A", 7, "stop")
    report = _report(tmp_path, log)
    assert (report["instances_captured"], report["records"]) == (1, 1)
    assert [(c["memory_id"], c["captured_by"], c["reused_by"], c["via"]) for c in report["reuse_candidates"]] == [
        ("M", "B", "A", "get")]


def test_blank_lines_are_not_counted_as_malformed(tmp_path: Path) -> None:
    log = Log()
    log.session("A", 1, 2)
    log.lines.insert(1, "")
    assert _report(tmp_path, log)["health"]["malformed_lines"] == 0


def test_the_window_excludes_sessions_opened_before_the_clock(tmp_path: Path) -> None:
    log = Log()
    log.session("early", -30, -20, [(-25, "record", {"memory_id": "M", "operation_id": "op1"})])
    log.session("late", 10, 15, [(11, "get", {"memory_id": "M"})])
    report = _report(tmp_path, log)
    assert report["instances"] == 1
    assert report["reuse_candidates"] == []  # the capture happened before the pilot, so it is not counted
    assert _report(tmp_path, log, since=T0 - timedelta(hours=1), until=T0)["instances"] == 1


def test_health_reports_what_would_make_a_zero_untrustworthy(tmp_path: Path) -> None:
    log = Log()
    log.session("ok", 1, 5, [(2, "status", {})])
    log.session("killed", 6, None, [(7, "status", {})])
    log.seq["gappy"] = 0
    log.event("gappy", 8, "start")
    log.seq["gappy"] = 3  # two lines lost
    log.call("gappy", 9, "status")
    log.lines.append("{not json")
    log.seq["orphan"] = 1
    log.call("orphan", 10, "status")
    log.lines.append(json.dumps({"v": 1, "ts": (T0 + timedelta(minutes=11)).isoformat(), "instance": "mixed", "seq": 1,
                                 "instructions": "off", "instructions_digest": None, "event": "start"}))
    health = _report(tmp_path, log)["health"]
    assert health["malformed_lines"] == 1
    assert health["seq_gaps"] == 3  # two lost in "gappy", one before the orphan's first surviving line
    assert health["instances_without_start"] == 1
    assert health["instances_without_stop"] == 4
    assert health["mixed_treatments"] is True


def test_the_cli_refuses_a_naive_time_and_a_missing_log(tmp_path: Path, capsys) -> None:
    assert usage.main(["--log", str(tmp_path / "missing.jsonl"), "--since", T0.isoformat()]) == 2
    assert usage.main(["--log", str(Log().write(tmp_path / "u.jsonl")), "--since", "2026-11-01T12:00:00"]) == 2


def test_the_real_server_and_reporter_find_a_cross_session_reuse(tmp_path: Path) -> None:
    db, log = tmp_path / "memory.sqlite3", tmp_path / "usage.jsonl"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    server = StdioServerParameters(command=sys.executable, env=env, cwd=REPO,
                                   args=["-m", "nexus_memory", "--namespace", "reuse", "--db", str(db), "--usage-log", str(log)])
    since = datetime.now(UTC) - timedelta(seconds=1)

    async def scenario() -> str:
        async with Client(server) as first:
            receipt = await first.call_tool("record", {"content": "the reuse probe fact", "idempotency_key": "probe-1"})
            memory_id = receipt.structured_content["memory_id"]
            assert not (await first.call_tool("get", {"memory_id": memory_id})).is_error  # same session: not reuse
        async with Client(server) as second:
            assert not (await second.call_tool("search", {"query": "reuse probe"})).is_error
        return memory_id

    memory_id = anyio.run(scenario)
    report = usage.summarize(log, since)
    assert report["instances"] == 2 and report["records"] == 1
    assert [(c["memory_id"], c["via"]) for c in report["reuse_candidates"]] == [(memory_id, "search_hit")]
    health = report["health"]
    assert (health["malformed_lines"], health["seq_gaps"], health["instances_without_start"],
            health["instances_without_stop"], health["mixed_treatments"]) == (0, 0, 0, 0, False)
