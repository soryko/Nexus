"""The release tools' failure paths, exercised through their real command-line entry points.

These tools exist to turn a quiet failure into a loud one. A host whose interpreter links an
old SQLite, a host without uv, a path that names nothing: each of those ends with an MCP
client seeing a closed transport and no reason for it. So what is under test here is mostly
the *unhappy* half, and it is tested by running the actual scripts in a subprocess and
reading their exit status, because the exit status is what CI and a human both act on.

Two deliberate limits on what these prove:

  * The floor tests build synthetic interpreters. They establish that the installer reports
    and refuses correctly given a probe result; they do not establish anything about any real
    distributor's build. Only `tools/install.py` run on a host does that.
  * The transport negative control uses a real executable that really exits without speaking
    MCP. That one is not synthetic, and it carries a timeout so a hang fails the suite
    instead of stalling it.

The successful lifecycle path is deliberately NOT tested here. It needs a built environment
and a real server, it is what CI's fresh-install jobs run, and a mocked version of it would
assert that the mock works.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "tools" / "install.py"
CHECK = REPO / "tools" / "check_install.py"

# Enough for a subprocess that builds nothing; the transport control gets its own budget.
TIMEOUT = 120


def run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a release tool the way a person does: real interpreter, real argv, real exit."""
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, timeout=TIMEOUT, cwd=REPO,
        env={**os.environ, **(env or {})},
    )


