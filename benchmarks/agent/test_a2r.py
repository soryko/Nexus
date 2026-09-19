"""Counterexamples for the A2-R assessment and its separate accounting.

No model, no network, no fixtures, no host configuration. Every case is one where the obvious
implementation is silently wrong: an absence read as a pass, a comment read as a test, a
failing test read as a broken interpreter, two sweeps sharing one budget.

    python3 test_a2r.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import a1_config                                                        # noqa: E402
import assess_a2r as A                                                  # noqa: E402
import identity as ident                                                # noqa: E402
import run_a2r as R2                                                    # noqa: E402
import run_calibration as RC                                            # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def bash(index, command, result="", is_error=False):
    return {"index": index, "name": "Bash", "input": {"command": command},
            "result": result, "is_error": is_error}


def record(patch=None, calls=None, **kw):
    r = {"arm": "baseline", "functional": "pass", "usage": {"input_tokens": 10},
         "terminal": {"terminal": "completed", "scored": True, "truncated": False},
         "runtime_identity": {"a2_python": "/venv/bin/python3", "python_version": "3.14.7",
                              "pytest_version": "9.1.1", "sys_executable": "/venv/bin/python3"},
         "tool_calls": calls or []}
    if patch is not None:
        r["patch"] = patch
    r.update(kw)
    return r


NODE = "tests/test_options.py::test_eager_flag_is_processed_once"
PYTEST_PASS = "============ 3 passed in 0.42s ============"
PYTEST_FAIL = "======= 2 failed, 23 deselected in 0.51s ======="
NO_PYTEST = "/usr/bin/python3: No module named pytest"


# ------------------------------------------------------------------ the repair, non-vacuous
def test_an_absent_error_with_no_attempt_is_not_a_pass():
    """THE central case. `30/k2/notes` passed v2's hidden checks having never invoked pytest.
    A repair check keyed to the ABSENCE of `No module named pytest` scores that run clean and
    learns nothing, because nothing was attempted for the repair to have fixed."""
    r = A.repair_check(record(), [bash(1, "ls src/click"), bash(2, "git diff")])
    check("no invocation -> no_relevant_invocation",
          r["state"] == "no_relevant_invocation", r["state"])
    check("no invocation -> nothing to judge", r["relevant_invocation"] is False)
    check("no invocation -> not counted as a pinned run", r["pinned_invocations"] == 0)


def test_a_failing_test_is_not_a_broken_interpreter():
    """The documented command ran perfectly and the tests were red. That is the normal state
    of a run that has not fixed the bug yet, and reading it as a toolchain failure would
    report the repair broken on every unfinished arm-run."""
    r = A.repair_check(record(), [bash(1, 'PYTHONPATH=src "$A2_PYTHON" -m pytest -q',
                                       PYTEST_FAIL)])
    check("red tests -> the command RAN", r["pinned_ran"] == 1, json.dumps(r["evidence"]))
    check("red tests -> not not_runnable", r["pinned_not_runnable"] == 0)
    check("red tests -> state pinned_ran", r["state"] == "pinned_ran", r["state"])


def test_no_module_named_pytest_is_the_toolchain_failing():
    r = A.repair_check(record(), [bash(1, 'PYTHONPATH=src "$A2_PYTHON" -m pytest -q',
                                       NO_PYTEST)])
    check("missing pytest -> not_runnable", r["pinned_not_runnable"] == 1, json.dumps(r))
    check("missing pytest -> state", r["state"] == "pinned_not_runnable", r["state"])


def test_the_programs_own_missing_module_is_not_the_toolchain():
    """`No module named 'click'` is a forgotten PYTHONPATH, not a broken interpreter. The
    interpreter demonstrably ran: it produced the traceback."""
    out = ("Traceback (most recent call last):\n  File \"repro.py\", line 1\n"
           "ModuleNotFoundError: No module named 'click'")
    r = A.repair_check(record(), [bash(1, '"$A2_PYTHON" repro.py', out)])
    check("other module -> the interpreter ran", r["pinned_ran"] == 1, json.dumps(r))
    check("other module -> not a toolchain failure", r["pinned_not_runnable"] == 0)
    check("other module -> reported separately",
          r["other_missing_modules"] == ["click"], str(r["other_missing_modules"]))


def test_looking_an_interpreter_up_is_not_running_one():
    """`which -a python3` was the first call in most v2 arm-runs, and `echo "$A2_PYTHON"` is
    how an agent checks the pin. Neither is an invocation.

    The second is the dangerous one: without the lookup guard, `echo "$A2_PYTHON"` counts as
    the documented command, its output is a plausible-looking path, and the run is recorded as
    having RUN the pinned interpreter. That is the vacuous pass this whole module exists to
    refuse -- and it is worse than the absence it replaces, because it is a positive claim."""
    r = A.repair_check(record(), [bash(1, "which -a python3", "/usr/bin/python3")])
    check("which -> not an interpreter use",
          r["other_interpreter_invocations"] == 0, json.dumps(r["evidence"]))
    check("which -> still no relevant invocation",
          r["state"] == "no_relevant_invocation", r["state"])
    e = A.repair_check(record(), [bash(1, 'echo "$A2_PYTHON"', "/venv/bin/python3")])
    check("echo $A2_PYTHON -> not an invocation",
          e["pinned_invocations"] == 0, json.dumps(e["evidence"]))
    check("echo $A2_PYTHON -> not recorded as a successful run",
          e["pinned_ran"] == 0 and e["state"] == "no_relevant_invocation", e["state"])
    check("echo $A2_PYTHON -> counted as a MENTION, not an invocation",
          A.classify_command('echo "$A2_PYTHON"')["mention"] == 1,
          json.dumps(A.classify_command('echo "$A2_PYTHON"')))
    check("which -a python3 -> counted as a lookup",
          A.classify_command("which -a python3")["lookup"] == 1,
          json.dumps(A.classify_command("which -a python3")))


def test_choosing_another_interpreter_is_a_different_fact_from_a_broken_one():
    """A gate refusal, a broken documented command and an agent who typed `python3` anyway are
    three different things. v2's summaries had one number for all of them."""
    r = A.repair_check(record(), [bash(1, "python3 -m pytest -q", NO_PYTEST)])
    check("other interpreter counted", r["other_interpreter_invocations"] == 1, json.dumps(r))
    check("other interpreter -> documented command untested",
          r["pinned_invocations"] == 0, json.dumps(r))
    check("other interpreter -> its own state",
          r["state"] == "other_interpreter_only", r["state"])


