"""Counterexamples for the calibration reporter and the usage reconciliation.

No model, no network, no fixtures. Each case below is a way the §5 verdict can be wrong while
looking right, and the first two are the ones that actually happened in prose before this
program existed:

  * two COMPLETE ceilings were read as two QUALIFYING ceilings. Eligibility is the gate that
    lets the rule be applied; it is not the rule.
  * a partially covered ceiling's low truncation rate was read as the encouraging direction,
    when §5 does not admit it to the comparison at all.

    python3 test_report_calibration.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reconcile_usage as RU                                            # noqa: E402
import report_calibration as R                                          # noqa: E402

FAILS: list[str] = []

LAUNCH = {
    "product_revision": "p" * 40, "harness_revision": "h" * 40,
    "config_version": "calib-v2", "schedule_digest": "s" * 64,
    "corpus_digest_registered": "c" * 16,
    "ceilings": {30: {"config_digest": "d30", "max_turns": 30},
                 45: {"config_digest": "d45", "max_turns": 45},
                 60: {"config_digest": "d60", "max_turns": 60}},
    "prompts": {t: f"prompt-{t}" for t in R.TASKS},
}


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILS.append(f"{name}: {detail}")


def identity(task: str, ceiling: int, **override) -> dict:
    ident = {"config_version": LAUNCH["config_version"],
             "product_revision": LAUNCH["product_revision"],
             "harness_revision": LAUNCH["harness_revision"],
             "corpus_digest_registered": LAUNCH["corpus_digest_registered"],
             "schedule_digest": LAUNCH["schedule_digest"],
             "config_digest": LAUNCH["ceilings"][ceiling]["config_digest"],
             "max_turns_applied": ceiling,
             "prompt_digest": LAUNCH["prompts"][task]}
    ident.update(override)
    return ident


def write_row(scratch: Path, top: str, task: str, ceiling: int, arms: list[dict],
              ident: dict | None = "default") -> None:
    """One row: `<top>/run-<task>/attempt1/records.json` with one record per arm."""
    at = scratch / top / f"run-{task}" / "attempt1"
    at.mkdir(parents=True, exist_ok=True)
    recs = []
    for a in arms:
        terminal = a["terminal"]
        recs.append({
            "arm": a["arm"],
            "terminal": {"terminal": terminal,
                         "scored": terminal not in ("env_fail", "unknown"),
                         "truncated": terminal in ("max_turns", "timeout")},
            "scored": {"passed": a.get("passed", False)},
            "usage": a.get("usage", {"input_tokens": 100, "output_tokens": 10,
                                     "cache_read_input_tokens": 0,
                                     "cache_creation_input_tokens": 0}),
            "result": {"num_turns": ceiling + 1},
        })
    body = {"task": task, "max_turns": ceiling, "records": recs}
    if ident != "default":
        body["identity"] = ident
    else:
        body["identity"] = identity(task, ceiling)
    (at / "records.json").write_text(json.dumps(body))


def full_ceiling(scratch: Path, ceiling: int, terminals: list[str],
                 passes: list[bool] | None = None, top: str | None = None) -> None:
    """All 12 arm-runs at one ceiling, terminals given in (task, arm) order."""
    passes = passes or [False] * 12
    it = iter(zip(terminals, passes))
    for task in R.TASKS:
        arms = []
        for arm in R.ARMS:
            t, p = next(it)
            arms.append({"arm": arm, "terminal": t, "passed": p})
        write_row(scratch, top or f"c{ceiling}", task, ceiling, arms)


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def report(scratch: Path) -> dict:
    cells, excluded = R.read_cells(scratch, LAUNCH)
    table = R.ceiling_table(cells)
    return {"table": table, "selection": R.apply_selection_rule(table),
            "excluded": excluded, "cells": cells}


# ------------------------------------------------------- the rule, where prose got it wrong
def test_two_complete_ceilings_above_the_threshold_select_nothing():
    """THE case this sweep produced. Complete is not qualifying.

    Ceiling 30 truncates 12/12 and ceiling 45 truncates 9/12. Both are eligible -- §5's
    two-eligible-ceilings condition is satisfied -- and neither is under 20%, so the rule
    selects nothing. Reading "two complete ceilings" as "the rule can now pick one" is the
    mistake; the rule can now be APPLIED, and applying it returns no ceiling.
    """
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    full_ceiling(d, 45, ["max_turns"] * 9 + ["completed"] * 3)
    sel = report(d)["selection"]
    check("two failing complete ceilings -> no selection", sel["selected"] is None,
          json.dumps(sel["per_ceiling"]))
    check("both are still eligible", sel["eligible"] == [30, 45], str(sel["eligible"]))
    check("30 fails on truncation, not on coverage",
          sel["per_ceiling"][30]["status"] == "fails_truncation",
          json.dumps(sel["per_ceiling"][30]))
    check("45 fails on truncation, not on coverage",
          sel["per_ceiling"][45]["status"] == "fails_truncation",
          json.dumps(sel["per_ceiling"][45]))


def test_a_qualifying_ceiling_is_selected():
    """The positive control. Without it, a reporter that always returns None would pass."""
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    full_ceiling(d, 45, ["max_turns"] * 2 + ["completed"] * 10)
    sel = report(d)["selection"]
    check("2/12 truncated qualifies", sel["selected"] == 45, json.dumps(sel))


def test_exactly_the_threshold_qualifies():
    """'at most 20%' includes 20%. 2/12 is 16.7%; 20% of 12 is 2.4, so 2 is the largest
    passing count and 3 is the smallest failing one."""
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    full_ceiling(d, 45, ["max_turns"] * 2 + ["completed"] * 10)
    check("2 truncated of 12 qualifies", report(d)["selection"]["selected"] == 45)
    d2 = _tmp()
    full_ceiling(d2, 30, ["max_turns"] * 12)
    full_ceiling(d2, 45, ["max_turns"] * 3 + ["completed"] * 9)
    check("3 truncated of 12 does not", report(d2)["selection"]["selected"] is None)


def test_lowest_qualifying_ceiling_wins_and_higher_is_not_reached():
    d = _tmp()
    full_ceiling(d, 30, ["completed"] * 12)
    full_ceiling(d, 45, ["completed"] * 12)
    full_ceiling(d, 60, ["completed"] * 12)
    sel = report(d)["selection"]
    check("lowest qualifying wins", sel["selected"] == 30, json.dumps(sel))
    check("60 is not reached", sel["per_ceiling"][60]["status"] == "not_reached",
          json.dumps(sel["per_ceiling"][60]))


def test_correctness_clause_refuses_a_ceiling_that_solves_less():
    """A larger ceiling that raises truncation-free runs while lowering tasks solved has
    bought exploration, not progress. §5 refuses it."""
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 3 + ["completed"] * 9, [True] * 9 + [False] * 3)
    full_ceiling(d, 45, ["completed"] * 12, [True] * 4 + [False] * 8)
    sel = report(d)["selection"]
    check("30 fails truncation (3/12)", sel["per_ceiling"][30]["status"] == "fails_truncation")
    check("45 refused on correctness", sel["per_ceiling"][45]["status"] == "fails_correctness",
          json.dumps(sel["per_ceiling"][45]))
    check("nothing selected", sel["selected"] is None, json.dumps(sel))


# ------------------------------------------------------------------ coverage and exclusion
def test_partial_ceiling_is_ineligible_however_good_it_looks():
    """Ceiling 60 at 3/12 with zero truncation is not a candidate. Comparing a complete
    ceiling against a partial one would let coverage, not the ceiling, decide."""
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    full_ceiling(d, 45, ["max_turns"] * 12)
    write_row(d, "c60", "k1", 60, [{"arm": a, "terminal": "completed", "passed": True}
                                   for a in R.ARMS])
    rep = report(d)
    sel = rep["selection"]
    check("partial ceiling ineligible", sel["per_ceiling"][60]["status"] == "ineligible",
          json.dumps(sel["per_ceiling"][60]))
    check("nothing selected", sel["selected"] is None, json.dumps(sel))
    check("its nine missing cells are named",
          len(rep["table"][60]["missing_cells"]) == 9, str(rep["table"][60]["missing_cells"]))


def test_fewer_than_two_eligible_selects_nothing_even_at_zero_truncation():
    """A grid of one point cannot show that a lower ceiling was insufficient."""
    d = _tmp()
    full_ceiling(d, 30, ["completed"] * 12)
    sel = report(d)["selection"]
    check("one eligible ceiling -> no selection", sel["selected"] is None, json.dumps(sel))
    check("and says why", "fewer than two eligible" in sel["reason"], sel["reason"])


def test_quarantine_is_by_identity_not_by_directory_name():
    """The exclusion must survive a rename in either direction.

    A row under a directory called `c30` whose harness revision differs is NOT this sweep,
    and a row under a directory called `quarantine-anything` whose identity matches IS. Name
    the exclusion after the directory and both cases go the wrong way in silence.
    """
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    full_ceiling(d, 45, ["max_turns"] * 12)
    # same directory prefix, different harness: must be excluded
    write_row(d, "c60", "k1", 60,
              [{"arm": a, "terminal": "completed", "passed": True} for a in R.ARMS],
              ident=identity("k1", 60, harness_revision="x" * 40))
    rep = report(d)
    check("foreign harness excluded from analysis", rep["table"][60]["arm_runs"] == 0,
          json.dumps(rep["table"][60]))
    check("and the reason names the field",
          any("harness_revision" in r for e in rep["excluded"] for r in e["reasons"]),
          json.dumps(rep["excluded"]))

    d2 = _tmp()
    full_ceiling(d2, 30, ["max_turns"] * 12, top="anything-at-all")
    rep2 = report(d2)
    check("matching identity under an odd directory name is IN scope",
          rep2["table"][30]["arm_runs"] == 12, json.dumps(rep2["table"][30]))


def test_row_with_no_identity_is_excluded():
    """v1 wrote no identity block at all; that is not poolable with a frozen launch."""
    d = _tmp()
    write_row(d, "v1-c30-unrepaired-sandbox", "k1", 30,
              [{"arm": a, "terminal": "max_turns"} for a in R.ARMS], ident=None)
    rep = report(d)
    check("no identity -> excluded", rep["table"][30]["arm_runs"] == 0,
          json.dumps(rep["table"][30]))
    check("reason is stated", "records no identity at all" in json.dumps(rep["excluded"]),
          json.dumps(rep["excluded"]))


def test_barren_row_is_excluded_from_analysis():
    """A row where the forwarder was down measured nothing; its 3 arm-runs are not coverage."""
    d = _tmp()
    write_row(d, "c30", "k1", 30, [{"arm": a, "terminal": "env_fail"} for a in R.ARMS])
    rep = report(d)
    check("barren row excluded", rep["table"][30]["arm_runs"] == 0,
          json.dumps(rep["table"][30]))
    check("reason is stated", "measured nothing" in json.dumps(rep["excluded"]),
          json.dumps(rep["excluded"]))


def test_excluded_rows_still_count_in_the_ledger():
    """Analysis scope shrinks; accounting scope does not. Quarantined rows were still paid
    for, and dropping them from the ledger once lost 6 425 690 tokens."""
    d = _tmp()
    full_ceiling(d, 30, ["max_turns"] * 12)
    write_row(d, "v1-c30-unrepaired-sandbox", "k1", 30,
              [{"arm": a, "terminal": "max_turns",
                "usage": {"input_tokens": 1000, "output_tokens": 0,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}} for a in R.ARMS], ident=None)
    import run_calibration as RC
    led = RC.consumed(d)
    cells, _ = R.read_cells(d, LAUNCH)
    check("quarantined tokens are in the ledger", led["tokens_known"] == 12 * 110 + 3 * 1000,
          json.dumps(led))
    check("quarantined arm-runs are NOT in the analysis", len(cells) == 12, str(len(cells)))


def test_unresolved_arm_run_leaves_consumption_uncertain():
    d = _tmp()
    arm = d / "c60" / "run-k1" / "attempt1" / "arms" / "baseline"
    arm.mkdir(parents=True)
    (arm / "launched.json").write_text(json.dumps({"arm": "baseline"}))
    import run_calibration as RC
    led = RC.consumed(d)
    check("launched but unaccounted -> unresolved", led["unresolved"] == 1, json.dumps(led))
    check("consumption is not certain", led["consumption_certain"] is False, json.dumps(led))


# --------------------------------------------------------------- the launch record parser
def test_launch_record_parses_the_real_file():
    """The parser reads the committed launch record, not a fixture of what it should say."""
    real = Path(__file__).parent.parent.parent / "LAUNCH-A2.md"
    if not real.exists():
        check("LAUNCH-A2.md present", False, f"{real} missing")
        return
    got = R.parse_launch_record(real)
    check("harness revision is a full sha", len(got["harness_revision"]) == 40,
          got["harness_revision"])
    check("three ceilings", sorted(got["ceilings"]) == [30, 45, 60], str(got["ceilings"]))
    check("four prompt digests", sorted(got["prompts"]) == list(R.TASKS), str(got["prompts"]))
    check("ceiling 45 max_turns is 45", got["ceilings"][45]["max_turns"] == 45,
          str(got["ceilings"][45]))


def test_launch_record_missing_fields_raises():
    """A launch record that does not say what identity is may not be silently defaulted."""
    p = _tmp() / "LAUNCH.md"
    p.write_text("# nothing here\n")
    try:
        R.parse_launch_record(p)
    except ValueError:
        return
    check("empty launch record raises", False, "parsed an empty launch record")


# ------------------------------------------------------------------- usage reconciliation
def _trace(path: Path, msgs: list[tuple], envelope: dict | None) -> None:
    lines = []
    for mid, inp, cread in msgs:
        lines.append(json.dumps({"type": "assistant", "message": {
            "id": mid, "usage": {"input_tokens": inp, "output_tokens": 0,
                                 "cache_read_input_tokens": cread,
                                 "cache_creation_input_tokens": 0}}}))
    if envelope is not None:
        lines.append(json.dumps({"type": "result", "usage": envelope}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_repeated_message_ids_are_counted_once():
    """The whole reason the naive sum was wrong: 81 messages, 35 responses."""
    d = _tmp()
    t = d / "c60" / "run-k1" / "attempt1" / "arms" / "baseline" / "trace.jsonl"
    _trace(t, [("a", 10, 100), ("a", 10, 100), ("a", 10, 100), ("b", 20, 200)], None)
    s = RU.scan_trace(t)
    check("naive sum triple-counts a", s["naive_total"] == 3 * 110 + 220, json.dumps(s))
    check("dedup counts a once", s["dedup_total"] == 110 + 220, json.dumps(s))
    check("distinct ids", s["distinct_message_ids"] == 2, json.dumps(s))


def test_dedup_reproducing_a_known_envelope_is_the_control():
    d = _tmp()
    t = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline" / "trace.jsonl"
    _trace(t, [("a", 10, 100), ("a", 10, 100), ("b", 20, 200)],
           {"input_tokens": 30, "output_tokens": 500, "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 0})
    rep = RU.build(d)
    check("agrees with the envelope on the observed fields",
          rep["validation"]["exact_on_observed_fields"] == 1, json.dumps(rep["validation"]))
    check("nothing vacuous here", rep["validation"]["vacuous_nothing_to_compare"] == 0,
          json.dumps(rep["validation"]))


def test_zero_against_zero_is_not_an_agreement():
    """36 forwarder-down traces compare zero with zero. Counted as successes they would take
    the control from 34 real agreements to 70 and validate nothing."""
    d = _tmp()
    t = d / "v2-void" / "run-k1" / "attempt1" / "arms" / "baseline" / "trace.jsonl"
    _trace(t, [("a", 0, 0)], {"input_tokens": 0, "output_tokens": 0,
                              "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 0})
    v = RU.build(d)["validation"]
    check("vacuous, not exact", v["vacuous_nothing_to_compare"] == 1 and
          v["exact_on_observed_fields"] == 0, json.dumps(v))


def test_a_trace_short_of_its_envelope_is_reported_not_rounded_away():
    d = _tmp()
    t = d / "c45" / "run-k3" / "attempt1" / "arms" / "baseline" / "trace.jsonl"
    _trace(t, [("a", 10, 100)], {"input_tokens": 30, "output_tokens": 0,
                                 "cache_read_input_tokens": 300,
                                 "cache_creation_input_tokens": 0})
    v = RU.build(d)["validation"]
    check("shortfall reported", len(v["mismatched"]) == 1, json.dumps(v))
    check("shortfall quantified", v["mismatched"][0]["shortfall_total"] == 220, json.dumps(v))


def test_same_id_with_different_usage_is_flagged():
    """The provider documents this case. On this sweep it never occurred; if it ever does,
    'one usage per ID' is choosing between two figures and must say so."""
    d = _tmp()
    t = d / "c30" / "run-k1" / "attempt1" / "arms" / "baseline" / "trace.jsonl"
    _trace(t, [("a", 10, 100), ("a", 99, 100)], None)
    s = RU.scan_trace(t)
    check("conflict recorded", len(s["conflicting_ids"]) == 1, json.dumps(s))


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
