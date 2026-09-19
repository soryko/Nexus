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
    check("echo $A2_PYTHON -> counted as a lookup",
          A.classify_command('echo "$A2_PYTHON"')["lookup"] == 1,
          json.dumps(A.classify_command('echo "$A2_PYTHON"')))


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


def test_a_whole_suite_run_is_flagged_rather_than_silently_counted():
    """A full-suite run does execute the addition, but it does not name it. The distinction
    belongs to the reader."""
    calls = [bash(1, 'PYTHONPATH=src "$A2_PYTHON" -m pytest -q', PYTEST_PASS)]
    c = A.requirement_compliance(record(patch=NEW_TEST, calls=calls), calls)
    check("suite run -> executed yes", c["test_executed"] == "yes", json.dumps(c))
    check("suite run -> flagged as whole-suite",
          c["evidence"]["execution"][0]["whole_suite"] is True,
          json.dumps(c["evidence"]["execution"]))


def test_relevance_is_never_machine_decided():
    """Whether the added test covers THIS bug is not decidable from a diff. Twelve patches is
    a readable number; the field stays `unreviewed` until a human fills it."""
    c = A.requirement_compliance(record(patch=NEW_TEST), [])
    check("relevance unreviewed", c["relevance"] == "unreviewed", json.dumps(c))


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
