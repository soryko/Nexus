"""Task-requirement compliance, scored separately from functional correctness.

`protocol-a1` section 8 counts *evidence* facts. This counts *deliverables*: things the
prompt asked the agent to produce. "Add a regression test" is a requirement, not a fact, and
a patch can pass every hidden check while ignoring it -- d3's Nexus arm did exactly that.

Three dimensions, never merged:

    functional correctness   hidden acceptance checks pass or fail
    requirement compliance   this file
    termination reason       completed / max_turns / timeout

Runs over saved patches and traces. No model is invoked.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The deliverables every A1 task prompt states, in the prompt's own words.
REQUIREMENTS = {
    "R1_fix_in_src": "Fix the behaviour in `src/`",
    "R2_extend_tests": "extend the existing test suite to cover it",
    "R3_no_unrelated": "Do not change behaviour unrelated to this bug",
    "R4_reply_done": "When you are done, reply DONE",
    "R5_consult_first": "Before your first source edit, consult any available prior-work memory",
}
# What "nothing unrelated" permits depends on what the task asked for. d4 is a
# configuration task, so pyproject.toml is the deliverable, not collateral damage; the first
# version of this list marked all three d4 arms non-compliant for doing the task correctly.
ALLOWED_PATHS = {
    "default": ("src/click/", "tests/", "CHANGES.rst"),
    "d4": ("pyproject.toml", "tests/", "CHANGES.rst"),
}
MEMORY_TOOLS = {"mcp__nexus__search", "mcp__nexus__get", "mcp__nexus__history",
                "mcp__nexus__status"}


def changed_files(patch: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)


def compliance(arm: str, patch: str, calls: list[dict], result: str | None,
               memory_available: bool, task: str = "") -> dict:
    files = changed_files(patch)
    added_tests = re.findall(r"^\+\s*def (test_\w+)", patch, re.M)
    # A new test function is not the only way to extend coverage, and requiring one marked
    # every d2 arm non-compliant for doing exactly what the upstream fix did: appending
    # assertions to an existing test. New assertions inside a tests/ file, and a new
    # parametrise decorator over an existing test, both count.
    added_params = bool(re.search(r"^\+\s*@pytest\.mark\.parametrize", patch, re.M))
    in_tests = False
    added_asserts = 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            in_tests = line[6:].startswith("tests/")
        elif in_tests and re.match(r"^\+\s*assert\b", line):
            added_asserts += 1

    def first_index(pred):
        for i, c in enumerate(calls):
            if pred(c):
                return i
        return None

    i_mem = first_index(lambda c: c.get("name") in MEMORY_TOOLS
                        or "NOTES-FROM-EARLIER-WORK" in json.dumps(c.get("input", {})))
    i_edit = first_index(lambda c: "src/click/" in json.dumps(c.get("input", {}))
                         and c.get("name") in ("Edit", "Write"))

    # d4 is a configuration task: its prompt asks for a registered marker and a test, not a
    # src/ change. Requiring a src/ edit there would fail a correct patch.
    out = {
        "R1_fix_in_src": (None if task == "d4"
                          else any(f.startswith("src/click/") for f in files)),
        "R2_extend_tests": bool(added_tests) or added_params or added_asserts > 0,
        "R3_no_unrelated": all(f.startswith(ALLOWED_PATHS.get(task, ALLOWED_PATHS["default"]))
                              for f in files) and bool(files),
        "R4_reply_done": bool(result) and "DONE" in result.upper(),
        # R5 is only applicable where there was something to consult. Where nothing was
        # available the prompt says "proceed using the repository", so it cannot be failed.
        "R5_consult_first": (None if not memory_available
                             else (i_mem is not None and (i_edit is None or i_mem < i_edit))),
    }
    if task == "d4":
        out["R6_marker_registered_where_read"] = (
            "pyproject.toml" in " ".join(files) and "integration" in patch)
        out["R7_no_setup_cfg"] = not any(f.endswith("setup.cfg") for f in files)
    applicable = [v for v in out.values() if v is not None]
    out["_met"] = sum(1 for v in applicable if v)
    out["_applicable"] = len(applicable)
    out["_files"] = files
    out["_added_tests"] = added_tests
    out["_added_asserts"] = added_asserts
    return out


def main(argv: list[str]) -> int:
    rows = []
    for run_dir in argv[1:]:
        run = Path(run_dir)
        data = json.loads((run / "records.json").read_text())
        task = data["task"]
        for rec in data["records"]:
            arm = rec["arm"]
            patch = (run / "arms" / arm / "patch.diff").read_text()
            res = rec.get("result") or {}
            available = arm == "nexus" and (
                (rec.get("memory_visibility") or {}).get("visible", 0) > 0)
            if arm == "notes":
                available = True
            c = compliance(arm, patch, rec["tool_calls"], res.get("result"), available, task)
            term = rec["terminal"]["terminal_reason"]
            rows.append({
                "run": run.name, "task": task, "arm": arm,
                # dimension 1 -- unchanged instrument
                "functional_correctness": rec["scored"]["passed"],
                # dimension 2 -- this file
                "requirement_compliance": f"{c['_met']}/{c['_applicable']}",
                "failed_requirements": [k for k, v in c.items()
                                        if not k.startswith("_") and v is False],
                # dimension 3
                "termination_reason": term,
                # the two scoring rules, both reported, neither silently replacing the other
                "outcome_original_rule": ("fail (truncated)" if term in ("max_turns", "timeout")
                                          else ("pass" if rec["scored"]["passed"] else "fail")),
                "outcome_amended_rule": "pass" if rec["scored"]["passed"] else "fail",
                "tool_calls": len(rec["tool_calls"]),
            })
    print(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
