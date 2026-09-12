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
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update", "str_replace_editor"}

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


def first_delivery_event(calls: list[dict]) -> dict:
    """The first event at which prior-work CONTENT actually arrived.

    Keyed on `resolved_at`, not `issued_at`: a `search` requested before the edit whose hits
    come back after it delivered nothing the edit could have used. Errored results never
    count, whatever they were asked for.
    """
    best = None
    for c in calls:
        if c.get("is_error") or c.get("resolved_at") is None:
            continue
        got = c.get("result") or ""
        content_bearing = False
        if c["name"] == "mcp__nexus__get" and '"content"' in got:
            content_bearing = True
        elif c["name"] == "mcp__nexus__search":
            try:
                content_bearing = bool((json.loads(got) or {}).get("hits"))
            except Exception:
                content_bearing = False
        elif NOTES_NAME in json.dumps(c.get("input") or {}) and len(got) > 200:
            content_bearing = True
        if content_bearing and (best is None or c["resolved_at"] < best["at"]):
            best = {"found": True, "at": c["resolved_at"], "issued_at": c["issued_at"],
                    "index": c["index"], "tool": c["name"]}
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


def _pytest(python: str, work: Path, targets: list[str]) -> dict:
    """Run pytest and read its JUnit report, so failure/error/skip are distinguished by
    pytest itself rather than inferred from an exit code."""
    xml = work.parent / f"{work.name}-junit.xml"
    run = subprocess.run([python, "-m", "pytest", *targets, "-q", "-p", "no:randomly",
                          f"--junit-xml={xml}"],
                         cwd=work, capture_output=True, text=True,
                         env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
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
        except ET.ParseError:
            pass
        xml.unlink(missing_ok=True)
    text = f"{run.stdout}\n{run.stderr}"
    if not cases:
        # pytest says "file or directory not found" for a missing file and "ERROR: not
        # found: <file>::<name>" for a missing test id. Both mean the same thing here: the
        # arm's test does not exist on this tree yet, which is the expected baseline for a
        # newly added test and must not be read as a failure.
        absent = ("file or directory not found" in text
                  or re.search(r"^ERROR: not found:", text, re.M) is not None)
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
            "tail": (run.stdout.strip().splitlines() or [""])[-1]}


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
    out: dict = {"applicable": True, "added_tests": ids, "skipped_tests": skipped}
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
        out["candidate"] = ({"verdict": "patch_did_not_apply",
                             "tail": (ac.stderr or ac.stdout).strip()[:120]}
                            if ac.returncode else _pytest(python, cand, ids))

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

    # --- structural: cheap, honest about what they are ---
    structural = {
        "S1_touched_src": (None if task == "d4"
                           else any(f.startswith("src/click/") for f in files)),
        "S2_paths_within_scope": bool(files) and all(
            f.startswith(ALLOWED.get(task, ALLOWED["default"])) for f in files),
        "S3_added_test_file_change": any(f.startswith("tests/") for f in files),
    }

    # --- executed: the only requirement a machine can settle here ---
    reg = regression_discriminates(patch, pristine, python, task)
    executed = {"E1_regression_discriminates": (reg.get("discriminates")
                                                if reg["applicable"] else False)}

    # --- exact, not substring ---
    txt = final_text(t["result"])
    # "reply DONE" is satisfied by a reply that OPENS with DONE, punctuation allowed, and
    # by nothing else. Exact equality rejects "DONE. Summary: ..." which plainly complies;
    # a substring test accepts "NOT DONE" and any prose containing the word. The rule is the
    # first word of the reply.
    first_word = re.match(r"\s*([A-Za-z]+)", txt)
    protocol = {"P1_final_reply_opens_done":
                bool(first_word) and first_word.group(1).upper() == "DONE"}

    # --- consultation must have DELIVERED content before the first edit, not merely been
    # --- issued before it
    delivery = first_delivery_event(t["calls"])
    edit = first_edit_event(t["calls"], task)
    memory_offered = arm in ("nexus", "notes")
    protocol["P2_content_delivered_before_edit"] = (
        None if not memory_offered
        else bool(delivery["found"] and (not edit["found"] or delivery["at"] < edit["at"])))

    checks = {**structural, **executed, **protocol}
    applicable = [v for v in checks.values() if v is not None]
    return {
        "task": task, "arm": arm,
        "checks": checks,
        "compliance": f"{sum(1 for v in applicable if v)}/{len(applicable)}",
        "failed": [k for k, v in checks.items() if v is False],
        "regression_probe": reg,
        "consultation": {"first_delivery": delivery, "first_edit": edit},
        "final_reply": txt[:60],
        "unsettled_by_machine": [
            "relevance of the source change to the reported defect",
            "whether the added test covers the defect rather than merely failing in the "
            "predicted way",
            "a Bash write performed after `cd` into the deliverable directory, which "
            "first_edit_event matches on the path and would miss",
        ],
        "trace_health": {"orphan_results": t["orphans"], "unresolved_calls": t["unresolved"]},
    }
