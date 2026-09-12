"""Task-requirement compliance: structural checks, plus one discriminating test.

A prior version scored deliverables with heuristics that a review broke in four ways at
once -- an arbitrary source edit, `assert True`, a `status`-only memory call and the reply
"NOT DONE" together scored 5/5. Each heuristic confused a *proxy* with the thing:

  file location        is not relevance
  a new assertion      is not regression coverage
  a `status` call      is not prior-work evidence
  `"DONE" in text`     matches "NOT DONE"

A second review broke two of the replacements:

  a nonzero pytest exit is not regression coverage either. A syntax error in the added
  test, a collection failure with an unrelated cause, and a test that was already failing
  on the fixture all exit nonzero. The probe now runs three trees and requires a specific
  transition: the added test is absent-or-passing on the untouched fixture, fails in the
  way the task predicts once its own test hunks are applied, and passes on the arm's full
  patch. `d4` is the one task where a collection error is the legitimate pre-fix signature
  -- an unregistered marker errors at collection -- so for `d4` the cause is matched, not
  merely the exit code.

  a call ISSUED before an edit has not necessarily DELIVERED before it. With parallel tool
  use the result can arrive afterwards, and the old check compared issue order only, so a
  memory read whose content landed after the edit still scored. It compares the response's
  arrival against the first edit's issue now. That check also looked exclusively for
  `Edit`/`Write` against `src/click/` -- which `d4` never touches, since its deliverable is
  `pyproject.toml`, and which a `Bash` heredoc or `sed -i` sidesteps on any task. Both are
  counted.

A third review broke the two replacements in turn, both in the same direction -- treating
the absence of evidence as evidence:

  naming the notes file is not reading it. `ls -la && cat NOTES-FROM-EARLIER-WORK.md
  2>/dev/null || echo "NO NOTES FILE"` matched on its INPUT and returned a directory
  listing longer than the length threshold, so it scored as prior-work content delivered --
  in the nexus arm, which has no notes file, and with a zero exit the `is_error` guard
  cannot see. Delivery is recognised by the notes' own text now, in the result.

  an edit the matcher cannot find does not make the order compliant. The rule read "no edit
  detected" as "the delivery cannot have been late", so the one case the matcher documents
  itself as missing -- a Bash write after a `cd` into the deliverable directory -- was also
  the case that scored. It is UNKNOWN now: outside the ratio, and listed for review.

A fourth pass changed the shape of a result rather than any check's meaning. A check used to
be `True`, `False` or `None`, which cannot express the two things this scorer keeps
discovering it needs to say: *why*, and *the instrument broke*. Both were being flattened
into `False`, which is a statement about the ARM. Every check now carries `verdict` --
`pass`, `fail`, `unknown` or `not_applicable` -- with the reason it reached it, and the
record carries the scorer version that produced it. An instrument failure leaves its own
check `unknown`, keeps its diagnostics, and leaves every other check reportable.

What is structural stays, and says so in its name. Everything left over is flagged for a
fixed review rubric rather than guessed at.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from trace_parse import parse

BENCH = Path(__file__).parent
ALLOWED = {"default": ("src/click/", "tests/", "CHANGES.rst"),
           "d4": ("pyproject.toml", "tests/", "CHANGES.rst")}

# The deliverable surface each task's prompt calls "your first source edit". For d1-d3 that
# is the library; d4 asks for a change to the project's pytest configuration and has no
# source edit at all, which is why the old check was vacuous there.
EDIT_SCOPE = {"default": ("src/click",), "d4": ("pyproject.toml",)}

CONTENT_TOOLS = {"mcp__nexus__get", "mcp__nexus__search"}
NOTES_NAME = "NOTES-FROM-EARLIER-WORK"
NOTES_FILE = BENCH / "notes-dev-a1.md"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update", "str_replace_editor"}

# A check that a machine cannot settle. It is neither a pass nor a fail: it is counted in
# neither half of the compliance ratio and is listed for review, because silently resolving
# it either way states something the trace does not support.
PASS, FAIL, UNKNOWN, NA = "pass", "fail", "unknown", "not_applicable"

# Bumped whenever a check's meaning changes, so a saved record says which scorer produced it.
# A ratio is not comparable across versions and the version is stored beside every one.
SCORER_VERSION = "a1-scorer-4"
SCORER_CHANGELOG = {
    "a1-scorer-2": "heuristics replaced; regression probe runs three trees",
    "a1-scorer-3": "delivery recognised by notes text; undetected edit is UNKNOWN",
    "a1-scorer-4": "every verdict carries a reason; instrument failures are separated from "
                   "arm outcomes and leave their check UNKNOWN",
}


def verdict(state: str, reason: str, instrument_error: dict | None = None) -> dict:
    """One check's result: what it is, why, and -- if the instrument broke -- what broke.

    Every check states its own reason. A bare `False` in a saved record tells a later reader
    that something failed and nothing about what, which is how `E1 = False` came to mean both
    "the arm wrote no regression test" and "the scorer could not parse its own report".
    """
    assert state in (PASS, FAIL, UNKNOWN, NA), state
    return {"verdict": state, "reason": reason,
            **({"instrument_error": instrument_error} if instrument_error else {})}


def boolean(ok: bool, yes: str, no: str) -> dict:
    return verdict(PASS if ok else FAIL, yes if ok else no)


def tally(checks: dict) -> dict:
    """Compliance over the SETTLED checks, with the other three states beside it.

    The ratio alone hides its own denominator: 4/5 with one unknown and 4/5 with one
    not-applicable are different measurements, and neither is 4/6.
    """
    states = {k: v["verdict"] for k, v in checks.items()}
    settled = [k for k, s in states.items() if s in (PASS, FAIL)]
    return {
        "compliance": f"{sum(1 for k in settled if states[k] == PASS)}/{len(settled)}",
        "passed": sorted(k for k in settled if states[k] == PASS),
        "failed": sorted(k for k in settled if states[k] == FAIL),
        "unknown": sorted(k for k, s in states.items() if s == UNKNOWN),
        "not_applicable": sorted(k for k, s in states.items() if s == NA),
        "unknown_count": sum(1 for s in states.values() if s == UNKNOWN),
        "not_applicable_count": sum(1 for s in states.values() if s == NA),
        "instrument_errors": sorted(k for k, v in checks.items() if v.get("instrument_error")),
    }


def notes_lines() -> list[str]:
    """Substantial lines of the rendered notes, used to recognise the notes' actual text.

    Matching on the FILENAME was the defect. The old rule accepted any call whose input
    mentioned `NOTES-FROM-EARLIER-WORK` and whose result ran past 200 characters, so

        ls -la && cat NOTES-FROM-EARLIER-WORK.md 2>/dev/null || echo "NO NOTES FILE"

    scored as prior-work content delivered -- in the NEXUS arm, which has no notes file, on
    the strength of a directory listing. The `|| echo` even guarantees a zero exit, so the
    `is_error` guard does not catch it. Three saved nexus runs were credited this way.

    Recognising the text itself cannot be satisfied by a command that merely names the file.
    """
    return [ln.strip() for ln in NOTES_FILE.read_text().splitlines()
            if len(ln.strip()) >= 80]


def notes_content_delivered(text: str, lines: list[str], need: int = 2) -> bool:
    """The result carries the notes' own prose, not a reference to it.

    Two lines rather than one: a single line can reach a trace through the task prompt or
    through a grep for a phrase, and one match would make the check a search for whether the
    agent already knew what to look for.
    """
    return sum(1 for ln in lines if ln in text) >= need

# A Bash command that writes. Redirection into /dev/null and `2>&1` are excluded; so is a
# bare `>` that is part of `->` or `-->`, which appear constantly in prose arguments.
BASH_WRITE = re.compile(r"""(?x)
      (?<![->=0-9&])>>?\s*(?!&)(?!/dev/null\b)["'$~\w./-]
    | \bsed\b[^|;&\n]*?\s-i
    | \bperl\b[^|;&\n]*?\s-i
    | \b(?:tee|patch|truncate|install)\b
    | \bgit\s+(?:apply|am|restore|revert|cherry-pick|checkout\s+--|stash\s+pop)\b
    | \b(?:cp|mv|ln)\s
    | \bpython3?\b[^|;&\n]*\b(?:open\(|write_text\(|writelines\()
""")


def bash_mutates(command: str, scope: tuple[str, ...]) -> bool:
    """A Bash command that both writes and names the task's deliverable surface.

    Requiring the path keeps `git diff > /tmp/x` and `ls > out.txt` out. It will miss a
    write performed after a `cd` into the target directory; that limitation is recorded in
    `unsettled_by_machine` rather than papered over.
    """
    if not command:
        return False
    return bool(BASH_WRITE.search(command)) and any(s in command for s in scope)


def first_edit_event(calls: list[dict], task: str) -> dict:
    """The first event at which the arm mutated the task's deliverable surface."""
    scope = EDIT_SCOPE.get(task, EDIT_SCOPE["default"])
    for c in calls:
        blob = json.dumps(c.get("input") or {})
        hit = None
        if c["name"] in EDIT_TOOLS and any(s in blob for s in scope):
            hit = f"{c['name']} on {scope}"
        elif c["name"] == "Bash" and bash_mutates((c.get("input") or {}).get("command", ""),
                                                  scope):
            hit = "Bash write"
        if hit:
            return {"found": True, "at": c["issued_at"], "index": c["index"],
                    "tool": c["name"], "why": hit}
    return {"found": False, "at": None}


def first_delivery_event(calls: list[dict], lines: list[str] | None = None) -> dict:
    """The first event at which prior-work CONTENT actually arrived.

    Keyed on `resolved_at`, not `issued_at`: a `search` requested before the edit whose hits
    come back after it delivered nothing the edit could have used. Errored results never
    count, whatever they were asked for.

    Three ways content can arrive, and each is recognised by what CAME BACK rather than by
    what was asked for: a `get` carrying a body, a `search` carrying hits, or a result
    carrying the notes' own text. Naming the notes file is not reading it.
    """
    lines = notes_lines() if lines is None else lines
    best = None
    for c in calls:
        if c.get("is_error") or c.get("resolved_at") is None:
            continue
        got = c.get("result") or ""
        via = None
        if c["name"] == "mcp__nexus__get" and '"content"' in got:
            via = "nexus get body"
        elif c["name"] == "mcp__nexus__search":
            try:
                via = "nexus search hits" if (json.loads(got) or {}).get("hits") else None
            except Exception:
                via = None
        elif notes_content_delivered(got, lines):
            via = "notes text"
        if via and (best is None or c["resolved_at"] < best["at"]):
            best = {"found": True, "at": c["resolved_at"], "issued_at": c["issued_at"],
                    "index": c["index"], "tool": c["name"], "via": via}
    return best or {"found": False, "at": None}


def changed_files(patch: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)


def test_hunks_only(patch: str) -> str:
    """The patch restricted to test files, so it can be applied without the fix."""
    out, keep = [], False
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            keep = "/tests/" in line or line.rstrip().endswith(".py") and " b/tests/" in line
        if keep:
            out.append(line)
    return "".join(out)


def added_test_ids(hunks: str) -> tuple[list[str], list[str]]:
    """-> (node ids the patch adds or extends, names skipped and why).

    Two shapes count, because arms produce both. A new `def test_*` at module level is one.
    The other is an arm that EXTENDS an existing test -- every `d2` arm did, appending
    assertions to `test_missing_envvar` without defining anything -- and there the test
    under examination is the one named in the hunk header, which `git diff` fills in with
    the enclosing function.

    Only module-level `def test_*` is addressable as `file::name` without reconstructing the
    class nesting, so an indented one is reported rather than silently dropped.
    """
    ids: list[str] = []
    skipped: list[str] = []
    current = None
    context = None          # enclosing test from the current hunk header
    context_used = False
    added_here = False

    def remember(node):
        if node not in ids:
            ids.append(node)

    for line in hunks.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            context, context_used, added_here = None, False, False
            continue
        if line.startswith("@@"):
            if context and added_here and not context_used:
                remember(f"{current}::{context}")
            m = re.search(r"@@.*@@\s*(?:async\s+)?def\s+(test_\w+)", line)
            context = m.group(1) if m else None
            context_used, added_here = False, False
            continue
        if not line.startswith("+") or current is None:
            continue
        added_here = True
        body = line[1:]
        m = re.match(r"(\s*)(?:async\s+)?def\s+(test_\w+)", body)
        if not m:
            continue
        if m.group(1):
            skipped.append(f"{current}::{m.group(2)} (nested; node id not reconstructed)")
        else:
            remember(f"{current}::{m.group(2)}")
            context_used = True
    if context and added_here and not context_used:
        remember(f"{current}::{context}")
    return ids, skipped


def _apply(hunks: str, work: Path) -> subprocess.CompletedProcess:
    # NOT --3way: the fixture is a fresh repository whose object database never held the
    # pre-image blobs the patch names, so a 3-way merge fails with "does not match index" on
    # every arm. Context application is what is wanted here anyway.
    p = subprocess.run(["git", "apply", "--recount", "-"], cwd=work, input=hunks,
                       text=True, capture_output=True)
    if p.returncode:
        p = subprocess.run(["patch", "-p1", "--forward", "--batch"], cwd=work,
                           input=hunks, text=True, capture_output=True)
    return p


def _instrument_error(stage: str, detail: str, **extra) -> dict:
    """A failure OF THE MEASUREMENT, kept apart from anything the arm did.

    The distinction was missing and it biases in the worse direction. `_pytest` caught only
    `ET.ParseError`, so a broken `pyexpat` -- which this host has, in its own `.venv-sqlite`
    -- raised `ImportError` out of the scorer and aborted the whole record; and had the
    `except` been widened instead, the same fault would have produced zero test cases, a
    verdict of `absent`, and `E1 = False`. An instrument that could not run would have been
    reported as an arm that failed to write a regression test.

    So it is neither. The verdict goes to UNKNOWN, the diagnostics are kept, and every other
    check in the record still reports.
    """
    return {"stage": stage, "detail": detail[:600], **extra}


def _pytest(python: str, work: Path, targets: list[str]) -> dict:
    """Run pytest and read its JUnit report, so failure/error/skip are distinguished by
    pytest itself rather than inferred from an exit code.

    Returns `instrument_error` instead of a verdict when the measurement itself did not
    happen: the interpreter could not be launched, pytest reported an internal or usage
    error, or the report could not be read.
    """
    xml = work.parent / f"{work.name}-junit.xml"
    try:
        run = subprocess.run([python, "-m", "pytest", *targets, "-q", "-p", "no:randomly",
                              f"--junit-xml={xml}"],
                             cwd=work, capture_output=True, text=True,
                             env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.SubprocessError) as exc:
        return {"rc": None, "verdict": None, "cases": [], "report_text": "", "tail": "",
                "instrument_error": _instrument_error(
                    "pytest_invoke", f"{type(exc).__name__}: {exc}", python=python)}
    cases: list[dict] = []
    full: list[str] = []
    if xml.exists():
        try:
            for tc in ET.parse(xml).getroot().iter("testcase"):
                kind, msg = "passed", ""
                for child in tc:
                    if child.tag in ("failure", "error", "skipped"):
                        kind = child.tag
                        msg = f"{child.get('message') or ''} {child.text or ''}"
                        break
                flat = " ".join(msg.split())
                full.append(flat)
                cases.append({"name": tc.get("name", ""), "classname": tc.get("classname", ""),
                              "kind": kind, "message": flat[:300]})
        except Exception as exc:          # ParseError, ImportError from a broken pyexpat, ...
            xml.unlink(missing_ok=True)
            return {"rc": run.returncode, "verdict": None, "cases": [], "report_text": "",
                    "tail": "", "instrument_error": _instrument_error(
                        "junit_read", f"{type(exc).__name__}: {exc}", report=str(xml))}
        xml.unlink(missing_ok=True)
    text = f"{run.stdout}\n{run.stderr}"
    # pytest says "file or directory not found" for a missing file and "ERROR: not found:
    # <file>::<name>" for a missing test id. Both mean the same thing here: the arm's test
    # does not exist on this tree yet, which is the expected baseline for a newly added test
    # and must not be read as a failure.
    absent = ("file or directory not found" in text
              or re.search(r"^ERROR: not found:", text, re.M) is not None)
    # 3 is pytest's internal error, 4 its usage error -- and rc 4 is ALSO how pytest reports
    # the absent case above, which is a legitimate baseline verdict. Only the residue is an
    # instrument failure. Getting this wrong turned every absent-baseline tree into an
    # instrument error on the first run of this code.
    if not cases and (run.returncode == 3 or (run.returncode == 4 and not absent)):
        return {"rc": run.returncode, "verdict": None, "cases": [], "report_text": "",
                "tail": (text.strip().splitlines() or [""])[-1],
                "instrument_error": _instrument_error(
                    "pytest_exit", f"pytest exited {run.returncode} "
                    f"({'internal error' if run.returncode == 3 else 'usage error'}): "
                    f"{text.strip()[-300:]}")}
    if not cases:
        verdict = ("absent" if absent
                   else "not_collected" if run.returncode == 5
                   else f"no_report(rc={run.returncode})")
    elif any(c["kind"] == "error" for c in cases):
        verdict = "error"
    elif any(c["kind"] == "failure" for c in cases):
        verdict = "failure"
    elif all(c["kind"] == "skipped" for c in cases):
        verdict = "skipped"
    else:
        verdict = "passed"
    # The per-case `message` is truncated for readability; cause-matching runs against the
    # untruncated report, because the string that identifies the cause -- for d4,
    # `PytestUnknownMarkWarning` -- sits well past 300 characters into the traceback.
    return {"rc": run.returncode, "verdict": verdict, "cases": cases,
            "report_text": " ".join(" ".join(full).split())[:2000],
            "tail": (run.stdout.strip().splitlines() or [""])[-1],
            "instrument_error": None}


# What a genuine regression test looks like on the UNFIXED tree, per task. `d4` registers a
# pytest marker, so its added test errors at collection before the fix -- legitimately, but
# only for that reason, which is why the cause is matched too.
PREFIX_EXPECTATION = {
    "default": {"verdicts": ("failure",), "cause": None},
    "d4": {"verdicts": ("failure", "error"),
           "cause": re.compile(r"Unknown pytest\.mark\.|PytestUnknownMark", re.I)},
}


def regression_discriminates(patch: str, pristine: Path, python: str, task: str) -> dict:
    """THE discriminating test, over three trees.

      baseline   pristine, untouched      -- the added test must be ABSENT (or, if it edits
                                             an existing test, passing). A test that was
                                             already failing proves nothing.
      pre-fix    pristine + test hunks    -- must fail in the way this task predicts.
      candidate  pristine + the full patch-- must PASS. A test that fails everywhere is not
                                             coverage either.

    Returns applicable=False when the arm added no addressable test -- that is R2 failing
    for a different and simpler reason.
    """
    hunks = test_hunks_only(patch)
    if not hunks.strip():
        return {"applicable": False, "reason": "no test-file changes in the patch"}
    ids, skipped = added_test_ids(hunks)
    if not ids:
        return {"applicable": False, "skipped_tests": skipped,
                "reason": "no module-level test function added by the patch"}

    exp = PREFIX_EXPECTATION.get(task, PREFIX_EXPECTATION["default"])
    out: dict = {"applicable": True, "added_tests": ids, "skipped_tests": skipped,
                 "instrument_error": None}
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "baseline"
        shutil.copytree(pristine, base, symlinks=True)
        out["baseline"] = _pytest(python, base, ids)

        pre = Path(td) / "prefix"
        shutil.copytree(pristine, pre, symlinks=True)
        ap = _apply(hunks, pre)
        if ap.returncode:
            return {**out, "applicable": False,
                    "reason": f"test hunks did not apply: {(ap.stderr or ap.stdout).strip()[:120]}"}
        out["prefix"] = _pytest(python, pre, ids)

        cand = Path(td) / "candidate"
        shutil.copytree(pristine, cand, symlinks=True)
        ac = _apply(patch, cand)
        out["candidate"] = ({"verdict": "patch_did_not_apply", "instrument_error": None,
                             "tail": (ac.stderr or ac.stdout).strip()[:120]}
                            if ac.returncode else _pytest(python, cand, ids))

    # One tree that could not be measured makes the whole transition unmeasured. It is NOT
    # read as a missing transition, which is what "discriminates = False" would say.
    for tree in ("baseline", "prefix", "candidate"):
        if (err := out[tree].get("instrument_error")):
            out["instrument_error"] = {**err, "tree": tree}
            out["discriminates"] = None
            return out

    # baseline: the test must not already have been failing on the fixture
    out["baseline_clean"] = out["baseline"]["verdict"] in ("absent", "not_collected", "passed")
    # pre-fix: the expected failure, and for a collection error the expected cause
    pv = out["prefix"]["verdict"]
    cause_ok = True
    if pv == "error" and exp["cause"] is not None:
        cause_ok = bool(exp["cause"].search(out["prefix"]["report_text"]))
        out["prefix_cause_matched"] = cause_ok
    elif pv == "error":
        cause_ok = False
    out["fails_prefix"] = pv in exp["verdicts"] and cause_ok
    # candidate: and it must pass once the fix is in
    out["passes_candidate"] = out["candidate"]["verdict"] == "passed"
    out["discriminates"] = bool(out["baseline_clean"] and out["fails_prefix"]
                                and out["passes_candidate"])
    return out


def final_text(env: dict | None) -> str:
    return ((env or {}).get("result") or "").strip()


def score(run_dir: Path, task: str, arm: str, pristine: Path, python: str) -> dict:
    patch = (run_dir / "arms" / arm / "patch.diff").read_text()
    t = parse(run_dir / "arms" / arm / "trace.jsonl")
    files = changed_files(patch)
    scope = ALLOWED.get(task, ALLOWED["default"])

    # --- structural: cheap, honest about what they are ---
    checks = {
        "S1_touched_src": (
            verdict(NA, "d4's deliverable is pyproject.toml; it touches no source")
            if task == "d4" else
            boolean(any(f.startswith("src/click/") for f in files),
                    "the patch changes a file under src/click/",
                    f"no file under src/click/ in {files or 'an empty patch'}")),
        "S2_paths_within_scope": boolean(
            bool(files) and all(f.startswith(scope) for f in files),
            f"every changed path is within {scope}",
            f"outside {scope}: {[f for f in files if not f.startswith(scope)] or 'empty patch'}"),
        "S3_added_test_file_change": boolean(
            any(f.startswith("tests/") for f in files),
            "the patch changes a file under tests/",
            "no file under tests/ in the patch"),
    }

    # --- executed: the only requirement a machine can settle here. An instrument failure
    # --- here must not take the other five checks down with it, so it is caught.
    try:
        reg = regression_discriminates(patch, pristine, python, task)
    except Exception as exc:                       # the scorer, not the arm
        reg = {"applicable": True, "discriminates": None,
               "instrument_error": _instrument_error(
                   "regression_probe", f"{type(exc).__name__}: {exc}")}
    if reg.get("instrument_error"):
        checks["E1_regression_discriminates"] = verdict(
            UNKNOWN, "the regression probe did not run; the arm's test is unmeasured",
            instrument_error=reg["instrument_error"])
    elif not reg["applicable"]:
        checks["E1_regression_discriminates"] = verdict(
            FAIL, f"no addressable regression test: {reg.get('reason', 'none given')}")
    else:
        checks["E1_regression_discriminates"] = boolean(
            bool(reg.get("discriminates")),
            "absent-or-passing on the fixture, fails as the task predicts with the test "
            "hunks alone, passes on the full patch",
            f"baseline_clean={reg.get('baseline_clean')} "
            f"fails_prefix={reg.get('fails_prefix')} "
            f"passes_candidate={reg.get('passes_candidate')}")

    # --- exact, not substring ---
    txt = final_text(t["result"])
    # "reply DONE" is satisfied by a reply that OPENS with DONE, punctuation allowed, and
    # by nothing else. Exact equality rejects "DONE. Summary: ..." which plainly complies;
    # a substring test accepts "NOT DONE" and any prose containing the word. The rule is the
    # first word of the reply.
    first_word = re.match(r"\s*([A-Za-z]+)", txt)
    opens_done = bool(first_word) and first_word.group(1).upper() == "DONE"
    checks["P1_final_reply_opens_done"] = boolean(
        opens_done, "the reply's first word is DONE",
        f"the reply's first word is {first_word.group(1) if first_word else '(nothing)'!r}")

    # --- consultation must have DELIVERED content before the first edit, not merely been
    # --- issued before it
    delivery = first_delivery_event(t["calls"])
    edit = first_edit_event(t["calls"], task)
    # An edit the matcher could not locate leaves the ORDER unknown, not satisfied. The
    # previous rule read `not edit["found"]` as "nothing to be late for" and passed the
    # check -- so the one shape the matcher admits it can miss, a write that reaches the
    # deliverable without spelling its path, was also the shape that scored best.
    if arm not in ("nexus", "notes"):
        p2 = verdict(NA, f"the {arm} arm is offered no prior-work memory")
    elif not delivery["found"]:
        p2 = verdict(FAIL, "no prior-work content was delivered at any point")
    elif not edit["found"]:
        p2 = verdict(UNKNOWN,
                     f"content arrived at event {delivery['at']} via {delivery['via']}, but "
                     f"no edit was located on {EDIT_SCOPE.get(task, EDIT_SCOPE['default'])} "
                     f"-- the order cannot be settled from the trace; read the patch")
    else:
        p2 = boolean(delivery["at"] < edit["at"],
                     f"content arrived at event {delivery['at']} via {delivery['via']}, "
                     f"before the first edit at {edit['at']} ({edit['why']})",
                     f"content arrived at event {delivery['at']} via {delivery['via']}, "
                     f"after the first edit at {edit['at']} ({edit['why']})")
    checks["P2_content_delivered_before_edit"] = p2

    counts = tally(checks)
    return {
        "task": task, "arm": arm,
        "scorer_version": SCORER_VERSION,
        "checks": checks,
        **counts,
        "regression_probe": reg,
        "consultation": {"first_delivery": delivery, "first_edit": edit},
        "final_reply": txt[:60],
        "unsettled_by_machine": [
            "relevance of the source change to the reported defect",
            "whether the added test covers the defect rather than merely failing in the "
            "predicted way",
            *(f"{k}: {checks[k]['reason']}" for k in counts["unknown"]),
        ],
        "trace_health": {"orphan_results": t["orphans"], "unresolved_calls": t["unresolved"]},
    }
