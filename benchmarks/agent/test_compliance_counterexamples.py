"""The reviews' counterexamples, as a regression test on the scorer itself.

Round one: a patch that changed an arbitrary source line, asserted `assert True`, consulted
memory with a `status` call only, and signed off "NOT DONE" scored 5/5.

Round two: three more ways to pass without complying --
  * a memory call ISSUED before the edit whose result ARRIVES after it,
  * an edit made through `Bash` rather than `Edit`/`Write`, and on `d4` an edit to
    `pyproject.toml`, neither of which the old check looked at,
  * an added test whose nonzero pytest exit comes from a syntax error rather than from the
    defect.

Each must now be rejected, and the compliant shapes must still be accepted -- a check that
rejects everything is not a check.
"""
from __future__ import annotations
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from score_compliance import score, bash_mutates, first_edit_event, first_delivery_event

PY_CLICK = sys.argv[1] if len(sys.argv) > 1 else "python3"
BENCH = Path(__file__).parent
PRISTINE = Path(json.loads((BENCH / "run-dev-a1-attempt4" / "fixtures.json").read_text())[0]["path"])
PRISTINE_D4 = Path(json.loads((BENCH / "run-dev-d4" / "fixtures.json").read_text())[0]["path"])

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

# A test file that does not parse. `pytest` exits nonzero on it, which the old probe read as
# "the regression fails before the fix".
SYNTAX_ERROR_PATCH = '''diff --git a/src/click/core.py b/src/click/core.py
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
+def test_broken(:
+    assert False
+
 import pytest
'''


def trace(*blocks) -> list[dict]:
    return list(blocks)


def call(tid, name, inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def parallel(*pairs):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp} for tid, name, inp in pairs]}}


def result(tid, text, is_error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": is_error,
         "content": [{"type": "text", "text": text}]}]}}


def envelope(text):
    return {"type": "result", "is_error": False, "subtype": "success",
            "terminal_reason": "completed", "result": text}


HITS = json.dumps({"hits": [{"memory_id": "c1", "excerpt": "x" * 400}]})

# round one: status-only consultation, irrelevant edit, "NOT DONE"
TRACE_ROUND_ONE = trace(
    call("t1", "mcp__nexus__status", {}), result("t1", '{"active_memories":13}'),
    call("t2", "Edit", {"file_path": "/x/src/click/core.py"}), result("t2", "ok"),
    envelope("NOT DONE"))

# round two: search and edit issued together; the search's hits arrive AFTER the edit landed
TRACE_LATE_DELIVERY = trace(
    parallel(("t1", "mcp__nexus__search", {"query": "envvar"}),
             ("t2", "Edit", {"file_path": "/x/src/click/core.py"})),
    result("t2", "ok"),
    result("t1", HITS),
    envelope("DONE"))

# round two: the same two calls, delivered in the order the protocol asks for
TRACE_TIMELY_DELIVERY = trace(
    call("t1", "mcp__nexus__search", {"query": "envvar"}), result("t1", HITS),
    call("t2", "Edit", {"file_path": "/x/src/click/core.py"}), result("t2", "ok"),
    envelope("DONE"))

# round two: the edit is a Bash heredoc, and it precedes the consultation
TRACE_BASH_EDIT = trace(
    call("t1", "Bash", {"command": "cat > src/click/core.py <<'EOF'\nx = 1\nEOF"}),
    result("t1", "ok"),
    call("t2", "mcp__nexus__search", {"query": "envvar"}), result("t2", HITS),
    envelope("DONE"))

# round two, d4: the deliverable is pyproject.toml, which the old check never looked at
TRACE_D4_LATE = trace(
    call("t1", "Edit", {"file_path": "/x/pyproject.toml"}), result("t1", "ok"),
    call("t2", "mcp__nexus__search", {"query": "marker"}), result("t2", HITS),
    envelope("DONE"))


def run_score(patch, tr, task, arm, pristine):
    with tempfile.TemporaryDirectory() as td:
        run = Path(td) / "run"
        (run / "arms" / arm).mkdir(parents=True)
        (run / "arms" / arm / "patch.diff").write_text(patch)
        (run / "arms" / arm / "trace.jsonl").write_text("\n".join(json.dumps(x) for x in tr))
        return score(run, task, arm, pristine, PY_CLICK)


