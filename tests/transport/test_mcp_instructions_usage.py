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

import anyio
import pytest
from mcp import Client, StdioServerParameters
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_server import INSTRUCTIONS, main
from nexus_memory.transport.usage import MAX_HIT_IDS, UsageLog

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
            assert (await client.call_tool("status")).structured_content["instructions"] == "on"
        async with Client(_server(tmp_path / "off.sqlite3", "--no-instructions")) as client:
            assert not client.instructions
            off_tools = sorted(tool.name for tool in (await client.list_tools()).tools)
            assert (await client.call_tool("status")).structured_content["instructions"] == "off"
        assert on_tools == off_tools

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

    start, calls = events[0], events[1:]
    assert start["event"] == "start"
    assert (start["instructions"], start["namespace"], start["actor"]) == ("on", "usage-test", "local")
    assert start["database_location_fingerprint"] == result["status"]["database_location_fingerprint"]
    assert {event["instance"] for event in events} == {start["instance"]}

    assert [(event["tool"], event["outcome"]) for event in calls] == [
        ("record", "ok"), ("get", "ok"), ("search", "ok"), ("revise", "revision_conflict"),
        ("get", "invalid_input"), ("get", "not_found"), ("history", "ok"), ("forget", "ok"), ("status", "ok"),
    ]
    by_position = dict(enumerate(calls))
    for position in (0, 1, 6, 7):
        assert by_position[position]["memory_id"] == memory_id
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


@pytest.mark.parametrize("destination", ["database", "wal", "hard_link", "aliased_directory", "directory", "symlink"])
def test_unsafe_usage_log_destinations_are_refused_at_startup(tmp_path: Path, capsys, destination: str) -> None:
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    if destination == "database":
        target = db
    elif destination == "wal":
        target = Path(str(db) + "-wal")
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


def test_a_failing_log_counts_failures_and_never_raises(tmp_path: Path) -> None:
    usage = UsageLog(tmp_path / "usage.jsonl", tmp_path / "memory.sqlite3")
    usage.write({"event": "call", "tool": "status", "outcome": "ok"})
    os.close(usage._descriptor)  # the destination goes bad after startup
    usage.write({"event": "call", "tool": "status", "outcome": "ok"})
    assert (usage.events_written, usage.write_failures) == (1, 1)
