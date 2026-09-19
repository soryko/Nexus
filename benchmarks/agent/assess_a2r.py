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
# invocation as the DOCUMENTED one is the variable IN COMMAND POSITION -- not the variable
# appearing somewhere in the text. `test -n "$A2_PYTHON" && echo ready` mentions it and runs
# nothing; a classifier keyed to the substring records that as the pinned interpreter having
# run successfully, which is a positive claim about an event that did not happen.
PINNED_TOKEN = re.compile(r'^\$\{?A2_PYTHON\}?$')
PINNED_ANYWHERE = re.compile(r'\$\{?A2_PYTHON\}?')

# Another interpreter, also in command position: `python3`, `/usr/bin/python3.9`, `pytest`.
INTERPRETER_TOKEN = re.compile(r'^[\w./-]*(?:python\d?(?:\.\d+)?|pytest)$')

# Leading `VAR=value` assignments, which precede the command without being it. This is how
# `PYTHONPATH=src "$A2_PYTHON" -m pytest` is recognised as a pinned invocation and
# `PYTHONPATH=src python3 -m pytest` as a foreign one.
ASSIGNMENTS = re.compile(r'^(?:[A-Za-z_]\w*=(?:"[^"]*"|\'[^\']*\'|\S*)\s+)*')

# Looking an interpreter up, or testing it, is not running it.
LOOKUP = re.compile(r'^(?:which|command|type|whereis|echo|ls|cat|head|tail|grep|rg|find|'
                    r'printf|test|\[|\[\[|stat|file|dirname|basename|env)$')

# Splits a shell command into segments that each have their own command position.
SEGMENT = re.compile(r'(?:\|\||&&|[;|\n])')


def command_token(seg: str) -> str:
    """-> the word in command position, unquoted, or "" when there is none."""
    rest = ASSIGNMENTS.sub("", seg.strip(), count=1).lstrip()
    if not rest:
        return ""
    token = rest.split()[0]
    if len(token) > 1 and token[0] in "\"'" and token[-1] == token[0]:
        token = token[1:-1]
    return token.strip("\"'")


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


def classify_segment(seg: str) -> str:
    """-> 'pinned' | 'other_interpreter' | 'lookup' | 'mention' | 'none'.

    `mention` is the category that did not exist and had to: a segment naming `$A2_PYTHON`
    without invoking it. It is neither evidence for the repair nor evidence against it.
    """
    token = command_token(seg)
    if not token:
        return "none"
    if PINNED_TOKEN.match(token):
        return "pinned"
    if LOOKUP.match(token):
        return "mention" if PINNED_ANYWHERE.search(seg) else "lookup"
    if INTERPRETER_TOKEN.match(token):
        return "other_interpreter"
    return "mention" if PINNED_ANYWHERE.search(seg) else "none"


def classify_command(command: str) -> dict:
    """-> which interpreters this one Bash command actually INVOKES."""
    out = {"pinned": 0, "other_interpreter": 0, "lookup": 0, "mention": 0}
    for seg in segments(command):
        kind = classify_segment(seg)
        if kind in out:
            out[kind] += 1
    return out


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


# --------------------------------------------------------- did the added test actually run

RUNS_PYTEST = re.compile(r'(?:^|\s)(?:-m\s+pytest|pytest)(?:\s|$)')
# Positional arguments: what a pytest invocation was pointed at. Anything beginning with `-`
# is a flag, and `-m pytest` is the module selector rather than a target.
FLAG = re.compile(r'^-')

# The named test did NOT execute, whatever else the command printed.
DID_NOT_EXECUTE = (
    re.compile(r"No module named ['\"]?pytest"),
    re.compile(r'command not found'),
    re.compile(r'\bno tests ran\b', re.I),
    re.compile(r'ERROR collecting', re.I),
    re.compile(r'errors? during collection', re.I),
    re.compile(r'ERROR: (?:not found|file or directory not found)', re.I),
)
COUNT = re.compile(r'\b(\d+) (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)\b')

