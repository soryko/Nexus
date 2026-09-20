"""The saved-file path: frozen A/B schedule -> arm-run records -> scoring -> decision.

`test_a3_decision.py` exercises the decision table against synthetic `ArmRun` objects. That
is necessary and it is not sufficient, and the reason is on the record: the fix-leakage scan
had tests too, and it still published "none" on four tasks, because nothing exercised the
path from the FILES to the REPORT. Its defect lived in the join, where the tests were not.

So this module is the join, and `rehearse_a3.py` drives it end to end with no model:

    schedule -> stubbed execution -> saved records -> compliance scoring
             -> NORMALISATION -> decision report

Three things it exists to get right.

**The result identity is `(task, attempt, policy)`.** Both A3 policies run the *nexus* arm --
they differ in one paragraph of the prompt and nothing else -- so `(task, arm)` names eight
slots for sixteen arm-runs and the second of each pair overwrites the first. The historical
harness wrote `run-<task>/attempt<n>/arms/<arm>/`, which has the collision built into the
path. `arm_run_dir` puts the policy in the path, above `arms/`, so the existing scorer runs
over it unchanged and two policies can never land in one directory.

**The launch gate keys on CONSUMPTION, not on outcomes.** A pair whose functional result is
unresolved is a dimension going indeterminate; the sweep continues. A pair whose CONSUMPTION
is unresolved means the sweep does not know what it has spent, and §7 stops it. `may_start`
returns False for the second and True for the first.

**A bound violation is an observation.** `bound_respected` is computed here from the trace --
at most one search and at most three full-body fetches -- and recorded. It excludes nothing
from any dimension. Excluding those arm-runs would select the sample on the outcome being
measured.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import a3_ledger
import score_compliance
from run_calibration import usage_tokens
from a3_decision import CONSULT_TOOLS, A3_PLAN, ArmRun, Plan, decide
from trace_parse import parse

#: Both policies run this arm. The policy is NOT an arm and never becomes one.
ARM = "nexus"

#: `search` is the bounded call; `get` and `history` return full bodies. `status` is a
#: consultation but fetches no body, so it is counted and does not spend a fetch.
FETCH_TOOLS = ("get", "history")

#: Counting methods, named. An arm-run records WHICH one produced its number, because two
#: methods are two units and their sum has no denominator.
def _utf8_div4(text: str) -> int:
    return (len(text.encode()) + 3) // 4


COUNTING_METHODS = {"harness-utf8-bytes-div4": _utf8_div4}
DEFAULT_COUNTING_METHOD = "harness-utf8-bytes-div4"


FACTS_FILE = Path(__file__).parent / "facts-a3.json"


def registered_facts(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """A3's predeclared task-relevant facts, LOADED from the frozen file.

    `facts-a3.json` is declared before launch and is what the production pipeline uses.
    Computing the classes at scoring time would let the equivalence relation move after the
    numbers exist, which is the one thing predeclaring them is for.
    """
    d = json.loads(Path(path or FACTS_FILE).read_text())
    return {task: {fid: f["members"] for fid, f in facts.items()}
            for task, facts in d["facts"].items()}


def registered_fact_detail(path: Path | None = None) -> dict:
    return json.loads(Path(path or FACTS_FILE).read_text())


def arm_run_dir(root: Path, task: str, attempt: int, policy: str) -> Path:
    """The directory for one arm-run, distinct in all three coordinates.

    `<root>/run-<task>/attempt<n>/policy-<P>/arms/nexus`. The policy sits ABOVE `arms/`, so
    `score_compliance.score(<...>/policy-<P>, task, "nexus", ...)` reads the right tree with
    no change to the scorer.
    """
    return Path(root) / f"run-{task}" / f"attempt{attempt}" / f"policy-{policy}" / "arms" / ARM


def policy_run_dir(root: Path, task: str, attempt: int, policy: str) -> Path:
    """What the scorer is handed: the parent of `arms/`."""
    return arm_run_dir(root, task, attempt, policy).parent.parent


def consultation_calls(calls: list[dict]) -> dict[str, int]:
    """Every consultation tool, counted separately. `status` included."""
    out = {t: 0 for t in CONSULT_TOOLS}
    for c in calls:
        name = (c.get("name") or "")
        if name.startswith("mcp__nexus__"):
            tool = name.split("mcp__nexus__", 1)[1]
            if tool in out:
                out[tool] += 1
    return out


def bound_respected(calls: list[dict]) -> bool:
    """At most one search and at most three full-body fetches. RECORDED, never a filter."""
    n = consultation_calls(calls)
    return n["search"] <= 1 and sum(n[t] for t in FETCH_TOOLS) <= 3


def delivered_text(calls: list[dict]) -> tuple[int, str]:
    """Bytes of tool-result text returned by consultation calls, and that text.

    Delivered CONTEXT VOLUME. Not attributable provider spend: retrieved content reappears
    in later requests and cache accounting differs, which is why it is reported beside
    `provider_total_tokens` and never instead of it.
    """
    text = "".join((c.get("result") or "") for c in calls
                   if (c.get("name") or "").startswith("mcp__nexus__"))
    return len(text.encode()), text


def facts_delivered(calls: list[dict], facts: dict[str, list[str]],
                    bodies: dict[str, str], task: str) -> dict[str, bool | None]:
    """EVERY member of every equivalence class, measured BY EVENT ORDERING.

    The registered measure is delivery **before the first source edit** (§5.6). An earlier
    version of this function concatenated every consultation result and asked whether the
    body appeared anywhere in it, which credits a memory retrieved AFTER the edit it was
    supposed to inform -- the same defect `score_compliance.P2` exists to prevent,
    reintroduced one layer up.

    Four outcomes, and the third is the one a boolean cannot carry:

      True   the body arrived, and arrived before the first edit
      False  the body never arrived at all -- ordering is then irrelevant
      False  the body arrived only AFTER the first edit: late is not delivered
      None   the body arrived but the first edit could not be located, so the ORDER is
             unknown. Unresolved, never "delivered": the same rule `score_compliance` uses
             when its edit matcher misses.

    A member with no recorded body is `None` too -- not measured is not "not delivered".
    """
    edit = score_compliance.first_edit_event(calls, task)
    out: dict[str, bool | None] = {}
    for members in facts.values():
        for mid in members:
            body = bodies.get(mid)
            if not body:
                out[mid] = None
                continue
            arrivals = [c["resolved_at"] for c in calls
                        if not c.get("is_error") and c.get("resolved_at") is not None
                        and score_compliance.notes_content_delivered(
                            c.get("result") or "", [body], need=1)]
            if not arrivals:
                out[mid] = False            # never delivered; ordering does not arise
            elif not edit["found"]:
                out[mid] = None             # delivered, but against what? order unknown
            else:
                out[mid] = min(arrivals) < edit["at"]
    return out


#: pytest INVOKED, at a command position. `echo pytest` and `grep pytest ...` mention it and
#: run nothing, and the first version of this matched them: the token was searched for
#: anywhere in the command. A command position is the start of the string or just after a
#: separator, optionally preceded by `VAR=value` assignments and an interpreter with `-m`.
_CMD_POS = r"(?:^|[;&|(]|&&|\|\|)\s*(?:[A-Za-z_][\w]*=[^\s]*\s+)*"
_PYTEST = re.compile(_CMD_POS + r"(?:[\w./$-]*python[\w.]*\s+-m\s+)?pytest\b")
#: An explicit path argument to that invocation.
_PATH_ARG = re.compile(r"(?<![\w-])(tests?/[\w./:-]+|[\w./-]+_test\.py[\w:]*)")
#: Collection without execution. `--collect-only` reports what WOULD run.
_COLLECT_ONLY = re.compile(r"(?<![\w-])--collect-only(?![\w-])|(?<![\w-])--co(?![\w-])")
#: pytest's own summary line. At least one test must have REACHED a verdict: a run that is
#: entirely deselected, or that collected and ran nothing, executed no test.
_RAN = re.compile(r"(?<![\w])(\d+)\s+(passed|failed|error|errors|xpassed|xfailed)(?![\w])")
_NO_TESTS = re.compile(r"no tests ran|collected 0 items", re.I)
#: A summary that reports only non-verdict outcomes. `-k nomatch` prints "2 deselected",
#: exits 0, and executed nothing -- a summary WAS produced, so this is `False` and not the
#: `None` that "no recognisable summary" earns.
_NO_VERDICT = re.compile(r"(?<![\w])(\d+)\s+(deselected|skipped)(?![\w])")


def _command_text(call: dict) -> str:
    """The shell command a call actually runs, not the JSON envelope around it.

    Matching against `json.dumps(input)` put a `"` immediately before a command that STARTS
    with `pytest`, so a command-position anchor could never fire on the ordinary case while
    still firing on `echo pytest` (which has a space in front of it). The envelope has to
    come off before the anchor means anything.
    """
    inp = call.get("input") or {}
    if isinstance(inp, dict):
        for key in ("command", "cmd", "script"):
            v = inp.get(key)
            if isinstance(v, str):
                return v
    return json.dumps(inp)


def _tests_actually_ran(result: str) -> bool | None:
    """Did pytest's own output say a test reached a verdict?

    -> True  at least one passed/failed/errored
       False the run produced a summary and nothing reached a verdict (all deselected,
             nothing collected, "no tests ran")
       None  no recognisable summary, so the trace does not settle it

    Exit status is not used. A deselected run exits 0 and a collection-only run exits 0, and
    both were counted as execution before this.
    """
    if not result:
        return None
    if _NO_TESTS.search(result):
        return False
    hits = [(int(n), w) for n, w in _RAN.findall(result)]
    if hits:
        return any(n > 0 for n, _ in hits)
    if _NO_VERDICT.search(result):
        return False                        # a summary, and nothing reached a verdict
    return None


def observed_test_execution(calls: list[dict], added_tests: list[str]) -> bool | None:
    """Did the ARM-RUN actually run the test it submitted? Read from the trace.

    Separate from `E1_regression_discriminates`, which is an OFFLINE probe: the scorer takes
    the patch, applies it to three trees of its own and runs pytest itself, afterwards. That
    establishes something about the submitted test and NOTHING about whether the agent ever
    executed it -- and §5.3 asks for both, because A2-R's k1/nexus passed the hidden checks
    having added no test at all. Retrospective verification is not an execution event.

      True   pytest was INVOKED at a command position, not collection-only, on a target
             that would collect the added test, and its own output says at least one test
             reached a verdict
      False  pytest was never invoked; or only on paths that exclude the added test; or it
             ran and nothing reached a verdict (everything deselected, nothing collected)
      None   pytest was invoked but its output does not settle whether tests ran -- the
             result is missing, errored, or carries no recognisable summary

    Three ways this was overcredited before, all of them reported and all reproduced in
    `test_a3_pipeline.py`:

      `echo pytest`      the token was searched for anywhere in the command, so mentioning
                         pytest counted as running it;
      `--collect-only`   collection reports what WOULD run and exits 0;
      full deselection   `-k nomatch` runs nothing, prints "2 deselected", and exits 0.

    None of the three executes a test, and all three are excluded here. The evidence is
    pytest's own summary rather than the exit status, because all three exit 0.
    """
    if not added_tests:
        return False
    files = {t.split("::", 1)[0] for t in added_tests}
    names = {t.split("::", 1)[1] for t in added_tests if "::" in t}
    saw_unsettled = False
    for c in calls:
        cmd = _command_text(c)
        if not _PYTEST.search(cmd):
            continue                        # mentioned, not invoked
        if _COLLECT_ONLY.search(cmd):
            continue                        # collection is not execution
        if c.get("is_error") or c.get("result") is None:
            saw_unsettled = True
            continue
        targets = set(_PATH_ARG.findall(cmd))
        covers = (not targets
                  or any(f in t or t in f for f in files for t in targets)
                  or any(n in cmd for n in names))
        if not covers:
            continue                        # ran, but not over the added test
        ran = _tests_actually_ran(c.get("result") or "")
        if ran is True:
            return True
        if ran is None:
            saw_unsettled = True            # invoked over it; outcome unreadable
    return None if saw_unsettled else False


def required_work_observations(scored: dict, calls: list[dict]) -> dict:
    """Three separate observations, and the registered criterion built from two of them.

    They are different claims about different evidence and the first draft read one as all
    three:

      offline_discrimination  the scorer's own probe over three trees -- absent on the
                              fixture, failing with the test hunks alone, passing on the
                              full patch. Evidence about the SUBMITTED TEST.
      observed_execution      whether the arm-run itself ran that test. Evidence about the
                              AGENT, read from the trace.
      reviewed_relevance      whether the test covers the reported defect rather than merely
                              failing in the predicted way. NOT machine-settled --
                              `score_compliance` already lists it under
                              `unsettled_by_machine` -- and it is REPORTED, never folded
                              into the criterion. A launch may not substitute a machine
                              verdict for a review that has not happened.

    §5.3's criterion is: does the patch add a test node, AND did the arm-run execute it.
    So the criterion is `offline_discrimination AND observed_execution`, stated explicitly:

      fail        the probe says the test does not discriminate -- the work is not there
      fail        the probe passes but the agent NEVER RAN IT -- half the work is missing
      pass        the probe passes and the agent ran it
      unresolved  either observation is unknown. Unresolved is not compliance.
    """
    e1 = (scored.get("checks", {}).get("E1_regression_discriminates") or {})
    offline = {"pass": "pass", "fail": "fail"}.get(e1.get("verdict"), "unknown")
    added = (scored.get("regression_probe") or {}).get("added_tests") or []
    executed = observed_test_execution(calls, added)
    if offline == "fail":
        criterion = "fail"
    elif offline == "pass" and executed is True:
        criterion = "pass"
    elif offline == "pass" and executed is False:
        criterion = "fail"
    else:
        criterion = "unresolved"
    return {"offline_discrimination": offline,
            "observed_execution": executed,
            "reviewed_relevance": "unreviewed",
            "relevance_is_in_the_criterion": False,
            "added_tests": added,
            "criterion": criterion,
            "criterion_rule": "offline_discrimination AND observed_execution; relevance is "
                              "reported and decides nothing"}


@dataclass(frozen=True)
class Normalised:
    """An `ArmRun` plus the evidence it was built from, so a reader can audit the mapping."""
    arm_run: ArmRun
    sources: dict


def normalise(root: Path, task: str, attempt: int, policy: str, *,
              facts: dict[str, list[str]], bodies: dict[str, str],
              pristine: Path, python: str, notes_file: Path,
              counting_method: str = DEFAULT_COUNTING_METHOD) -> Normalised:
    """One saved arm-run -> one `ArmRun`. Absent evidence becomes `None`, never a zero.

    Every field that cannot be read is unresolved. That is the whole point of the stage: a
    normaliser that defaults a missing number to 0 reports a saving, and a missing outcome
    to False reports a loss.
    """
    d = arm_run_dir(root, task, attempt, policy)
    t = parse(d / "trace.jsonl")
    calls = t["calls"]

    scored = score_compliance.score(policy_run_dir(root, task, attempt, policy),
                                    task, ARM, pristine, python, notes_file=notes_file)
    work = required_work_observations(scored, calls)

    # functional and adoption are recorded by the runner and the trace reader; absent is
    # unresolved, and `.get` on a missing file must not become False.
    fn = d / "functional.json"
    functional = json.loads(fn.read_text()).get("passed") if fn.is_file() else None
    on = d / "outcomes.json"
    outcomes = json.loads(on.read_text()) if on.is_file() else {}
    # A HUMAN review of whether the added test covers the reported defect. Absent means not
    # reviewed, which blocks acceptance and never causes a failure. Nothing computes it.
    rn = d / "relevance.json"
    reviewed = json.loads(rn.read_text()).get("relevant") if rn.is_file() else None

    rec = d / "record.json"
    record = json.loads(rec.read_text()) if rec.is_file() else {}
    usage = (record.get("record") or record).get("usage") or {}
    # ONE rule for what a usage block accounts for, shared with `a3_ledger` and
    # `run_calibration`. Reading `total_tokens` here while the ledger summed the four
    # registered categories would let the decision's cost dimension and the launch gate
    # disagree about the same arm-run -- and a usage block of four nulls, which the runner
    # writes whenever the envelope carried no usage, would read as a measurement.
    total = usage_tokens(usage)
    # An accounting that is absent, or that the runner marked unreconciled, is unresolved.
    resolved = bool(record.get("accounting_resolved", rec.is_file())) and total is not None

    nbytes, text = delivered_text(calls)
    method = COUNTING_METHODS.get(counting_method)

    arm_run = ArmRun(
        task=task, policy=policy, attempt=attempt,
        functional_pass=functional,
        required_test_work=work["criterion"],
        facts_delivered=facts_delivered(calls, facts, bodies, task),
        stale_exposed=outcomes.get("stale_exposed"),
        stale_adopted=outcomes.get("stale_adopted"),
        consultation_calls=consultation_calls(calls),
        delivered_bytes=nbytes,
        delivered_text_tokens=method(text) if method else None,
        token_counting_method=counting_method if method else None,
        provider_total_tokens=total,
        accounting_resolved=resolved,
        # recorded for B; meaningless for A, which is not bounded
        bound_respected=bound_respected(calls) if policy == "B" else None,
        relevance_reviewed=reviewed,
    )
    return Normalised(arm_run, {
        "dir": str(d),
        "compliance": scored.get("compliance"),
        "E1": (scored.get("checks", {}).get("E1_regression_discriminates") or {}).get("verdict"),
        "required_work": work,
        "relevance_reviewed": reviewed,
        "facts_delivered": arm_run.facts_delivered,
        "functional_source": "functional.json" if fn.is_file() else "absent -> unresolved",
        "accounting_source": "record.json" if rec.is_file() else "absent -> unresolved",
        "consultation_calls": arm_run.consultation_calls,
        "bound_respected": arm_run.bound_respected,
    })


def unresolved_arm_run(task: str, attempt: int, policy: str,
                       facts: dict[str, list[str]]) -> ArmRun:
    """A cell that was LAUNCHED and left nothing usable. Everything unknown, nothing zero.

    It is a record, not an absence: `validity` counts it toward coverage and `consumption`
    stops further launches on it. Skipping it -- which is what keying on `trace.jsonl` did --
    turns a run that spent tokens into a cell that never happened.
    """
    return ArmRun(task=task, policy=policy, attempt=attempt,
                  functional_pass=None, required_test_work="unresolved",
                  facts_delivered={m: None for members in facts.values() for m in members},
                  stale_exposed=None, stale_adopted=None,
                  consultation_calls={}, delivered_bytes=None,
                  delivered_text_tokens=None, token_counting_method=None,
                  provider_total_tokens=None, accounting_resolved=False,
                  bound_respected=None, relevance_reviewed=None)


def collect(root: Path, required_facts: dict[str, dict[str, list[str]]],
            plan: Plan = A3_PLAN, **kw) -> tuple[list[ArmRun], list[dict]]:
    """Every arm-run the plan registers that has actually been saved.

    `required_facts` is indexed PER TASK here. Handing one task's equivalence classes to all
    four would leave the other three tasks' members unmeasured, and unmeasured is
    `unresolved` -- so every task but one would go indeterminate on information for a reason
    that has nothing to do with the policy. That is what the first draft of this function
    did, and the rehearsal is what showed it.

    A cell with no directory is NOT normalised into an unresolved row: it is absent, and
    `validity` reports it as missing coverage. Inventing a row for it would turn "did not
    run" into "ran and told us nothing", which are different facts.
    """
    runs, sources = [], []
    for task, attempt, policy in plan.cells():
        d = arm_run_dir(root, task, attempt, policy)
        # EITHER marker proves a launch: A3's own, written first by `run_a3`, and the
        # runner's, written inside `invoke`. Checking only one of them made a cell that the
        # ledger counts as started invisible to the reporter.
        launched = any((d / m).is_file() for m in a3_ledger.MARKERS)
        usable = (d / "trace.jsonl").is_file() and (d / "patch.diff").is_file()
        if not launched and not usable:
            continue                        # never started: missing coverage, not a row
        if not usable:
            # LAUNCHED and nothing usable came back. It spent an unknown amount and it is a
            # row. Keying on the trace made this case vanish.
            runs.append(unresolved_arm_run(task, attempt, policy,
                                           required_facts.get(task, {})))
            sources.append({"dir": str(d), "state": "launched, no usable artifacts",
                            "E1": None, "accounting_source": "launch marker only",
                            "consumption": "UNRESOLVED"})
            continue
        n = normalise(root, task, attempt, policy,
                      facts=required_facts.get(task, {}), **kw)
        runs.append(n.arm_run)
        sources.append(n.sources)
    return runs, sources


def saved_consumption(root: Path, plan: Plan = A3_PLAN) -> list[str]:
    """Arm-runs LAUNCHED without usable terminal accounting. Delegates to `a3_ledger`.

    It used to key on `trace.jsonl` and skip anything without one, with the comment "not
    started: absent, not unresolved". That asserted "not started" from "no trace", and they
    are different facts: `run_arms_isolated` buffers the whole trace until the subprocess
    returns, so an interruption inside that window leaves a launch marker, spent tokens, and
    no trace. Such a row reported `[]` -- the failure class that lost A2-R's k4, rebuilt one
    layer up. The ledger is keyed on the MARKER, which is written before the process starts.
    """
    return a3_ledger.read(root, plan).unresolved


def may_start_from_disk(root: Path, task: str, attempt: int,
                        plan: Plan = A3_PLAN) -> dict:
    """PAIR ADMISSION: the budget gate, checked once before a pair starts.

    NOT the only gate. `a3_ledger.fatal_stop` is checked before EVERY arm-run, including the
    second member of a pair already admitted -- reserving a pair does not authorise
    launching its B after A lost its accounting or the boundary refused.
    """
    return a3_ledger.may_admit_pair(root, task, attempt, plan)


def may_start(runs: list[ArmRun], task: str, attempt: int) -> dict:
    """The same gate over already-normalised runs, for a caller that has them in hand.

    Keys on CONSUMPTION alone. §7: an arm-run whose consumption cannot be accounted for
    stops the sweep -- the row stays unresolved, never zero, never covered by an allowance
    written to unblock it. An unresolved OUTCOME stops nothing.
    """
    from a3_decision import consumption
    c = consumption(runs)
    if not c["further_launches_permitted"]:
        return {"may_start": False, "task": task, "attempt": attempt,
                "stopping_reason": c["stopping_reason"],
                "reason": f"consumption unresolved in {c['unresolved_arm_runs']}; "
                          f"no new pair is started"}
    return {"may_start": True, "task": task, "attempt": attempt, "stopping_reason": None,
            "reason": "every saved arm-run's consumption is accounted for"}


def report(root: Path, required_facts: dict[str, dict[str, list[str]]],
           plan: Plan = A3_PLAN, **kw) -> dict:
    """The whole path, from the saved files to the decision."""
    runs, sources = collect(root, required_facts, plan, **kw)
    out = decide(runs, required_facts, plan=plan)
    out["normalisation"] = sources
    out["root"] = str(root)
    return out
