"""Describe what each calibration arm-run DID, from the records already saved. No model runs.

`CLOSEOUT-calib-a2.md` §9 step 1. The calibration selected no ceiling, and the finding that
should drive the next decision is that **11 of its 16 hidden-check passes ended at
`max_turns`** -- functional success and normal termination are substantially different
quantities here, and a pooled truncation rate cannot tell a run that solved the task and was
then cut off from one that never solved it. This program separates them descriptively.

It is a DESCRIPTION, not an explanation. Four limits are built into what it reports, and each
of them is a claim it deliberately does not make:

  issue order is not execution order   `tool_calls` carries the order the model ISSUED calls.
                                       Nothing here establishes that they executed in that
                                       order, so "after the last source write" means after it
                                       IN ISSUE ORDER and is reported under that name.
  an errored command may still write    A `cat > f <<EOF` that ends in a nonzero exit can have
                                       written the file before failing -- measured here: one
                                       compound command wrote its repro AND failed on pytest
                                       in the same call. Exit status is reported beside a
                                       write, never as a veto on it.
  a passing patch does not date itself  The record carries a FINAL patch, not a history of one.
                                       When a patch first became correct is NOT ANSWERED and
                                       is not inferable from anything here.
  a completion message is not a verdict A run saying "tests pass" is a statement the model
                                       made, not a verification that happened. Completion
                                       messages are quoted and never scored.

Source writes are found through the `Edit` tool and through Bash redirects, `sed -i` and `cp`
into `src/`. That the first channel is essentially the only one used is measured rather than
assumed: every arm-run whose final patch touches `src/` has at least one such call, and every
arm-run with none has a patch that does not touch `src/`. The consistency check is reported.

Usage:  diagnose_workflow.py <scratch> [--json <out.json>] [--only <task>/<arm>]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import report_calibration as RC                                         # noqa: E402

NOTES_FILE = "NOTES-FROM-EARLIER-WORK.md"

# A command that RUNS pytest, as against one that merely names it -- `which pytest` and
# `find / -name pytest` are the agent hunting for the binary, which is friction to report and
# not a test attempt to count.
RUNS_PYTEST = re.compile(r'(?:python[0-9.]*\s+-m\s+pytest|(?:^|[;&|]\s*|/)pytest)\b')
WRITE_INTO_SRC = re.compile(r'(?:>>?|tee(?:\s+-a)?)\s*["\']?(?:\./)?(src/[\w./-]+)')
SED_IN_PLACE = re.compile(r'sed\s+(?:-[a-zA-Z]*i[a-zA-Z]*\s|--in-place)')
CP_INTO_SRC = re.compile(r'\bcp\s+\S+\s+(?:\./)?src/')
# git operations that CHANGE SOURCE STATE without a redirect or `sed -i`: `git stash push --
# src/...` reverts the working tree, `git checkout -- src/...` discards. Twelve occur in this
# sweep, mostly as a negative control -- revert the fix, re-run the tests, restore it -- and
# all but one land AFTER the arm-run's last `Edit`. They are mutation events, they are not
# authored edits, and counting them as either would be wrong. They are reported on their own
# and are the reason the phrase here is "last identified source-EDIT event": the final-diff
# control validates run-level detection of authored edits, NOT a complete mutation timeline.
GIT_SRC_MUTATION = re.compile(r'git\s+(?:checkout|stash|restore|apply|reset|clean)\b[^|;&]*src/')
INVESTIGATE = re.compile(r'^\s*(grep|rg|cat|ls|find|head|tail|sed -n|awk|wc|git log|git show|git diff)\b')
# Running a local script -- `python3 repro.py`. This is verification too, and two arm-runs did
# ALL of theirs this way without ever invoking pytest, one of them passing the hidden checks.
# `-m pytest` and `-c "..."` carry no `.py`, so neither is matched here.
RUNS_SCRIPT = re.compile(r'python[0-9.]*\s+(?:-\S+\s+)*([\w./-]+\.py)\b')
WRITE_TARGET = re.compile(r'(?:>>?|tee(?:\s+-a)?)\s*["\']?([\w./-]+\.py)')
# Searching the HOST for another copy of click to compare the fixture against. Reported
# because it is a distinctive way turns were spent, and because its INTENSITY differs between
# outcomes while its presence does not -- which is the sort of thing a count makes visible and
# a yes/no does not.
REFERENCE_HUNT = re.compile(r'find\s+/\s|find / -name|site-packages/click|uv/archive|'
                            r'\.cache/uv|pip show click', re.I)

# Observable friction. Each is a string that appeared in a tool RESULT; none of them is a cause.
FRICTION = {
    "no_module": re.compile(r'No module named'),
    "denied": re.compile(r'Operation not permitted|Permission denied'),
    "not_found": re.compile(r'command not found|No such file or directory'),
    "traceback": re.compile(r'Traceback \(most recent call last\)'),
}

PYTEST_COUNTS = re.compile(r'(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed)')


def pytest_outcome(result: str) -> dict:
    """What a test attempt's output SAYS, as a small dict. Never a pass/fail judgement."""
    out: dict = {"counts": {}, "signals": [], "exit": None}
    if result.startswith("Exit code"):
        head = result.split("\n", 1)[0]
        m = re.search(r'Exit code (\d+)', head)
        out["exit"] = int(m.group(1)) if m else None
    for n, word in PYTEST_COUNTS.findall(result):
        key = "error" if word.startswith("error") else word
        out["counts"][key] = out["counts"].get(key, 0) + int(n)
    for name, pat in FRICTION.items():
        if pat.search(result):
            out["signals"].append(name)
    if not out["counts"] and not out["signals"]:
        out["signals"].append("no_pytest_summary_in_output")
    return out


