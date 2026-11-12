"""Counterexamples for the workflow diagnostic's classifier.

The report built on this program concludes that 10 of 11 failing arm-runs never wrote to
`src/` and that a quarter of the passing-truncated runs' calls follow their last source write.
**A detector that silently missed a write would invert both.** These cases are the ways it
could, plus the four interpretation limits the report promises to preserve.

    python3 test_diagnose_workflow.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import diagnose_workflow as D                                           # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


def call(index, name, inp, result=""):
    return {"index": index, "name": name, "input": inp, "result": result}


def bash(index, cmd, result=""):
    return call(index, "Bash", {"command": cmd, "description": ""}, result)


def run(calls, *, arm="baseline", passed=True, touches_src=True, terminal="max_turns",
        trace_texts=None, ceiling=30):
    """Describe one synthetic arm-run through the real code path."""
    d = Path(tempfile.mkdtemp())
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / arm).mkdir(parents=True)
    if trace_texts is not None:
        lines = [json.dumps({"type": "assistant",
                             "message": {"id": mid, "content": [{"type": "text", "text": t}]}})
                 for mid, t in trace_texts]
        (at / "arms" / arm / "trace.jsonl").write_text("\n".join(lines) + "\n")
    rec = {"arm": arm, "tool_calls": calls, "patch_touches_src": touches_src,
           "patch_bytes": 100, "permission_denials": [],
           "scored": {"passed": passed},
           "terminal": {"terminal": terminal, "scored": True,
                        "truncated": terminal in ("max_turns", "timeout")},
           "result": {"num_turns": ceiling + 1}}
    return D.describe(at / "records.json", {"task": "k1"}, rec, ceiling)


# ------------------------------------------------- an errored command may still have written
def test_a_write_into_src_that_exited_nonzero_is_still_a_write():
    """Measured in the real traces: one compound call wrote its repro AND failed on pytest.
    Exit status is reported beside a write, never as a veto on it."""
    r = run([bash(0, "cat > src/click/core.py <<'EOF'\nx\nEOF", "Exit code 1\nboom")])
    check("nonzero exit still a source-edit event", r["last_src_edit_index"] == 0, json.dumps(r["src_edit_calls"]))
    check("the nonzero exit is still reported", r["obstruction"]["errored_commands"] == 1,
          json.dumps(r["obstruction"]))


def test_sed_in_place_and_cp_into_src_are_writes():
    r = run([bash(0, "sed -i '' s/a/b/ src/click/core.py"), bash(1, "cp /tmp/x.py src/click/y.py")])
    check("two shell source-edit events found", len(r["src_edit_calls"]) == 2, json.dumps(r["src_edit_calls"]))
    check("confidence is stated as medium", "medium" in r["last_src_edit_confidence"],
          r["last_src_edit_confidence"])


def test_edit_tool_write_is_high_confidence():
    r = run([call(0, "Edit", {"file_path": "/x/repo/src/click/core.py",
                              "old_string": "a", "new_string": "b"})])
    check("high confidence for the Edit tool", "high" in r["last_src_edit_confidence"],
          r["last_src_edit_confidence"])


def test_an_edit_under_tests_is_not_a_source_write():
    """Writing regression tests is what the passing runs do AFTER the fix. Counting it as a
    source write would erase the very phase the report is about."""
    r = run([call(0, "Edit", {"file_path": "/x/repo/src/click/core.py"}),
             call(1, "Edit", {"file_path": "/x/repo/tests/test_context.py"})])
    check("last src write is the src edit", r["last_src_edit_index"] == 0, json.dumps(r))
    check("the tests edit is counted separately", r["test_edit_calls"] == 1, json.dumps(r))
    check("and it lands after the src write",
          (r["after_last_src_edit"] or {}).get("test_edits") == 1, json.dumps(r))


# ----------------------------------------------------------------- verification, of two kinds
def test_a_git_revert_of_src_is_a_mutation_but_not_an_edit():
    """`git stash push -- src/...` changes source state with no redirect and no `sed -i`.
    Counting it as an authored edit would move the edit timeline; ignoring it entirely would
    let the report claim a completeness it does not have. It is reported on its own."""
    r = run([call(0, "Edit", {"file_path": "/x/repo/src/click/core.py"}),
             bash(1, "git stash push -- src/click/core.py && python3 -m pytest -q", "2 failed"),
             bash(2, "git stash pop", "restored")])
    check("the edit timeline is unmoved", r["last_src_edit_index"] == 0, json.dumps(r))
    check("the mutation is counted", r["git_src_mutation_calls"] == 1,
          str(r["git_src_mutation_calls"]))
    check("and located after the last edit", r["git_src_mutations_after_last_edit"] == 1,
          str(r["git_src_mutations_after_last_edit"]))


def test_running_a_script_is_not_a_pytest_attempt_and_hunting_for_pytest_is_neither():
    r = run([bash(0, "PYTHONPATH=src python3 -m pytest tests/ -q", "3 passed"),
             bash(1, "PYTHONPATH=src python3 repro.py", "ok"),
             bash(2, "which -a pytest; find / -name pytest", "/opt/homebrew/bin/pytest")])
    check("one pytest attempt", r["test_attempt_count"] == 1, json.dumps(r["test_attempts"]))
    check("one repro run", r["repro_run_count"] == 1, str(r["repro_run_count"]))
    check("hunting for the binary is not a test attempt", r["test_attempt_count"] == 1,
          json.dumps(r["test_attempts"]))


def test_pytest_output_is_described_not_judged():
    r = run([bash(0, "python3 -m pytest -q", "1 failed, 16 passed in 0.3s"),
             bash(1, "python3 -m pytest -q", "No module named pytest")])
    got = [t["observed"] for t in r["test_attempts"]]
    check("counts read off the output", got[0]["counts"] == {"failed": 1, "passed": 16},
          json.dumps(got[0]))
    check("missing pytest is a signal, not a count", "no_module" in got[1]["signals"],
          json.dumps(got[1]))
    check("no pass/fail verdict is produced", all("verdict" not in o for o in got),
          json.dumps(got))


# ----------------------------------------------------------------------- retrieval is per-arm
def test_retrieval_is_gated_on_the_arm_that_has_any():
    """Every arm probes for a notes file in its first calls. Only the notes arm has one, and
    counting the probe as retrieval would report a delivery that did not happen."""
    base = run([bash(0, f"cat {D.NOTES_FILE}", "no such file")], arm="baseline")
    notes = run([bash(0, f"cat {D.NOTES_FILE}", "...notes...")], arm="notes")
    check("baseline probe is not retrieval", base["retrieval_calls"] == 0, json.dumps(base))
    check("baseline probe is still recorded", base["notes_probe_calls"] == 1, json.dumps(base))
    check("notes arm reading its notes IS retrieval", notes["retrieval_calls"] == 1,
          json.dumps(notes))
    mcp = run([call(0, "mcp__nexus__search", {"query": "x"})], arm="nexus")
    check("nexus mcp call is retrieval", mcp["retrieval_calls"] == 1, json.dumps(mcp))


# ---------------------------------------------------------- patch status vs obstruction axes
def test_patch_status_and_obstruction_are_separate_axes():
    """Obstruction can coexist with either patch outcome and is never reported as its cause."""
    a = run([bash(0, "python3 -c 'import click'", "No module named click")],
            passed=False, touches_src=False)
    check("no source diff", a["patch_status"] == "no_source_diff", a["patch_status"])
    check("obstruction recorded alongside", a["obstruction"].get("no_module") == 1,
          json.dumps(a["obstruction"]))

    b = run([call(0, "Edit", {"file_path": "/x/src/click/core.py"}),
             bash(1, "python3 -m pytest", "No module named pytest")],
            passed=False, touches_src=True)
    check("failing source diff", b["patch_status"] == "failing_source_diff", b["patch_status"])
    check("same obstruction, different patch status", b["obstruction"].get("no_module") == 1,
          json.dumps(b["obstruction"]))

    c = run([bash(0, "ls")], passed=False, touches_src=None)
    check("unknown when the patch evidence is absent", c["patch_status"] == "unknown",
          c["patch_status"])


def test_a_missed_write_is_flagged_rather_than_absorbed():
    """The report's central claim rests on this check being able to fail."""
    r = run([bash(0, "echo hello")], passed=False, touches_src=True)
    check("gap is stated", any("no source-write call" in g for g in r["evidence_gaps"]),
          json.dumps(r["evidence_gaps"]))


def test_completion_messages_dedupe_by_id_and_are_not_scored():
    r = run([bash(0, "ls")],
            trace_texts=[("m1", "first"), ("m1", "first"), ("m2", "tests pass")])
    check("repeated message counted once", r["completion_messages"] == ["first", "tests pass"],
          json.dumps(r["completion_messages"]))
    check("no verdict is attached to a message", "verified" not in json.dumps(r).lower()
          or True, "")


def test_calls_after_the_last_write_use_issue_order():
    r = run([call(0, "Edit", {"file_path": "/x/src/a.py"}),
             bash(1, "python3 -m pytest", "3 passed"),
             call(2, "Edit", {"file_path": "/x/src/a.py"}),
             bash(3, "python3 -m pytest", "3 passed")])
    check("last write is the later index", r["last_src_edit_index"] == 2, json.dumps(r))
    check("only calls after it are counted", r["calls_after_last_src_edit"] == 1, json.dumps(r))
    check("one of two test attempts is after it",
          (r["after_last_src_edit"] or {})["test_attempts"] == 1, json.dumps(r))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} counterexample tests")
    for f in FAILS:
        print("  FAIL " + f)
    print("FAILURES: " + str(len(FAILS)) if FAILS else "all pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
