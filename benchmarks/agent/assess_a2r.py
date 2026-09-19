"""What an A2-R arm-run shows about the repaired toolchain and about the task requirement --
separately, and without a ceiling-selection rule.

Three things this is careful about, each because the obvious version of it is wrong.

THE REPAIR IS NOT AN ABSENCE.  "`No module named pytest` did not appear" is VACUOUS in a run
that never attempted pytest, and v2 contained such a run (`30/k2/notes` passed the hidden
checks having never invoked pytest at all). An absence proves the repair only where the thing
that would have produced the message was actually attempted. So five facts are reported
separately and never collapsed into a verdict:

    1. the gate passed, and what runtime identity it recorded
    2. the agent invoked the documented pinned command
    3. that invocation ran, or did not
    4. the agent used another interpreter
    5. no relevant invocation was observed at all

(4) and (5) are not failures of the repair. An agent choosing a different command is a
different fact from the documented command being broken, and the two were the same number in
every summary that counted `No module named pytest`.

A GATE REFUSAL HAS A CAUSE.  It stops spending either way, but it is only evidence about the
interpreter when the check that refused is one of the interpreter checks. `gate_cause`
classifies it before anything calls it an interpreter-repair failure.

A CHANGED FILE UNDER tests/ IS NOT A TEST.  The tail requires extending the existing suite to
cover the fix. A comment, a rename, a whitespace change and an unrelated edit all touch
`tests/` and satisfy nothing. Four observations are kept independent -- a source diff, a
substantive test addition, whether that test was executed, and its result -- and `unknown` is
a real value used wherever the evidence is missing. Nothing is inferred from edit counts.
Relevance is left `unreviewed`: twelve patches is small enough to read, and a machine cannot
establish that a test covers the bug.

`a1-scorer-4` is the REGISTERED compliance scorer and is untouched. Where its verdict is
available it is reported beside these observations under its own name, never merged with them.

    python3 assess_a2r.py <scratch> [--json out.json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

ARMS = ("baseline", "nexus", "notes")
TASKS = ("k1", "k2", "k3", "k4")
CELLS = len(ARMS) * len(TASKS)

UNKNOWN = "unknown"

# ----------------------------------------------------------------- the pinned command

# The prompt documents `PYTHONPATH=src "$A2_PYTHON" -m pytest <paths> -q`. What identifies an
# invocation as the DOCUMENTED one is the variable, because that is the whole repair: the
# interpreter arrives by name instead of by resolution.
PINNED = re.compile(r'\$\{?A2_PYTHON\}?')

# Another interpreter, in command position. Matched after leading `VAR=value` assignments are
# stripped, so `PYTHONPATH=src python3 -m pytest` is an interpreter use and `PYTHONPATH=src
# "$A2_PYTHON" ...` is not. `\b` keeps `python_version` and `PYTHONPATH` out.
INTERPRETER = re.compile(r'^(?:[A-Za-z_][\w]*=\S*\s+)*'
                         r'["\']?(?P<cmd>[\w./-]*(?:python\d?(?:\.\d+)?|pytest))\b')

# Looking an interpreter up is not using one. `which -a python3` was the FIRST call in most v2
# arm-runs; counting it as "the agent chose another interpreter" would have reported a choice
# nobody made.
LOOKUP = re.compile(r'^(?:which|command|type|whereis|echo|ls|cat|head|grep|rg|find|printf)\b')

# Splits a shell command into segments that each have their own command position.
SEGMENT = re.compile(r'(?:\|\||&&|[;|\n])')

# The repair-relevant failures: the interpreter did not exist, or it had no pytest. A test
# that FAILS is not one of these -- the documented command ran perfectly and the tests were
# red, which is the normal state of a run that has not fixed the bug yet. Conflating the two
# would report the repair as broken on every unfinished run.
NOT_RUNNABLE = (
    re.compile(r"No module named ['\"]?pytest"),
    re.compile(r'command not found'),
    re.compile(r'no such file or directory', re.I),
    re.compile(r'bad interpreter'),
    re.compile(r'unbound variable'),
    re.compile(r'permission denied', re.I),
)
# Evidence the interpreter executed, whatever the tests then did.
DID_RUN = (
    re.compile(r'\b\d+ (?:passed|failed|error|errors|deselected|skipped)\b'),
    re.compile(r'^=+ .*(?:test session starts|passed|failed).*=+$', re.M),
    re.compile(r'\bcollected \d+ item'),
    re.compile(r'\bno tests ran\b'),
    re.compile(r'^Python \d+\.\d+', re.M),
    re.compile(r'^Traceback \(most recent call last\)', re.M),
)
# A missing module that is NOT pytest. The program's own import problem, reported separately
# so it cannot be read as the toolchain failing.
OTHER_MODULE = re.compile(r"No module named ['\"]?(?!pytest)(\w+)")

# Gate checks that bear on the interpreter. A gate refusal on the forwarder or on egress stops
# spending too, and says nothing whatever about the repair.
INTERPRETER_CHECKS = ("pinned interpreter", "documented import", "documented test command",
                      "reproduction script")


def segments(command: str) -> list[str]:
    return [s.strip() for s in SEGMENT.split(command) if s.strip()]


def classify_command(command: str) -> dict:
    """-> which interpreters this one Bash command actually invokes."""
    pinned = other = lookup = 0
    for seg in segments(command):
        if LOOKUP.match(seg):
            lookup += 1
            continue
        if PINNED.search(seg):
            pinned += 1
        elif (m := INTERPRETER.match(seg)) and not PINNED.search(m.group("cmd")):
            other += 1
    return {"pinned": pinned, "other_interpreter": other, "lookup": lookup}


def invocation_outcome(result: str) -> str:
    """-> 'not_runnable' | 'ran' | 'unknown' for one pinned invocation.

    `not_runnable` is tested first: a run can print `No module named pytest` AND a traceback,
    and the toolchain failure is the one that matters.
    """
    text = result or ""
    if any(p.search(text) for p in NOT_RUNNABLE):
        return "not_runnable"
    if any(p.search(text) for p in DID_RUN):
        return "ran"
    if text.startswith("Exit code 0") or (text.strip() and not text.startswith("Exit code")):
        return "ran"
    return UNKNOWN


def repair_check(record: dict, calls: list[dict]) -> dict:
    """The five facts, reported separately. No verdict is derived that an absence could carry.

    `state` is a convenience label over the facts below it, not an extra measurement. Where it
    is `no_relevant_invocation`, this arm-run supports NO claim about the repair in either
    direction, and the report says so rather than counting it as a pass.
    """
    ri = record.get("runtime_identity") or {}
    gate_checks = record.get("gate_checks") or []
    failed = [c.get("check", "?") for c in gate_checks if c.get("passed") is False]
    if ri.get("gated") is False:
        gate = "not_gated"
    elif failed:
        gate = "refused"
    elif gate_checks or ri:
        gate = "passed"
    else:
        gate = UNKNOWN

    # A refusal stops spending whatever refused. Only an interpreter check makes it evidence
    # about the interpreter.
    cause = None
    if gate == "refused":
        cause = ("interpreter" if any(any(k in f for k in INTERPRETER_CHECKS) for f in failed)
                 else "other_environment")

    pinned_calls, other_calls, outcomes = [], [], {"ran": 0, "not_runnable": 0, UNKNOWN: 0}
    other_modules: list[str] = []
    for c in calls:
        if (c.get("name") or "") != "Bash":
            continue
        command = (c.get("input") or {}).get("command") or ""
        k = classify_command(command)
        result = str(c.get("result") or "")
        if k["pinned"]:
            # Attribution: a command mixing both interpreters cannot have its result assigned
            # to one of them, so its outcome is unknown and says so.
            out = invocation_outcome(result) if not k["other_interpreter"] else UNKNOWN
            outcomes[out] += 1
            pinned_calls.append({"index": c.get("index"), "outcome": out,
                                 "mixed": bool(k["other_interpreter"]),
                                 "command": command[:160]})
            if m := OTHER_MODULE.search(result):
                other_modules.append(m.group(1))
        if k["other_interpreter"]:
            other_calls.append({"index": c.get("index"), "command": command[:160]})

    if gate == "refused":
        state = f"gate_refused_{cause}"
    elif pinned_calls and outcomes["ran"]:
        state = "pinned_ran"
    elif pinned_calls and outcomes["not_runnable"]:
        state = "pinned_not_runnable"
    elif pinned_calls:
        state = "pinned_outcome_unknown"
    elif other_calls:
        state = "other_interpreter_only"
    else:
        state = "no_relevant_invocation"

    return {"state": state,
            # 1 -- the gate
            "gate": gate, "gate_failed_checks": failed, "gate_cause": cause,
            "runtime_identity": ri or None,
            "runtime_identity_recorded": bool(ri.get("a2_python")),
            # 2 -- did the agent invoke the documented command
            "pinned_invocations": len(pinned_calls),
            # 3 -- did it run
            "pinned_ran": outcomes["ran"],
            "pinned_not_runnable": outcomes["not_runnable"],
            "pinned_outcome_unknown": outcomes[UNKNOWN],
            # 4 -- did it use another interpreter
            "other_interpreter_invocations": len(other_calls),
            # 5 -- was there anything to judge at all
            "relevant_invocation": bool(pinned_calls or other_calls),
            # reported, never the verdict: a missing module that is not pytest is the
            # program's import problem, not the toolchain's
            "other_missing_modules": sorted(set(other_modules)),
            "evidence": {"pinned": pinned_calls[:6], "other_interpreter": other_calls[:6]}}


# ----------------------------------------------------------------- the requirement

TEST_FILE = re.compile(r'^(?:tests?/|.*/tests?/)')
HUNK_DEF = re.compile(r'@@.*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)')
ADDED_DEF = re.compile(r'^(\s*)(?:async\s+)?def\s+(test_\w+)')


def substantive(line: str) -> bool:
    """An added line that could carry a test. Blank lines and comments cannot.

    This is the difference between "a file under tests/ changed" and "the suite was extended",
    and it is the whole of correction 3: a comment satisfies the diff and not the tail.
    """
    body = line[1:].strip()
    return bool(body) and not body.startswith("#")


def patch_files(patch: str) -> list[str]:
    return re.findall(r'^\+\+\+ b/(.+)$', patch or "", re.M)


def test_additions(patch: str) -> dict:
    """-> the tests this patch adds or extends, with the evidence, or nothing.

    Two shapes count, as `score_compliance.added_test_ids` established: a new module-level
    `def test_*`, and substantive lines added inside an existing test named by the hunk header.
    Unlike that function -- which is the REGISTERED scorer and stays as it is -- a hunk whose
    only additions are comments or blank lines counts as neither.
    """
    nodes: list[str] = []
    files: list[str] = []
    nested: list[str] = []
    current = None
    context = None
    added_substantive = False
    context_used = False

    def close():
        if current and context and added_substantive and not context_used:
            node = f"{current}::{context}"
            if context.startswith("test_") and node not in nodes:
                nodes.append(node)

    for line in (patch or "").splitlines():
        if line.startswith("+++ b/"):
            close()
            current = line[6:].strip()
            if not TEST_FILE.match(current):
                current = None
            elif current not in files:
                files.append(current)
            context, added_substantive, context_used = None, False, False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            close()
            m = HUNK_DEF.search(line)
            context = m.group(1) if m else None
            added_substantive, context_used = False, False
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if not substantive(line):
            continue
        added_substantive = True
        if m := ADDED_DEF.match(line[1:]):
            if m.group(1):
                nested.append(f"{current}::{m.group(2)} (nested; node id not reconstructed)")
            else:
                node = f"{current}::{m.group(2)}"
                if node not in nodes:
                    nodes.append(node)
                context_used = True
    close()
    return {"nodes": nodes, "files": files, "nested": nested}


def executed(calls: list[dict], adds: dict) -> dict:
    """Was the added test actually run, and what happened.

    Matched on the node id or, failing that, the file path -- a run of the whole file does
    execute a test added to it. A run of the WHOLE SUITE is matched too, and flagged as such,
    because it executes the addition without naming it and the distinction belongs to the
    reader rather than to a silent yes.
    """
    hits: list[dict] = []
    targets = adds["nodes"] + adds["files"]
    for c in calls:
        if (c.get("name") or "") != "Bash":
            continue
        command = (c.get("input") or {}).get("command") or ""
        if "pytest" not in command:
            continue
        named = [t for t in targets if t.split("::")[-1] in command or t in command]
        whole = not re.search(r'pytest\s+\S*(?:/|::)', command)
        if named or whole:
            hits.append({"index": c.get("index"), "named": named, "whole_suite": whole,
                         "outcome": invocation_outcome(str(c.get("result") or "")),
                         "result": _pytest_counts(str(c.get("result") or "")),
                         "command": command[:160]})
    return {"executed": bool(hits), "calls": hits[:6]}


def _pytest_counts(result: str) -> dict:
    out = {}
    for word in ("passed", "failed", "error", "errors", "skipped"):
        if m := re.search(rf'\b(\d+) {word}\b', result):
            out[word] = int(m.group(1))
    return out


def requirement_compliance(record: dict, calls: list[dict]) -> dict:
    """Four independent observations. `unknown` where the evidence is missing.

    Nothing here is inferred from how many times the agent edited a file. An edit count is a
    count of edits.
    """
    patch = record.get("patch")
    if patch is None:
        return {"source_diff": UNKNOWN, "test_addition": UNKNOWN, "test_executed": UNKNOWN,
                "test_result": UNKNOWN, "relevance": "unreviewed",
                "why_unknown": "no patch recorded for this arm-run",
                "evidence": {}}
    files = patch_files(patch)
    adds = test_additions(patch)
    src = any(f.startswith("src/") or "/src/" in f for f in files)
    run = executed(calls, adds) if adds["nodes"] or adds["files"] else {"executed": False,
                                                                       "calls": []}
    if not adds["nodes"]:
        result = UNKNOWN
    elif not run["executed"]:
        result = UNKNOWN
    else:
        outcomes = [h["result"] for h in run["calls"]]
        passed = any(o.get("passed") and not o.get("failed") and not o.get("error")
                     for o in outcomes)
        failed = any(o.get("failed") or o.get("error") or o.get("errors") for o in outcomes)
        result = "passed" if passed and not failed else "failed" if failed else UNKNOWN

    return {
        # (a) a final source diff
        "source_diff": "yes" if src else "no",
        # (b) a substantive addition to or extension of the existing suite
        "test_addition": "yes" if adds["nodes"] else "no",
        # (c) execution of that test
        "test_executed": ("yes" if run["executed"] else "no") if adds["nodes"] else UNKNOWN,
        # (d) its result
        "test_result": result,
        # NOT machine-decidable: whether the added test covers THIS bug. Twelve patches is a
        # readable number; a reviewer fills this in and the report shows it empty until then.
        "relevance": "unreviewed",
        "evidence": {"patch_files": files, "test_nodes": adds["nodes"],
                     "test_files": adds["files"], "nested_skipped": adds["nested"],
                     "execution": run["calls"]},
    }


# ----------------------------------------------------------------- reading a sweep

def read_cells(scratch: Path) -> list[dict]:
    """Every arm-run A2-R wrote, at whatever ceiling it wrote it."""
    cells = []
    for records in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(records.read_text())
        task = data.get("task") or records.parent.parent.name.replace("run-", "")
        ceiling = int(re.sub(r'\D', '', records.parents[2].name) or 0)
        for rec in data.get("records", []):
            arm = rec.get("arm") or "?"
            arm_dir = records.parent / "arms" / arm
            calls = rec.get("tool_calls")
            if calls is None:
                trace = arm_dir / "trace.json"
                calls = (json.loads(trace.read_text()).get("tool_calls", [])
                         if trace.exists() else [])
            terminal = rec.get("terminal") or {}
            cells.append({
                "ceiling": ceiling, "task": task, "arm": arm,
                "functional": rec.get("functional"),
                "scorer_a1_4": rec.get("compliance", UNKNOWN),
                "terminal": terminal.get("terminal", UNKNOWN),
                "scored": terminal.get("scored"),
                "truncated": terminal.get("truncated"),
                "num_turns": rec.get("num_turns"),
                "wall_clock_s": rec.get("wall_clock_s"),
                "tokens": _tokens(rec.get("usage")),
                "repair": repair_check(rec, calls),
                "requirement": requirement_compliance(rec, calls),
            })
    return cells


def _tokens(u) -> int | None:
    import run_calibration as RC
    return RC.usage_tokens(u)


def build(scratch: Path) -> dict:
    import run_calibration as RC
    cells = read_cells(scratch)
    ledger = RC.consumed(scratch)
    ceilings = sorted({c["ceiling"] for c in cells})
    return {"scratch": str(scratch), "ceilings": ceilings, "cells": cells,
            "ledger": ledger, "expected_cells": CELLS}


# ----------------------------------------------------------------- the report

# The closed calibration's outstanding consumption, carried HERE so that A2-R's clean ledger
# cannot be read as the project's. It is not resolved by this sweep, not covered by a new
# allowance, and not treated as zero. `CLOSEOUT-calib-a2.md` §7 is the reconciliation.
CLOSED_SWEEP_UNRESOLVED = {
    "arm_run": "c60/run-k1/attempt1/arms/baseline",
    "note": ("one arm-run of the closed calibration has no usable usage record; its "
             "consumption is UNRESOLVED and the closed sweep's total is a lower bound"),
    "budget_overshoot_tokens": 560_844,
}

STATE_MEANING = {
    "pinned_ran": "the documented command was invoked and ran",
    "pinned_not_runnable": "the documented command was invoked and could not run",
    "pinned_outcome_unknown": "invoked; the result does not settle whether it ran",
    "other_interpreter_only": "the agent used a different interpreter; the documented "
                              "command was never invoked, so this run tests nothing about it",
    "no_relevant_invocation": "no interpreter was invoked at all; NO claim about the repair "
                              "is available from this run, in either direction",
    "gate_refused_interpreter": "the gate refused on an interpreter check",
    "gate_refused_other_environment": "the gate refused on something other than the "
                                      "interpreter; this is not evidence about the repair",
}


def _bar(title: str) -> str:
    return f"\n{title}\n{'-' * len(title)}\n"


def render(rep: dict) -> str:
    cells, ledger = rep["cells"], rep["ledger"]
    out = ["A2-R — the repaired toolchain at one ceiling",
           "=" * 46,
           "",
           "A DEVELOPMENT measurement. It selects no ceiling, compares no arms, and makes no",
           "causal claim against the closed v2 calibration. No selection rule is applied here:",
           "freeze-calib-a2 §5 belongs to the calibration, which is closed with none selected."]

    if len(rep["ceilings"]) > 1:
        out += ["", f"REFUSED TO SUMMARISE: {len(rep['ceilings'])} ceilings present "
                    f"({rep['ceilings']}). A2-R is one operating point; pooling two of them "
                    f"here would be the comparison this design excludes."]
        return "\n".join(out) + "\n"

    ceiling = rep["ceilings"][0] if rep["ceilings"] else None
    out += ["", f"ceiling {ceiling}   |   {len(cells)} of {rep['expected_cells']} arm-runs "
                f"present   |   scratch {rep['scratch']}"]

    # --- 1 functional correctness --------------------------------------------------------
    out.append(_bar("1. Functional correctness (a1-functional-2)"))
    passed = [c for c in cells if c["functional"] == "pass"]
    out.append(f"{len(passed)} of {len(cells)} arm-runs pass. Per cell, no pooling across "
               f"tasks:")
    out.append("")
    out.append(f"  {'task':5} {'arm':9} {'functional':11} {'terminal':10} {'turns':>6} "
               f"{'wall_s':>7}")
    for c in cells:
        out.append(f"  {c['task']:5} {c['arm']:9} {str(c['functional']):11} "
                   f"{c['terminal']:10} {str(c['num_turns']):>6} "
                   f"{str(c['wall_clock_s']):>7}")

    # --- 2 requirement compliance ---------------------------------------------------------
    out.append(_bar("2. Requirement compliance — four independent observations"))
    out.append("The tail requires: fix the behaviour in src/, AND extend the existing test")
    out.append("suite to cover it. A changed file under tests/ is not an extension: a comment")
    out.append("or an unrelated edit satisfies the diff and not the requirement. `unknown` is")
    out.append("used where the evidence is missing and nothing is inferred from edit counts.")
    out.append("")
    out.append(f"  {'task':5} {'arm':9} {'src diff':9} {'test add':9} {'executed':9} "
               f"{'result':8} {'relevance':10} {'a1-scorer-4':11}")
    for c in cells:
        r = c["requirement"]
        out.append(f"  {c['task']:5} {c['arm']:9} {r['source_diff']:9} "
                   f"{r['test_addition']:9} {r['test_executed']:9} {r['test_result']:8} "
                   f"{r['relevance']:10} {str(c['scorer_a1_4']):11}")
    out.append("")
    out.append("`a1-scorer-4` is the REGISTERED compliance scorer, reported under its own name")
    out.append("and never merged with the four observations beside it. Read its ratio with the")
    out.append("known defect in mind: `test_compliance_counterexamples.py` shows a run scoring")
    out.append("4/4 with two checks UNKNOWN, so an unmeasurable check lands in the numerator")
    out.append("and the ratio is an upper bound. That defect is not repaired here -- A1 and the")
    out.append("closed calibration were scored with it -- which is a further reason the four")
    out.append("observations above are kept independent of it.")
    unreviewed = [f"{c['task']}/{c['arm']}" for c in cells
                  if c["requirement"]["test_addition"] == "yes"]
    if unreviewed:
        out.append("")
        out.append(f"{len(unreviewed)} patch(es) add a test and need the evidence-linked "
                   f"review that fills in `relevance`:")
        for c in cells:
            r = c["requirement"]
            if r["test_addition"] != "yes":
                continue
            out.append(f"    {c['task']}/{c['arm']}: "
                       f"{', '.join(r['evidence']['test_nodes']) or '—'}")

    # --- 3 termination --------------------------------------------------------------------
    out.append(_bar("3. Termination — reported separately from correctness"))
    kinds: dict[str, int] = {}
    for c in cells:
        kinds[c["terminal"]] = kinds.get(c["terminal"], 0) + 1
    out.append("   " + "   ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "   none")
    trunc = [c for c in cells if c["truncated"]]
    out.append(f"   {len(trunc)} of {len(cells)} truncated. In v2, 11 of 16 PASSING arm-runs")
    out.append("   ended at max_turns — which is why this is not folded into correctness.")

    # --- 4 the repair ---------------------------------------------------------------------
    out.append(_bar("4. The repaired toolchain — five facts, never an absence"))
    out.append("An absent `No module named pytest` proves nothing in a run that never tried.")
    out.append("")
    states: dict[str, int] = {}
    for c in cells:
        s = c["repair"]["state"]
        states[s] = states.get(s, 0) + 1
    for s, n in sorted(states.items(), key=lambda kv: -kv[1]):
        out.append(f"   {n:>2}  {s:28} {STATE_MEANING.get(s, '')}")
    out.append("")
    out.append(f"  {'task':5} {'arm':9} {'gate':10} {'ident':6} {'pinned':>6} {'ran':>4} "
               f"{'broke':>6} {'other':>6}")
    for c in cells:
        r = c["repair"]
        out.append(f"  {c['task']:5} {c['arm']:9} {r['gate']:10} "
                   f"{'yes' if r['runtime_identity_recorded'] else 'no':6} "
                   f"{r['pinned_invocations']:>6} {r['pinned_ran']:>4} "
                   f"{r['pinned_not_runnable']:>6} "
                   f"{r['other_interpreter_invocations']:>6}")
    silent = [c for c in cells if not c["repair"]["relevant_invocation"]]
    if silent:
        out.append("")
        out.append(f"{len(silent)} arm-run(s) invoked no interpreter at all. They are EXCLUDED")
        out.append("from any statement about the repair rather than counted as clean:")
        for c in silent:
            out.append(f"    {c['task']}/{c['arm']}")
    refused = [c for c in cells if c["repair"]["gate"] == "refused"]
    for c in refused:
        r = c["repair"]
        out.append(f"    GATE REFUSED {c['task']}/{c['arm']} on {r['gate_cause']}: "
                   f"{', '.join(r['gate_failed_checks'])}")

    # --- 5 resources ----------------------------------------------------------------------
    out.append(_bar("5. Total resource use — every arm-run, failures included"))
    out.append(f"   charged     {ledger['tokens_budgeted']:>12,} tokens "
               f"({ledger['tokens_known']:,} measured + "
               f"{ledger['tokens_allowance']:,} allowance)")
    out.append(f"   arm-runs    {ledger['arm_runs']:>12}")
    out.append(f"   unresolved  {ledger['unresolved']:>12}")
    out.append(f"   consumption {'CERTAIN' if ledger['consumption_certain'] else 'A LOWER BOUND':>12}")
    for u in ledger["unresolved_arm_runs"]:
        out.append(f"      {u}")
    out.append("")
    out.append("The soft launch threshold is where this sweep stops STARTING rows. An arm-run")
    out.append("cannot be interrupted part-way, so finishing above it by up to one row is")
    out.append("possible by design, and a largest-observed-row reservation bounds nothing")
    out.append("about the next row's consumption.")

    # --- the closed sweep, still outstanding ----------------------------------------------
    out.append(_bar("The closed calibration is still not fully accounted for"))
    out.append(f"   {CLOSED_SWEEP_UNRESOLVED['arm_run']}")
    out.append(f"   {CLOSED_SWEEP_UNRESOLVED['note']}.")
    out.append(f"   Its charge exceeded the calibration cap by "
               f"{CLOSED_SWEEP_UNRESOLVED['budget_overshoot_tokens']:,} tokens.")
    out.append("   A2-R does not resolve it, does not cover it with a new allowance, and does")
    out.append("   not treat it as zero. It is a separate ledger and stays outstanding.")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    if not scratch.is_dir():
        raise SystemExit(f"no such scratch: {scratch}")
    rep = build(scratch)
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