# The arm-run's own NEGATIVE CONTROL: revert the fix, re-run the new test, show it fails. The
# failure is the POINT -- it is how a regression test is shown to discriminate -- and it is not
# the test's result under the patch the arm actually produced. v2 contained these and so does
# this sweep: `git stash push src/click/core.py && pytest <node> -q` ran in three arm-runs, and
# reading it as the result reported a passing arm-run's own test as failing.
SRC_MUTATION = re.compile(r'git\s+(?:checkout|stash|restore|apply|reset|clean)\b[^|;&]*src/')


def pytest_targets(seg: str) -> list[str]:
    """-> the positional targets of one pytest invocation."""
    words = ASSIGNMENTS.sub("", seg.strip(), count=1).split()
    out, skip = [], True          # skip the interpreter itself
    for w in words:
        if skip:
            skip = False
            continue
        if w in ("-m", "--module"):
            skip = True
            continue
        if w == "pytest" or FLAG.match(w):
            continue
        out.append(w.strip('"\''))
    return out


def pytest_invocations(calls: list[dict]) -> list[dict]:
    """Every call that INVOKES pytest through an interpreter. `echo pytest` is not one.

    A command whose segments run two different interpreters cannot have its single result
    attributed to either, and is marked so rather than credited to one.
    """
    out = []
    for c in calls:
        if (c.get("name") or "") != "Bash":
            continue
        command = (c.get("input") or {}).get("command") or ""
        kinds, targets, ran_pytest = set(), [], False
        for seg in segments(command):
            kind = classify_segment(seg)
            if kind in ("pinned", "other_interpreter") and RUNS_PYTEST.search(seg):
                kinds.add(kind)
                targets += pytest_targets(seg)
                ran_pytest = True
        if not ran_pytest:
            continue
        out.append({"index": c.get("index"), "targets": targets,
                    "mixed": len(kinds) > 1,
                    # Reverting the fix in the same command makes this a control, not a
                    # measurement of the patch.
                    "reverts_source": bool(SRC_MUTATION.search(command)),
                    "result": str(c.get("result") or ""),
                    "command": command[:160]})
    return out


def counts(result: str) -> dict:
    return {k: int(n) for n, k in COUNT.findall(result)}


def executed_named(inv: dict) -> bool | None:
    """-> did the test this invocation NAMED actually execute? None when unsettled.

    `No module named pytest` is an ATTEMPT, not an execution: v2's most common friction
    produced exactly this, and counting it as a run credits the repair with the event it was
    supposed to make possible. Deselection and collection failure are the same shape.
    """
    if any(p.search(inv["result"]) for p in DID_NOT_EXECUTE):
        return False
    c = counts(inv["result"])
    # Deselection needs no clause of its own. A purely deselected run reports no passes and
    # no failures, so it falls through to `None` -- unsettled, which is the right answer. An
    # explicit `deselected -> False` was dead everywhere except `0 passed, 1 xfailed, 5
    # deselected`, where it was WRONG: an xfailed test ran and failed as expected.
    if c.get("passed") or c.get("failed") or c.get("xfailed") or c.get("xpassed"):
        return True
    return None


def shell_writes(cmd: str, path: str) -> bool:
    """Does this shell command WRITE that file?

    Naming a file is not writing it, and `>` is not a redirect wherever it appears. The first
    version of this asked only whether the command contained the path and any `>` at all --
    so `pytest tests/test_context.py -q 2>&1 | tail` was read as a WRITE to
    `tests/test_context.py`, which put the boundary after the last test run in every arm-run
    that redirected stderr, and demoted every piece of execution evidence in the sweep.

    A write is the file appearing as the TARGET: after a redirect, after `tee`, or as the
    operand of `sed -i`, `cp` or `mv`.
    """
    f = re.escape(path)
    return bool(re.search(rf'>>?\s*[\'"]?\S*{f}', cmd)
                or re.search(rf'\btee\b[^|;&]*{f}', cmd)
                or re.search(rf'\bsed\b[^|;&]*\s-i\b[^|;&]*{f}', cmd)
                or re.search(rf'\b(?:cp|mv|install)\b[^|;&]*{f}', cmd))