def test_PYTHONPATH_is_not_an_interpreter():
    """`PYTHONPATH=src "$A2_PYTHON" ...` is the documented command. A classifier matching the
    word `PYTHON` anywhere calls it a foreign interpreter and inverts the finding."""
    k = A.classify_command('PYTHONPATH=src "$A2_PYTHON" -m pytest tests/ -q')
    check("documented command -> pinned", k["pinned"] == 1, json.dumps(k))
    check("documented command -> not another interpreter",
          k["other_interpreter"] == 0, json.dumps(k))


def test_a_mixed_command_cannot_have_its_result_attributed():
    """One result, two interpreters, no way to say which produced the error."""
    r = A.repair_check(record(), [bash(1, 'python3 -c pass; "$A2_PYTHON" -m pytest', NO_PYTEST)])
    check("mixed -> outcome unknown", r["pinned_outcome_unknown"] == 1, json.dumps(r))
    check("mixed -> not blamed on the pinned command", r["pinned_not_runnable"] == 0)
    check("mixed -> both counted", r["pinned_invocations"] == 1
          and r["other_interpreter_invocations"] == 1, json.dumps(r))


def test_a_gate_refusal_on_the_forwarder_is_not_an_interpreter_failure():
    """A refusal stops spending whatever refused it. Only an interpreter check makes it
    evidence about the interpreter."""
    rec = record(gate_checks=[{"check": "model forwarder reachable", "passed": False},
                              {"check": "pinned interpreter is named, not resolved",
                               "passed": True}])
    r = A.repair_check(rec, [])
    check("forwarder refusal -> cause classified",
          r["gate_cause"] == "other_environment", str(r["gate_cause"]))
    check("forwarder refusal -> its own state",
          r["state"] == "gate_refused_other_environment", r["state"])
    rec2 = record(gate_checks=[{"check": "pinned interpreter is named, not resolved",
                                "passed": False}])
    check("interpreter refusal -> cause interpreter",
          A.repair_check(rec2, [])["gate_cause"] == "interpreter")


def test_an_empty_result_does_not_establish_that_it_ran():
    """Negative control on the outcome classifier: a check that says `ran` for anything is
    the same check as no check."""
    check("empty result -> unknown", A.invocation_outcome("") == "unknown")
    check("whitespace result -> unknown", A.invocation_outcome("   \n ") == "unknown")


# ------------------------------------------------------------------ the requirement
NEW_TEST = """diff --git a/tests/test_options.py b/tests/test_options.py
--- a/tests/test_options.py
+++ b/tests/test_options.py
@@ -10,3 +10,5 @@ def test_existing():
+
+def test_eager_flag_is_processed_once(runner):
+    assert runner.invoke(cli, ["--flag"]).exit_code == 0
"""

COMMENT_ONLY = """diff --git a/tests/test_options.py b/tests/test_options.py
--- a/tests/test_options.py
+++ b/tests/test_options.py
@@ -10,3 +10,5 @@ def test_existing():
+
+    # TODO: cover the eager case
"""

EXTENDED = """diff --git a/tests/test_options.py b/tests/test_options.py
--- a/tests/test_options.py
+++ b/tests/test_options.py
@@ -10,3 +10,4 @@ def test_missing_envvar():
+    assert result.exit_code == 2
"""

SRC_ONLY = """diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,3 +1,4 @@
+        self._eager_done = True
"""


def test_a_comment_under_tests_is_not_an_extension():
    """THE central case for correction 3. The diff touches tests/, the tail is not satisfied,
    and any check keyed to `a file under tests/ changed` says yes."""
    a = A.test_additions(COMMENT_ONLY)
    check("comment -> no test node", a["nodes"] == [], json.dumps(a))
    check("comment -> the file is still recorded as touched",
          a["files"] == ["tests/test_options.py"], json.dumps(a))


def test_a_new_test_function_is_an_extension():
    a = A.test_additions(NEW_TEST)
    check("new def -> node id",
          a["nodes"] == ["tests/test_options.py::test_eager_flag_is_processed_once"],
          json.dumps(a))


def test_extending_an_existing_test_counts():
    """Every d2 arm extended `test_missing_envvar` without defining anything. The test under
    examination is the one the hunk header names."""
    a = A.test_additions(EXTENDED)
    check("extension -> enclosing node",
          a["nodes"] == ["tests/test_options.py::test_missing_envvar"], json.dumps(a))


def test_a_source_only_patch_adds_no_test():
    a = A.test_additions(SRC_ONLY)
    check("src only -> no test node", a["nodes"] == [], json.dumps(a))
    check("src only -> no test file", a["files"] == [], json.dumps(a))
    c = A.requirement_compliance(record(patch=SRC_ONLY), [])
    check("src only -> source_diff yes", c["source_diff"] == "yes", json.dumps(c))
    check("src only -> test_addition no", c["test_addition"] == "no", json.dumps(c))
    check("src only -> executed unknown, not no",
          c["test_executed"] == "unknown", json.dumps(c))


def test_no_patch_is_unknown_not_no():
    """Missing evidence is not evidence of absence, and `no` is what a reader acts on."""
    c = A.requirement_compliance(record(), [])
    check("no patch -> every observation unknown",
          {c["source_diff"], c["test_addition"], c["test_executed"], c["test_result"]}
          == {"unknown"}, json.dumps(c))


def test_execution_is_not_inferred_from_edit_counts():
    """`test_edit_calls` is a count of edits. A run can edit tests/ ten times and never run
    one, and v2 contained runs that did exactly that."""
    calls = [{"index": i, "name": "Edit",
              "input": {"file_path": "tests/test_options.py"}, "result": "ok"}
             for i in range(10)]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("edits without a run -> executed no", c["test_executed"] == "no", json.dumps(c))
    check("edits without a run -> result unknown", c["test_result"] == "unknown")


