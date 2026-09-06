"""B2a test 15 and B2b test 15: one real MCP round trip, and no binding argument anywhere.

The guard here is what B2a §1 and B2b §2 disagree about until the distinction is made, so it
is stated as the invariant rather than as a list of spellings. No tool may accept a repository
*identifier*, a verification target or a pattern argument. B2b's ``search`` carries a
``repository`` **filter** whose whole domain is two constants: it selects nothing, binds
nothing, and a caller cannot write, enumerate or learn a ``repository_id`` through it. That is
asserted below by feeding it the process's real ``repository_id`` and requiring a rejection.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

PROJECT_ROOT = Path(__file__).parents[2]
# Names that would identify a repository, point verification somewhere, or root a path. Bare
# "repository" is not among them: see the module docstring, and the scoped check below.
FORBIDDEN = {"repository_id", "repo", "repo_id", "commit_root", "path_root", "namespace", "actor"}


def _git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    (path / "sub").mkdir()
    (path / "sub" / "file.txt").write_text("a\n")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "one")
    return path


def _server(db: Path, *extra: str) -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "nexus_memory", "--namespace", "transport-test", "--db", str(db), *extra],
        env=env,
        cwd=PROJECT_ROOT,
    )


def _property_names(schema: object) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= _property_names(value)
    return names


def test_stdio_reference_round_trip_and_no_binding_argument(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    blob = _git(repo, "hash-object", "sub/file.txt")

    async def scenario() -> None:
        async with Client(_server(tmp_path / "memory.sqlite3", "--repo", str(repo))) as client:
            listed = await client.list_tools()
            for tool in listed.tools:
                assert not (_property_names(tool.input_schema) & FORBIDDEN), tool.name
                assert tool.input_schema.get("additionalProperties") is False
                # Only search carries "repository", and only as a filter.
                if tool.name != "search":
                    assert "repository" not in _property_names(tool.input_schema), tool.name
            record_schema = next(tool for tool in listed.tools if tool.name == "record").input_schema
            assert "references" in record_schema["properties"]
            search_schema = next(tool for tool in listed.tools if tool.name == "search").input_schema
            # No pattern argument anywhere on the read path either: B2b §3 admits no glob,
            # wildcard, regex or pathspec-magic surface, so no property offers one.
            assert not (_property_names(search_schema) & {"pattern", "glob", "regex", "path_root", "commit_scope"})

            status = await client.call_tool("status")
            assert status.structured_content["verification"] == "available"
            repository_id = status.structured_content["repository_id"]
            assert repository_id

            arguments = {
                "content": "the retry loop lives here",
                "idempotency_key": "ref-1",
                "references": [{"path": "sub/file.txt"}, {"path": "sub/file.txt", "commit": "HEAD"}],
            }
            recorded = await client.call_tool("record", arguments)
            assert not recorded.is_error, recorded.content
            retried = await client.call_tool("record", arguments)
            assert retried.structured_content == recorded.structured_content

            fetched = await client.call_tool("get", {"memory_id": recorded.structured_content["memory_id"]})
            assert not fetched.is_error
            assert fetched.structured_content["references"] == [{
                "repository_id": repository_id, "commit_oid": head, "path": "sub/file.txt", "object_oid": blob,
                "entry_type": "blob", "mode": "100644",
                "checked_at": fetched.structured_content["references"][0]["checked_at"],
                "evidence": ["commit_resolved", "path_resolved", "content_unverified"],
            }]
            searched = await client.call_tool("search", {"query": "retry"})
            assert searched.structured_content["hits"][0]["references"][0]["commit_oid"] == head

            # B2b test 15: record a second referenced memory and filter for exactly one of them.
            second = await client.call_tool("record", {
                "content": "the parser lives here", "idempotency_key": "ref-2",
                "references": [{"path": "sub/other.txt"}],
            })
            assert second.is_error  # sub/other.txt is not in the commit; add it and retry
            _git(repo, "config", "user.email", "t@example.invalid")
            (repo / "sub" / "other.txt").write_text("b\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "two")
            second = await client.call_tool("record", {
                "content": "the parser lives here", "idempotency_key": "ref-2b",
                "references": [{"path": "sub/other.txt"}],
            })
            assert not second.is_error, second.content

            filtered = await client.call_tool("search", {"reference_paths": ["sub/file.txt"]})
            assert not filtered.is_error, filtered.content
            hits = filtered.structured_content["hits"]
            assert [hit["memory_id"] for hit in hits] == [recorded.structured_content["memory_id"]]
            assert "references" in hits[0]["match_reasons"]

            bound = await client.call_tool("search", {"repository": "bound"})
            assert not bound.is_error, bound.content
            assert len(bound.structured_content["hits"]) == 2

            # The filter admits two constants and no identifier: the process's own
            # repository_id is refused, so it can be neither written nor confirmed.
            for value in (repository_id, "", "Bound", "any-repo"):
                refused = await client.call_tool("search", {"repository": value})
                assert refused.is_error, value
                assert refused.content[0].text.startswith("invalid_reference:"), value
            # No pattern language, and no revision spec.
            for arguments in (
                {"reference_path_prefix": ""},
                {"reference_commits": ["HEAD"]},
                {"reference_commits": ["abc123"]},
                {"reference_paths": ["../etc/passwd"]},
            ):
                refused = await client.call_tool("search", arguments)
                assert refused.is_error, arguments
                assert refused.content[0].text.startswith("invalid_reference:"), arguments
                assert str(tmp_path) not in refused.content[0].text
            # An explicit null on each of the four is "absent", and answers as omitted.
            omitted = await client.call_tool("search", {})
            nulled = await client.call_tool("search", {
                "repository": None, "reference_paths": None,
                "reference_path_prefix": None, "reference_commits": None,
            })
            assert not nulled.is_error, nulled.content
            assert nulled.structured_content == omitted.structured_content

            # Errors are stable codes with no path and no git text.
            for path in ("../etc/passwd", "/etc/passwd", "sub"):
                refused = await client.call_tool("record", {"content": "x", "idempotency_key": path, "references": [{"path": path}]})
                assert refused.is_error
                text = refused.content[0].text
                assert text.split(":")[0] in {"invalid_reference", "unsupported_reference_type"}, text
                assert str(tmp_path) not in text and "fatal" not in text
            missing = await client.call_tool("record", {"content": "x", "idempotency_key": "missing", "references": [{"path": "nope"}]})
            assert missing.content[0].text.startswith("path_not_in_commit:")
            unknown = await client.call_tool("record", {"content": "x", "idempotency_key": "unknown", "references": [{"path": "sub/file.txt", "commit": "0" * 40}]})
            assert unknown.content[0].text.startswith("commit_not_found:")

            # Injected binding fields are rejected at the top level and inside a reference.
            for injected in (
                {"content": "x", "idempotency_key": "i1", "repository": "other"},
                {"content": "x", "idempotency_key": "i2", "references": [{"path": "sub/file.txt", "repository_id": "other"}]},
                {"content": "x", "idempotency_key": "i3", "references": [{"path": "sub/file.txt", "path_root": "/"}]},
            ):
                rejected = await client.call_tool("record", injected)
                assert rejected.is_error
                assert rejected.content[0].text.startswith("invalid_input:")
            after = await client.call_tool("status")
            assert after.structured_content["active_memories"] == 2

    anyio.run(scenario)


def test_stdio_without_repo_reports_verification_unavailable(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(_server(tmp_path / "memory.sqlite3")) as client:
            status = await client.call_tool("status")
            assert status.structured_content["verification"] == "unavailable"
            assert status.structured_content["repository_id"] is None
            plain = await client.call_tool("record", {"content": "plain", "idempotency_key": "p"})
            assert not plain.is_error
            refused = await client.call_tool("record", {"content": "x", "idempotency_key": "r", "references": [{"path": "a"}]})
            assert refused.is_error and refused.content[0].text.startswith("verification_unavailable:")

    anyio.run(scenario)


def test_launch_refuses_a_non_checkout_and_repo_id_without_repo(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    plain = tmp_path / "plain"
    plain.mkdir()
    unbound = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--namespace", "n", "--db", str(tmp_path / "db"), "--repo", str(plain)],
        capture_output=True, text=True, env=env, cwd=PROJECT_ROOT,
    )
    assert unbound.returncode == 1
    assert unbound.stderr.strip() == "startup_error: repository_unbound: the repository path is not a git checkout"

    orphaned = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--namespace", "n", "--db", str(tmp_path / "db"), "--repo-id", "x"],
        capture_output=True, text=True, env=env, cwd=PROJECT_ROOT,
    )
    assert orphaned.returncode == 2
    assert "--repo-id requires --repo" in orphaned.stderr
