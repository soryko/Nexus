"""Server instructions, launch-bound status fields, and the content-free usage log.

The usage log is an instrument: a pilot reads "no use" from it. So these tests check that it
records every call exactly once, that it records nothing it promised not to (bodies, queries,
tags, keys, paths, unknown argument names, identifiers a failed call was given), that it refuses
destinations that could corrupt the store, and that a failing log is visible rather than silent.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import subprocess

import anyio
import pytest
from mcp import Client, StdioServerParameters
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport import usage as usage_module
from nexus_memory.transport.mcp_server import INSTRUCTIONS, INSTRUCTIONS_DIGEST, main
from nexus_memory.transport.usage import MAX_HIT_IDS, UnsafeUsageLog, UsageLog

PROJECT_ROOT = Path(__file__).parents[2]
BODY = "sentinel-body-7f3a with secret-token-XYZ inside"
TAG = "sentinel-tag-9c"
KEY = "sentinel-key-41"
UNKNOWN_ID = "sentinel-unknown-id-55"


def _server(db: Path, *extra: str) -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "nexus_memory", "--namespace", "usage-test", "--db", str(db), *extra],
        env=env,
        cwd=PROJECT_ROOT,
    )


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("ascii").splitlines()]


def test_instructions_are_sent_and_the_flag_removes_only_them(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(_server(tmp_path / "on.sqlite3")) as client:
            assert client.instructions == INSTRUCTIONS
            on_tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            status = (await client.call_tool("status")).structured_content
            assert (status["instructions"], status["instructions_digest"]) == ("on", INSTRUCTIONS_DIGEST)
        async with Client(_server(tmp_path / "off.sqlite3", "--no-instructions")) as client:
            assert not client.instructions
            off_tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            status = (await client.call_tool("status")).structured_content
            assert (status["instructions"], status["instructions_digest"]) == ("off", None)
        assert on_tools == off_tools
        assert INSTRUCTIONS_DIGEST == hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()[:12]

    anyio.run(scenario)


def test_status_reports_scope_and_a_location_fingerprint_but_no_path(tmp_path: Path) -> None:
    db = tmp_path / "state" / "memory.sqlite3"

    async def scenario() -> dict:
        async with Client(_server(db)) as client:
            return (await client.call_tool("status")).structured_content

    status = anyio.run(scenario)
    assert status["namespace"] == "usage-test"
    assert status["actor"] == "local"
    assert status["service_version"]
    assert status["database_location_fingerprint"] == hashlib.sha256(
        os.fsencode(os.path.realpath(db))).hexdigest()[:12]
    assert (status["usage_log"], status["usage_events_written"], status["usage_write_failures"]) == ("off", None, None)
    rendered = json.dumps(status)
    assert str(tmp_path) not in rendered and os.path.realpath(tmp_path) not in rendered
    assert "memory.sqlite3" not in rendered


def test_usage_log_records_each_call_once_and_nothing_it_promised_not_to(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    log = tmp_path / "usage.jsonl"

    async def scenario() -> dict:
        async with Client(_server(db, "--usage-log", str(log))) as client:
            receipt = (await client.call_tool(
                "record", {"content": BODY, "idempotency_key": KEY, "tags": [TAG], "kind": "decision"})).structured_content
            memory_id, revision_id = receipt["memory_id"], receipt["revision_id"]
            assert not (await client.call_tool("get", {"memory_id": memory_id})).is_error
            assert not (await client.call_tool("search", {"query": "sentinel-body-7f3a"})).is_error
            stale = await client.call_tool("revise", {
                "memory_id": memory_id, "expected_revision_id": "stale-revision",
                "content": "replacement", "idempotency_key": "sentinel-key-42"})
            assert stale.is_error
            assert (await client.call_tool("get", {"memory_id": memory_id, "surprise_field": 1})).is_error
            assert (await client.call_tool("get", {"memory_id": UNKNOWN_ID})).is_error
            assert not (await client.call_tool("history", {"memory_id": memory_id})).is_error
            assert not (await client.call_tool("forget", {
                "memory_id": memory_id, "expected_revision_id": revision_id, "idempotency_key": "sentinel-key-43"})).is_error
            status = (await client.call_tool("status")).structured_content
            return {"memory_id": memory_id, "status": status}

    result = anyio.run(scenario)
    memory_id = result["memory_id"]
    events = _events(log)

    start, calls, stop = events[0], events[1:-1], events[-1]
    assert start["event"] == "start"
    assert (start["instructions"], start["namespace"], start["actor"]) == ("on", "usage-test", "local")
    assert start["database_location_fingerprint"] == result["status"]["database_location_fingerprint"]
    assert {event["instance"] for event in events} == {start["instance"]}
    # Gaps in seq are lost lines, so it must count every attempt from 1 with none missing.
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    # Every line names the treatment, so a lost start line cannot orphan an instance's calls.
    assert {(event["instructions"], event["instructions_digest"]) for event in events} == {("on", INSTRUCTIONS_DIGEST)}
    assert (stop["event"], stop["reason"]) == ("stop", "end_of_input")
    assert (stop["events_written"], stop["write_failures"], stop["reopens"]) == (len(events) - 1, 0, 0)

    assert [(event["tool"], event["outcome"]) for event in calls] == [
        ("record", "ok"), ("get", "ok"), ("search", "ok"), ("revise", "revision_conflict"),
        ("get", "invalid_input"), ("get", "not_found"), ("history", "ok"), ("forget", "ok"), ("status", "ok"),
    ]
    by_position = dict(enumerate(calls))
    for position in (0, 1, 6, 7):
        assert by_position[position]["memory_id"] == memory_id
    for position in (0, 7):  # writes carry their receipt's identifiers, so replays can be told apart
        assert by_position[position]["operation_id"] and by_position[position]["revision_id"]
    search = by_position[2]
    assert search["hits"] >= 1 and memory_id in search["hit_ids"] and search["hit_ids_truncated"] is False
    # A failed call contributes no identifiers, even ones the caller supplied.
    for position in (3, 4, 5, 8):
        assert "memory_id" not in by_position[position] and "hit_ids" not in by_position[position]
    for event in calls:
        assert isinstance(event["ms"], int) and event["ms"] >= 0
        assert isinstance(event["client"], dict) and event["client"]["name"]

    raw = log.read_bytes()
    for forbidden in (BODY, "secret-token-XYZ", TAG, KEY, "sentinel-key-42", "sentinel-body-7f3a", UNKNOWN_ID,
                      "surprise_field", "stale-revision", "replacement", str(tmp_path), os.path.realpath(tmp_path)):
        assert forbidden.encode() not in raw, forbidden
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600

    # Status is computed before its own call is logged: one start event plus eight calls.
    assert (result["status"]["usage_log"], result["status"]["usage_events_written"],
            result["status"]["usage_write_failures"]) == ("on", 9, 0)


def test_search_hit_identifiers_are_capped(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    extra = 3

    async def scenario() -> None:
        async with Client(_server(tmp_path / "memory.sqlite3", "--usage-log", str(log))) as client:
            for index in range(MAX_HIT_IDS + extra):
                await client.call_tool("record", {"content": f"shared-term entry {index}", "idempotency_key": f"cap-{index}"})
            await client.call_tool("search", {"query": "shared-term", "limit": 100})

    anyio.run(scenario)
    search = [event for event in _events(log) if event.get("tool") == "search"][0]
    assert search["hits"] == MAX_HIT_IDS + extra
    assert len(search["hit_ids"]) == MAX_HIT_IDS and search["hit_ids_truncated"] is True


def test_two_servers_can_share_one_log_without_corrupting_lines(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    log = tmp_path / "usage.jsonl"
    per_server = 25

    async def one() -> None:
        async with Client(_server(db, "--usage-log", str(log))) as client:
            for _ in range(per_server):
                await client.call_tool("status")

    async def scenario() -> None:
        async with anyio.create_task_group() as group:
            group.start_soon(one)
            group.start_soon(one)

    anyio.run(scenario)
    events = _events(log)  # every line must parse
    assert sum(event["event"] == "start" for event in events) == 2
    assert sum(event["event"] == "call" for event in events) == 2 * per_server
    assert len({event["instance"] for event in events}) == 2


def test_a_replayed_write_is_distinguishable_from_a_new_one(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"

    async def scenario() -> None:
        async with Client(_server(tmp_path / "memory.sqlite3", "--usage-log", str(log))) as client:
            for key in ("replay-1", "replay-1", "replay-2"):
                assert not (await client.call_tool("record", {"content": f"body for {key}", "idempotency_key": key})).is_error

    anyio.run(scenario)
    records = [event for event in _events(log) if event.get("tool") == "record"]
    assert len(records) == 3
    assert records[0]["operation_id"] == records[1]["operation_id"] != records[2]["operation_id"]


@pytest.mark.parametrize("change", ["deleted", "renamed"])
def test_a_log_moved_while_the_server_runs_is_recreated_and_marked(tmp_path: Path, change: str) -> None:
    log = tmp_path / "usage.jsonl"

    async def scenario() -> dict:
        async with Client(_server(tmp_path / "memory.sqlite3", "--usage-log", str(log))) as client:
            await client.call_tool("status")
            if change == "deleted":
                log.unlink()
            else:
                log.rename(tmp_path / "rotated.jsonl")
            await client.call_tool("status")
            return (await client.call_tool("status")).structured_content

    status = anyio.run(scenario)
    events = _events(log)
    assert events[0]["event"] == "reopened" and events[0]["reopens"] == 1
    assert [event.get("tool") for event in events if event["event"] == "call"] == ["status", "status"]
    assert events[-1]["event"] == "stop" and events[-1]["reopens"] == 1
    assert (status["usage_log"], status["usage_write_failures"]) == ("on", 0)
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


@pytest.mark.parametrize("destination", ["database", "wal", "case_alias", "hard_link", "aliased_directory", "directory", "symlink"])
def test_unsafe_usage_log_destinations_are_refused_at_startup(tmp_path: Path, capsys, destination: str) -> None:
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    if destination == "database":
        target = db
    elif destination == "wal":
        target = Path(str(db) + "-wal")
    elif destination == "case_alias":
        # On a case-insensitive filesystem this is the rollback journal SQLite may create later. It
        # does not exist yet. Two layers refuse it: the folded-spelling check before opening, and the
        # identity check after O_CREAT, which then removes the file it just made. On APFS the second
        # layer alone suffices (a mutation removing the first stays green); both are kept.
        target = tmp_path / "MEMORY.sqlite3-JOURNAL"
        assert not Path(str(db) + "-journal").exists()
    elif destination == "hard_link":
        target = tmp_path / "linked.jsonl"
        os.link(db, target)
    elif destination == "aliased_directory":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        target = alias / "memory.sqlite3"
    elif destination == "directory":
        target = tmp_path / "a-directory"
        target.mkdir()
    else:
        real = tmp_path / "real.jsonl"
        real.touch()
        target = tmp_path / "link.jsonl"
        target.symlink_to(real)
    before = db.read_bytes()

    code = main(["--namespace", "unsafe-test", "--db", str(db), "--usage-log", str(target)])

    error = capsys.readouterr().err
    assert code == 1
    assert error.startswith("startup_error: invalid_configuration: usage log path ")
    assert str(tmp_path) not in error and os.path.realpath(tmp_path) not in error
    assert db.read_bytes() == before
    if destination == "case_alias":
        assert not target.exists()


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGTERM") or sys.platform == "win32", reason="POSIX signals")
def test_sigterm_still_writes_the_stop_event(tmp_path: Path) -> None:
    import signal
    import time

    log = tmp_path / "usage.jsonl"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "nexus_memory", "--namespace", "term-test", "--db", str(tmp_path / "memory.sqlite3"),
         "--usage-log", str(log)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        deadline = time.monotonic() + 30
        while not (log.exists() and log.read_text("ascii").strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)  # let the server reach its read loop after writing the start event
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    events = _events(log)
    assert [event["event"] for event in events] == ["start", "stop"]
    assert events[-1]["reason"] == "sigterm"


def test_a_fifo_destination_is_refused_without_hanging(tmp_path: Path) -> None:
    fifo = tmp_path / "usage.fifo"
    os.mkfifo(fifo)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--namespace", "fifo-test", "--db", str(tmp_path / "memory.sqlite3"),
         "--usage-log", str(fifo)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=30,
    )
    assert completed.returncode == 1
    assert completed.stderr.startswith("startup_error: invalid_configuration: usage log path is not a regular file")


def test_an_uninspectable_destination_gets_a_startup_error_not_a_traceback(tmp_path: Path, capsys) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        code = main(["--namespace", "locked-test", "--db", str(tmp_path / "memory.sqlite3"),
                     "--usage-log", str(locked / "deeper" / "usage.jsonl")])
    finally:
        locked.chmod(0o700)
    error = capsys.readouterr().err
    assert code == 1
    assert error.startswith("startup_error: invalid_configuration: usage log path cannot be ")
    assert "Traceback" not in error and str(tmp_path) not in error


def test_missing_log_directories_are_created_private_and_an_existing_log_is_tightened(tmp_path: Path) -> None:
    fresh = tmp_path / "new" / "nested" / "usage.jsonl"
    UsageLog(fresh, tmp_path / "memory.sqlite3").close()
    assert stat.S_IMODE(os.stat(fresh.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(fresh).st_mode) == 0o600
    loose = tmp_path / "loose.jsonl"
    loose.touch()
    loose.chmod(0o644)
    UsageLog(loose, tmp_path / "memory.sqlite3").close()
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o600


def test_a_short_write_is_finished_so_the_next_line_survives(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "usage.jsonl"
    usage = UsageLog(log_path, tmp_path / "memory.sqlite3")
    real_write = os.write
    calls = {"n": 0}

    def short_once(descriptor, data):
        calls["n"] += 1
        return real_write(descriptor, bytes(data[:7]) if calls["n"] == 1 else data)

    monkeypatch.setattr(usage_module.os, "write", short_once)
    usage.write({"event": "call", "tool": "status", "outcome": "ok"})
    usage.write({"event": "call", "tool": "get", "outcome": "ok"})
    usage.close()
    assert [event["tool"] for event in _events(log_path)] == ["status", "get"]
    assert (usage.events_written, usage.write_failures) == (2, 0)


def test_unsafe_destinations_raise_the_dedicated_error(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    with pytest.raises(UnsafeUsageLog):
        UsageLog(db, db)


def test_a_failing_log_counts_failures_and_never_raises(tmp_path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    folder = tmp_path / "logs"
    folder.mkdir()
    usage = UsageLog(folder / "usage.jsonl", tmp_path / "memory.sqlite3")
    usage.write({"event": "call", "tool": "status", "outcome": "ok"})
    # The destination goes bad after startup: the file is gone and cannot be re-created.
    (folder / "usage.jsonl").unlink()
    folder.chmod(0o500)
    try:
        usage.write({"event": "call", "tool": "status", "outcome": "ok"})
    finally:
        folder.chmod(0o700)
    assert (usage.attempts, usage.events_written, usage.write_failures) == (2, 1, 1)
    # Once the destination can be re-created the log resumes, and the lost attempt shows as a gap.
    usage.write({"event": "call", "tool": "get", "outcome": "ok"})
    usage.close()
    assert [(event["event"], event["seq"]) for event in _events(folder / "usage.jsonl")] == [("reopened", 3), ("call", 4)]