def fake_python(tmp_path: Path, name: str, version: tuple[int, int, int],
                sqlite: str) -> Path:
    """An executable that answers the installer's probe with a chosen pair of versions.

    The installer decides from the probe's JSON, so a script that emits that JSON is a
    faithful stand-in for an interpreter with those numbers -- and lets a 3.51.2-linked
    interpreter be tested on a host that does not have one.
    """
    exe = tmp_path / name
    exe.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"python": {list(version)!r},
                          "sqlite": {sqlite!r},
                          "executable": {str(exe)!r}}}))
        """))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return exe


class TestInstallerRefuses:
    """An interpreter that cannot run the server must be named as such, not worked around."""

    def test_reports_both_failed_floors_and_refuses(self, tmp_path: Path) -> None:
        # Below on BOTH counts. Reporting only the first would leave someone fixing Python
        # and rerunning into the same closed transport.
        bad = fake_python(tmp_path, "py-old", (3, 11, 9), "3.50.4")
        r = run(INSTALL, "--python", str(bad))

        assert r.returncode == 1, r.stdout + r.stderr
        assert "python 3.11.9 <" in r.stdout
        assert "sqlite 3.50.4 <" in r.stdout
        assert "No interpreter meets BOTH floors" in r.stdout

    def test_a_modern_python_with_an_old_sqlite_is_still_refused(self, tmp_path: Path) -> None:
        # The case the whole tool exists for: the Python version looks fine and the
        # environment still cannot start the server.
        bad = fake_python(tmp_path, "py-new-old-sqlite", (3, 13, 14), "3.51.2")
        r = run(INSTALL, "--python", str(bad))

        assert r.returncode == 1, r.stdout + r.stderr
        assert "sqlite 3.51.2 <" in r.stdout
        assert "python 3.13" not in r.stdout.split("(")[-1]  # not blamed for the Python

    def test_an_explicit_interpreter_is_never_substituted(self, tmp_path: Path) -> None:
        # The host running this has a qualifying interpreter -- this very one. If --python
        # were advisory, that one would be found and the build would "succeed" against an
        # interpreter the caller did not choose. Silence about the substitution would be
        # worse than the failure.
        bad = fake_python(tmp_path, "py-explicit-bad", (3, 13, 14), "3.50.4")
        r = run(INSTALL, "--python", str(bad))

        assert r.returncode == 1, r.stdout + r.stderr
        assert str(bad) in r.stdout
        assert sys.executable not in r.stdout
        assert "chosen:" not in r.stdout

    def test_an_interpreter_that_will_not_run_is_reported_not_crashed(self, tmp_path: Path) -> None:
        missing = tmp_path / "not-an-interpreter"
        r = run(INSTALL, "--python", str(missing))

        assert r.returncode == 1, r.stdout + r.stderr
        assert "would not run" in r.stdout
        assert "Traceback" not in r.stderr


class TestInstallerPrerequisites:
    """Missing uv is a prerequisite failure with a fix, not a FileNotFoundError."""

    def test_missing_uv_exits_2_with_an_actionable_message(self, tmp_path: Path) -> None:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        # A PATH with no uv on it, and a venv target under tmp so a regression that builds
        # anyway cannot touch the checkout's .venv.
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        r = run(INSTALL, "--python", str(good), "--venv", str(tmp_path / "venv"),
                env={"PATH": str(empty_bin)})

        assert r.returncode == 2, r.stdout + r.stderr
        assert "uv is required" in r.stdout
        assert "docs.astral.sh/uv" in r.stdout          # names the fix
        assert "Traceback" not in r.stderr
        assert not (tmp_path / "venv").exists()          # and built nothing

    def test_check_only_reports_without_needing_uv(self, tmp_path: Path) -> None:
        # A report must work on a host that cannot yet build anything; otherwise the tool
        # that diagnoses a broken host requires the host to be fixed first.
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        r = run(INSTALL, "--python", str(good), "--check-only",
                env={"PATH": str(empty_bin)})

        assert r.returncode == 0, r.stdout + r.stderr
        assert "chosen:" in r.stdout


class TestCheckerFailurePaths:
    """The checker's job is to fail loudly; these are the ways it must fail."""

    def test_missing_server_exits_2_before_any_transport_attempt(self, tmp_path: Path) -> None:
        r = run(CHECK, "--server", str(tmp_path / "nexus-memory"))

        assert r.returncode == 2, r.stdout + r.stderr
        assert "no installed command at" in r.stdout
        assert "Traceback" not in r.stderr

    def test_a_server_that_exits_without_serving_mcp_exits_1_with_a_diagnosis(
            self, tmp_path: Path) -> None:
        # Not a mock: a real executable that really starts and really closes its stdio
        # without speaking MCP. This is what a below-floor install looks like from the
        # client's side -- the server writes `unsupported_runtime` to a stderr nobody reads
        # and exits, and the only symptom is the closed transport.
        dud = tmp_path / "nexus-memory"
        dud.write_text(f"#!{sys.executable}\nimport sys; sys.exit(1)\n")
        dud.chmod(dud.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        report = tmp_path / "report.json"
        # Its own timeout: a checker that hangs against a dead server is itself the defect,
        # and it must surface as a failure rather than as a stalled suite.
        proc = subprocess.run(
            [sys.executable, str(CHECK), "--server", str(dud), "--json", str(report)],
            capture_output=True, text=True, timeout=90, cwd=REPO,
        )

        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "server answers MCP" in proc.stdout
        assert "did not serve MCP" in proc.stdout
        assert "Traceback" not in proc.stderr

        # The failure is recorded, not merely printed.
        written = json.loads(report.read_text())
        assert written["passed"] is False
        assert any(not s["passed"] for s in written["steps"])

    def test_the_json_report_is_a_file_not_stdout(self, tmp_path: Path) -> None:
        # The flag's documented behaviour, asserted, because its help text said otherwise.
        dud = tmp_path / "nexus-memory"
        dud.write_text(f"#!{sys.executable}\nimport sys; sys.exit(1)\n")
        dud.chmod(dud.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        report = tmp_path / "nested" / "report.json"
        report.parent.mkdir()

        subprocess.run([sys.executable, str(CHECK), "--server", str(dud),
                        "--json", str(report)],
                       capture_output=True, text=True, timeout=90, cwd=REPO)

        assert report.is_file()
        assert json.loads(report.read_text())["server"] == str(dud)


class TestFloorsAreReadNotHardcoded:
    """The installer's floors must track the sources that enforce them."""

    def test_floors_match_the_enforcing_sources(self) -> None:
        sys.path.insert(0, str(REPO / "tools"))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_install", INSTALL)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            py_floor, sq_floor = mod.floors()
        finally:
            sys.path.pop(0)

        # Not compared against literals: compared against the files that decide.
        assert f'">={".".join(map(str, py_floor))}"'.strip('"') in \
            (REPO / "pyproject.toml").read_text().replace(" ", "")
        sqlite_src = (REPO / "src/nexus_memory/storage/sqlite.py").read_text()
        assert "MINIMUM_SQLITE" in sqlite_src
        assert ", ".join(map(str, sq_floor)) in sqlite_src.replace(",", ", ").replace("  ", " ")