def test_running_the_added_test_is_observed_with_its_result():
    calls = [bash(1, 'PYTHONPATH=src "$A2_PYTHON" -m pytest '
                     'tests/test_options.py::test_eager_flag_is_processed_once -q',
                  PYTEST_PASS)]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("named run -> executed yes", c["test_executed"] == "yes", json.dumps(c))
    check("named run -> result passed", c["test_result"] == "passed", json.dumps(c))


def test_a_whole_suite_run_does_not_establish_the_added_tests_execution():
    """A suite run executes many tests; nothing in its output says THIS one was among them.
    The first version of this reporter credited it, flagged `whole_suite: true`, and took the
    suite's aggregate `1 passed` as the added test's own result."""
    calls = [bash(1, 'PYTHONPATH=src "$A2_PYTHON" -m pytest -q', "603 passed in 41s")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("suite run -> executed unknown", c["test_executed"] == "unknown", json.dumps(c))
    check("suite run -> no aggregate result borrowed",
          c["test_result"] == "unknown", json.dumps(c))
    check("suite run -> listed as a candidate for the manual review",
          len(c["evidence"]["execution_candidates"]) == 1,
          json.dumps(c["evidence"]["execution_candidates"]))


def test_mentioning_the_variable_is_not_invoking_the_interpreter():
    """`test -n "$A2_PYTHON" && echo ready` names the pin and runs nothing. A classifier keyed
    to the SUBSTRING reports the pinned interpreter as having run successfully -- a positive
    claim about an event that did not happen, which is worse than the absence it replaced."""
    for cmd in ('test -n "$A2_PYTHON" && echo ready', 'echo "$A2_PYTHON"',
                '[ -x "$A2_PYTHON" ]', 'ls -l "$A2_PYTHON"',
                'echo "using $A2_PYTHON" >> notes.md'):
        r = A.repair_check(record(), [bash(1, cmd, "ready")])
        check(f"mention is not an invocation: {cmd}",
              r["pinned_invocations"] == 0 and r["pinned_ran"] == 0, json.dumps(r["evidence"]))
        check(f"mention is not a relevant invocation: {cmd}",
              r["state"] == "no_relevant_invocation", r["state"])
        check(f"mention is counted as a mention: {cmd}",
              A.classify_command(cmd)["mention"] >= 1, json.dumps(A.classify_command(cmd)))


def test_the_variable_in_command_position_is_an_invocation():
    """Negative control on the test above: a classifier that refuses everything is not a
    classifier."""
    for cmd in ('"$A2_PYTHON" --version', 'PYTHONPATH=src "$A2_PYTHON" -m pytest -q',
                '${A2_PYTHON} repro.py'):
        r = A.repair_check(record(), [bash(1, cmd, "3 passed")])
        check(f"command position is an invocation: {cmd}",
              r["pinned_invocations"] == 1, json.dumps(A.classify_command(cmd)))


def test_attempting_pytest_is_not_executing_tests():
    """`No module named pytest` is an ATTEMPT. Counting it as the added test running credits
    the repair with exactly the event it exists to make possible."""
    calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', "No module named pytest")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("failed attempt -> executed unknown", c["test_executed"] == "unknown", json.dumps(c))
    check("failed attempt -> result unknown", c["test_result"] == "unknown")


def test_executing_another_file_is_not_executing_the_added_test():
    calls = [bash(1, '"$A2_PYTHON" -m pytest -q tests/test_other.py', "1 passed")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("another file -> executed unknown", c["test_executed"] == "unknown", json.dumps(c))
    check("another file -> result unknown", c["test_result"] == "unknown", json.dumps(c))


def test_the_word_pytest_in_an_echo_is_not_an_invocation():
    calls = [bash(1, "echo pytest", "pytest")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("echo pytest -> no invocation at all", c["test_executed"] == "no", json.dumps(c))


def test_deselection_and_collection_failure_are_not_execution():
    """The cases that matter are the MIXED ones. A result with no counts at all is unsettled
    whatever the rule, so a test using only those would pass with the guard removed and prove
    nothing. Each result below carries a passing count AND a reason the named test was not the
    thing that passed -- which is the shape pytest actually emits when one file fails to
    collect, or when a node id no longer exists after a revert."""
    for result in ("0 passed, 23 deselected in 0.3s",
                   "ERROR collecting tests/test_options.py\n1 passed in 0.4s",
                   "ERROR: not found: tests/test_options.py::test_gone\n"
                   "1 passed, no tests ran in 0.02s",
                   "/v/bin/python3: No module named pytest\n1 passed"):
        calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', result)]
        c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
        check(f"not execution: {result[:30]!r}", c["test_executed"] == "unknown", json.dumps(c))
        check(f"no result borrowed: {result[:30]!r}", c["test_result"] == "unknown")

    # Negative control: the same shape WITHOUT the disqualifying signal must settle, or the
    # four cases above are passing because nothing ever settles.
    calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', "1 passed in 0.4s")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("the same count WITHOUT a disqualifier settles", c["test_executed"] == "yes",
          json.dumps(c))


def test_a_run_issued_before_the_last_test_edit_cannot_establish_execution():
    """Issue order is not execution order, so this direction is used only to WITHHOLD a claim:
    a run issued before the test file was last written cannot have run the final added test."""
    calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', "1 passed"),
             {"index": 2, "name": "Edit", "input": {"file_path": "tests/test_options.py"},
              "result": "ok"}]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("run before the last edit -> unknown", c["test_executed"] == "unknown", json.dumps(c))
    after = [{"index": 1, "name": "Edit", "input": {"file_path": "tests/test_options.py"},
              "result": "ok"},
             bash(2, f'"$A2_PYTHON" -m pytest -q {NODE}', "1 passed")]
    c2 = A.requirement_compliance(record(patch=NEW_TEST, calls=after), after)
    check("run after the last edit -> yes", c2["test_executed"] == "yes", json.dumps(c2))


def test_redirecting_stderr_is_not_writing_the_test_file():
    """Found by running the reporter over a REAL sweep, not by reading it.

    The write detector asked only whether a command contained the path and any `>`. Every arm
    that ran `pytest tests/test_x.py -q 2>&1 | tail` was therefore recorded as having WRITTEN
    that file, which pushed the boundary past the last test run and demoted every piece of
    execution evidence in the sweep to `unknown`. Naming a file is not writing it, and `2>&1`
    is not a redirect to anything."""
    F = "tests/test_options.py"
    for cmd in (f'PYTHONPATH=src "$A2_PYTHON" -m pytest {F} -q 2>&1 | tail -15',
                f'PYTHONPATH=src "$A2_PYTHON" -m pytest {F} -q >/dev/null 2>&1',
                f'git stash push src/click/core.py >/dev/null 2>&1 && pytest {F} -q',
                f'grep -n foo {F}'):
        check(f"not a write: {cmd[:46]}", A.shell_writes(cmd, F) is False, cmd)
    # Negative control: a detector that says False to everything is not a detector.
    for cmd in (f"cat > {F} <<'EOF'", f'echo x >> {F}', f"sed -i '' 's/a/b/' {F}",
                f'cp /tmp/new.py {F}', f'tee {F} < /tmp/x'):
        check(f"is a write: {cmd[:46]}", A.shell_writes(cmd, F) is True, cmd)


def test_the_boundary_is_the_last_WRITE_not_the_last_mention():
    """End to end through `executed`: a run that names the added test AFTER the last real edit
    settles, even though later commands mention the file while redirecting stderr."""
    calls = [
        {"index": 1, "name": "Edit", "input": {"file_path": "/x/tests/test_options.py"},
         "result": "ok"},
        bash(2, f'PYTHONPATH=src "$A2_PYTHON" -m pytest -q {NODE} 2>&1 | tail -5',
             "1 passed in 0.1s"),
        bash(3, 'PYTHONPATH=src "$A2_PYTHON" -m pytest tests/test_options.py -q 2>&1 | tail',
             "133 passed"),
    ]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("the named run after the last write settles execution",
          c["test_executed"] == "yes", json.dumps(c))
    check("and carries its own result", c["test_result"] == "passed", json.dumps(c))


def test_an_arms_own_negative_control_is_not_the_tests_result():
    """Found in the A2-R sweep itself, not by reading the code.

    Three arm-runs ran `git stash push src/click/core.py && pytest <node> -q` -- reverting
    their own fix to show the new test fails without it. That failure is the POINT: it is how
    a regression test is shown to discriminate. Reading it as the test's result reported two
    arm-runs that passed the hidden checks as having a failing test."""
    good = bash(2, f'PYTHONPATH=src "$A2_PYTHON" -m pytest -q {NODE}', "6 passed in 0.1s")
    ctrl = bash(3, f'git stash push src/click/core.py && PYTHONPATH=src "$A2_PYTHON" '
                   f'-m pytest -q {NODE} 2>&1 | tail', "1 failed, 5 passed in 0.1s")
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=[good, ctrl]), [good, ctrl])
    check("the result comes from the run under the patch",
          c["test_result"] == "passed", json.dumps(c))
    check("execution still settles", c["test_executed"] == "yes", json.dumps(c))
    check("the control is reported in its own right",
          c["discriminating_control"] == "yes", json.dumps(c))
    check("and kept as evidence, not as execution",
          len(c["evidence"]["negative_controls"]) == 1
          and len(c["evidence"]["execution"]) == 1,
          json.dumps({"ctl": c["evidence"]["negative_controls"],
                      "exe": c["evidence"]["execution"]})[:300])

    # A control ALONE settles nothing about the patch: the source was reverted.
    only = A.requirement_compliance(record(patch=NEW_TEST, calls=[ctrl]), [ctrl])
    check("a control alone leaves execution unknown",
          only["test_executed"] == "unknown", json.dumps(only))
    check("and says why", "control" in (only.get("why_execution_unsettled") or ""),
          str(only.get("why_execution_unsettled")))
    check("while still reporting that the test discriminates",
          only["discriminating_control"] == "yes", json.dumps(only))

    # Negative control on the control detector: a plain run is not a control.
    plain = A.requirement_compliance(record(patch=NEW_TEST, calls=[good]), [good])
    check("a plain run is not a control", plain["discriminating_control"] == "no",
          json.dumps(plain))


