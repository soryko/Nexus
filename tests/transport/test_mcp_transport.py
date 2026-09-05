from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters
from nexus_memory.domain.errors import UnsupportedRuntime
from nexus_memory.transport.mcp_server import _default_db, _prepare_new_storage, main


PROJECT_ROOT = Path(__file__).parents[2]


def _server(db: Path) -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "nexus_memory", "--namespace", "transport-test", "--db", str(db)],
        env=env,
        cwd=PROJECT_ROOT,
    )


def test_stdio_tools_enforce_contract_and_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(_server(tmp_path / "state" / "memory.sqlite3")) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {"record", "get", "revise", "forget", "status"}

            expected_properties = {
                "record": {"content", "idempotency_key", "kind", "tags", "source_uri", "snapshot"},
                "get": {"memory_id", "revision_id"},
                "revise": {
                    "memory_id", "expected_revision_id", "content", "idempotency_key",
                    "kind", "tags", "source_uri", "snapshot",
                },
                "forget": {"memory_id", "expected_revision_id", "idempotency_key"},
                "status": set(),
            }
            for name, tool in tools.items():
                assert set(tool.input_schema["properties"]) == expected_properties[name]
                assert tool.input_schema.get("additionalProperties") is False
                assert tool.output_schema["type"] == "object"

            status = await client.call_tool("status")
            assert not status.is_error
            assert status.structured_content["durable_storage"] == "sqlite-wal-full"
            assert status.structured_content["active_memories"] == 0

            record_args = {
                "content": "exact body\nwith whitespace ",
                "idempotency_key": "create-1",
                "kind": "constraint",
                "tags": ["Project/A", " project/a "],
                "source_uri": "file:///caller/asserted",
                "snapshot": "abc123",
            }
            recorded = await client.call_tool("record", record_args)
            retried = await client.call_tool("record", record_args)
            assert not recorded.is_error
            assert recorded.structured_content == retried.structured_content
            first = recorded.structured_content

            fetched = await client.call_tool("get", {"memory_id": first["memory_id"]})
            assert not fetched.is_error
            assert fetched.structured_content["content"] == record_args["content"]
            assert fetched.structured_content["tags"] == ["project/a"]

            revised = await client.call_tool(
                "revise",
                {
                    "memory_id": first["memory_id"],
                    "expected_revision_id": first["revision_id"],
                    "content": "replacement",
                    "idempotency_key": "revise-1",
                },
            )
            assert not revised.is_error
            current = revised.structured_content

            stale = await client.call_tool(
                "revise",
                {
                    "memory_id": first["memory_id"],
                    "expected_revision_id": first["revision_id"],
                    "content": "must not leak",
                    "idempotency_key": "revise-stale",
                },
            )
            assert stale.is_error
            assert "revision_conflict:" in stale.content[0].text
            assert "must not leak" not in stale.content[0].text

            forgotten = await client.call_tool(
                "forget",
                {
                    "memory_id": first["memory_id"],
                    "expected_revision_id": current["revision_id"],
                    "idempotency_key": "forget-1",
                },
            )
            assert not forgotten.is_error
            for revision_id in (None, first["revision_id"]):
                args = {"memory_id": first["memory_id"]}
                if revision_id:
                    args["revision_id"] = revision_id
                missing = await client.call_tool("get", args)
                assert missing.is_error
                assert "not_found:" in missing.content[0].text

            injected = await client.call_tool(
                "record",
                {"content": "private", "idempotency_key": "bad-scope", "namespace": "other"},
            )
            assert injected.is_error
            assert "invalid_input:" in injected.content[0].text
            assert "private" not in injected.content[0].text

            invalid = await client.call_tool(
                "record", {"content": "SENSITIVE_INVALID_BODY", "idempotency_key": "bad", "tags": "not-an-array"}
            )
            assert invalid.is_error
            assert "invalid_input:" in invalid.content[0].text
            assert "SENSITIVE_INVALID_BODY" not in invalid.content[0].text

            oversized_body = "SENSITIVE_DOMAIN_BODY" + ("x" * 65536)
            domain_invalid = await client.call_tool(
                "record", {"content": oversized_body, "idempotency_key": "domain-invalid"}
            )
            assert domain_invalid.is_error
            assert "invalid_input:" in domain_invalid.content[0].text
            assert "SENSITIVE_DOMAIN_BODY" not in domain_invalid.content[0].text

    anyio.run(scenario)


def test_cli_help_and_safe_startup_failure(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    help_result = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--help"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "--namespace" in help_result.stdout

    unusable = tmp_path / "database-directory"
    unusable.mkdir()
    failed = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--namespace", "test", "--db", str(unusable)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0
    assert failed.stderr.startswith("startup_error: storage_integrity:")
    assert "Traceback" not in failed.stderr
    assert str(unusable) not in failed.stderr


def test_startup_failure_names_the_domain_cause(tmp_path: Path, capsys, monkeypatch) -> None:
    def unsupported(_path: Path) -> None:
        raise UnsupportedRuntime("SQLite 3.51.3 or newer is required")

    monkeypatch.setattr("nexus_memory.transport.mcp_server.SQLiteRepository", unsupported)
    code = main(["--namespace", "test", "--db", str(tmp_path / "memory.sqlite3")])
    stderr = capsys.readouterr().err

    assert code == 1
    assert stderr.strip() == "startup_error: unsupported_runtime: SQLite 3.51.3 or newer is required"
    assert "Traceback" not in stderr
    assert str(tmp_path) not in stderr


def test_default_path_is_cwd_independent_and_storage_creation_can_race(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", "relative-data")
    monkeypatch.chdir(home)
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(home))
        expected = home / "Nexus Memory" / "memory.sqlite3"
    elif sys.platform == "darwin":
        expected = home / "Library" / "Application Support" / "Nexus Memory" / "memory.sqlite3"
    else:
        expected = home / ".local" / "share" / "nexus-memory" / "memory.sqlite3"
    assert _default_db() == expected

    path = tmp_path / "shared" / "nested" / "memory.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_prepare_new_storage, [path] * 8))
    assert path.is_file()
