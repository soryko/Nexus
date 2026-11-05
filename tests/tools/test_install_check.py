"""The packaged lifecycle checker: where it looks for the server, and how it exits.

The checker moved into the package so that verifying an installation stops depending on
a script from the source checkout. Two properties carry that move, and both are easy to
lose silently:

  * It finds the server next to its OWN interpreter. A virtual environment's `bin/python`
    is usually a symlink to the base interpreter, so resolving that symlink before taking
    the parent directory would look for `nexus-memory` beside the *base* runtime -- where
    it does not exist, or worse, where a different installation's copy does.
  * Its documented exit codes survive the move to a console entry point and a module
    entry point. A wrapper that swallows a nonzero exit turns a failed check into a
    passing CI step, which is the one failure this whole tool exists to prevent.

The exit-code tests run real subprocesses against real files for the same reason the
existing release-tool tests do: the exit status is what CI and a human actually read.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "tools" / "check_install.py"

TIMEOUT = 120


def _dud_server(tmp_path: Path) -> Path:
    """A real executable that really starts and really closes without speaking MCP."""
    dud = tmp_path / "nexus-memory"
    dud.write_text(f"#!{sys.executable}\nimport sys; sys.exit(1)\n")
    dud.chmod(dud.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dud


class TestDefaultServerSelection:
    """The server is the one in the checker's own environment, not the base runtime's."""

    def test_default_server_keeps_the_environment_path(self, tmp_path: Path) -> None:
        from nexus_memory.install_check import default_server

        python = tmp_path / "runtime with spaces" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

        assert default_server(str(python)) == python.with_name("nexus-memory")

    def test_default_server_does_not_follow_the_symlink_to_the_base_runtime(
            self, tmp_path: Path) -> None:
        # The negative half of the property above, stated against the real base
        # interpreter: a `resolve()` slipped into `default_server` would make this the
        # directory of `sys.executable`, and the test above would still pass whenever the
        # two happened to coincide.
        from nexus_memory.install_check import default_server

        python = tmp_path / "env" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

        chosen = default_server(str(python))
        assert chosen.parent == python.parent
        assert chosen.parent != Path(sys.executable).resolve().parent

    def test_default_server_is_absolute_even_for_a_relative_executable(
            self, tmp_path: Path) -> None:
        from nexus_memory.install_check import default_server

        assert default_server("bin/python").is_absolute()


class TestModuleEntryPointExitCodes:
    """`python -m nexus_memory.install_check` must carry the documented exits."""

    def _run(self, *args: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "nexus_memory.install_check", *args],
            capture_output=True, text=True, timeout=timeout, cwd=REPO,
        )

    def test_missing_server_exits_2(self, tmp_path: Path) -> None:
        r = self._run("--server", str(tmp_path / "nexus-memory"))

        assert r.returncode == 2, r.stdout + r.stderr
        assert "no installed command at" in r.stdout
        assert "Traceback" not in r.stderr

    def test_a_server_that_does_not_serve_mcp_exits_1(self, tmp_path: Path) -> None:
        report = tmp_path / "report.json"
        r = self._run("--server", str(_dud_server(tmp_path)), "--json", str(report),
                      timeout=90)

        assert r.returncode == 1, r.stdout + r.stderr
        assert "server answers MCP" in r.stdout
        assert "did not serve MCP" in r.stdout
        assert "Traceback" not in r.stderr

        written = json.loads(report.read_text())
        assert written["passed"] is False
        assert any(not s["passed"] for s in written["steps"])

    def test_the_failure_advice_does_not_require_the_source_checkout(
            self, tmp_path: Path) -> None:
        # The whole point of packaging the checker: someone running it from an installed
        # environment may have no `tools/` directory at all, so advice that tells them to
        # run a script from one is advice they cannot follow.
        r = self._run("--server", str(_dud_server(tmp_path)), timeout=90)

        # Anchor on the advice actually being printed. Asserting only the absence of a
        # string would pass just as happily against empty output -- including the empty
        # output of a checker that failed to start at all.
        assert r.returncode == 1, r.stdout + r.stderr
        assert "did not serve MCP" in r.stdout
        assert "tools/install.py" not in r.stdout
        assert "tools/check_install.py" not in r.stdout


class TestCompatibilityWrapper:
    """`tools/check_install.py` still works, and still fails the same way."""

    def _run(self, *args: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(WRAPPER), *args],
            capture_output=True, text=True, timeout=timeout, cwd=REPO,
        )

    def test_wrapper_missing_server_exits_2(self, tmp_path: Path) -> None:
        r = self._run("--server", str(tmp_path / "nexus-memory"))

        assert r.returncode == 2, r.stdout + r.stderr
        assert "Traceback" not in r.stderr

    def test_wrapper_transport_failure_exits_1(self, tmp_path: Path) -> None:
        r = self._run("--server", str(_dud_server(tmp_path)), timeout=90)

        assert r.returncode == 1, r.stdout + r.stderr
        assert "Traceback" not in r.stderr

    def test_wrapper_does_not_put_the_checkout_src_on_sys_path(self) -> None:
        # If the wrapper injected `src/`, it would test the working tree rather than the
        # installation -- and would keep "working" on a host where the package is not
        # actually installed, which is the failure this milestone removes.
        #
        # Read the CODE, not the text: the file is free to discuss `sys.path` in its
        # docstring, and a grep over the whole source would be a check that fails for
        # the wrong reason and then gets loosened until it never fails at all.
        import ast

        tree = ast.parse(WRAPPER.read_text())
        touches = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "path"
            and isinstance(node.value, ast.Name) and node.value.id == "sys"
        ]
        assert not touches, f"wrapper manipulates sys.path at line(s) " \
                            f"{[n.lineno for n in touches]}"

    def test_wrapper_delegates_rather_than_reimplementing_the_check(self) -> None:
        # A second copy of the checker is a second checker that can disagree with the
        # installed one, and the copy people run from the checkout is the one that would
        # be stale. So the wrapper must import the real thing.
        import ast

        tree = ast.parse(WRAPPER.read_text())
        imported = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "nexus_memory.install_check" in imported
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
