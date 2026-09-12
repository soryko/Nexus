"""Task-requirement compliance: structural checks, plus one discriminating test.

A prior version scored deliverables with heuristics that a review broke in four ways at
once -- an arbitrary source edit, `assert True`, a `status`-only memory call and the reply
"NOT DONE" together scored 5/5. Each heuristic confused a *proxy* with the thing:

  file location        is not relevance
  a new assertion      is not regression coverage
  a `status` call      is not prior-work evidence
  `"DONE" in text`     matches "NOT DONE"

What is structural stays, and now says so in its name. The one requirement that can be
settled by execution is settled by execution: a regression test must FAIL on the pre-fix
tree. Everything left over is flagged for a fixed review rubric rather than guessed at.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from trace_parse import parse

BENCH = Path(__file__).parent
ALLOWED = {"default": ("src/click/", "tests/", "CHANGES.rst"),
           "d4": ("pyproject.toml", "tests/", "CHANGES.rst")}
CONTENT_TOOLS = {"mcp__nexus__get", "mcp__nexus__search"}
NOTES_NAME = "NOTES-FROM-EARLIER-WORK"


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


def regression_discriminates(patch: str, pristine: Path, python: str) -> dict:
    """THE discriminating test: do the arm's own tests fail on the unfixed tree?

    A test that passes before the fix does not cover the regression, whatever it asserts.
    Returns applicable=False when the arm added no test at all -- that is R2 failing for a
    different and simpler reason.
    """
    hunks = test_hunks_only(patch)
    if not hunks.strip():
        return {"applicable": False, "reason": "no test-file changes in the patch"}
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "tree"
        shutil.copytree(pristine, work, symlinks=True)
        # NOT --3way: the fixture is a fresh repository whose object database never held
        # the pre-image blobs the patch names, so a 3-way merge fails with "does not match
        # index" on every arm. Context application is what is wanted here anyway.
        p = subprocess.run(["git", "apply", "--recount", "-"], cwd=work, input=hunks,
                           text=True, capture_output=True)
        if p.returncode:
            p = subprocess.run(["patch", "-p1", "--forward", "--batch"], cwd=work,
                               input=hunks, text=True, capture_output=True)
        if p.returncode:
            return {"applicable": False,
                    "reason": f"test hunks did not apply: {(p.stderr or p.stdout).strip()[:120]}"}
        files = [f for f in changed_files(hunks)]
        run = subprocess.run([python, "-m", "pytest", *files, "-q", "-p", "no:randomly"],
                             cwd=work, capture_output=True, text=True,
                             env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
        return {"applicable": True, "fails_prefix": run.returncode != 0,
                "summary": (run.stdout.strip().splitlines() or [""])[-1]}


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
    reg = regression_discriminates(patch, pristine, python)
    executed = {"E1_regression_fails_prefix": reg.get("fails_prefix") if reg["applicable"] else False}

    # --- exact, not substring ---
    txt = final_text(t["result"])
    # "reply DONE" is satisfied by a reply that OPENS with DONE, punctuation allowed, and
    # by nothing else. Exact equality rejects "DONE. Summary: ..." which plainly complies;
    # a substring test accepts "NOT DONE" and any prose containing the word. The rule is the
    # first word of the reply.
    first_word = re.match(r"\s*([A-Za-z]+)", txt)
    protocol = {"P1_final_reply_opens_done":
                bool(first_word) and first_word.group(1).upper() == "DONE"}

    # --- consultation must have DELIVERED something, not merely been called ---
    first_content = first_edit = None
    for c in t["calls"]:
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
        if content_bearing and first_content is None:
            first_content = c["index"]
        if (c["name"] in ("Edit", "Write")
                and "src/click/" in json.dumps(c.get("input") or {}) and first_edit is None):
            first_edit = c["index"]
    memory_offered = arm in ("nexus", "notes")
    protocol["P2_consulted_content_before_edit"] = (
        None if not memory_offered
        else (first_content is not None and (first_edit is None or first_content < first_edit)))

    checks = {**structural, **executed, **protocol}
    applicable = [v for v in checks.values() if v is not None]
    return {
        "task": task, "arm": arm,
        "checks": checks,
        "compliance": f"{sum(1 for v in applicable if v)}/{len(applicable)}",
        "failed": [k for k, v in checks.items() if v is False],
        "regression_probe": reg,
        "final_reply": txt[:60],
        "unsettled_by_machine": [
            "relevance of the source change to the reported defect",
            "whether the added test covers the defect rather than merely failing",
        ],
        "trace_health": {"orphan_results": t["orphans"], "unresolved_calls": t["unresolved"]},
    }
