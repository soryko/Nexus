"""Counterexamples for the calibration driver, its accounting, and the environment gate.

No model, no network, no fixtures. Every case below is one that was WRONG in a published
revision, and each is the kind of wrong that is silent: a budget that stops counting, a gate
that passes a failing test run, a summary that crashes only on the path nobody exercised.

    python3 test_calibration_counterexamples.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_calibration as RC                                            # noqa: E402
import verify_arm_environment as V                                      # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


# --------------------------------------------------------------------------- accounting
def test_missing_envelope_is_unresolved_not_zero():
    """A trace with no terminal envelope is consumption of UNKNOWN size, never of zero size."""
    d = _tmp()
    arm = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "trace.jsonl").write_text('{"type":"assistant","message":{"content":[]}}\n')
    c = RC.consumed(d)
    check("trace without envelope -> unresolved", c["unresolved"] == 1, json.dumps(c))
    check("trace without envelope -> counted as a run", c["arm_runs"] == 1, json.dumps(c))


def test_record_without_usage_is_unresolved_not_zero():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "records.json").write_text(json.dumps({"records": [{"arm": "baseline"}]}))
    c = RC.consumed(d)
    check("record without usage -> unresolved", c["unresolved"] == 1, json.dumps(c))


def test_per_arm_record_counts_when_row_never_completed():
    """Two arms finish, the row dies before records.json. Their tokens are still spent."""
    d = _tmp()
    arm = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "record.json").write_text(json.dumps(
        {"arm": "baseline", "record": {"usage": {"input_tokens": 10, "output_tokens": 5}}}))
    c = RC.consumed(d)
    check("per-arm record counted", c["tokens_known"] == 15 and c["unresolved"] == 0,
          json.dumps(c))


def test_quarantine_by_rename_does_not_drop_consumption():
    """The glob must not depend on a directory's name -- renaming once dropped 6.4M tokens."""
    d = _tmp()
    at = d / "v1-c30-quarantined" / "run-k1" / "attempt1"
    at.mkdir(parents=True)
    (at / "records.json").write_text(json.dumps(
        {"records": [{"arm": "baseline", "usage": {"input_tokens": 100}}]}))
    c = RC.consumed(d)
    check("renamed directory still counted", c["tokens_known"] == 100, json.dumps(c))


def test_arm_counted_once_across_sources():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "record.json").write_text(json.dumps(
        {"arm": "baseline", "record": {"usage": {"input_tokens": 7}}}))
    (at / "records.json").write_text(json.dumps(
        {"records": [{"arm": "baseline", "usage": {"input_tokens": 7}}]}))
    c = RC.consumed(d)
    check("no double count", c["tokens_known"] == 7 and c["arm_runs"] == 1, json.dumps(c))


# --------------------------------------------------------------------------- restart
def test_partial_attempt_is_preserved_not_overwritten():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "trace.jsonl").write_text("{}\n")
    moved = RC.preserve_partial(at)
    check("partial moved aside", moved is not None and moved.exists(), str(moved))
    check("original path freed", not at.exists(), str(at))
    check("evidence survives", moved and (moved / "arms" / "baseline" / "trace.jsonl").exists())


def test_completed_attempt_is_not_moved():
    d = _tmp()
    at = d / "c30" / "run-k1" / "attempt1"
    (at / "arms" / "baseline").mkdir(parents=True)
    (at / "arms" / "baseline" / "trace.jsonl").write_text("{}\n")
    (at / "records.json").write_text("{}")
    check("completed attempt untouched", RC.preserve_partial(at) is None)


# --------------------------------------------------------------------------- identity
class _Plan:
    schedule_digest = "abc123"


def test_incompatible_configuration_is_refused_on_resume():
    d = _tmp()
    rec = d / "records.json"
    cfg = {"config_version": "calib-v2", "max_turns": 45, "corpus_digest": "deadbeef"}
    rec.write_text(json.dumps({
        "max_turns": 30, "schedule_digest": "abc123", "corpus_digest_registered": "deadbeef",
        "identity": {"config_version": "calib-v1"}}))
    why = RC.incompatible(rec, cfg, _Plan())
    check("config mismatch refused", why and "config_version" in why, str(why))
    check("ceiling mismatch refused", why and "max_turns" in why, str(why))


def test_records_without_identity_are_refused():
    d = _tmp()
    rec = d / "records.json"
    cfg = {"config_version": "calib-v2", "max_turns": 30, "corpus_digest": "d"}
    rec.write_text(json.dumps({"max_turns": 30, "schedule_digest": "abc123",
                               "corpus_digest_registered": "d"}))
    why = RC.incompatible(rec, cfg, _Plan())
    check("no identity block refused", why and "identity" in why, str(why))


def test_matching_configuration_resumes():
    d = _tmp()
    rec = d / "records.json"
    cfg = {"config_version": "calib-v2", "max_turns": 30, "corpus_digest": "d"}
    rec.write_text(json.dumps({
        "max_turns": 30, "schedule_digest": "abc123", "corpus_digest_registered": "d",
        "identity": {"config_version": "calib-v2"}}))
    check("matching identity resumes", RC.incompatible(rec, cfg, _Plan()) is None)


# --------------------------------------------------------------------------- gate predicates
def test_gate_rejects_a_failing_test_run():
    """`1 failed, 23 passed` passed the previous predicate."""
    outs = {
        "1 failed, 23 passed in 0.1s": False,
        "23 passed in 0.02s": True,
        "no tests ran in 0.01s": False,
        "ERROR tests/x.py - Interrupted: 1 error during collection": False,
    }
    for text, want in outs.items():
        m = V.PYTEST_SUMMARY.search(text)
        n = int(m.group(1)) if m else 0
        bad = bool(V.PYTEST_BAD.search(text))
        got = n > 0 and not bad
        check(f"pytest predicate {text[:28]!r}", got == want, f"got {got} want {want}")