def last_test_edit(calls: list[dict], files: list[str]) -> int | None:
    """-> the index of the last call that WROTE one of the added test files.

    Used ONLY to WITHHOLD a claim, never to grant one. Issue order is not execution order, so
    a run issued after the last write is not thereby shown to have seen it -- but a run issued
    BEFORE the test file was last written cannot have executed the final added test, and that
    direction is safe.
    """
    seen = None
    for c in calls:
        path = ((c.get("input") or {}).get("file_path") or "")
        name = c.get("name") or ""
        if name in ("Edit", "Write", "NotebookEdit") and any(path.endswith(f) for f in files):
            seen = c.get("index")
        elif name == "Bash":
            cmd = (c.get("input") or {}).get("command") or ""
            if any(shell_writes(cmd, f) for f in files):
                seen = c.get("index")
    return seen


def executed(calls: list[dict], adds: dict) -> dict:
    """Was the ADDED test executed, and with what result?

    Four distinctions the first version of this collapsed, each of which credited an event
    that did not happen:

      mentioning pytest      `echo pytest` contains the word and runs nothing.
      attempting pytest      a pinned invocation answering `No module named pytest` executed
                             no test at all.
      executing a FILE       `pytest tests/test_other.py` runs tests, none of them this one.
      an aggregate result    a suite's `1 passed` is not the added test's result.

    So `yes` requires an invocation that NAMES the node id, is issued after the test file was
    last written, is not mixed, and whose result shows that test ran. Everything else is
    `unknown` with its candidates listed: for twelve patches, reading the evidence beats
    extending this parser.
    """
    invocations = pytest_invocations(calls)
    if not invocations:
        return {"verdict": "no", "why": "no pytest invocation in the run",
                "calls": [], "candidates": [], "controls": []}

    boundary = last_test_edit(calls, adds["files"])
    settled, candidates, controls = [], [], []
    for inv in invocations:
        named = [n for n in adds["nodes"] if n in inv["targets"]
                 or n.split("::")[-1] in inv["targets"]]
        files = [f for f in adds["files"] if any(f in t for t in inv["targets"])]
        whole = not inv["targets"]
        row = {"index": inv["index"], "named": named, "file_only": bool(files and not named),
               "whole_suite": whole, "mixed": inv["mixed"],
               "reverts_source": inv["reverts_source"],
               "counts": counts(inv["result"]), "command": inv["command"]}
        before = (boundary is not None and inv["index"] is not None
                  and inv["index"] < boundary)
        row["before_last_test_edit"] = before
        ran = executed_named(inv)
        row["named_test_ran"] = ran
        if named and not inv["mixed"] and not before and ran is True:
            # A control DID execute the test -- but against reverted source, so it settles
            # neither execution under the final patch nor the test's result.
            (controls if inv["reverts_source"] else settled).append(row)
        else:
            row["why_not_settled"] = (
                "names no added test" if not named else
                "two interpreters in one command" if inv["mixed"] else
                "issued before the test file was last written" if before else
                "the result does not show the named test running")
            candidates.append(row)

    if settled:
        return {"verdict": "yes", "why": "an invocation named the added test and it ran",
                "calls": settled, "candidates": candidates, "controls": controls}
    return {"verdict": UNKNOWN,
            "why": ("the only invocation naming the added test reverted the fix first, so it "
                    "is a control and not a measurement of this patch" if controls else
                    "pytest ran, but no invocation establishes that the ADDED test executed"),
            "calls": [], "candidates": candidates, "controls": controls}


def result_of(settled: list[dict]) -> str:
    """The added test's own result, from an invocation that named it. Never an aggregate."""
    if not settled:
        return UNKNOWN
    passed = failed = False
    for row in settled:
        c = row["counts"]
        if c.get("failed") or c.get("error") or c.get("errors"):
            failed = True
        elif c.get("passed") or c.get("xpassed"):
            passed = True
    return "failed" if failed else "passed" if passed else UNKNOWN


