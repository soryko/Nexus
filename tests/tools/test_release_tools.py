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
import shlex
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


def recording_uv(tmp_path: Path) -> tuple[Path, Path]:
    """A `uv` that records each invocation's argv and otherwise does nothing.

    Lets a test assert what the installer asked uv for -- or, just as usefully, that it
    never asked uv anything at all.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv-argv.jsonl"
    uv = bin_dir / "uv"
    uv.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        with open({str(log)!r}, "a") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\\n")
        """))
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, log


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


class TestInstallerBuildsARuntimeNotADevelopmentTree:
    """The sync must ask for a NON-editable installation, and must say so to uv.

    This is the whole of the v0.1.0a2 claim, and it is invisible in a passing install: an
    editable environment and a normal one both start the server, pass the lifecycle check
    and print the same success message. They differ only once the checkout is gone, which
    no fast test can stage. So this records what the installer actually asks uv for, by
    putting a `uv` on PATH that writes down its argv -- the real script, the real CLI, the
    real command construction, and no assumption that the flags reached the subprocess.

    The end-to-end property (a server that runs with its source deleted) is established by
    the installed-distribution job in CI, not here.
    """

    def _sync_argv(self, tmp_path: Path) -> list[str]:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        bin_dir, log = recording_uv(tmp_path)
        r = run(INSTALL, "--python", str(good), "--venv", str(tmp_path / "venv"),
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})

        # The fake uv builds nothing, so the installer's re-check of the built environment
        # finds no interpreter and refuses to report success. That refusal is correct and
        # is asserted so this test cannot pass against an installer that skipped the
        # re-check entirely.
        assert r.returncode == 2, r.stdout + r.stderr
        assert "Nothing has been verified" in r.stdout

        assert log.exists(), (
            "the recording uv never ran; the installer's exit says nothing about the "
            "sync command when the venv step failed first\n" + r.stdout + r.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert [c[0] for c in calls if c] == ["venv", "sync"], calls
        sync = [c for c in calls if c and c[0] == "sync"]
        assert len(sync) == 1, calls
        return sync[0]

    def test_the_sync_is_not_editable(self, tmp_path: Path) -> None:
        assert "--no-editable" in self._sync_argv(tmp_path)

    def test_the_sync_excludes_development_dependencies(self, tmp_path: Path) -> None:
        # pytest and hypothesis have no business in a user's runtime.
        assert "--no-dev" in self._sync_argv(tmp_path)

    def test_the_sync_stays_frozen_and_names_the_chosen_interpreter(
            self, tmp_path: Path) -> None:
        argv = self._sync_argv(tmp_path)
        assert "--frozen" in argv
        assert argv[argv.index("--python") + 1] == str(tmp_path / "py-good")


class TestInstallerRefusesAnExistingEnvironment:
    """A destination that already holds an environment is a question, not a crash.

    `uv venv` will not reuse a directory, and left to itself it answers with uv's own
    wording and a `--clear` hint -- an offer to delete an environment a client may be
    running right now. The installer has no business taking that offer on someone's
    behalf, and it should not let uv do the explaining either: the reader needs to know
    which destination is occupied, how to check what is already there, and that this
    command changed nothing.

    The refusal must therefore be diagnostic, must keep exit 2, and above all must leave
    the existing environment exactly as it found it.
    """

    def _existing_env(self, tmp_path: Path) -> tuple[Path, Path]:
        """A directory that looks like an environment, plus a sentinel inside it."""
        venv = tmp_path / "existing runtime"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /somewhere\nversion = 3.13.14\n")
        sentinel = venv / "bin" / "nexus-memory"
        sentinel.write_text("#!/bin/sh\necho original\n")
        return venv, sentinel

    def test_it_refuses_with_exit_2_and_names_the_destination(self, tmp_path: Path) -> None:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv, _ = self._existing_env(tmp_path)
        r = run(INSTALL, "--python", str(good), "--venv", str(venv))

        assert r.returncode == 2, r.stdout + r.stderr
        assert str(venv) in r.stdout                      # which one is occupied
        assert "Traceback" not in r.stderr

    def test_it_says_how_to_check_what_is_there_and_how_to_install_elsewhere(
            self, tmp_path: Path) -> None:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv, _ = self._existing_env(tmp_path)
        r = run(INSTALL, "--python", str(good), "--venv", str(venv))

        # Both ways forward, named. A refusal that only says "no" sends the reader to
        # uv's hint, which is the one option this tool declines to take for them.
        assert "nexus-memory-check" in r.stdout           # verify what is already there
        assert "--venv" in r.stdout                       # or choose a fresh destination

    def test_it_does_not_offer_to_clear_the_existing_environment(
            self, tmp_path: Path) -> None:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv, _ = self._existing_env(tmp_path)
        r = run(INSTALL, "--python", str(good), "--venv", str(venv))

        # Deleting an environment a client may be running is the user's decision to make
        # deliberately, not one to copy out of a hint. Checked across BOTH streams: uv
        # writes its hint to stderr, so asserting on stdout alone would pass while the
        # suggestion still reached the reader.
        both = r.stdout + r.stderr
        assert "--clear" not in both
        assert "UV_VENV_CLEAR" not in both

    def test_it_refuses_before_uv_runs_at_all(self, tmp_path: Path) -> None:
        # The installer must recognise this itself rather than forwarding uv's failure.
        # Asserted as the actual property -- uv is never invoked -- because the obvious
        # proxy, looking for uv's phrasing in the output, matches our own message too:
        # "an environment already exists at" is what either of us would write.
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv, _ = self._existing_env(tmp_path)
        bin_dir, log = recording_uv(tmp_path)

        r = run(INSTALL, "--python", str(good), "--venv", str(venv),
                env={"PATH": f"{bin_dir}:{os.environ['PATH']}"})

        assert r.returncode == 2, r.stdout + r.stderr
        assert not log.exists(), (
            f"uv was invoked before the refusal: {log.read_text()}")
        # And the post-failure path, which only runs after a command fails, is not reached.
        assert "Nothing has been verified" not in r.stdout + r.stderr

    def test_the_existing_environment_is_left_exactly_as_it_was(
            self, tmp_path: Path) -> None:
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv, sentinel = self._existing_env(tmp_path)
        before = {p.relative_to(venv): p.read_bytes()
                  for p in sorted(venv.rglob("*")) if p.is_file()}

        r = run(INSTALL, "--python", str(good), "--venv", str(venv))

        assert r.returncode == 2, r.stdout + r.stderr
        after = {p.relative_to(venv): p.read_bytes()
                 for p in sorted(venv.rglob("*")) if p.is_file()}
        assert after == before, "the refusal modified the existing environment"
        assert sentinel.read_text() == "#!/bin/sh\necho original\n"

    def test_an_empty_directory_is_not_an_existing_environment(
            self, tmp_path: Path) -> None:
        # Refusing here would break the ordinary case of pointing at a path someone has
        # already created, e.g. with `mkdir -p`. Only a real environment refuses.
        good = fake_python(tmp_path, "py-good", (3, 13, 14), "3.53.1")
        venv = tmp_path / "empty dir"
        venv.mkdir()
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        r = run(INSTALL, "--python", str(good), "--venv", str(venv),
                env={"PATH": str(empty_bin)})

        # It gets PAST the existing-environment check and stops at the missing uv,
        # which is the next gate -- so the refusal above is specific, not a blanket
        # "the directory exists".
        assert "uv is required" in r.stdout, r.stdout


class TestThePrintedCommandsAreRunnable:
    """What the installer prints must survive being pasted into a shell.

    A path with a space in it is the ordinary case on macOS -- "Application Support",
    "My Project" -- and an unquoted one splits into several words. The shell then reports
    127, which reads as "this command is not installed" for a command that is installed and
    working. The CI driver cannot catch this: it invokes the checker as an argument LIST,
    which never goes through word splitting at all.

    So this builds a real environment at a path containing spaces, takes the command the
    installer actually printed, and runs that text through bash.
    """

    @pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build")
    @pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
    def test_the_printed_verification_command_runs_verbatim(self, tmp_path: Path) -> None:
        venv = tmp_path / "runtime with spaces" / "0.1.0a2"
        r = run(INSTALL, "--python", sys.executable, "--venv", str(venv))
        assert r.returncode == 0, r.stdout + r.stderr

        printed = [ln.strip() for ln in r.stdout.splitlines()
                   if ln.strip().endswith(("nexus-memory-check", "nexus-memory-check'",
                                           'nexus-memory-check"'))]
        assert printed, f"the installer printed no verification command:\n{r.stdout}"
        command = printed[-1]
        assert " " in str(venv)                      # the hazard is actually present

        # One word after splitting, and that word is the checker that was installed.
        assert shlex.split(command) == [str(venv / "bin" / "nexus-memory-check")]

        # And the text itself, through a shell, exactly as a reader would paste it.
        proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                              timeout=300, cwd=str(tmp_path))
        assert proc.returncode == 0, (
            f"the printed command failed in a shell (exit {proc.returncode}); "
            f"127 means it word-split on the spaces\n{command}\n"
            f"{proc.stdout[-1500:]}{proc.stderr[-1500:]}")
        assert "PASS: 9/9 checks" in proc.stdout


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