def check(label, got, want):
    ok = got is want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r} (want {want!r})")
    return ok


def main() -> int:
    ok = True
    print("round one -- the original four counterexamples")
    r = run_score(BAD_PATCH, TRACE_ROUND_ONE, "d1", "nexus", PRISTINE)
    print(f"  compliance: {r['compliance']}   failed: {r['failed']}")
    ok &= check("E1 rejects `assert True`", r["checks"]["E1_regression_discriminates"], False)
    ok &= check('P1 rejects "NOT DONE"', r["checks"]["P1_final_reply_opens_done"], False)
    ok &= check("P2 rejects a status-only call",
                r["checks"]["P2_content_delivered_before_edit"], False)

    print("\nround two -- a nonzero pytest exit from a syntax error")
    r = run_score(SYNTAX_ERROR_PATCH, TRACE_ROUND_ONE, "d1", "nexus", PRISTINE)
    probe = r["regression_probe"]
    print(f"  probe: applicable={probe.get('applicable')} "
          f"baseline={probe.get('baseline', {}).get('verdict')} "
          f"prefix={probe.get('prefix', {}).get('verdict')} "
          f"candidate={probe.get('candidate', {}).get('verdict')}")
    print(f"  components: baseline_clean={probe.get('baseline_clean')} "
          f"fails_prefix={probe.get('fails_prefix')} "
          f"passes_candidate={probe.get('passes_candidate')}")
    ok &= check("E1 rejects a test file that does not parse",
                r["checks"]["E1_regression_discriminates"], False)

    print("\nround two -- delivery order vs issue order")
    r = run_score(BAD_PATCH, TRACE_LATE_DELIVERY, "d1", "nexus", PRISTINE)
    c = r["consultation"]
    print(f"  first delivery at event {c['first_delivery']['at']}, "
          f"first edit at event {c['first_edit']['at']}")
    ok &= check("P2 rejects hits that arrive after the edit",
                r["checks"]["P2_content_delivered_before_edit"], False)
    r = run_score(BAD_PATCH, TRACE_TIMELY_DELIVERY, "d1", "nexus", PRISTINE)
    ok &= check("P2 still accepts hits that arrive before the edit",
                r["checks"]["P2_content_delivered_before_edit"], True)

    print("\nround two -- edits the old check could not see")
    r = run_score(BAD_PATCH, TRACE_BASH_EDIT, "d1", "nexus", PRISTINE)
    print(f"  first edit: {r['consultation']['first_edit']}")
    ok &= check("P2 counts a Bash heredoc as an edit",
                r["checks"]["P2_content_delivered_before_edit"], False)
    r = run_score(BAD_PATCH, TRACE_D4_LATE, "d4", "nexus", PRISTINE_D4)
    print(f"  first edit: {r['consultation']['first_edit']}")
    ok &= check("P2 counts a d4 pyproject.toml edit",
                r["checks"]["P2_content_delivered_before_edit"], False)

    print("\nround two -- the Bash write matcher, in isolation")
    SRC = ("src/click",)
    for cmd, want in [
        ("cat > src/click/core.py <<'EOF'", True),
        ("sed -i '' 's/a/b/' src/click/core.py", True),
        ("python3 -c \"open('src/click/core.py','w').write(x)\"", True),
        ("cp /tmp/fixed.py src/click/core.py", True),
        ("git apply /tmp/p.diff -- src/click", True),
        ("grep -rn envvar src/click", False),
        ("cat src/click/core.py", False),
        ("pytest tests -q 2>&1 | tail -5", False),
        ("git diff src/click > /dev/null", False),
        ("echo 'a -> b' # src/click", False),
    ]:
        ok &= check(f"bash_mutates({cmd!r})", bash_mutates(cmd, SRC), want)

    print("\n  unsettled by machine:")
    for u in r["unsettled_by_machine"]:
        print(f"    - {u}")
    print("\n  RESULT:", "all counterexamples rejected, compliant shapes accepted" if ok
          else "SCORER STILL ACCEPTS SOMETHING")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