def classify_call(tc: dict, arm: str) -> dict:
    """One tool call -> the descriptive categories it belongs to. A call may be several."""
    name = tc.get("name") or "?"
    inp = tc.get("input") or {}
    result = str(tc.get("result") or "")
    kinds: list[str] = []
    detail = ""

    if name == "Edit":
        path = inp.get("file_path") or ""
        detail = path
        kinds.append("src_edit" if "/src/" in path or path.startswith("src/") else "other_edit")
        if "/tests/" in path or path.startswith("tests/"):
            kinds[-1] = "test_edit"
    elif name == "Read":
        path = inp.get("file_path") or ""
        detail = path
        # Retrieval is arm-specific: only the notes arm HAS a notes file. Every arm looks for
        # one in its first call or two, and for baseline and nexus that probe finds nothing --
        # counting it as retrieval would report a delivery that did not happen.
        kinds.append("retrieval" if (path.endswith(NOTES_FILE) and arm == "notes")
                     else "notes_probe" if path.endswith(NOTES_FILE) else "read")
    elif name.startswith("mcp__nexus__"):
        kinds.append("retrieval" if arm == "nexus" else "notes_probe")
        detail = json.dumps(inp)[:120]
    elif name == "Bash":
        cmd = inp.get("command") or ""
        detail = cmd
        if RUNS_PYTEST.search(cmd):
            kinds.append("test_attempt")
        elif RUNS_SCRIPT.search(cmd):
            kinds.append("repro_run")
        if WRITE_INTO_SRC.search(cmd) or (SED_IN_PLACE.search(cmd) and "src/" in cmd) \
                or CP_INTO_SRC.search(cmd):
            kinds.append("src_edit")
        if GIT_SRC_MUTATION.search(cmd):
            kinds.append("git_src_mutation")
        if NOTES_FILE in cmd:
            kinds.append("retrieval" if arm == "notes" else "notes_probe")
        if INVESTIGATE.match(cmd.strip()):
            kinds.append("investigate")
        if not kinds:
            kinds.append("other_bash")
    else:
        kinds.append("other")

    friction = [n for n, p in FRICTION.items() if p.search(result)]
    exit_code = None
    if result.startswith("Exit code"):
        m = re.search(r'Exit code (\d+)', result.split("\n", 1)[0])
        exit_code = int(m.group(1)) if m else None

    return {"index": tc.get("index"), "tool": name, "kinds": kinds, "detail": detail,
            "friction": friction, "exit_code": exit_code,
            "result_empty": result.strip() == "",
            "outcome": pytest_outcome(result) if "test_attempt" in kinds else None}


