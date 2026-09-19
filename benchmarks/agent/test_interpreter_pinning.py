"""Counterexamples for the interpreter repair. No model, no network, no paid run.

The defect: the environment gate ran `PYTHONPATH=src python3 -m pytest` under `/bin/zsh -c`
and passed, while the agent's shell resolved a different `python3` and failed. `No module
named pytest` then appears in 10 of 11 failing v2 arm-runs and in ALL SIXTEEN passing ones.

Two things had to be true for that to survive a gate:

  * the gate resolved the interpreter through PATH, the same way the agent did, so both were
    guesses that happened to differ;
  * the gate ran only ONE shell startup, and not the one whose PATH the arms' own recorded
    `which -a python3` output matches.

So the repair is checked on both axes: the interpreter must arrive by NAME (`$A2_PYTHON`,
set in one place so the gate and the runner cannot disagree), and every documented command
must work and agree under EVERY startup in `STARTUPS`.

These cases are host-independent: they stub `sandbox-exec` and `/bin/zsh` rather than running
them, so a machine without the fixtures still exercises the code path that decides.

    python3 test_interpreter_pinning.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import a1_config                                                        # noqa: E402
import run_calibration as RC                                            # noqa: E402
import verify_arm_environment as V                                      # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


class FakeShell:
    """Stand in for `sandbox-exec ... /bin/zsh [-l] -c <script>`.

    `replies` maps a startup name to (returncode, stdout). The point is to drive the gate's
    OWN decision logic with a startup that disagrees -- the situation it was blind to -- and
    with one that does not, without needing a sandbox or a real interpreter.
    """

    def __init__(self, replies):
        self.replies = replies
        self.seen: list[tuple[str, str]] = []

    def __call__(self, argv, **kw):
        startup = "login" if "-l" in argv else "non_login"
        script = argv[-1]
        self.seen.append((startup, script))
        rc, out = self.replies[startup]
        return subprocess.CompletedProcess(argv, rc, out, "")


def with_shell(replies, fn):
    real = subprocess.run
    shell = FakeShell(replies)
    subprocess.run = shell
    try:
        return fn(), shell
    finally:
        subprocess.run = real


TMP = Path(tempfile.mkdtemp())
(TMP / "repo").mkdir()
PROFILE, REPO = TMP / "sandbox.sb", TMP / "repo"
ENV = {"A2_PYTHON": "/pinned/python"}


# ------------------------------------------------------------------ the environment channel
def test_the_pinned_interpreter_is_set_in_one_place():
    """The gate and the runner must not each resolve their own. `child_env` sets it, so every
    caller gets the same value and none can forget."""
    env = a1_config.child_env("http://x", "k")
    check("A2_PYTHON is in the child environment", bool(env.get("A2_PYTHON")), str(env.keys()))
    check("it is the configuration's pytest interpreter",
          env["A2_PYTHON"] == a1_config.load().pytest_python, env.get("A2_PYTHON", ""))
    check("it is declared harness-set, not inherited",
          "A2_PYTHON" in a1_config.ENV_HARNESS_SET, str(a1_config.ENV_HARNESS_SET))
    check("and it is NOT on the inherit allowlist",
          "A2_PYTHON" not in a1_config.ENV_ALLOWLIST, str(sorted(a1_config.ENV_ALLOWLIST)))


def test_an_explicit_interpreter_overrides_the_configured_one():
    env = a1_config.child_env("http://x", "k", python="/some/other/python")
    check("explicit pin wins", env["A2_PYTHON"] == "/some/other/python", env["A2_PYTHON"])


# --------------------------------------------------------------- both startups are exercised
def test_every_documented_command_runs_under_every_startup():
    """A gate that probes one startup tests a command the agent may never execute."""
    for fn, name in ((lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV), "pinned"),
                     (lambda: V.check_tests(PROFILE, REPO, ENV, "tests/x.py"), "tests"),
                     (lambda: V.check_repro_script(PROFILE, REPO, ENV), "repro")):
        _, shell = with_shell({"non_login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1\n1 passed\nREPRO-OK"),
                               "login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1\n1 passed\nREPRO-OK")}, fn)
        got = sorted({s for s, _ in shell.seen})
        check(f"{name} probes both startups", got == ["login", "non_login"], str(got))


def test_two_startups_are_configured():
    check("login startup is probed", "login" in V.STARTUPS, str(sorted(V.STARTUPS)))
    check("login startup actually passes -l", "-l" in V.STARTUPS["login"],
          str(V.STARTUPS["login"]))


# ------------------------------------------------- the exact defect, as a negative control
def test_a_startup_that_disagrees_is_REFUSED():
    """THE case. Under `zsh -c` the command worked; under a login shell it did not. The old
    gate saw only the first and passed. This must now fail."""
    r, _ = with_shell({"non_login": (0, "A2RUNTIME /opt/homebrew/bin/python3 3.14.7 9.1.1"),
                       "login": (1, "No module named pytest")},
                      lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV))
    check("disagreeing startups refuse", r["passed"] is False, json.dumps(r))

    r2, _ = with_shell({"non_login": (0, "1 passed in 0.01s"),
                        "login": (1, "No module named pytest")},
                       lambda: V.check_tests(PROFILE, REPO, ENV, "tests/x.py"))
    check("the documented test command refuses too", r2["passed"] is False, json.dumps(r2))


def test_the_same_interpreter_under_both_startups_passes():
    """The positive control: without it, a check that always refuses would pass this file."""
    r, _ = with_shell({"non_login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1"),
                       "login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1")},
                      lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV))
    check("agreement passes", r["passed"] is True, json.dumps(r))
    check("and the runtime is reported", "3.14.7" in r.get("runtime", ""), json.dumps(r))


def test_different_interpreters_that_both_succeed_are_still_refused():
    """Both startups working is not enough -- they must be the SAME interpreter. Two working
    pythons of different versions is exactly the state that produced a 3.9.6/3.14.7 split."""
    r, _ = with_shell({"non_login": (0, "A2RUNTIME /a/python 3.14.7 9.1.1"),
                       "login": (0, "A2RUNTIME /b/python 3.9.6 9.1.1")},
                      lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV))
    check("two different working interpreters refuse", r["passed"] is False, json.dumps(r))


def test_an_unset_interpreter_refuses_rather_than_falling_back():
    r = V.check_pinned_interpreter(PROFILE, REPO, {})
    check("unset A2_PYTHON refuses", r["passed"] is False, json.dumps(r))
    check("and says so", "A2_PYTHON" in r["detail"], r["detail"])


def test_empty_output_is_not_agreement():
    """Two startups that both print nothing 'agree'. Nothing was compared."""
    r, _ = with_shell({"non_login": (0, ""), "login": (0, "")},
                      lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV))
    check("silence is not agreement", r["passed"] is False, json.dumps(r))


# ------------------------------------------------------------------- identity and versioning
def test_the_repaired_configuration_is_a_new_version():
    check("config version moved off v2", RC.CONFIG_VERSION != "calib-v2", RC.CONFIG_VERSION)
    check("and is v3", RC.CONFIG_VERSION == "calib-v3", RC.CONFIG_VERSION)


def test_the_prompt_names_the_interpreter_and_keeps_the_task_requirement():
    reg = json.loads((Path(__file__).parent / "prompts-calib-a2.json").read_text())
    env_block = reg["environment"]
    check("the environment block names A2_PYTHON", "$A2_PYTHON" in env_block, env_block[:80])
    check("it no longer documents a bare `python3 -m pytest`",
          "python3 -m pytest" not in env_block, env_block)
    check("the task still requires extending the test suite",
          "extend the existing test suite" in reg["tails"]["default"],
          reg["tails"]["default"])
    check("no network is still stated", "no network" in env_block, env_block[:200])
    check("scratch still belongs in the checkout", "inside the checkout" in env_block,
          env_block[-120:])


def test_the_runtime_identity_is_reported_for_the_record():
    r, _ = with_shell({"non_login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1\n1 passed\nREPRO-OK"),
                       "login": (0, "A2RUNTIME /pinned/python 3.14.7 9.1.1\n1 passed\nREPRO-OK")},
                      lambda: V.check_pinned_interpreter(PROFILE, REPO, ENV))
    check("runtime travels with the check", r.get("runtime", "").startswith("/pinned/python"),
          json.dumps(r))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} counterexample tests")
    for f in FAILS:
        print("  FAIL " + f)
    print("FAILURES: " + str(len(FAILS)) if FAILS else "all pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
