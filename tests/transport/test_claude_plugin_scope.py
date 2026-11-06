"""The patched PR #17 launcher plus the hook engine, without Claude: scope and unenrolled behaviour.

Skipped when the PR #17 branch is not available locally. Uses a throwaway HOME and synthetic data.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import anyio
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "tools" / "probes"))
import claude_matrix as cm  # noqa: E402

NM = Path("/Users/soko/Cerebros/nexus-memory")


def available() -> bool:
    return NM.is_dir() and subprocess.run(
        ["git", "-C", str(NM), "rev-parse", "--verify", "-q", "feat/project-scope-integration"],
        capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not available(), reason="PR #17 branch not available locally")


def test_launcher_patch_gives_enrolled_ids_unenrolled_one_line_and_no_cross_project(tmp_path: Path) -> None:
    world = cm.World(tmp_path / "w", NM)
    for name in "AB":
        world.enroll(name)
        anyio.run(world.seed, name)

    async def run() -> None:
        from mcp import Client
        args = lambda cwd, sid: {"cwd": str(cwd), "session_id": sid, "prompt": "Explain quasarflux"}  # noqa: E731
        async with Client(world.params(world.repos["A"])) as a:
            hit = (await a.call_tool("nexus_recall", args(world.repos["A"], "1"))).structured_content
            ctx = hit["hookSpecificOutput"]["additionalContext"]
            assert ctx.count("memory_id=") == 3 and cm.MARK["A"] not in str(hit)
            assert "get" in {t.name for t in (await a.list_tools()).tools}
            for other in ("B", "C"):
                assert (await a.call_tool("nexus_recall", args(world.repos[other], other))).structured_content == {}
        async with Client(world.params(world.repos["B"])) as b:
            text = str((await b.call_tool("nexus_recall", args(world.repos["B"], "1"))).structured_content)
            assert cm.MARK["A"] not in text and "memory_id=" in text
        # C is not enrolled: the patched launcher serves only the no-op hook tools, one line per session.
        async with Client(world.params(world.repos["C"])) as c:
            assert {t.name for t in (await c.list_tools()).tools} == {"nexus_session_start", "nexus_recall", "nexus_pre_tool"}
            first = (await c.call_tool("nexus_recall", args(world.repos["C"], "1"))).structured_content
            assert "not enrolled" in first["systemMessage"]
            for _ in range(3):
                assert (await c.call_tool("nexus_recall", args(world.repos["C"], "1"))).structured_content == {}
    anyio.run(run)
    assert len(list(world.store.glob("*.sqlite3"))) == 2  # no store was created for the unenrolled project


def test_the_launcher_patch_still_applies_to_the_pr17_launcher(tmp_path: Path) -> None:
    cm.World(tmp_path / "w", NM)  # raises if `git apply` fails
