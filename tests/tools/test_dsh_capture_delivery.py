"""Capture pilot §7: Nexus delivery evidence, not a DSH Save implementation.

Uses only synthetic payloads, disposable databases and real MCP stdio processes.
Does not prove Host approval, draft privacy, Host journaling or recovery policy,
flag/revocation behavior, Save/Dismiss races, or restart discovery without the
originating DSH session. The fixture identity witness is not a production
store-generation proof; if that proof is unavailable, recovery stays blocked.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
import pytest
from anyio.streams.buffered import BufferedByteReceiveStream
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from mcp.types import LATEST_PROTOCOL_VERSION

REPO = Path(__file__).resolve().parents[2]
NAMESPACE = "capture-delivery-test"
ACTOR = "delivery-test"
ARGS = {
    "content": "  Résumé: café and e\u0301\n\tKeep exact bytes. \n",
    "idempotency_key": "synthetic-approved-capture",
    "kind": "procedure",
    "tags": ["capture-delivery"],
    "source_uri": "file:///synthetic-capture",
    "snapshot": "synthetic-snapshot",
}


def _server(db: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-B", "-m", "nexus_memory", "--namespace", NAMESPACE, "--actor", ACTOR,
            "--db", str(db), "--capture-policy", "explicit", "--no-instructions",
        ],
        env={"PYTHONPATH": str(REPO / "src")},
        cwd=db.parent,
    )


async def _ok(client: Client, tool: str, args: dict | None = None) -> dict:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _durable_state(status: dict) -> tuple[int, int, int]:
    return status["active_memories"], status["revisions"], status["latest_durable_seq"]


def _assert_receipt(receipt: dict) -> None:
    assert receipt["operation"] == "record"
    for key in ("memory_id", "revision_id", "operation_id"):
        assert isinstance(receipt[key], str) and receipt[key]
    assert type(receipt["durable_seq"]) is int and receipt["durable_seq"] > 0


async def _assert_only_original(client: Client, receipt: dict) -> None:
    lookup = {"memory_id": receipt["memory_id"]}
    memory = await _ok(client, "get", lookup)
    assert memory["content"].encode("utf-8") == ARGS["content"].encode("utf-8")
    for field in ("kind", "tags", "source_uri", "snapshot"):
        assert memory[field] == ARGS[field]
    assert memory["revision_id"] == memory["current_revision_id"] == receipt["revision_id"]
    history = await _ok(client, "history", lookup)
    assert history["cursor"] is None
    (entry,) = history["entries"]
    assert entry["revision_id"] == receipt["revision_id"]
    assert entry["parent_revision_id"] is None and entry["is_current"]
    assert _durable_state(await _ok(client, "status")) == (1, 1, receipt["durable_seq"])


async def _cut_stdio(db: Path, *, send_record: bool) -> dict | None:
    """Kill the server before record send or before delivering its reply.

    The intercepted wire reply is a test oracle, not an acknowledged client
    receipt. Waiting for that exact frame locates the post-commit cut without a
    sleep or a race with the SQLite transaction. No server code is patched.
    """
    server = _server(db)
    async with await anyio.open_process(
        [server.command, *server.args],
        cwd=server.cwd,
        env=get_default_environment() | (server.env or {}),
        stderr=None,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        reader = BufferedByteReceiveStream(process.stdout)

        async def send(method: str, params: dict | None = None, request_id: int | None = None) -> None:
            message = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            if request_id is not None:
                message["id"] = request_id
            await process.stdin.send((json.dumps(message) + "\n").encode("utf-8"))

        async def response(request_id: int) -> dict:
            message = json.loads(await reader.receive_until(b"\n", 65536))
            assert message["id"] == request_id and "error" not in message, message
            return message["result"]

        try:
            await send("initialize", {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "delivery-wire-cut-test", "version": "1"},
            }, 1)
            await response(1)
            await send("notifications/initialized")
            if not send_record:
                return None
            await send("tools/call", {"name": "record", "arguments": ARGS}, 2)
            cut_reply = await response(2)
            assert not cut_reply.get("isError"), cut_reply
            return cut_reply["structuredContent"]
        finally:
            if process.returncode is None:
                process.kill()
            with anyio.CancelScope(shield=True):
                await process.wait()


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    async def bounded() -> None:
        with anyio.fail_after(20):
            await scenario()

    anyio.run(bounded)


def test_commit_before_receipt_replays_exactly_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "delivery.sqlite3"
        original = await _cut_stdio(db, send_record=True)
        assert original is not None
        _assert_receipt(original)

        async with Client(_server(db)) as client:
            # Confirm durable state before making the write-capable retry.
            await _assert_only_original(client, original)
            assert await _ok(client, "record", ARGS) == original

        async with Client(_server(db)) as client:
            canary = "SYNTHETIC_ALTERED_CAPTURE_BODY"
            conflict = await client.call_tool("record", {**ARGS, "content": ARGS["content"] + canary})
            assert conflict.is_error and conflict.structured_content is None
            assert conflict.content[0].text.startswith("idempotency_conflict:")
            assert canary not in conflict.content[0].text
            assert await _ok(client, "record", ARGS) == original
            await _assert_only_original(client, original)

    _run(scenario)


def test_no_send_leaves_no_commit_but_same_record_call_can_create_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "delivery.sqlite3"
        # Cut after the handshake but before record enters transport. The next
        # process inspects the resulting store without attempting a write.
        assert await _cut_stdio(db, send_record=False) is None
        async with Client(_server(db)) as client:
            assert _durable_state(await _ok(client, "status")) == (0, 0, 0)

        async with Client(_server(db)) as client:
            # The same call used for ambiguous recovery above is a create when
            # no first attempt committed. It is not a receipt lookup.
            created = await _ok(client, "record", ARGS)
            _assert_receipt(created)
            await _assert_only_original(client, created)

    _run(scenario)


def _assert_match_in_fixture(db: Path, pinned_original: Path) -> None:
    # Only a test oracle: the fixture owns both paths, keeps the original file
    # alive, and swaps closed stores. This cannot attest an in-place rollback
    # or prevent a check/use race in production.
    if not db.samefile(pinned_original):
        raise ValueError("fixture database identity changed")


@pytest.mark.parametrize("replacement", ["fresh", "pre-commit-backup"])
def test_same_path_fingerprint_does_not_bind_recovery(tmp_path: Path, replacement: str) -> None:
    async def scenario() -> None:
        db = tmp_path / "delivery.sqlite3"
        backup = tmp_path / "pre-commit.sqlite3"
        pinned_original = tmp_path / "original.sqlite3"
        async with Client(_server(db)) as client:
            before = await _ok(client, "status")
            assert before["namespace"] == NAMESPACE and before["actor"] == ACTOR
            assert before["database_location_fingerprint"]
            assert _durable_state(before) == (0, 0, 0)
        # Only copy the closed SQLite store, not a live file with pending WAL.
        assert not db.with_name(db.name + "-wal").exists()
        if replacement == "pre-commit-backup":
            shutil.copyfile(db, backup)
        pinned_original.hardlink_to(db)

        async with Client(_server(db)) as client:
            _assert_match_in_fixture(db, pinned_original)
            original = await _ok(client, "record", ARGS)
            _assert_receipt(original)
        assert not db.with_name(db.name + "-wal").exists()

        # Keep the old inode alive so it cannot be reused by the replacement.
        if replacement == "pre-commit-backup":
            backup.replace(db)
        else:
            db.unlink()
        async with Client(_server(db)) as client:
            replaced = await _ok(client, "status")
            binding = ("namespace", "actor", "database_location_fingerprint")
            assert tuple(replaced[k] for k in binding) == tuple(before[k] for k in binding)
            with pytest.raises(ValueError, match="fixture database identity changed"):
                _assert_match_in_fixture(db, pinned_original)
            # No record is sent to the rejected destination in this branch.
            assert _durable_state(await _ok(client, "status")) == (0, 0, 0)

        async with Client(_server(db)) as client:
            # Deliberate unsafe control, confined to this disposable store:
            # a path-only caller would get a successful but NEW write receipt.
            wrong = await _ok(client, "record", ARGS)
            _assert_receipt(wrong)
            assert wrong["memory_id"] != original["memory_id"]
            await _assert_only_original(client, wrong)

        async with Client(_server(pinned_original)) as client:
            assert await _ok(client, "record", ARGS) == original
            await _assert_only_original(client, original)

    _run(scenario)
