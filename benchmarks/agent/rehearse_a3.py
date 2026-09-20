"""A model-free integration rehearsal of the DOWNSTREAM A3 path. No model, no arm-run.

    saved records -> REAL compliance scoring -> normalisation -> decision report

**Not the whole path, and it used to say it was.** Execution is stubbed by WRITING the
records: this file produces `record.json`, `functional.json`, `outcomes.json` and the traces
itself. That validates everything downstream of the artifacts and NOTHING about the thing
that produces them -- it cannot establish that the real runner emits what the reporter
consumes, nor that the prompt whose digest a preflight froze is the prompt that reaches the
model. `rehearse_a3_production.py` covers that half, by driving `run_a3.py` with only the
model endpoint replaced.

What this file still earns its place for: it is fast, it is deterministic, it needs no
`sandbox-exec` and no fixtures, so it runs on a Linux CI runner -- and the four defects it
caught were all in the join between the artifacts and the decision.

`test_a3_decision.py` checks the decision table against synthetic `ArmRun` objects, and that
is not enough on its own. The fix-leakage scan had tests too. Its defect was in the join --
no `fixtures.json` recorded a clone, so the scan returned an empty set and an empty set
matches nothing -- and the tests were on either side of the join, never across it. This
rehearsal is across it: the records are written to disk, the real scorer reads them back, and
the decision comes out the far end.

Stubbed execution means the arm-runs are written rather than run. Everything downstream of
the trace is the production path: `score_compliance.score` applies the patch, runs pytest
over three trees and returns its real verdicts; `a3_pipeline.normalise` maps those artifacts
onto the decision table's inputs; `a3_decision.decide` decides.

    python3 rehearse_a3.py [--out <dir>] [--json <path>]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent
sys.path.insert(0, str(BENCH))

import a3_ledger as L                                                     # noqa: E402
import a3_pipeline as P                                                   # noqa: E402
import schedule as S                                                      # noqa: E402
from a3_decision import A3_PLAN, Plan, render                             # noqa: E402

POLICIES = ("A", "B")
SEED = 20260919

#: Two predeclared task-relevant facts per task, each an EQUIVALENCE CLASS of two memories.
#: Either member satisfies the fact, which is the repair for treating memory IDENTITY as the
#: requirement: a bounded policy reaching the redundant member has lost nothing.
FACTS = {t: {f"{t}-mechanism": [f"{t}-m-primary", f"{t}-m-equivalent"]}
         for t in A3_PLAN.tasks}

#: The bodies those memory ids carry. Long enough that `notes_content_delivered` recognises
#: the prose rather than a mention of it -- the defect that credited three nexus runs for a
#: directory listing.
BODIES = {
    mid: (f"Memory {mid}: the {t} mechanism resolves its value through the documented "
          f"precedence chain, and the sentinel it stores is resolved by the caller rather "
          f"than raised at parse time, which is the distinction this task turns on.")
    for t in A3_PLAN.tasks for mid in (f"{t}-m-primary", f"{t}-m-equivalent")
}


# ---------------------------------------------------------------------------------------
# The fixture: a tiny real package with a real defect, so the regression probe has a real
# transition to measure. absent on the baseline -> fails with the test hunks alone -> passes
# on the full patch.
# ---------------------------------------------------------------------------------------

CORE_BUGGY = "def add_one(value):\n    return value\n"
CORE_FIXED = "def add_one(value):\n    return value + 1\n"
TEST_EXISTING = "from pkg.core import add_one\n\n\ndef test_add_one_is_callable():\n    assert add_one(0) is not None\n"
TEST_ADDED = "\n\ndef test_add_one_adds_one():\n    assert add_one(1) == 2\n"


#: Interpreters that might run the regression probe, best first. The probe shells out to
#: pytest and then READS ITS JUNIT XML, so it needs pytest *and* a working `pyexpat`.
#: `.venv-sqlite` -- the venv this suite otherwise runs under, because the product needs a
#: newer SQLite than the project venv links -- has pytest and no expat, and the probe
#: therefore returns `unknown` on every tree. `unknown` is the honest answer and it is also
#: indistinguishable, in the final report, from a policy difference nobody measured. So the
#: interpreter is resolved explicitly and named in the report, never inherited from whatever
#: `sys.executable` happens to be.
PROBE_CANDIDATES = (str(REPO / ".venv" / "bin" / "python"),
                    "/opt/homebrew/bin/python3", "/usr/bin/python3")


class NoProbeInterpreter(RuntimeError):
    """Raised rather than running a rehearsal whose executed check cannot execute."""


def check_host_can_read_reports() -> None:
    """THIS process must be able to parse the JUnit XML. Two interpreters, again.

    `regression_discriminates` shells out to `python -m pytest --junit-xml=...` and then
    parses that file with `ElementTree` IN THE CALLING PROCESS. So the pytest interpreter
    and the XML interpreter are different interpreters, and only the first is a parameter.
    The first version of this check pinned the parameter and left the caller alone, which
    fixed nothing: `.venv-sqlite` -- the venv this suite runs under, because the product
    needs a newer SQLite than the project venv links -- has pytest and no `pyexpat`, so
    every tree came back `junit_read: No module named expat` and every task reported
    `required_work: indeterminate`. That is the honest answer to a question nobody asked,
    and in the final report it is indistinguishable from a policy difference.

    Same shape as the interpreter-pinning defect A2-R closed: a gate probed one interpreter
    and the thing being measured ran under another.
    """
    try:
        import pyexpat                                                     # noqa: F401
    except Exception as exc:
        raise NoProbeInterpreter(
            f"this interpreter ({sys.executable}) cannot parse the probe's JUnit report "
            f"({type(exc).__name__}: {exc}). The regression probe would return `unknown` on "
            f"every tree and the rehearsal would measure nothing. Run it under an "
            f"interpreter with pyexpat -- {PROBE_CANDIDATES[0]} has it.") from exc


def probe_python(candidates: tuple[str, ...] = PROBE_CANDIDATES) -> str:
    """The SUBPROCESS interpreter: it needs pytest. Refuses if there is none.

    Refusing is the point. A rehearsal that runs with a crippled probe reports
    `required_work: indeterminate` on all four tasks and looks like a finding.
    """
    check_host_can_read_reports()
    checked = []
    for c in (*candidates, sys.executable):
        if not Path(c).exists():
            checked.append(f"{c}: absent")
            continue
        r = subprocess.run([c, "-c", "import pytest"], capture_output=True, text=True)
        if r.returncode == 0:
            return c
        checked.append(f"{c}: {(r.stderr or '').strip().splitlines()[-1:]}")
    raise NoProbeInterpreter(
        "no interpreter with pytest for the probe subprocess. Checked: " + "; ".join(checked))


def usage_block(total: int) -> dict:
    """`total` split across the four registered categories, deterministically."""
    out = total // 100
    cache = total // 4
    return {"input_tokens": total - out - cache, "output_tokens": out,
            "cache_read_input_tokens": cache, "cache_creation_input_tokens": 0}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"GIT_AUTHOR_NAME": "r", "GIT_AUTHOR_EMAIL": "r@r", "GIT_COMMITTER_NAME": "r",
           "GIT_COMMITTER_EMAIL": "r@r", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          env=env, check=True)


def make_pristine(root: Path) -> Path:
    """The pre-fix tree every arm-run is scored against."""
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "core.py").write_text(CORE_BUGGY)
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_pkg.py").write_text(TEST_EXISTING)
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = ['src']\ntestpaths = ['tests']\n")
    return root


def make_patches(work: Path) -> dict[str, str]:
    """Real unified diffs, produced by git so the scorer's `git apply` cannot reject them.

    `full`     the fix and a discriminating test -- what the task asks for.
    `src_only` the fix and no test. A2-R's k1/nexus passed the hidden checks in this shape,
               which is why required test work is a dimension of its own.
    """
    repo = work / "patchsrc"
    make_pristine(repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pre")

    (repo / "src" / "pkg" / "core.py").write_text(CORE_FIXED)
    src_only = _git(repo, "diff").stdout

    (repo / "tests" / "test_pkg.py").write_text(TEST_EXISTING + TEST_ADDED)
    full = _git(repo, "diff").stdout
    return {"full": full, "src_only": src_only, "empty": ""}


# ---------------------------------------------------------------------------------------
# Stubbed execution -- writes exactly the artifacts a real arm-run writes.
# ---------------------------------------------------------------------------------------

def _trace(task: str, policy: str, *, deliver: list[str], searches: int, fetches: int,
           empty_searches: int = 0, edit: bool = True, ran_test: bool = True,
           deliver_late: bool = False) -> str:
    """A real trace in the runner's own JSONL shape.

    Consultation comes back BEFORE the edit, because that is what the compliance scorer
    measures: content that arrived after the first source edit delivered nothing the edit
    could have used.
    """
    lines, ev = [], 0

    def call(name: str, inp: dict, result: str) -> None:
        nonlocal ev
        tid = f"t{ev}"
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result}]}}))
        ev += 1

    def consult() -> None:
        for i in range(searches):
            hits = [{"memory_id": m, "excerpt": BODIES[m][:60]} for m in deliver] or []
            call("mcp__nexus__search", {"query": f"{task} mechanism {i}"},
                 json.dumps({"hits": hits}))
        for i in range(fetches):
            mid = deliver[i] if i < len(deliver) else None
            call("mcp__nexus__get", {"memory_id": mid or "unrelated"},
                 json.dumps({"memory_id": mid,
                             "content": BODIES.get(mid, "unrelated body")}))

    if not deliver_late:
        consult()
    # A second search that returns nothing is a real bound violation that delivers almost
    # no text. It is the only way to vary bound compliance while holding delivered volume
    # fixed, and without it "a violation changes the decision" would be measuring the extra
    # bytes the violation carried rather than the violation.
    for i in range(empty_searches):
        call("mcp__nexus__search", {"query": f"{task} retry {i}"}, json.dumps({"hits": []}))
    if edit:
        call("Edit", {"file_path": "src/click/core.py", "old_string": "a", "new_string": "b"},
             "ok")
    if deliver_late:
        consult()                      # AFTER the edit: retrieved, and too late to inform it
    if ran_test:
        # §5.3 asks whether the ARM-RUN executed its test, which the offline probe cannot
        # say. A stub whose agent never runs pytest fails the criterion, correctly.
        call("Bash", {"command": "PYTHONPATH=src python -m pytest tests/test_pkg.py -q"},
             "1 passed")
    lines.append(json.dumps({"type": "result", "subtype": "success",
                             "result": "DONE. the behaviour is fixed and covered.",
                             "num_turns": 2 + ev}))
    return "\n".join(lines) + "\n"


def write_arm_run(root: Path, task: str, attempt: int, policy: str, patches: dict,
                  *, patch: str = "full", functional: bool | None = True,
                  deliver: list[str] | None = None, searches: int = 1, fetches: int = 3,
                  empty_searches: int = 0,
                  total_tokens: int | None = 100_000, accounted: bool = True,
                  stale_exposed: bool | None = False,
                  stale_adopted: bool | None = False,
                  ran_test: bool = True, deliver_late: bool = False,
                  artifacts: bool = True, reviewed: bool | None = True) -> Path:
    """One saved arm-run, at an identity distinct in (task, attempt, policy).

    The launch marker goes down FIRST, exactly as the runner writes it, so `artifacts=False`
    reproduces the interrupted launch: a marker, spent tokens, and nothing else.
    """
    d = P.arm_run_dir(root, task, attempt, policy)
    d.mkdir(parents=True, exist_ok=True)
    L.mark_launch(root, task, attempt, policy, identity={"config_version": "rehearsal"},
                  max_turns=45, wall_clock_s=600, prompt_digest=f"stub-{policy}")
    if not artifacts:
        return d
    # A is the current policy and consults without a bound, so the default delivers the
    # whole equivalence class. B's overrides narrow it. If both delivered the same text the
    # cost dimension would compare a number with itself and the threshold would test
    # nothing.
    deliver = list(FACTS[task][f"{task}-mechanism"]) if deliver is None else deliver
    (d / "patch.diff").write_text(patches[patch])
    (d / "trace.jsonl").write_text(_trace(task, policy, deliver=deliver, searches=searches,
                                          fetches=fetches, empty_searches=empty_searches,
                                          ran_test=ran_test, deliver_late=deliver_late))
    # A record with no usage is UNRESOLVED accounting, not zero tokens.
    # The REAL four categories `usage_tokens` sums. Writing `total_tokens` instead made the
    # ledger refuse every row -- correctly: a usage block it cannot read is not a
    # measurement, and the first run of this rehearsal against the ledger stopped the sweep
    # after one pair because of it.
    record = {"task": task, "policy": policy, "attempt": attempt,
              "accounting_resolved": accounted}
    if total_tokens is not None:
        record["usage"] = usage_block(total_tokens)
    (d / "record.json").write_text(json.dumps(record, indent=1))
    if functional is not None:
        (d / "functional.json").write_text(json.dumps({"passed": functional}))
    (d / "outcomes.json").write_text(json.dumps(
        {"stale_exposed": stale_exposed, "stale_adopted": stale_adopted}))
    # A HUMAN review of the added test's relevance. Written here because the downstream
    # rehearsal's job is to exercise the decision path, and an unreviewed sweep is
    # indeterminate by design -- which would make every scenario below indeterminate for a
    # reason unrelated to the scenario. `reviewed=None` omits it and exercises the gate.
    if reviewed is not None:
        (d / "relevance.json").write_text(json.dumps(
            {"relevant": reviewed, "reviewer": "rehearsal-fixture",
             "note": "synthetic: no human read this test"}))
    return d


# ---------------------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------------------

def cheaper_B(plan: Plan = A3_PLAN, *, consult_fetches: int = 1,
              total: int = 80_000, **extra) -> dict:
    """Overrides that make B genuinely cheaper: fewer fetches, fewer provider tokens.

    A `clean_sweep` where both policies deliver identically fails cost at a 0.000 drop,
    which is correct and tests nothing about the threshold. This is the scenario where the
    bound does what it is proposed to do.
    """
    return {(t, a, "B"): {"fetches": consult_fetches, "total_tokens": total,
                          "deliver": [f"{t}-m-primary"], **extra}
            for t in plan.tasks for a in range(1, plan.attempts + 1)}


def frozen_schedule(out: Path, plan: Plan = A3_PLAN, seed: int = SEED) -> S.Schedule:
    """The A/B order, drawn once from one generator and written before anything executes.

    `schedule.py` takes its arms as a parameter, so the A3 sweep registers `A,B` where the
    historical sweeps registered `baseline,nexus,notes`. What varies here is the POLICY.
    """
    sched = S.Schedule.create(list(plan.tasks), plan.attempts, seed, POLICIES)
    S.save(sched, out)
    return sched


def run_sweep(root: Path, patches: dict, sched: S.Schedule, plan: Plan = A3_PLAN,
              *, overrides: dict | None = None, stop_after_pairs: int | None = None,
              runs_log: list | None = None) -> list[dict]:
    """Execute the schedule pair by pair, consulting the launch gate before each pair.

    The gate is checked BEFORE a pair starts, never mid-pair: §8's launch unit is the A/B
    pair, because a B arm-run with no A beside it at the same task and attempt contributes
    to no dimension and can only spend.
    """
    overrides = overrides or {}
    log = []
    started = 0
    for task, attempt in plan.pairs():
        gate = P.may_start_from_disk(root, task, attempt, plan)
        log.append({"pair": f"{task}/attempt{attempt}", **gate})
        if not gate["may_start"]:
            break
        if stop_after_pairs is not None and started >= stop_after_pairs:
            log[-1] = {**log[-1], "may_start": False,
                       "reason": "soft launch threshold reached; no new pair is started",
                       "stopping_reason": "soft_threshold"}
            break
        for policy in sched.order_for(task, attempt):
            kw = dict(overrides.get((task, attempt, policy), {}))
            write_arm_run(root, task, attempt, policy, patches, **kw)
            if runs_log is not None:
                runs_log.append((task, attempt, policy))
        started += 1
    return log


def build(root: Path, plan: Plan = A3_PLAN, *, overrides: dict | None = None,
          stop_after_pairs: int | None = None, python: str | None = None) -> dict:
    """Set the fixture up, freeze a schedule, execute it stubbed, and report."""
    root.mkdir(parents=True, exist_ok=True)
    pristine = make_pristine(root / "pristine")
    patches = make_patches(root)
    patches["_pristine"] = pristine
    patches["_python"] = python or probe_python()
    notes = root / "notes.md"
    notes.write_text("\n".join(BODIES.values()))
    patches["_notes"] = notes

    sched = frozen_schedule(root / "schedule-a3.json", plan)
    gate_log = run_sweep(root, patches, sched, plan, overrides=overrides,
                         stop_after_pairs=stop_after_pairs)
    rep = P.report(root, FACTS, plan, bodies=BODIES,
                   pristine=pristine, python=patches["_python"], notes_file=notes)
    rep["schedule"] = {"digest": sched.schedule_digest, "seed": sched.seed,
                       "counterbalance": S.counterbalance(sched.rows, POLICIES)}
    rep["launch_gate"] = gate_log
    rep["probe_interpreter"] = patches["_python"]
    # The executed check must actually have executed somewhere, or the rehearsal measured
    # nothing and said so in a way that reads like a result.
    rep["probe_resolved_somewhere"] = any(
        n.get("E1") in ("pass", "fail") for n in rep["normalisation"])
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--json")
    a = ap.parse_args()
    tmp = None
    if a.out:
        root = Path(a.out)
        if root.exists():
            shutil.rmtree(root)
    else:
        tmp = tempfile.mkdtemp(prefix="a3-rehearsal-")
        root = Path(tmp)
    try:
        rep = build(root)
        print(render(rep))
        print(f"\nschedule {rep['schedule']['digest'][:16]}   "
              f"orders {rep['schedule']['counterbalance']['orders_used']}")
        for g in rep["launch_gate"]:
            print(f"  gate {g['pair']:<16} {'start' if g['may_start'] else 'STOP '}  "
                  f"{g['reason'][:80]}")
        if a.json:
            Path(a.json).write_text(json.dumps(rep, indent=1, default=str))
            print(f"\n-> {a.json}")
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