def requirement_compliance(record: dict, calls: list[dict]) -> dict:
    """Four independent observations. `unknown` where the evidence is missing.

    Nothing here is inferred from how many times the agent edited a file. An edit count is a
    count of edits.
    """
    patch = record.get("patch")
    provenance = record.get("patch_provenance", "absent")
    if patch is None:
        # ABSENT, not empty. An empty patch is a measurement -- the arm-run changed nothing --
        # and falls through below to `source_diff: no`. A missing one measures nothing.
        why = {"absent": "no patch.diff beside this arm-run's record",
               "inconsistent_missing_sidecar":
                   "the record claims a non-empty patch but no patch.diff is present"
               }.get(provenance, f"patch unavailable ({provenance})")
        return {"source_diff": UNKNOWN, "test_addition": UNKNOWN, "test_executed": UNKNOWN,
                "test_result": UNKNOWN, "relevance": "unreviewed",
                "why_unknown": why, "evidence": {"patch_provenance": provenance}}
    files = patch_files(patch)
    adds = test_additions(patch)
    src = any(f.startswith("src/") or "/src/" in f for f in files)
    run = (executed(calls, adds) if adds["nodes"]
           else {"verdict": UNKNOWN, "why": "no added test to look for",
                 "calls": [], "candidates": [], "controls": []})
    # Did the arm-run demonstrate that its own test discriminates -- revert the fix, watch the
    # new test fail? That is a FINDING about the test's quality, reported in its own right and
    # never folded into the test's result.
    disc = [k for k in run["controls"] if k["counts"].get("failed")
            or k["counts"].get("error") or k["counts"].get("errors")]

    return {
        # (a) a final source diff
        "source_diff": "yes" if src else "no",
        # (b) a substantive addition to or extension of the existing suite
        "test_addition": "yes" if adds["nodes"] else "no",
        # (c) execution of THAT test -- not of a file, not of the suite, not an attempt
        "test_executed": run["verdict"] if adds["nodes"] else UNKNOWN,
        # (d) its own result, from an invocation that named it
        "test_result": result_of(run["calls"]),
        # The arm-run's own negative control on its own test: reverted the fix and watched
        # the new test fail. Not part of (d) -- that failure is the point.
        "discriminating_control": ("yes" if disc else
                                   "ran, did not discriminate" if run["controls"] else "no"),
        # NOT machine-decidable: whether the added test covers THIS bug. Twelve patches is a
        # readable number; a reviewer fills this in and the report shows it empty until then.
        "relevance": "unreviewed",
        "why_execution_unsettled": run["why"] if run["verdict"] == UNKNOWN else None,
        "evidence": {"patch_provenance": provenance,
                     "patch_files": files, "test_nodes": adds["nodes"],
                     "test_files": adds["files"], "nested_skipped": adds["nested"],
                     "execution": run["calls"],
                     "negative_controls": run["controls"],
                     # Listed for the manual review, NOT counted as execution.
                     "execution_candidates": run["candidates"]},
    }


# ----------------------------------------------------------------- the runner's own format

# Everything below this line exists because the reporter was written against a record shape
# that no runner produces. It read `record["functional"]`, `record["num_turns"]` and
# `record["patch"]`; `run_arms_isolated` writes `record["scored"]["passed"]`,
# `record["result"]["num_turns"]`, and the patch as a SIDECAR FILE beside the record. Against
# real rows the report would have counted every genuine functional pass as no pass at all and
# left every patch observation unknown -- an integration defect that no amount of testing
# against invented fixtures could surface, because the fixtures agreed with the reader.
#
# The fix is an adapter AT THE BOUNDARY, in one place, so the analysis above it never learns
# two shapes. `replay_v2.py` is the control: the 27 saved v2 arm-runs must come back as 16
# functional passes, 11 of them terminated at `max_turns`.

FUNCTIONAL_SCORER = "a1-functional-2"
NOT_SCORED = "not_scored"

# The compliance artifact's field names, defined HERE and imported by the writer
# (`score_a2r_compliance.py`), so the two cannot drift apart. They already had, once, in the
# other direction: this reporter read three record fields the runner does not write.
COMPLIANCE_FILES = ("compliance-a2r.json", "compliance.json")
COMPLIANCE_KEY = ("task", "arm")
COMPLIANCE_RATIO = "requirement_compliance"
COMPLIANCE_UNKNOWN = "unknown_count"