def test_the_added_tests_own_result_comes_from_an_invocation_that_named_it():
    for result, want in (("1 passed in 0.1s", "passed"), ("1 failed in 0.1s", "failed")):
        calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', result)]
        c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
        check(f"named run result {result[:12]} -> {want}",
              c["test_executed"] == "yes" and c["test_result"] == want, json.dumps(c))
    # A pytest ERROR is a setup or teardown failure: the test body did not run, so neither
    # "executed" nor a result is established. The count is kept as evidence for the reviewer.
    calls = [bash(1, f'"$A2_PYTHON" -m pytest -q {NODE}', "1 error in 0.1s")]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("a pytest ERROR is not an execution", c["test_executed"] == "unknown", json.dumps(c))
    check("a pytest ERROR is kept as evidence",
          c["evidence"]["execution_candidates"][0]["counts"] == {"error": 1},
          json.dumps(c["evidence"]["execution_candidates"]))


# ------------------------------------------------------------------ the registered scorer
# `tally()` lives in `score_compliance.py`, whose own suite needs a host-local pristine
# checkout and is therefore NOT in CI. A2-R reports `a1-scorer-4`'s output beside its own
# observations, so the shape of that output is A2-R's concern -- and these cases are
# host-independent, so they run where the rest of that suite cannot.
def test_tally_excludes_unknowns_from_BOTH_halves_of_the_ratio():
    """A misreading this session made and published: `4/4` with two unknowns was called an
    inflated numerator. It is not. `tally` settles on PASS and FAIL only, so `4/4` means four
    passes among four SETTLED checks, with the unknowns carried separately."""
    from score_compliance import tally, verdict, PASS, FAIL, UNKNOWN, NA
    t = tally({"A": verdict(PASS, "p"), "B": verdict(PASS, "p"), "C": verdict(PASS, "p"),
               "D": verdict(PASS, "p"), "E": verdict(UNKNOWN, "u"), "F": verdict(UNKNOWN, "u")})
    check("four passes among four settled", t["compliance"] == "4/4", t["compliance"])
    check("both unknowns carried separately", t["unknown_count"] == 2, str(t))
    check("unknowns are not in the numerator", t["passed"] == ["A", "B", "C", "D"], str(t))

    # More unknowns must move the DENOMINATOR down, never the numerator up.
    t3 = tally({"A": verdict(PASS, "p"), "B": verdict(FAIL, "f"),
                "C": verdict(UNKNOWN, "u"), "D": verdict(UNKNOWN, "u"),
                "E": verdict(UNKNOWN, "u"), "F": verdict(NA, "n")})
    check("three unknowns -> one pass of two settled", t3["compliance"] == "1/2",
          t3["compliance"])
    check("three unknowns counted", t3["unknown_count"] == 3, str(t3))
    check("not-applicable is its own state", t3["not_applicable_count"] == 1, str(t3))

    # Every check unsettled: a ratio of nothing, and not a silent 0/0 pass.
    t0 = tally({"A": verdict(UNKNOWN, "u"), "B": verdict(UNKNOWN, "u")})
    check("nothing settled -> 0/0", t0["compliance"] == "0/0", t0["compliance"])
    check("nothing settled -> both unknown", t0["unknown_count"] == 2, str(t0))


