"""--hook-project-root is the host-neutral launch flag; --codex-project-root keeps working."""
from __future__ import annotations

import os
import subprocess
import sys

import anyio
import pytest
from mcp import Client, StdioServerParameters

from tests.transport.test_codex_integration import PROJECT, repository

HOOKS = {"nexus_session_start", "nexus_recall", "nexus_pre_tool"}


def params(tmp_path, repo, flag):
    return StdioServerParameters(command=sys.executable, args=[
        "-m", "nexus_memory", "--namespace", "hook-test", "--actor", "local",
        "--db", str(tmp_path / "db.sqlite3"), flag, str(repo),
    ], cwd=repo, env={**os.environ, "PYTHONPATH": str(PROJECT / "src")})


@pytest.mark.parametrize("flag", ["--hook-project-root", "--codex-project-root"])
def test_flag_registers_hooks_and_binds_the_checkout_for_verification(tmp_path, flag) -> None:
    repo = repository(tmp_path / "repo")

    async def run() -> None:
        async with Client(params(tmp_path, repo, flag)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert HOOKS <= names
            status = (await client.call_tool("status", {})).structured_content
            assert status["repository_id"] is not None
            assert status["verification"] != "unavailable"

    anyio.run(run)


def test_flag_refuses_an_unrelated_launch_cwd_and_repo_combination(tmp_path) -> None:
    repo, other = repository(tmp_path / "repo"), repository(tmp_path / "other")
    env = {**os.environ, "PYTHONPATH": str(PROJECT / "src")}
    base = [sys.executable, "-m", "nexus_memory", "--namespace", "n", "--db", str(tmp_path / "d.sqlite3")]
    both = subprocess.run([*base, "--hook-project-root", str(repo), "--repo", str(repo)],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert both.returncode == 2 and "mutually exclusive" in both.stderr
    wrong = subprocess.run([*base, "--hook-project-root", str(repo)], cwd=other, env=env,
                           capture_output=True, text=True, timeout=30)
    assert wrong.returncode == 1 and "startup_error" in wrong.stderr