def read_patch(arm_dir: Path, rec: dict) -> tuple[str | None, str]:
    """-> (patch text, provenance).

    A MISSING patch and an EMPTY patch are different measurements and must not collapse:

      absent        no `patch.diff` beside the record. Nothing is known about what the
                    arm-run changed; every patch observation is `unknown`.
      empty         `patch.diff` exists and is empty. The arm-run changed NOTHING, which is a
                    measurement -- `source_diff: no` is a finding, not a gap.
      inconsistent  the record claims `patch_bytes > 0` and the sidecar is absent or empty.
                    Reported rather than silently preferring one of the two.
    """
    f = arm_dir / "patch.diff"
    claimed = rec.get("patch_bytes")
    if not f.exists():
        if claimed:
            return None, "inconsistent_missing_sidecar"
        return None, "absent"
    text = f.read_text()
    if claimed is not None and bool(claimed) != bool(text):
        return text, "inconsistent_bytes"
    return text, ("empty" if not text else "sidecar")


def functional_of(rec: dict) -> tuple[str, str | None]:
    """-> ('pass'|'fail'|'unknown', why it is unknown).

    `scored.passed` is the functional verdict; a missing `scored` block is UNKNOWN and never
    a failure. A scorer that TIMED OUT did not measure the arm-run, so its `passed: false` is
    an instrument condition and is not reported as a functional failure.
    """
    s = rec.get("scored")
    if not isinstance(s, dict):
        return UNKNOWN, "no functional score recorded for this arm-run"
    if s.get("timed_out"):
        return UNKNOWN, "the functional scorer timed out; the arm-run is unmeasured"
    passed = s.get("passed")
    if passed is None:
        return UNKNOWN, "the functional score records no verdict"
    return ("pass" if passed else "fail"), None


def read_compliance(scratch: Path) -> dict:
    """-> {(task, arm): row} from a compliance artifact, or {} when none has been produced.

    `run_arms_isolated` does NOT score requirement compliance: `a1-scorer-4` is a separate
    offline pass writing its own file. An empty mapping therefore means NOT SCORED, which the
    report must say -- printing a ratio, or an unknown count, for a scorer that never ran
    would imply a measurement that does not exist.
    """
    for name in COMPLIANCE_FILES:
        f = scratch / name
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(rows, list):
            return {tuple(r.get(k) for k in COMPLIANCE_KEY): r
                    for r in rows if isinstance(r, dict)}
    return {}


def normalise(rec: dict, arm_dir: Path, compliance: dict, task: str, arm: str) -> dict:
    """The runner's saved arm-run, in the one shape everything above this line reads."""
    functional, why = functional_of(rec)
    patch, provenance = read_patch(arm_dir, rec)
    terminal = rec.get("terminal") or {}
    result = rec.get("result") or {}
    crow = compliance.get((task, arm))
    return {
        "functional": functional,
        "functional_unknown_because": why,
        "functional_scorer": (rec.get("scored") or {}).get("scorer_version"),
        "num_turns": result.get("num_turns"),
        "wall_clock_s": rec.get("wall_clock_s"),
        "terminal": terminal.get("terminal", UNKNOWN),
        "scored": terminal.get("scored"),
        "truncated": terminal.get("truncated"),
        "usage": rec.get("usage"),
        "tool_calls": rec.get("tool_calls") or [],
        "patch": patch,
        "patch_provenance": provenance,
        "patch_bytes": rec.get("patch_bytes"),
        "patch_touches_src": rec.get("patch_touches_src"),
        "runtime_identity": rec.get("runtime_identity") or {},
        "gate_checks": rec.get("gate_checks") or [],
        # NOT `unknown`: a scorer that never ran has not failed to decide anything.
        # A row the scorer could not run on carries a null ratio and a reason; that is not a
        # compliance result, so it reads NOT_SCORED like an absent artifact would.
        "scorer_a1_4": ((crow.get(COMPLIANCE_RATIO) or NOT_SCORED) if crow else NOT_SCORED),
        "scorer_a1_4_unknown": (crow.get(COMPLIANCE_UNKNOWN) if crow else None),
        "scorer_a1_4_source": (
            "compliance artifact" if crow and crow.get(COMPLIANCE_RATIO)
            else f"the scorer could not run: {crow.get('why_not')}" if crow
            else "a1-scorer-4 has not been run over this sweep"),
    }