def test_relevance_is_never_machine_decided():
    """Whether the added test covers THIS bug is not decidable from a diff. Twelve patches is
    a readable number; the field stays `unreviewed` until a human fills it."""
    c = A.requirement_compliance(record(patch=NEW_TEST), [])
    check("relevance unreviewed", c["relevance"] == "unreviewed", json.dumps(c))


# ------------------------------------------------- the runner's real schema and layout
def _runner_arm(arm, passed, num_turns, terminal="completed", patch=None,
                calls=None, scorer_timed_out=False, patch_bytes=None):
    """One arm-run shaped EXACTLY as `run_arms_isolated.py` writes it.

    Not as the reporter wished it were written. The published reporter read
    `record["functional"]`, `record["num_turns"]` and `record["patch"]`; the runner writes
    `record["scored"]["passed"]`, `record["result"]["num_turns"]`, and the patch as a sidecar
    file. Every earlier fixture here was invented by the same hand as the reader, so they
    agreed, and the report would have counted every genuine pass as no pass at all.
    """
    rec = {
        "arm": arm,
        "started_utc": "2026-09-19T00:00:00+00:00",
        "wall_clock_s": 410.2,
        "forced_verdict": None,
        "result": {"num_turns": num_turns, "subtype": "success", "is_error": False,
                   "terminal_reason": terminal,
                   "usage": {"input_tokens": 1000, "output_tokens": 100}},
        "scored": None if passed is None else {
            "exit": 0 if passed else 1,
            "summary": "27 passed in 0.03s" if passed else "2 failed, 25 passed in 0.04s",
            "failing_instances": 0 if passed else 2, "failing_functions": 0 if passed else 1,
            "scorer_version": "a1-functional-2",
            "passed": bool(passed), "timed_out": scorer_timed_out},
        "terminal": {"terminal": terminal, "meaning": "…",
                     "scored": True, "truncated": terminal in ("max_turns", "timeout")},
        "usage": {"input_tokens": 1000, "output_tokens": 100,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "tool_calls": calls or [],
        "patch_bytes": (len(patch) if patch is not None else 0)
        if patch_bytes is None else patch_bytes,
        "patch_touches_src": bool(patch and "src/click/" in patch),
    }
    return rec


def _runner_layout(tmp: Path, ceiling=45, task="k1", arms=None) -> Path:
    """`<scratch>/c<n>/run-<task>/attempt1/{records.json,arms/<arm>/patch.diff}`."""
    at = tmp / f"c{ceiling}" / f"run-{task}" / "attempt1"
    at.mkdir(parents=True, exist_ok=True)
    recs = []
    for arm, rec, patch in arms:
        (at / "arms" / arm).mkdir(parents=True, exist_ok=True)
        if patch is not None:
            (at / "arms" / arm / "patch.diff").write_text(patch)
        recs.append(rec)
    (at / "records.json").write_text(json.dumps(
        {"task": task, "attempt": 1, "seed": 20260914, "records": recs}))
    return tmp


SRC_AND_TEST = ("diff --git a/src/click/core.py b/src/click/core.py\n"
                "--- a/src/click/core.py\n+++ b/src/click/core.py\n"
                "@@ -1,3 +1,4 @@\n+    self._done = True\n"
                "diff --git a/tests/test_options.py b/tests/test_options.py\n"
                "--- a/tests/test_options.py\n+++ b/tests/test_options.py\n"
                "@@ -10,3 +10,4 @@ def test_existing():\n"
                "+def test_eager_flag_is_processed_once(runner):\n+    assert 1\n")


def test_the_reporter_reads_the_runners_actual_record_format():
    """THE integration case. A pass, a fail and an unmeasured arm-run, written the way the
    runner writes them, read the way the reporter reads them."""
    tmp = _tmp()
    node = "tests/test_options.py::test_eager_flag_is_processed_once"
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", True, 5, "completed", SRC_AND_TEST,
                                 [bash(1, f'PYTHONPATH=src "$A2_PYTHON" -m pytest -q {node}',
                                       "1 passed in 0.1s")]), SRC_AND_TEST),
        # NEGATIVE CONTROL: a genuine functional FAILURE must read as `fail`. A reader that
        # cannot find the verdict returns falsy for both, and then every arm-run looks alike.
        ("nexus", _runner_arm("nexus", False, 45, "max_turns", SRC_AND_TEST), SRC_AND_TEST),
        # no `scored` block at all: UNMEASURED, and never a failure.
        ("notes", _runner_arm("notes", None, 12, "completed", ""), ""),
    ])
    cells = {c["arm"]: c for c in A.read_cells(tmp)}
    check("three arm-runs read", len(cells) == 3, str(sorted(cells)))

    b = cells.get("baseline", {})
    check("a pass is read from scored.passed", b.get("functional") == "pass", str(b.get("functional")))
    check("turns are read from result.num_turns", b.get("num_turns") == 5, str(b.get("num_turns")))
    check("the patch sidecar is loaded", b.get("patch_provenance") == "sidecar",
          str(b.get("patch_provenance")))
    check("source diff observed from the sidecar",
          b.get("requirement", {}).get("source_diff") == "yes", json.dumps(b.get("requirement")))
    check("test addition observed from the sidecar",
          b.get("requirement", {}).get("test_addition") == "yes", json.dumps(b.get("requirement")))
    check("the named run settles execution",
          b.get("requirement", {}).get("test_executed") == "yes", json.dumps(b.get("requirement")))

    n = cells.get("nexus", {})
    check("a FAIL is read as fail, not as unknown and not as pass",
          n.get("functional") == "fail", str(n.get("functional")))
    check("its turns are read too", n.get("num_turns") == 45, str(n.get("num_turns")))

    t = cells.get("notes", {})
    check("a missing score is UNMEASURED, not failed", t.get("functional") == "unknown",
          str(t.get("functional")))
    check("and says why", bool(t.get("functional_unknown_because")),
          str(t.get("functional_unknown_because")))

    check("compliance is NOT_SCORED, not unknown",
          all(c["scorer_a1_4"] == A.NOT_SCORED for c in cells.values()),
          str([c["scorer_a1_4"] for c in cells.values()]))
    check("and no unknown count is invented",
          all(c["scorer_a1_4_unknown"] is None for c in cells.values()),
          str([c["scorer_a1_4_unknown"] for c in cells.values()]))
    check("the functional count is 1, not 0",
          sum(1 for c in cells.values() if c["functional"] == "pass") == 1,
          str([c["functional"] for c in cells.values()]))