def completion_messages(arm_dir: Path, limit: int = 3) -> tuple[list[str], list[str]]:
    """-> (last text blocks the model emitted, evidence gaps found reading the trace).

    Deduplicated by message ID for the same reason the usage reconciliation is: assistant
    messages repeat, and a repeated message is the same message seen again.
    """
    trace = arm_dir / "trace.jsonl"
    gaps: list[str] = []
    if not trace.exists():
        return [], ["no trace.jsonl"]
    seen: dict[str, str] = {}
    order: list[str] = []
    for line in trace.open(errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            gaps.append("unparseable trace line")
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        mid = msg.get("id")
        for blk in (msg.get("content") or []):
            if blk.get("type") == "text" and blk.get("text", "").strip():
                if mid not in seen:
                    seen[mid] = blk["text"].strip()
                    order.append(mid)
    return [seen[m] for m in order[-limit:]], gaps


def describe(rec_path: Path, data: dict, r: dict, ceiling: int) -> dict:
    task, arm = data.get("task"), r.get("arm")
    arm_dir = rec_path.parent / "arms" / str(arm)
    calls = [classify_call(tc, arm) for tc in (r.get("tool_calls") or [])]
    term = (r.get("terminal") or {}).get("terminal")
    passed = (r.get("scored") or {}).get("passed")
    touches_src = r.get("patch_touches_src")

    src_edits = [c for c in calls if "src_edit" in c["kinds"]]
    last_src = src_edits[-1]["index"] if src_edits else None
    after = [c for c in calls if last_src is not None and (c["index"] or 0) > last_src]

    tests = [c for c in calls if "test_attempt" in c["kinds"]]
    tests_after = [c for c in tests if last_src is not None and (c["index"] or 0) > last_src]
    repros = [c for c in calls if "repro_run" in c["kinds"]]

    # Repeated investigation: the same file read again, or the same command issued again.
    reads = Counter(c["detail"] for c in calls if "read" in c["kinds"])
    repeated_reads = {Path(k).name: v for k, v in reads.items() if v > 1}
    re_read_calls = sum(v - 1 for v in reads.values() if v > 1)
    # Rewriting the same scratch file again is the shape repeated investigation actually took:
    # five successive `cat > repro.py <<EOF` in one arm-run, each a slightly different body,
    # so an exact-string comparison of commands scores it zero. The TARGET is what repeats.
    targets = Counter(m for c in calls if c["tool"] == "Bash"
                      for m in WRITE_TARGET.findall(c["detail"]))
    repro_rewrites = {k: v for k, v in targets.items() if v > 1}
    repeated_cmds = sum(v - 1 for v in Counter(
        c["detail"].strip() for c in calls if c["tool"] == "Bash").values() if v > 1)

    msgs, gaps = completion_messages(arm_dir)

    # Evidence gaps, stated rather than smoothed over.
    if not (r.get("result") or {}):
        gaps.append("no terminal envelope (killed before it was emitted)")
    if (r.get("result") or {}).get("num_turns") is None and term != "timeout":
        gaps.append("num_turns absent")
    if any(c["result_empty"] for c in calls):
        gaps.append(f"{sum(1 for c in calls if c['result_empty'])} tool call(s) with empty output")
    if touches_src and not src_edits:
        gaps.append("patch touches src but no source-write call was identified")
    if src_edits and not touches_src:
        gaps.append("source-write call(s) identified but the final patch does not touch src")

    # Failure anatomy. Patch status and obstruction are SEPARATE axes: obstruction can
    # coexist with either patch outcome and is not established as its cause.
    if passed:
        patch_status = "passing_source_diff"
    elif touches_src is True:
        patch_status = "failing_source_diff"
    elif touches_src is False:
        patch_status = "no_source_diff"
    else:
        patch_status = "unknown"

    hunt = sum(1 for c in calls if c["tool"] == "Bash" and REFERENCE_HUNT.search(c["detail"]))
    friction = Counter()
    for c in calls:
        for f in c["friction"]:
            friction[f] += 1
    errored = sum(1 for c in calls if c["exit_code"] not in (None, 0))

    return {
        "ceiling": ceiling, "task": task, "arm": arm,
        "functional": ("pass" if passed else "fail" if passed is False else "unknown"),
        "terminal": term,
        "truncated": bool((r.get("terminal") or {}).get("truncated")),
        "tool_calls": len(calls),
        "num_turns": (r.get("result") or {}).get("num_turns"),
        "wall_clock_s": r.get("wall_clock_s"),

        "patch_status": patch_status,
        "patch_touches_src": touches_src,
        "patch_bytes": r.get("patch_bytes"),

        "src_edit_calls": [{"index": c["index"], "tool": c["tool"],
                             "path": Path(c["detail"]).name if c["tool"] == "Edit"
                             else c["detail"][:80]} for c in src_edits],
        "last_src_edit_index": last_src,
        "last_src_edit_confidence": (
            "none identified" if last_src is None else
            "high (Edit tool call naming a src path)"
            if src_edits[-1]["tool"] == "Edit" else
            "medium (shell write idiom naming a src path)"),

        "calls_after_last_src_edit": len(after),
        "after_last_src_edit": {
            "test_attempts": len(tests_after),
            "repro_runs": sum(1 for c in after if "repro_run" in c["kinds"]),
            "test_edits": sum(1 for c in after if "test_edit" in c["kinds"]),
            "retrieval": sum(1 for c in after if "retrieval" in c["kinds"]),
            "reads": sum(1 for c in after if "read" in c["kinds"]),
            "investigate": sum(1 for c in after if "investigate" in c["kinds"]),
            "other": sum(1 for c in after if set(c["kinds"]) <= {"other_bash", "other",
                                                                "other_edit"}),
        } if last_src is not None else None,

        "test_attempts": [{"index": c["index"], "after_last_src_edit":
                           last_src is not None and (c["index"] or 0) > last_src,
                           "command": c["detail"][:110].replace("\n", " ; "),
                           "observed": c["outcome"]} for c in tests],
        "test_attempt_count": len(tests),

        "retrieval_calls": sum(1 for c in calls if "retrieval" in c["kinds"]),
        "notes_probe_calls": sum(1 for c in calls if "notes_probe" in c["kinds"]),
        "test_edit_calls": sum(1 for c in calls if "test_edit" in c["kinds"]),
        "repro_run_count": len(repros),
        "repeated_reads": repeated_reads,
        "re_read_calls": re_read_calls,
        "repro_rewrites": repro_rewrites,
        "repeated_commands": repeated_cmds,
        "reference_hunt_commands": hunt,
        # Source-state mutations that are NOT authored edits. Reported separately, and never
        # folded into the edit timeline: their presence is why no claim is made here about
        # when the tree first held a correct patch.
        "git_src_mutation_calls": sum(1 for c in calls if "git_src_mutation" in c["kinds"]),
        "git_src_mutations_after_last_edit": sum(
            1 for c in calls if "git_src_mutation" in c["kinds"]
            and last_src is not None and (c["index"] or 0) > last_src),

        "obstruction": {"errored_commands": errored, **dict(friction),
                        "permission_denials": len(r.get("permission_denials") or [])},

        "completion_messages": msgs,
        "evidence_gaps": sorted(set(gaps)),
    }


def build(scratch: Path, launch: Path) -> dict:
    lr = RC.parse_launch_record(launch)
    rows: list[dict] = []
    for rec in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(rec.read_text())
        ident = data.get("identity")
        ceiling = (ident or {}).get("max_turns_applied") or data.get("max_turns")
        if RC.identity_mismatch(ident, data.get("task"), ceiling, lr):
            continue                      # quarantined / void: not this sweep
        for r in data.get("records", []):
            if not (r.get("terminal") or {}).get("scored"):
                continue
            rows.append(describe(rec, data, r, ceiling))
    rows.sort(key=lambda x: (x["ceiling"], x["task"], x["arm"]))

    consistency = {
        "rows": len(rows),
        "patch_touches_src_without_write_call":
            [f"{r['ceiling']}/{r['task']}/{r['arm']}" for r in rows
             if r["patch_touches_src"] and not r["src_edit_calls"]],
        "write_call_without_patch_touching_src":
            [f"{r['ceiling']}/{r['task']}/{r['arm']}" for r in rows
             if r["src_edit_calls"] and not r["patch_touches_src"]],
    }
    return {"rows": rows, "consistency": consistency}


def render(rep: dict) -> str:
    rows, out = rep["rows"], []
    w = out.append
    w("=" * 118)
    w("A2 CALIBRATION -- WORKFLOW DIAGNOSTIC (descriptive; computed from saved records, no "
      "model invoked)")
    w("=" * 118)
    w("Issue order, not execution order. An errored command may still have written. A passing")
    w("patch does not date itself. A completion message is not a verification.")

    w("\nONE ROW PER ARM-RUN")
    w(f"  {'ceil':<5}{'task':<5}{'arm':<9}{'verdict':<8}{'terminal':<10}{'calls':<6}"
      f"{'lastSrc':<8}{'after':<6}{'test':<5}{'t.aft':<6}{'repro':<6}{'tEdit':<6}"
      f"{'retr':<5}{'rerd':<5}{'rwrt':<5}{'gitm':<5}{'err':<4}")
    for r in rows:
        a = r["after_last_src_edit"] or {}
        dash = lambda v: v if r["last_src_edit_index"] is not None else "-"     # noqa: E731
        w(f"  {r['ceiling']:<5}{r['task']:<5}{r['arm']:<9}{r['functional']:<8}"
          f"{r['terminal']:<10}{r['tool_calls']:<6}"
          f"{(r['last_src_edit_index'] if r['last_src_edit_index'] is not None else '-'):<8}"
          f"{dash(r['calls_after_last_src_edit']):<6}"
          f"{r['test_attempt_count']:<5}{dash(a.get('test_attempts', 0)):<6}"
          f"{r['repro_run_count']:<6}{r['test_edit_calls']:<6}"
          f"{r['retrieval_calls']:<5}{r['re_read_calls']:<5}"
          f"{sum(v - 1 for v in r['repro_rewrites'].values()):<5}"
          f"{r['git_src_mutation_calls']:<5}{r['obstruction']['errored_commands']:<4}")
    w("  lastSrc = index of the last identified source-EDIT event (ISSUE order); after = "
      "calls issued after it.")
    w("  test / t.aft = pytest invocations, total and after that write. repro = local scripts "
      "run instead.")
    w("  tEdit = edits under tests/; retr = retrieval delivered to the arm that has any.")
    w("  rerd = re-reads of a file already read; rwrt = rewrites of a scratch file already "
      "written;")
    w("  err = tool calls returning a nonzero exit (a write inside one may still have "
      "landed);")
    w("  gitm = git operations that CHANGE SOURCE STATE (stash/checkout/restore) -- mutation "
      "events, not")
    w("  authored edits, mostly negative controls, and NOT part of the edit timeline above.")

    passing_trunc = [r for r in rows if r["functional"] == "pass" and r["truncated"]]
    w(f"\n(1) AFTER THE LAST IDENTIFIED SOURCE-EDIT EVENT -- the {len(passing_trunc)} "
      f"passing-but-truncated arm-runs")
    w("    The final-diff control validates run-level detection of authored edits. It does NOT")
    w("    establish a complete mutation timeline: `git stash push -- src/...` changes source")
    w("    state with no redirect and no `sed -i`, and most of this sweep's occurrences land")
    w("    after the last Edit. When a patch first became correct stays unanswered.")
    for r in passing_trunc:
        a = r["after_last_src_edit"] or {}
        w(f"  {r['ceiling']}/{r['task']}/{r['arm']}: last source-edit event at call "
          f"{r['last_src_edit_index']} of {r['tool_calls']} "
          f"({r['last_src_edit_confidence']})"
          + (f"; {r['git_src_mutations_after_last_edit']} git source mutation(s) after it"
             if r["git_src_mutations_after_last_edit"] else ""))
        w(f"      then {r['calls_after_last_src_edit']} calls: "
          f"{a.get('test_attempts', 0)} pytest, {a.get('repro_runs', 0)} repro runs, "
          f"{a.get('test_edits', 0)} test-file edits, {a.get('retrieval', 0)} retrieval, "
          f"{a.get('reads', 0)} reads, {a.get('investigate', 0)} inspections, "
          f"{a.get('other', 0)} other")
        for t in r["test_attempts"]:
            if not t["after_last_src_edit"]:
                continue
            o = t["observed"]
            desc = ", ".join(f"{v} {k}" for k, v in o["counts"].items()) or "no summary"
            sig = (" [" + ",".join(o["signals"]) + "]") if o["signals"] else ""
            w(f"      test @{t['index']}: {desc}{sig}")
        if r["completion_messages"]:
            w(f"      last message: \"{r['completion_messages'][-1][:150]}\"")
        if r["evidence_gaps"]:
            w(f"      gaps: {'; '.join(r['evidence_gaps'])}")

    fails = [r for r in rows if r["functional"] == "fail"]
    w(f"\n(2) FAILURES -- {len(fails)} arm-runs. Patch status and obstruction are separate "
      f"axes.")
    by_status: dict[str, list] = {}
    for r in fails:
        by_status.setdefault(r["patch_status"], []).append(r)
    for status, group in sorted(by_status.items()):
        w(f"  {status}: {len(group)}")
        for r in group:
            ob = {k: v for k, v in r["obstruction"].items() if v}
            w(f"      {r['ceiling']}/{r['task']}/{r['arm']}  {r['tool_calls']} calls, "
              f"{r['test_attempt_count']} pytest, {r['repro_run_count']} repro runs, "
              f"{r['retrieval_calls']} retrieval, {r['re_read_calls']} re-reads, "
              f"{r['reference_hunt_commands']} host searches for another click copy")
            w(f"          obstruction observed: {ob or 'none'}")
            if r["completion_messages"]:
                w(f"          last message: \"{r['completion_messages'][-1][:130]}\"")

    w("\nCONSISTENCY OF THE SOURCE-EDIT DETECTOR (run level; not a mutation timeline)")
    c = rep["consistency"]
    w(f"  arm-runs described                                  {c['rows']}")
    w(f"  patch touches src but no write call identified      "
      f"{c['patch_touches_src_without_write_call'] or 'none'}")
    w(f"  write call identified but patch does not touch src  "
      f"{c['write_call_without_patch_touching_src'] or 'none'}")
    w("=" * 118)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    scratch = Path(argv[1])
    launch = Path(argv[argv.index("--launch") + 1]) if "--launch" in argv else RC.DEFAULT_LAUNCH
    rep = build(scratch, launch)
    print(render(rep))
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"\nmachine-readable table -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