# ----------------------------------------------------------------- reading a sweep

def read_cells(scratch: Path) -> list[dict]:
    """Every arm-run A2-R wrote, normalised at the boundary. See `normalise` above."""
    compliance = read_compliance(scratch)
    cells = []
    for records in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(records.read_text())
        task = data.get("task") or records.parent.parent.name.replace("run-", "")
        ceiling = int(re.sub(r'\D', '', records.parents[2].name) or 0)
        for rec in data.get("records", []):
            arm = rec.get("arm") or "?"
            n = normalise(rec, records.parent / "arms" / arm, compliance, task, arm)
            cells.append({
                "ceiling": ceiling, "task": task, "arm": arm,
                "functional": n["functional"],
                "functional_unknown_because": n["functional_unknown_because"],
                "scorer_a1_4": n["scorer_a1_4"],
                "scorer_a1_4_unknown": n["scorer_a1_4_unknown"],
                "scorer_a1_4_source": n["scorer_a1_4_source"],
                "terminal": n["terminal"], "scored": n["scored"],
                "truncated": n["truncated"],
                "num_turns": n["num_turns"], "wall_clock_s": n["wall_clock_s"],
                "tokens": _tokens(n["usage"]),
                "patch_provenance": n["patch_provenance"],
                "repair": repair_check(n, n["tool_calls"]),
                "requirement": requirement_compliance(n, n["tool_calls"]),
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
    unmeasured = [c for c in cells if c["functional"] == UNKNOWN]
    out.append(f"{len(passed)} of {len(cells)} arm-runs pass. Per cell, no pooling across "
               f"tasks:")
    if unmeasured:
        out.append(f"{len(unmeasured)} arm-run(s) are UNMEASURED, not failed:")
        for c in unmeasured:
            out.append(f"    {c['task']}/{c['arm']}: {c['functional_unknown_because']}")
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
               f"{'result':8} {'relevance':10} {'a1-scorer-4':12}")
    for c in cells:
        r = c["requirement"]
        u = c["scorer_a1_4_unknown"]
        scorer = f"{c['scorer_a1_4']}{f' +{u}?' if u else ''}"
        out.append(f"  {c['task']:5} {c['arm']:9} {r['source_diff']:9} "
                   f"{r['test_addition']:9} {r['test_executed']:9} {r['test_result']:8} "
                   f"{r['relevance']:10} {scorer:12}")
    out.append("")
    sources = sorted({c["scorer_a1_4_source"] for c in cells})
    if all(c["scorer_a1_4"] == NOT_SCORED for c in cells):
        out.append("`a1-scorer-4` HAS NOT BEEN RUN over this sweep. `run_arms_isolated` does")
        out.append("not score requirement compliance -- it is a separate offline pass -- so the")
        out.append("column reads `not_scored`. That is not `unknown`: a scorer that never ran")
        out.append("has not failed to decide anything, and printing a ratio or an unknown count")
        out.append("here would imply a measurement that does not exist.")
    else:
        out.append("`a1-scorer-4` is the REGISTERED compliance scorer, reported under its own")
        out.append("name and never merged with the four observations beside it. Its ratio is")
        out.append("over the SETTLED checks only -- `tally()` excludes unknowns from both")
        out.append("halves -- so it is shown with the unknown count beside it where the")
        out.append("artifact carries one: `4/4 +2?` is four passes among four settled checks")
        out.append("with two unsettled, not four of six. Where the artifact carries no unknown")
        out.append("count, none is shown -- `+0?` would be a claim it does not make.")
        out.append(f"    provenance: {'; '.join(sources)}")
    odd = [c for c in cells if c["patch_provenance"].startswith("inconsistent")]
    if odd:
        out.append("")
        out.append("PATCH EVIDENCE INCONSISTENT for:")
        for c in odd:
            out.append(f"    {c['task']}/{c['arm']}: {c['patch_provenance']} "
                       f"(record claims patch_bytes)")
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