def test_an_empty_patch_is_a_measurement_and_a_missing_one_is_not():
    """`patch.diff` present and empty means the arm-run changed NOTHING -- a finding. No
    `patch.diff` at all means nothing is known. Collapsing them turns 'it did not touch src'
    into 'we cannot say', or worse, the reverse."""
    tmp = _tmp()
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", False, 30, "completed", ""), ""),
        ("nexus", _runner_arm("nexus", False, 30, "completed", None), None),
    ])
    cells = {c["arm"]: c for c in A.read_cells(tmp)}
    e = cells["baseline"]
    check("an empty patch is provenance 'empty'", e["patch_provenance"] == "empty",
          e["patch_provenance"])
    check("an empty patch MEASURES no source diff",
          e["requirement"]["source_diff"] == "no", json.dumps(e["requirement"]))
    m = cells["nexus"]
    check("a missing patch is provenance 'absent'", m["patch_provenance"] == "absent",
          m["patch_provenance"])
    check("a missing patch leaves source diff UNKNOWN",
          m["requirement"]["source_diff"] == "unknown", json.dumps(m["requirement"]))
    check("and says why", "patch.diff" in (m["requirement"].get("why_unknown") or ""),
          str(m["requirement"].get("why_unknown")))


def test_a_record_claiming_a_patch_with_no_sidecar_is_flagged():
    """The two sources disagree. Neither is silently preferred."""
    tmp = _tmp()
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", True, 5, "completed", None, patch_bytes=4231),
         None)])
    c = A.read_cells(tmp)[0]
    check("inconsistency is named", c["patch_provenance"] == "inconsistent_missing_sidecar",
          c["patch_provenance"])
    check("and the observation is withheld",
          c["requirement"]["source_diff"] == "unknown", json.dumps(c["requirement"]))


def test_a_scorer_that_timed_out_did_not_measure_a_failure():
    tmp = _tmp()
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", False, 5, "completed", "",
                                 scorer_timed_out=True), "")])
    c = A.read_cells(tmp)[0]
    check("a timed-out scorer is unmeasured, not a fail", c["functional"] == "unknown",
          str(c["functional"]))
    check("and says so", "timed out" in (c["functional_unknown_because"] or ""),
          str(c["functional_unknown_because"]))


def test_a_row_the_scorer_could_not_run_on_is_not_a_compliance_result():
    """`score_a2r_compliance` writes a null ratio and a reason when the pristine tree is gone.
    Reading that as a score would turn a missing instrument into a compliance finding -- the
    same error as reading a timed-out functional scorer as a failure."""
    tmp = _tmp()
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", True, 5, "completed", ""), "")])
    (tmp / A.COMPLIANCE_FILES[0]).write_text(json.dumps(
        [{"task": "k1", "arm": "baseline", A.COMPLIANCE_RATIO: None,
          A.COMPLIANCE_UNKNOWN: None, "scorer_ran": False,
          "why_not": "the recorded pristine tree is gone"}]))
    c = A.read_cells(tmp)[0]
    check("a null ratio reads as not_scored", c["scorer_a1_4"] == A.NOT_SCORED,
          str(c["scorer_a1_4"]))
    check("and the reason travels with it",
          "pristine tree is gone" in c["scorer_a1_4_source"], c["scorer_a1_4_source"])


def test_the_writer_and_the_reader_share_one_set_of_field_names():
    """The compliance artifact's keys are defined in the reader and imported by the writer, so
    a rename cannot leave the writer emitting a key nothing looks for. This asserts the import
    is real rather than two copies that happen to agree today."""
    import score_a2r_compliance as SC
    check("ratio field is shared", SC.COMPLIANCE_RATIO is A.COMPLIANCE_RATIO)
    check("unknown field is shared", SC.COMPLIANCE_UNKNOWN is A.COMPLIANCE_UNKNOWN)
    check("filename is shared", SC.COMPLIANCE_FILES is A.COMPLIANCE_FILES)