def test_gate_requires_env_argument():
    """The gate once built a restricted environment and then never passed it."""
    import inspect
    for fn in (V.check_interpreter, V.check_tests, V.check_heredoc, V.check_scratch):
        params = list(inspect.signature(fn).parameters)
        check(f"{fn.__name__} takes env", "env" in params, str(params))
    src = inspect.getsource(V._sh)
    check("_sh passes env to subprocess", "env=env" in src, src)


def test_not_tested_is_not_passed():
    """`cross_arm_read_blocked: None` means untested. Untested must not report as passing."""
    # other_profile None -> heredoc_probe reports cross_arm_read_blocked as None (untested).
    # The gate must map that to a FAILURE, not a pass.
    import isolation
    real = isolation.heredoc_probe
    isolation.heredoc_probe = lambda *a, **k: {"tmpprefix_set": True,
                                               "cross_arm_read_blocked": None}
    try:
        r = V.check_heredoc_private(Path("."), Path("."), {"TMPPREFIX": "/x/y"}, None, None)
        check("None is not a pass", r["passed"] is False, json.dumps(r))
    finally:
        isolation.heredoc_probe = real


# --------------------------------------------------------------------------- driver paths
def _stub_driver(tmp: Path, cap: int, tokens_per_arm: int, seed_reserve=None):
    """Run the real driver with a stubbed runner. No model, no sandbox, no network.

    The published revision crashed HERE, on the line after the last row, with
    `NameError: name 'spent' is not defined` -- the token-accounting change had removed the
    function the summary block still called. --dry-run returns before that line, so every
    rehearsal passed while the only path that writes a summary was broken.
    """
    import run_calibration as R
    real_run, real_fix, real_cap = R.subprocess.run, R.build_fixture_for, R.CAP_TOKENS
    real_seed = R.INITIAL_ROW_RESERVE

    def fake_run(cmd, **kw):
        root, task, attempt = Path(cmd[2]), cmd[3], cmd[4]
        at = root / f"run-{task}" / f"attempt{attempt}"
        cfg = json.loads((R.BENCH / f"calib-config-{root.name[1:]}.json").read_text())
        recs = []
        for arm in ("baseline", "nexus", "notes"):
            (at / "arms" / arm).mkdir(parents=True, exist_ok=True)
            (at / "arms" / arm / "trace.jsonl").write_text('{"type":"result","usage":{}}\n')
            recs.append({"arm": arm, "usage": {"input_tokens": tokens_per_arm}})
        (at / "records.json").write_text(json.dumps({
            "max_turns": cfg["max_turns"], "schedule_digest": R.sched.load(R.SCHEDULE).schedule_digest,
            "corpus_digest_registered": cfg.get("corpus_digest"),
            "identity": {"config_version": cfg["config_version"]}, "records": recs}))

        class D:
            returncode = 0
        return D()

    R.subprocess.run = fake_run
    R.build_fixture_for = lambda *a, **k: (a[1] / f"run-{a[0]}" / "base" / a[0]).mkdir(
        parents=True, exist_ok=True)
    R.CAP_TOKENS = cap
    if seed_reserve is not None:
        R.INITIAL_ROW_RESERVE = seed_reserve
    try:
        rc = R.main(["run_calibration.py", str(tmp), "--ceilings", "30,45"])
    finally:
        R.subprocess.run, R.build_fixture_for, R.CAP_TOKENS = real_run, real_fix, real_cap
        R.INITIAL_ROW_RESERVE = real_seed
    summary = tmp / "calibration-summary.json"
    return rc, (json.loads(summary.read_text()) if summary.exists() else None)


def test_driver_completes_and_writes_a_summary():
    tmp = _tmp()
    rc, summary = _stub_driver(tmp, cap=10_000_000, tokens_per_arm=1000)
    check("completion path returns 0", rc == 0, str(rc))
    check("completion writes a summary", summary is not None)
    if summary:
        check("summary reports completion",
              summary["stopping_reason"] == "completed", json.dumps(summary)[:200])
        check("summary counts every arm-run", summary["arm_runs"] == 24, str(summary["arm_runs"]))
        check("summary has no unresolved", summary["unresolved_arm_runs"] == [],
              str(summary["unresolved_arm_runs"]))
        check("overshoot is certain", summary["overshoot_certain"] is True)


def test_driver_stops_on_budget_and_writes_a_summary():
    tmp = _tmp()
    # Two rows fit; the third needs more than the cap allows, so it must stop BEFORE starting
    # it. The seeded reserve matches the stub's row size so the first row is not refused.
    rc, summary = _stub_driver(tmp, cap=7000, tokens_per_arm=1000, seed_reserve=3000)
    check("budget path returns 0", rc == 0, str(rc))
    check("budget stop writes a summary", summary is not None)
    if summary:
        check("summary reports the budget stop",
              summary["stopping_reason"] == "budget", json.dumps(summary)[:200])
        check("summary names where it stopped", summary["stopped_at"] is not None,
              str(summary.get("stopped_at")))
        check("grid is partial", summary["arm_runs"] < 24, str(summary["arm_runs"]))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as e:                                   # a crash is a failure
            FAILS.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
    for f in FAILS:
        print(f"  FAIL {f}")
    print(f"{len(tests) - len({f.split(':')[0] for f in FAILS})}/{len(tests)} "
          f"counterexample groups pass")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
