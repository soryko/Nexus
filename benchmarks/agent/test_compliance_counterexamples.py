"""The review's counterexamples, as a regression test on the scorer itself.

A prior scorer gave 5/5 to a patch that changed an arbitrary source line, asserted
`assert True`, consulted memory with a `status` call only, and signed off "NOT DONE".
Each must now be rejected.
"""
from __future__ import annotations
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from score_compliance import score

PY_CLICK = sys.argv[1] if len(sys.argv) > 1 else "python3"
PRISTINE = Path(json.loads((Path(__file__).parent / "run-dev-a1-attempt4" /
                            "fixtures.json").read_text())[0]["path"])

BAD_PATCH = '''diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,4 +1,5 @@
 from __future__ import annotations
+# an arbitrary, irrelevant source change
 
 import collections.abc as cabc
 import enum
diff --git a/tests/test_options.py b/tests/test_options.py
--- a/tests/test_options.py
+++ b/tests/test_options.py
@@ -1,5 +1,9 @@
 import os
 
+
+def test_vacuous():
+    assert True
+
 import pytest
'''

TRACE = [
 {"type": "assistant", "message": {"content": [
     {"type": "tool_use", "id": "t1", "name": "mcp__nexus__status", "input": {}}]}},
 {"type": "user", "message": {"content": [
     {"type": "tool_result", "tool_use_id": "t1",
      "content": [{"type": "text", "text": '{"active_memories":13}'}]}]}},
 {"type": "assistant", "message": {"content": [
     {"type": "tool_use", "id": "t2", "name": "Edit",
      "input": {"file_path": "/x/src/click/core.py"}}]}},
 {"type": "user", "message": {"content": [
     {"type": "tool_result", "tool_use_id": "t2",
      "content": [{"type": "text", "text": "ok"}]}]}},
 {"type": "result", "is_error": False, "subtype": "success",
  "terminal_reason": "completed", "result": "NOT DONE"},
]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        run = Path(td) / "run"
        (run / "arms" / "nexus").mkdir(parents=True)
        (run / "arms" / "nexus" / "patch.diff").write_text(BAD_PATCH)
        (run / "arms" / "nexus" / "trace.jsonl").write_text(
            "\n".join(json.dumps(x) for x in TRACE))
        r = score(run, "d1", "nexus", PRISTINE, PY_CLICK)

    expect_false = {
        "E1_regression_fails_prefix": "`assert True` is not regression coverage",
        "P1_final_reply_opens_done": '"NOT DONE" is not DONE',
        "P2_consulted_content_before_edit": "a status call delivers no prior-work evidence",
    }
    ok = True
    print(f"  compliance: {r['compliance']}   failed: {r['failed']}")
    for k, why in expect_false.items():
        got = r["checks"].get(k)
        good = got is False
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {k} -> {got}   ({why})")
    struct = r["checks"]["S1_touched_src"]
    print(f"  [note] S1_touched_src -> {struct}: structural only; "
          f"relevance is listed under unsettled_by_machine")
    print(f"  unsettled: {r['unsettled_by_machine']}")
    print("\n  RESULT:", "all counterexamples rejected" if ok else "SCORER STILL ACCEPTS SOMETHING")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