def test_a_compliance_artifact_is_read_when_one_exists():
    """Negative control on `not_scored`: a column that always says not_scored is not a
    reading."""
    tmp = _tmp()
    _runner_layout(tmp, arms=[
        ("baseline", _runner_arm("baseline", True, 5, "completed", ""), "")])
    (tmp / "compliance-a2r.json").write_text(json.dumps(
        [{"task": "k1", "arm": "baseline", "requirement_compliance": "4/4",
          "unknown_count": 2}]))
    c = A.read_cells(tmp)[0]
    check("the artifact's ratio is read", c["scorer_a1_4"] == "4/4", str(c["scorer_a1_4"]))
    check("its unknown count is read", c["scorer_a1_4_unknown"] == 2,
          str(c["scorer_a1_4_unknown"]))
    check("provenance names the artifact", "artifact" in c["scorer_a1_4_source"],
          c["scorer_a1_4_source"])


# ------------------------------------------------------------------ separate accounting
def _stub_configs(tmp: Path, ceiling: int) -> dict:
    d = tmp / "_configs"
    d.mkdir(parents=True, exist_ok=True)
    bench = tmp / "_bench"
    bench.mkdir(parents=True, exist_ok=True)
    (bench / "test-prompts.json").write_text(json.dumps(
        {"consult": "CONSULT", "environment": "ENV", "tails": {"default": "TAIL"},
         "tasks": {t: {"set": "development", "tail": "default", "body": f"BODY-{t}"}
                   for t in ("k1", "k2", "k3", "k4")}}))
    f = d / f"calib-config-{ceiling}.json"
    f.write_text(json.dumps({
        "source_clone": str(tmp), "pytest_python": sys.executable, "venv_python": str(tmp),
        "nexus_server": str(tmp), "config_version": RC.CONFIG_VERSION, "max_turns": ceiling,
        "corpus_digest": "0123456789abcdef", "bench": str(bench),
        "prompts": "test-prompts.json"}))
    return {ceiling: f}


def _stub_a2r(tmp: Path, threshold: int, tokens_per_arm: int, ceiling: int = 45,
              seed_reserve=None, no_usage_at=None, launches: list | None = None):
    """Drive the REAL A2-R path with a stubbed runner: no model, no sandbox, no network."""
    cfgs = _stub_configs(tmp, ceiling)
    real = (RC.subprocess.run, RC.build_fixture_for, RC.config_for, RC.INITIAL_ROW_RESERVE,
            R2.SCOPE, os.environ.get("A1_CONFIG"))
    digest = RC.sched.load(RC.SCHEDULE).schedule_digest
    idents = {t: ident.expected(a1_config.load(cfgs[ceiling]), t, digest, ceiling)
              for t in ("k1", "k2", "k3", "k4")}
    blank = {k: None for k in ("input_tokens", "output_tokens",
                               "cache_read_input_tokens", "cache_creation_input_tokens")}

    def fake_run(cmd, **kw):
        root, task, attempt = Path(cmd[2]), cmd[3], cmd[4]
        if launches is not None:
            launches.append(task)
        at = root / f"run-{task}" / f"attempt{attempt}"
        recs = []
        for arm in ("baseline", "nexus", "notes"):
            (at / "arms" / arm).mkdir(parents=True, exist_ok=True)
            recs.append({"arm": arm,
                         "usage": dict(blank) if no_usage_at == (task, arm)
                         else {"input_tokens": tokens_per_arm},
                         "terminal": {"terminal": "completed", "scored": True}})
        (at / "records.json").write_text(json.dumps(
            {"task": task, "attempt": int(attempt), "schedule_digest": digest,
             "identity": idents[task], "records": recs}))

        class D:
            returncode = 0
        return D()

    RC.subprocess.run = fake_run
    RC.build_fixture_for = lambda *a, **k: None
    RC.config_for = lambda c: cfgs[c]
    if seed_reserve is not None:
        RC.INITIAL_ROW_RESERVE = seed_reserve
    # The pin check reads the host configuration; give it this temporary one so the test
    # exercises the real refusal path rather than skipping it.
    os.environ["A1_CONFIG"] = str(cfgs[ceiling])
    R2.SCOPE = RC.Scope(name=R2.SCOPE.name, cap_tokens=threshold,
                        cap_label=R2.SCOPE.cap_label, soft=True,
                        summary_name=R2.SCOPE.summary_name,
                        partial_note=R2.SCOPE.partial_note,
                        config_locator=lambda c: cfgs[c])
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = R2.main(["run_a2r.py", str(tmp), "--ceiling", str(ceiling)])
    finally:
        (RC.subprocess.run, RC.build_fixture_for, RC.config_for, RC.INITIAL_ROW_RESERVE,
         R2.SCOPE, old_cfg) = real
        if old_cfg is None:
            os.environ.pop("A1_CONFIG", None)
        else:
            os.environ["A1_CONFIG"] = old_cfg
    s = tmp / "a2r-summary.json"
    return rc, (json.loads(s.read_text()) if s.exists() else None), buf.getvalue()


def test_a2r_never_reads_the_closed_calibrations_configuration():
    """The v2 `calib-config-{30,45,60}.json` are the closed sweep's frozen identity, and
    `preflight-a2.py` checks their digests against LAUNCH-A2.md. A `calib-v3` configuration
    written over `calib-config-45.json` would destroy the provenance of a published sweep --
    and it is the natural thing to do, because the driver's own locator names that file."""
    for c in (30, 45, 60):
        name = R2.config_for(c).name
        check(f"A2-R c{c} config is not the calibration's",
              not name.startswith("calib-config-"), name)
        check(f"A2-R c{c} config is named for A2-R", name == f"a2r-config-{c}.json", name)
    check("the scope carries its own locator, so the driver cannot fall back",
          R2.SCOPE.config_locator is R2.config_for)
    check("the calibration's own locator is unchanged",
          RC.config_for(45).name == "calib-config-45.json", RC.config_for(45).name)


def test_the_registered_threshold_is_twenty_million_and_soft():
    check("threshold is 20M", R2.SOFT_LAUNCH_THRESHOLD == 20_000_000,
          str(R2.SOFT_LAUNCH_THRESHOLD))
    check("threshold is declared soft", R2.SCOPE.soft is True)
    check("threshold is not called a cap", R2.SCOPE.cap_label == "soft launch threshold",
          R2.SCOPE.cap_label)


