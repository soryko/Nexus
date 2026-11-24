"""Autonomous hook launches and model-created spaces, over stdio."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from nexus_memory.domain.errors import InvalidInput
from nexus_memory.domain.models import MemoryInput, space_of, space_tag, spaces_of, with_space
from nexus_memory.transport.codex import AUTONOMOUS_CUE, SESSION_CUE
from test_codex_integration import PROJECT, repository


def server(db: Path, project: Path, *policy: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[
        "-m", "nexus_memory", "--namespace", "hook-test", "--actor", "local",
        "--db", str(db), "--hook-project-root", str(project), *policy,
    ], cwd=project, env={**os.environ, "PYTHONPATH": str(PROJECT / "src")})


def test_space_rules_in_the_domain() -> None:
    assert space_tag(" Auth ") == "space/auth"
    assert with_space(("x", "space/old"), "new") == ("x", "space/new")
    assert with_space(("x",), None) == ("x",)
    assert MemoryInput("c", tags=("space/auth", "a")).tags == ("a", "space/auth")
    for bad in ("", "a/b", "Ünïcode", "-lead", "x" * 59):
        with pytest.raises(InvalidInput):
            space_tag(bad)
    # Tags valid before spaces existed stay valid; only well-formed space tags are spaces.
    assert MemoryInput("c", tags=("space/a", "space/b")).tags == ("space/a", "space/b")
    assert MemoryInput("c", tags=("space/a/b",)).tags == ("space/a/b",)
    assert spaces_of(("space/a/b", "x", "space/b", "space/a")) == ("b", "a")
    assert space_of(("space/a/b",)) is None
    assert with_space(("space/a/b", "space/old", "x"), "new") == ("space/a/b", "x", "space/new")


def test_tags_written_before_spaces_still_retry_and_revise(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    legacy = ["space/auth/api", "space/auth", "space/release"]

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo, "--capture-policy", "autonomous")) as client:
            arguments = {"content": "Keys rotate", "idempotency_key": "k1", "tags": legacy}
            first = await client.call_tool("record", arguments)
            assert not first.is_error
            retry = await client.call_tool("record", arguments)
            assert retry.structured_content == first.structured_content  # the original receipt, not invalid_input

            memory_id = first.structured_content["memory_id"]
            stored = (await client.call_tool("get", {"memory_id": memory_id})).structured_content
            kept = await client.call_tool("revise", {
                "memory_id": memory_id, "expected_revision_id": stored["revision_id"], "idempotency_key": "k2",
                "content": "Keys rotate daily", "kind": stored["kind"], "tags": stored["tags"]})
            assert not kept.is_error
            listed = (await client.call_tool("spaces", {})).structured_content["spaces"]
            assert sorted((s["space"], s["memories"]) for s in listed) == [("auth", 1), ("release", 1)]

            moved = await client.call_tool("revise", {
                "memory_id": memory_id, "expected_revision_id": kept.structured_content["revision_id"],
                "idempotency_key": "k3", "content": "Keys rotate daily", "kind": stored["kind"],
                "tags": stored["tags"], "space": "security"})
            assert not moved.is_error
            current = (await client.call_tool("get", {"memory_id": memory_id})).structured_content
            assert current["tags"] == ["space/auth/api", "space/security"]

    anyio.run(run)


def test_spaces_are_created_by_recording_listed_and_searchable(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo, "--capture-policy", "autonomous")) as client:
            assert (await client.call_tool("spaces", {})).structured_content == {"spaces": []}
            first = await client.call_tool("record", {"content": "Tokens rotate hourly", "idempotency_key": "k1",
                                                       "kind": "decision", "space": "Auth", "tags": ["tokens"]})
            await client.call_tool("record", {"content": "Session cookies are httpOnly", "idempotency_key": "k2",
                                              "space": "auth"})
            await client.call_tool("record", {"content": "Release tags are signed", "idempotency_key": "k3",
                                              "space": "release"})
            await client.call_tool("record", {"content": "Unfiled note about tokens", "idempotency_key": "k4"})
            memory_id = first.structured_content["memory_id"]
            record = (await client.call_tool("get", {"memory_id": memory_id})).structured_content
            assert record["tags"] == ["space/auth", "tokens"]

            listed = (await client.call_tool("spaces", {})).structured_content["spaces"]
            assert [(s["space"], s["memories"]) for s in listed] == [("release", 1), ("auth", 2)]

            hits = (await client.call_tool("search", {"query": "tokens", "space": "auth"})).structured_content["hits"]
            assert [h["memory_id"] for h in hits] == [memory_id]  # the unfiled match is excluded

            # Revise moves it; forget removes it from the listing.
            revised = await client.call_tool("revise", {
                "memory_id": memory_id, "expected_revision_id": record["revision_id"], "idempotency_key": "k5",
                "content": "Tokens rotate hourly", "kind": "decision", "tags": ["tokens", "space/auth"],
                "space": "security"})
            current = (await client.call_tool("get", {"memory_id": memory_id})).structured_content
            assert current["tags"] == ["space/security", "tokens"]
            await client.call_tool("forget", {"memory_id": memory_id, "idempotency_key": "k6",
                                              "expected_revision_id": revised.structured_content["revision_id"]})
            listed = (await client.call_tool("spaces", {})).structured_content["spaces"]
            assert sorted((s["space"], s["memories"]) for s in listed) == [("auth", 1), ("release", 1)]

            bad = await client.call_tool("record", {"content": "x", "idempotency_key": "k7", "space": "a/b"})
            assert bad.is_error and "invalid_input" in str(bad.content)

    anyio.run(run)


def test_autonomous_launch_opens_discovery_but_keeps_the_checkout_check(tmp_path: Path) -> None:
    repo, other = repository(tmp_path / "repo"), repository(tmp_path / "other")

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo, "--capture-policy", "autonomous")) as client:
            await client.call_tool("record", {"content": "quasarflux cache keys include the tenant",
                                              "idempotency_key": "k1", "space": "cache"})
            fields = {"cwd": str(repo), "session_id": "s"}
            start = (await client.call_tool("nexus_session_start", fields)).structured_content
            assert start["hookSpecificOutput"]["additionalContext"] == AUTONOMOUS_CUE

            async def denied(name, inputs=None, **updates):
                r = await client.call_tool("nexus_pre_tool", {**fields, "tool_name": name, "tool_input": inputs, **updates})
                return r.structured_content.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

            # No prompt yet, no explore prefix: search, history, spaces and any get are the model's call.
            for name in ("search", "history", "spaces", "get"):
                assert not await denied(f"mcp__nexus-memory__{name}", {"memory_id": "x"})
            assert await denied("mcp__nexus-memory__search", {"query": "x"}, cwd=str(other))
            assert await denied("mcp__nexus-memory__nexus_recall")

            recall = (await client.call_tool("nexus_recall", {**fields, "prompt": "Explain quasarflux"})).structured_content
            context = recall["hookSpecificOutput"]["additionalContext"]
            assert "space=cache" in context and "tenant" not in context and "Nexus explore" not in context
            assert recall["systemMessage"] == "Nexus: 1 memory candidate offered for this prompt."
            silent = await client.call_tool("nexus_recall", {**fields, "prompt": "zzznomatchzzz unrelated words"})
            assert (silent.structured_content or {}) == {}

    anyio.run(run)


def test_explicit_hook_launch_keeps_the_task_guard(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")

    async def run() -> None:
        async with Client(server(tmp_path / "db.sqlite3", repo)) as client:  # no policy: explicit
            fields = {"cwd": str(repo), "session_id": "s"}
            start = (await client.call_tool("nexus_session_start", fields)).structured_content
            assert start["hookSpecificOutput"]["additionalContext"] == SESSION_CUE
            for name in ("search", "spaces"):
                r = await client.call_tool("nexus_pre_tool", {**fields, "tool_name": f"mcp__nexus-memory__{name}",
                                                              "tool_input": {}})
                assert r.structured_content["hookSpecificOutput"]["permissionDecision"] == "deny"
            status = (await client.call_tool("status", {})).structured_content
            assert status["capture_policy"] == "explicit"

    anyio.run(run)
