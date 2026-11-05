"""The host-neutral hook tools and the codex_* aliases are one engine with shared state."""
from __future__ import annotations

import anyio
from mcp import Client

from tests.transport.test_codex_integration import repository, server

NEW = {"nexus_session_start", "nexus_recall", "nexus_pre_tool"}
OLD = {"codex_session_start", "codex_recall", "task_pre_tool_use"}


def test_new_names_and_aliases_are_both_registered(tmp_path) -> None:
    repo = repository(tmp_path / "repo")

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo, repo)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert (NEW | OLD) <= names

    anyio.run(run)


def test_aliases_share_session_state_and_guarantees_with_new_names(tmp_path) -> None:
    repo, other = repository(tmp_path / "repo"), repository(tmp_path / "other")

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo, repo)) as client:
            ids = []
            for i in range(5):
                r = await client.call_tool("record", {"content": f"quasarflux body-{i}",
                                                      "idempotency_key": f"s{i}"})
                ids.append(r.structured_content["memory_id"])
            fields = {"cwd": str(repo), "session_id": "s"}
            first = await client.call_tool("nexus_recall", {**fields, "prompt": "Explain quasarflux"})
            context = first.structured_content["hookSpecificOutput"]["additionalContext"]
            assert context.count("memory_id=") == 3 and "body-" not in context
            # Dedupe state is shared: the alias sees what the new name already issued.
            again = await client.call_tool("codex_recall", {**fields, "prompt": "Explain quasarflux"})
            assert "hookSpecificOutput" not in (again.structured_content or {})
            admitted = context.split("memory_id=")[1].split()[0]

            async def denied(tool, name, inputs=None, **updates):
                r = await client.call_tool(tool, {**fields, "tool_name": name, "tool_input": inputs, **updates})
                return r.structured_content.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

            get, search = "mcp__nexus_memory__get", "mcp__nexus_memory__search"
            for tool in ("nexus_pre_tool", "task_pre_tool_use"):
                assert not await denied(tool, get, {"memory_id": admitted})
                assert await denied(tool, get, {"memory_id": "another"})
                assert await denied(tool, search, {"query": "x"})
                assert await denied(tool, get, {"memory_id": admitted}, cwd=str(other))
                assert await denied(tool, "mcp__nexus_memory__nexus_recall")
                assert await denied(tool, "mcp__nexus_memory__codex_recall")
            # Explore escape hatch and per-prompt reset work through the new names.
            await client.call_tool("nexus_recall", {**fields, "prompt": "Nexus explore: history"})
            assert not await denied("nexus_pre_tool", search, {"query": "x"})
            await client.call_tool("nexus_recall", {**fields, "prompt": "zzznomatchzzz"})
            assert await denied("nexus_pre_tool", search, {"query": "x"})
            await client.call_tool("nexus_recall", {**fields, "prompt": "Nexus explore: again"})
            await client.call_tool("nexus_session_start", fields)
            assert await denied("task_pre_tool_use", search, {"query": "x"})
            # Not enrolled: a different checkout gets nothing from any name.
            for tool in ("nexus_recall", "codex_recall"):
                r = await client.call_tool(tool, {"cwd": str(other), "session_id": "o", "prompt": "Explain quasarflux"})
                assert (r.structured_content or {}) == {}

    anyio.run(run)