def test_a2r_completes_and_writes_its_own_summary():
    tmp = _tmp()
    rc, s, _ = _stub_a2r(tmp, threshold=10_000_000, tokens_per_arm=1000)
    check("completion returns 0", rc == 0, str(rc))
    check("writes a2r-summary.json", s is not None)
    check("does NOT write the calibration's summary",
          not (tmp / "calibration-summary.json").exists())
    if s:
        check("summary names its own scope", s["scope"].startswith("A2-R"), s["scope"])
        check("summary records the threshold as soft", s["cap_is_soft"] is True)
        check("summary counts 12 arm-runs", s["arm_runs"] == 12, str(s["arm_runs"]))
        check("summary reports completion", s["stopping_reason"] == "completed",
              s["stopping_reason"])


def test_a2r_refuses_the_next_row_at_the_soft_threshold():
    tmp = _tmp()
    rc, s, out = _stub_a2r(tmp, threshold=6_000, tokens_per_arm=1000, seed_reserve=3_000)
    check("budget stop returns 0", rc == 0, str(rc))
    check("stopping reason is budget", s and s["stopping_reason"] == "budget",
          json.dumps(s)[:200] if s else "no summary")
    check("a partial A2-R is not described by a selection rule",
          "§5" not in out and "selecting a ceiling" not in out, out[-400:])


def test_a2r_refuses_to_continue_on_unresolved_consumption():
    """An arm-run with the runner's four-null usage shape is consumption of unknown size.

    The exit code alone does not test this. A sweep that runs every row and reports the
    problem at the END exits 1 too -- the terminal ledger check catches it -- so asserting
    `rc == 1` passes while the PRE-LAUNCH block does nothing and three more rows are paid for.
    What is asserted here is that the rows after the unaccounted one were never launched."""
    tmp, launched = _tmp(), []
    rc, s, out = _stub_a2r(tmp, threshold=10_000_000, tokens_per_arm=1000,
                           no_usage_at=("k1", "notes"), launches=launched)
    check("unresolved -> nonzero exit", rc == 1, str(rc))
    check("unresolved -> reason recorded",
          s and s["stopping_reason"] == "unresolved_accounting",
          json.dumps(s)[:200] if s else "no summary")
    check("unresolved -> not counted as zero", s and s["unresolved_arm_runs"], json.dumps(s))
    check("unresolved -> the NEXT row is never launched",
          launched == ["k1"], f"launched {launched}")
    check("unresolved -> the refusal is printed before the next row",
          "STOPPED BEFORE THE NEXT ROW" in out, out[-300:])


def test_a_fully_accounted_a2r_sweep_launches_every_row():
    """Negative control on the test above: a block that fires on every sweep is not a block."""
    launched = []
    _stub_a2r(_tmp(), threshold=10_000_000, tokens_per_arm=1000, launches=launched)
    check("accounted sweep launches all four rows",
          sorted(launched) == ["k1", "k2", "k3", "k4"], f"launched {launched}")


def test_a2r_refuses_a_scratch_that_belongs_to_the_calibration():
    """`consumed()` globs a whole scratch tree. Two sweeps in one directory are one budget,
    silently, and the closed calibration's unresolved arm-run would block A2-R's first row."""
    tmp = _tmp()
    (tmp / "calibration-summary.json").write_text("{}")
    check("a calibration scratch is refused", R2.scope_conflict(tmp) is not None)


def test_a2r_refuses_to_nest_inside_a_calibration_scratch():
    tmp = _tmp()
    (tmp / "calibration-summary.json").write_text("{}")
    inner = tmp / "a2r"
    inner.mkdir()
    why = R2.scope_conflict(inner)
    check("nesting is refused", why is not None and "inside" in why, str(why))


def test_a_clean_directory_is_accepted():
    """Negative control: a refusal that fires on everything is not a check."""
    check("a fresh directory is not refused", R2.scope_conflict(_tmp()) is None)


def test_a2r_refuses_the_calibrations_grid_flag():
    try:
        R2.main(["run_a2r.py", str(_tmp()), "--ceilings", "30,45"])
    except SystemExit as e:
        check("grid flag refused", "ONE ceiling" in str(e), str(e))
    else:
        FAILS.append("grid flag refused: --ceilings was accepted")


def test_an_unpinned_interpreter_refuses_before_spending():
    old = os.environ.get("A1_CONFIG")
    os.environ["A1_CONFIG"] = "/nonexistent/a1-config.json"
    try:
        why = R2.pin_absent()
        check("no configuration -> pin absent -> refuse", why is not None, str(why))
    finally:
        if old is None:
            os.environ.pop("A1_CONFIG", None)
        else:
            os.environ["A1_CONFIG"] = old


# ------------------------------------------------------------------ the report
def test_the_report_applies_no_selection_rule():
    tmp = _tmp()
    _stub_a2r(tmp, threshold=10_000_000, tokens_per_arm=1000)
    text = A.render(A.build(tmp))
    check("no selection rule is applied", "selection rule is applied" in text, text[:200])
    for word in ("selected", "qualif", "eligib"):
        check(f"report does not select ({word})",
              word not in text.lower().replace("selects no ceiling", "")
              .replace("no ceiling selected", "").replace("with none selected", ""),
              [ln for ln in text.splitlines() if word in ln.lower()][:2])


def test_the_report_carries_the_closed_sweeps_unresolved_consumption():
    """A2-R's own ledger being clean must not read as the project's being clean."""
    tmp = _tmp()
    _stub_a2r(tmp, threshold=10_000_000, tokens_per_arm=1000)
    text = A.render(A.build(tmp))
    check("closed sweep still outstanding", "c60/run-k1/attempt1/arms/baseline" in text,
          text[-600:])
    check("not resolved by a new allowance", "does not resolve it" in text)


def test_the_report_refuses_to_pool_two_ceilings():
    rep = {"scratch": "x", "ceilings": [30, 45], "cells": [], "ledger": {},
           "expected_cells": 12}
    text = A.render(rep)
    check("two ceilings -> refused", "REFUSED TO SUMMARISE" in text, text[:300])


def main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    for f in FAILS:
        print(f"FAIL  {f}")
    print(f"{len(tests) - len(FAILS)}/{len(tests)} counterexample groups pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
