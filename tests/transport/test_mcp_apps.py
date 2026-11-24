"""Tests for opt-in SEP-1865 MCP Apps resources, tool metadata, and open_atlas."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from nexus_memory.activity import ActivityJournal
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_apps import ATLAS_RESOURCE_URI, CARD_RESOURCE_URI, MCP_APP_MIME
from nexus_memory.transport.mcp_server import create_server

PROJECT_ROOT = Path(__file__).parents[2]


def test_mcp_apps_disabled_by_default_preserves_plain_server(tmp_path: Path) -> None:
    store = SQLiteRepository(tmp_path / "memory.sqlite3")
    service = MemoryService(store, Scope("test-ns", "local"))
    server = create_server(service)

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {
                "record",
                "get",
                "revise",
                "forget",
                "search",
                "history",
                "spaces",
                "status",
                "compare_at_head",
            }
            for tool in listed.tools:
                assert not (tool.meta and "ui" in tool.meta)
            resources = await client.list_resources()
            assert resources.resources == []

    anyio.run(scenario)


def test_mcp_apps_metadata_resources_and_open_atlas_work_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    store = SQLiteRepository(db_path)
    scope = Scope("apps-ns", "local")
    service = MemoryService(store, scope)
    r1 = service.record(MemoryInput("Always bind single checkout", kind="constraint", tags=("scope",)), "seed-1")
    service.record(MemoryInput("Use Option C sidecar + MCP apps", kind="decision", tags=("ui",)), "seed-2")
    journal = ActivityJournal(ActivityJournal.path_for_store(db_path))

    server = create_server(
        service,
        database=lambda: db_path,
        activity_journal=lambda: journal,
        enable_mcp_apps=True,
    )

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert "open_atlas" in tools
            for name, tool in tools.items():
                assert tool.input_schema.get("additionalProperties") is False, f"{name} missing additionalProperties=False"

            for inline_name in ("status", "get", "compare_at_head"):
                assert tools[inline_name].meta["ui"]["resourceUri"] == CARD_RESOURCE_URI

            atlas_meta = tools["open_atlas"].meta
            assert atlas_meta["ui"]["resourceUri"] == ATLAS_RESOURCE_URI
            entrypoints = atlas_meta["com.google.antigravity/ui"]["entrypoints"]
            assert [ep["type"] for ep in entrypoints] == ["aux_pane", "global"]

            listed_res = await client.list_resources()
            by_uri = {str(res.uri): res for res in listed_res.resources}
            assert {CARD_RESOURCE_URI, ATLAS_RESOURCE_URI} <= set(by_uri)
            assert by_uri[CARD_RESOURCE_URI].mime_type == MCP_APP_MIME
            assert by_uri[ATLAS_RESOURCE_URI].mime_type == MCP_APP_MIME

            for uri in (CARD_RESOURCE_URI, ATLAS_RESOURCE_URI):
                read = await client.read_resource(uri)
                assert len(read.contents) == 1
                item = read.contents[0]
                assert item.mime_type == MCP_APP_MIME
                html = item.text
                assert 1000 < len(html.encode("utf-8")) < 10 * 1024 * 1024
                assert "ui/initialize" in html
                assert "<style>" in html
                # No external script/stylesheet references violate default-src 'none' CSP.
                assert not re.search(r"<(?:script|link)[^>]+https?://", html, re.IGNORECASE)

            # Empty {} argument launch (how Antigravity aux_pane/global entrypoints invoke open_atlas)
            atlas_res = await client.call_tool("open_atlas", {})
            assert atlas_res.is_error is False
            sc = atlas_res.structured_content
            assert sc["active_memories"] == 2
            assert sc["revisions"] == 2
            assert sc["capture_policy"] == "explicit"
            assert len(sc["hits"]) == 2

            # Core inline card tools still return valid structuredContent
            status_res = await client.call_tool("status", {})
            assert status_res.is_error is False
            assert status_res.structured_content["database_location_fingerprint"] is not None

            get_res = await client.call_tool("get", {"memory_id": r1.memory_id})
            assert get_res.is_error is False
            assert get_res.structured_content["content"] == "Always bind single checkout"

            # Verify open_atlas did not mutate durable_seq and ActivityMiddleware logged tool_open_atlas
            after_status = service.status()
            assert after_status.latest_durable_seq == 2
            events = journal.list_events(limit=10)
            assert any(e.kind == "tool_open_atlas" and e.outcome == "ok" for e in events)

    anyio.run(scenario)


def test_stdio_cli_flag_enables_mcp_apps(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "nexus_memory", "--namespace", "apps-stdio", "--db", str(tmp_path / "m.sqlite3"), "--mcp-apps"],
        env=env,
        cwd=PROJECT_ROOT,
    )

    async def scenario() -> None:
        async with Client(params) as client:
            listed = await client.list_tools()
            assert any(t.name == "open_atlas" for t in listed.tools)
            resources = await client.list_resources()
            assert {str(r.uri) for r in resources.resources} == {CARD_RESOURCE_URI, ATLAS_RESOURCE_URI}

    anyio.run(scenario)
