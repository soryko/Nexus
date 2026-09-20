"""The DOWNSTREAM integration rehearsal, as checks.

    saved records -> REAL compliance scoring -> normalisation -> decision report

Execution is stubbed by WRITING the records, so nothing here establishes that the real
runner produces them. `test_a3_production.py` covers the production adapter and the egress
control; this file covers the join between the artifacts and the decision, which is where
the fix-leakage scan's defect lived.

`test_a3_decision.py` builds `ArmRun` objects in memory and asks what the table decides.
That is necessary and it is not sufficient, and the reason is on the record rather than
hypothetical: the fix-leakage scan had tests, and it still published "none" on four tasks,
because its defect was in the JOIN -- no `fixtures.json` recorded a clone, so the scan
returned an empty set and an empty set matches nothing. The tests sat on either side of that
join and never crossed it.

Everything below crosses it. Records are written to disk, `score_compliance` reads them back
and runs pytest over three real trees, `a3_pipeline` normalises the artifacts, and
`a3_decision` decides. Stubbed execution is the only stub: no model, no budget, no arm-run.

    <venv-with-pyexpat>/bin/python -m pytest benchmarks/agent/test_a3_pipeline.py -q

The probe parses JUnit XML in THIS process, so this file needs `pyexpat` here and not merely
in the interpreter it hands to pytest. `rehearse_a3.probe_python` refuses when it is missing
rather than letting every task report `required_work: indeterminate`, which is what an
interpreter without it produces and which reads exactly like a finding.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a3_pipeline as P                                                    # noqa: E402
import rehearse_a3 as R                                                    # noqa: E402
import schedule as S                                                       # noqa: E402
from a3_decision import A3_PLAN, Plan, consumption, validity               # noqa: E402


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    """One complete 16-arm-run sweep where B is genuinely cheaper and loses nothing."""
    return R.build(tmp_path_factory.mktemp("clean"), overrides=R.cheaper_B())


@pytest.fixture(scope="module")
def rejected(tmp_path_factory):
    """The same sweep with B losing both of one task's functional passes."""
    first = A3_PLAN.tasks[0]
    return R.build(tmp_path_factory.mktemp("reject"), overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"functional": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000},
        (first, 2, "B"): {"functional": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000}})


@pytest.fixture(scope="module")
def partial(tmp_path_factory):
    """The same sweep stopped at the soft threshold after three pairs."""
    return R.build(tmp_path_factory.mktemp("partial"), overrides=R.cheaper_B(),
                   stop_after_pairs=3)


# ---------------------------------------------------------------------------------------
# 1. All 16 planned arm-runs retain distinct identities.
# ---------------------------------------------------------------------------------------

def test_all_sixteen_arm_runs_retain_distinct_identities(clean):
    v = clean["validity"]
    assert v["records"] == v["distinct_identities"] == v["planned_arm_runs"] == 16
    assert v["duplicate_identities"] == [] and v["missing"] == [] and v["unplanned"] == []


def test_the_two_policies_never_share_a_directory(tmp_path):
    """`(task, arm)` is not an identity here: both policies run the nexus arm, and the
    historical layout `run-<task>/attempt<n>/arms/<arm>/` is one directory for both."""
    a = P.arm_run_dir(tmp_path, "k1", 1, "A")
    b = P.arm_run_dir(tmp_path, "k1", 1, "B")
    assert a != b
    assert a.name == b.name == P.ARM            # ... and yet the same arm
    assert {d.name for d in (a.parent.parent, b.parent.parent)} == {"policy-A", "policy-B"}


def test_every_saved_arm_run_is_reachable_and_distinct_on_disk(clean):
    dirs = {n["dir"] for n in clean["normalisation"]}
    assert len(dirs) == 16
    assert all(Path(d, "trace.jsonl").is_file() for d in dirs)


def test_the_schedule_is_over_policies_and_varies(clean):
    """The A/B order is drawn once from one generator and frozen before execution. A
    schedule whose every pair reads `A->B` is the constant `run_arms_isolated` had."""
    cb = clean["schedule"]["counterbalance"]
    assert cb["pairs"] == 8
    assert cb["distinct_orders"] == 2, cb["orders_used"]
    assert cb["every_arm_appears_once_per_pair"]


# ---------------------------------------------------------------------------------------
# 2. An unresolved first pair prevents the next pair from launching.
# ---------------------------------------------------------------------------------------

def test_unresolved_consumption_in_the_first_pair_stops_the_next(tmp_path):
    """§7: the sweep stops and the row stays unresolved -- never zero, never covered by an
    allowance written to unblock it. The gate is checked BEFORE a pair starts."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "A"): {"total_tokens": None, "accounted": False}})
    gates = rep["launch_gate"]
    assert gates[0]["may_start"] is True                      # the first pair did start
    assert gates[1]["may_start"] is False                     # the second did not
    assert gates[1]["stopping_reason"] == "unresolved_accounting"
    assert len(gates) == 2                                    # and nothing after it ran
    assert rep["validity"]["records"] == 2
    assert rep["further_launches_permitted"] is False


def test_an_unresolved_outcome_does_not_stop_the_sweep(tmp_path):
    """The separation the review asked for, through the files: a missing functional result
    is a dimension going indeterminate, not a sweep that has lost track of its spending."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "A"): {"functional": None}})
    assert all(g["may_start"] for g in rep["launch_gate"])
    assert rep["validity"]["records"] == 16
    assert rep["further_launches_permitted"] is True
    assert rep["consumption"]["unresolved_arm_runs"] == []


# ---------------------------------------------------------------------------------------
# 3. Partial coverage cannot be accepted as a complete comparison.
# ---------------------------------------------------------------------------------------

def test_a_sweep_stopped_at_the_soft_threshold_is_partial_not_accepted(partial):
    """Everything that ran is clean and B is cheaper on it. That is still not an accept."""
    rep = partial
    assert rep["validity"]["records"] == 6
    assert rep["experiment_valid"] is False
    assert rep["validity"]["partial"] is True
    assert rep["decision"] == "indeterminate"
    assert "did not run in full" in rep["reason"]
    assert rep["launch_gate"][-1]["stopping_reason"] == "soft_threshold"


def test_a_cell_that_never_ran_is_missing_not_unresolved(tmp_path):
    """Absent and measured-but-unsettled are different facts. Normalising a directory that
    does not exist into an unresolved row would turn "did not run" into "ran and told us
    nothing"."""
    rep = R.build(tmp_path, overrides=R.cheaper_B(), stop_after_pairs=2)
    assert len(rep["validity"]["missing"]) == 12
    assert len(rep["normalisation"]) == 4


# ---------------------------------------------------------------------------------------
# 4. Real scorer artifacts populate the decision engine correctly.
# ---------------------------------------------------------------------------------------

def test_the_regression_probe_actually_executed(clean):
    """The control on this whole file. An interpreter that cannot read the probe's JUnit
    report returns `unknown` on every tree, every task reports `required_work:
    indeterminate`, and the rehearsal measures nothing while looking like it measured
    something."""
    assert clean["probe_resolved_somewhere"] is True
    assert all(n["E1"] == "pass" for n in clean["normalisation"]), \
        [n["E1"] for n in clean["normalisation"]]
    assert clean["dimensions"]["required_work"]["state"] == "hold"


def test_required_work_comes_from_the_scorer_not_from_a_default(tmp_path):
    """A patch that fixes `src/` and adds no test is the A2-R k1/nexus shape: it passed the
    hidden checks. Here the scorer settles E1 as a real failure and the dimension sees it."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"patch": "src_only", "fetches": 1, "total_tokens": 80_000,
                          "deliver": [f"{first}-m-primary"]},
        (first, 2, "B"): {"patch": "src_only", "fetches": 1, "total_tokens": 80_000,
                          "deliver": [f"{first}-m-primary"]}})
    by_dir = {n["dir"]: n for n in rep["normalisation"]}
    b1 = next(v for k, v in by_dir.items() if f"run-{first}/attempt1/policy-B" in k)
    assert b1["E1"] == "fail"
    assert rep["dimensions"]["required_work"]["per_task"][first]["state"] == "fail"
    assert rep["decision"] == "reject"


def test_delivered_facts_are_read_from_the_trace_per_task(clean):
    """Every member of every equivalence class is measured, for the task it belongs to.
    Handing one task's classes to all four leaves the rest unmeasured -- which is
    `unresolved`, and would make three of four tasks indeterminate for no reason about the
    policy. The rehearsal is what caught that."""
    assert clean["dimensions"]["information"]["state"] == "hold"
    for task in A3_PLAN.tasks:
        assert clean["dimensions"]["information"]["per_task"][task]["state"] == "hold"


def test_an_equivalent_memory_satisfies_the_fact_through_the_files(tmp_path):
    """B fetches the OTHER member of the class. Nothing is lost: the fact is the
    requirement, not the memory's identity."""
    rep = R.build(tmp_path, overrides={
        (t, a, "B"): {"deliver": [f"{t}-m-equivalent"], "fetches": 1, "total_tokens": 80_000}
        for t in A3_PLAN.tasks for a in (1, 2)})
    assert rep["dimensions"]["information"]["state"] == "hold"
    k1 = rep["dimensions"]["information"]["per_task"]["k1"]["facts"]["k1-mechanism"]
    assert k1["A"] is True and k1["B"] is True


def test_consultation_calls_and_cost_units_come_from_the_trace(clean):
    c = clean["dimensions"]["cost"]
    assert c["calls"]["A"]["search"] == 8 and c["calls"]["A"]["get"] == 24
    assert c["calls"]["B"]["search"] == 8 and c["calls"]["B"]["get"] == 8
    assert c["consultation_drop"] >= 0.40 and c["total_drop"] >= 0.10
    assert c["delivered_bytes"]["B"] < c["delivered_bytes"]["A"]
    assert c["counting_methods"] == [P.DEFAULT_COUNTING_METHOD]


# ---------------------------------------------------------------------------------------
# 5. Policy violations remain recorded observations, not exclusions.
# ---------------------------------------------------------------------------------------

def test_a_bound_violation_is_recorded_and_excludes_nothing(tmp_path):
    """Two searches and four fetches is a B arm-run that broke the candidate policy. It is
    counted, kept, and still contributes to every dimension. Excluding it would select the
    sample on the outcome being measured."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"searches": 2, "fetches": 4, "total_tokens": 80_000,
                          "deliver": [f"{first}-m-primary"]}})
    bc = rep["bound_compliance"]
    assert bc["n"] == 8 and bc["violated"] == 1 and bc["respected"] == 7
    assert bc["excluded_from_any_dimension"] == 0
    assert rep["validity"]["records"] == 16               # still in the sweep
    # and it still counts toward the task it belongs to
    assert rep["dimensions"]["correctness"]["per_task"][first]["B"] == 2


def test_bound_compliance_decides_nothing_by_itself(tmp_path, clean):
    """Every B arm-run breaks the bound and NOTHING ELSE CHANGES. The decision is unmoved.

    The violation is a second search that returns no hits, so delivered volume is held
    fixed. A violation that also fetched three more bodies would change the decision through
    COST, and reading that as "the violation decided it" is the confusion this isolates --
    the first version of this check made exactly that mistake and had to be rewritten.
    """
    over = {(t, a, "B"): {**R.cheaper_B()[(t, a, "B")], "empty_searches": 1}
            for t in A3_PLAN.tasks for a in (1, 2)}
    rep = R.build(tmp_path, overrides=over)
    assert rep["states"] == clean["states"]        # every dimension identical
    assert rep["bound_compliance"]["violated"] == 8
    assert rep["bound_compliance"]["excluded_from_any_dimension"] == 0
    assert rep["decision"] == "accept_for_further_development", rep["reason"]


# ---------------------------------------------------------------------------------------
# 6. The decision reaches accepted, rejected AND indeterminate through the saved files.
# ---------------------------------------------------------------------------------------

def test_the_pipeline_can_reach_accept(clean):
    assert clean["decision"] == "accept_for_further_development", clean["reason"]
    assert clean["experiment_valid"] is True
    assert clean["criteria_applied"] is True


def test_the_pipeline_can_reach_reject(rejected):
    """B loses a functional pass on one task. Not offset by the other three."""
    first, rep = A3_PLAN.tasks[0], rejected
    assert rep["decision"] == "reject"
    assert "correctness" in rep["failed"]
    assert rep["dimensions"]["correctness"]["per_task"][first]["state"] == "fail"
    for other in A3_PLAN.tasks[1:]:
        assert rep["dimensions"]["correctness"]["per_task"][other]["state"] == "hold"


def test_the_pipeline_can_reach_indeterminate(tmp_path):
    """One arm-run's stale adoption could not be settled from the trace. Complete coverage,
    no established failure, and the evidence does not decide."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"stale_adopted": None, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000}})
    assert rep["decision"] == "indeterminate"
    assert rep["experiment_valid"] is True
    assert rep["indeterminate"] == ["stale_advice"]


def test_all_three_outcomes_are_reachable_from_one_fixture(clean, rejected, partial):
    """Stated as one check so a future change that collapses the table into a constant --
    always accept, or always indeterminate -- fails here rather than passing quietly."""
    assert {clean["decision"], rejected["decision"], partial["decision"]} == {
        "accept_for_further_development", "reject", "indeterminate"}


# ---------------------------------------------------------------------------------------
# A3's BOUNDARY precondition, and how narrowly its green board may be read.
#
# These run against the frozen `boundary-evidence-a3.json` rather than re-probing: the
# probes need `sandbox-exec` and a prepared arm, neither of which exists on a Linux runner.
# What CI can check is that the artifact still carries its own limits.
# ---------------------------------------------------------------------------------------

import json as _json                                                       # noqa: E402
import isolation                                                           # noqa: E402

BOUNDARY = _json.loads((BENCH / "boundary-evidence-a3.json").read_text())


def test_the_shadow_controls_are_required_not_merely_present():
    """`check_boundary` excludes a `None` from `all_hold` -- right for a control that may not
    apply on a host, and exactly wrong for one A3 declares a precondition. Naming them in
    `require` is what makes them binding."""
    for k in ("cache_shadow_unreadable", "cache_shadow_file_unreadable",
              "cache_shadow_listing_unreadable"):
        assert k in isolation.REQUIRE_A3
    assert BOUNDARY["required_unresolved"] == [] and BOUNDARY["required_failed"] == []


def test_the_green_board_carries_what_it_does_not_establish():
    c = BOUNDARY["conclusion"]
    assert any("every equivalent destination" in n for n in c["not_established"])
    assert any("later sweep" in n for n in c["not_established"])
    assert c["established"], "a conclusion with no established half is not a conclusion"


def test_the_unresolved_redirect_is_carried_not_closed():
    """`redirect_reproduced: false` does not invalidate an exact-path deny test -- the test
    depends on a known file at a known path, not on how it got there. What stays open is
    whether the write channel that created the original exposure is still open, and that is
    carried as unresolved rather than being read as a fix."""
    c = BOUNDARY["conclusion"]
    assert c["redirect_reproduced"] is False
    assert c["placement"] == "direct"
    assert c["deny_test_valid_regardless_of_placement"] is True
    assert any("was NOT" in n and "unresolved" in n for n in c["not_established"])


def test_a_reproduced_redirect_would_say_so_instead():
    """The control on the sentence above: it is generated from the evidence, not pasted."""
    c = isolation.boundary_conclusion(
        {**BOUNDARY, "cache_shadow_sentinel": {**BOUNDARY["cache_shadow_sentinel"],
                                               "redirect_reproduced": True,
                                               "placement": "redirect"}})
    assert c["redirect_reproduced"] is True
    assert not any("was NOT" in n for n in c["not_established"])
    # ... and the path-class limit survives either way
    assert any("every equivalent destination" in n for n in c["not_established"])


# ---------------------------------------------------------------------------------------
# A3's PROMPT precondition: two policies, one appended paragraph, nothing else.
# ---------------------------------------------------------------------------------------

import a3_prompts as AP                                                    # noqa: E402


def test_the_A_prompts_are_byte_identical_to_calib_v3():
    """§2. An A arm-run must be given exactly what a v3 / A2-R nexus arm-run was given, or
    the two policies differ in more than the paragraph the design varies."""
    d = AP.assembly_diff()
    assert d["holds"], d["failures"]
    assert all(r["A_matches_calib_v3"] for r in d["per_task"].values())


def test_the_A_digests_match_what_the_A2R_preflight_froze():
    """An independent check on the sentence above: `preflight-a2r.py` froze these four
    digests for calib-v3 before A3 existed, and nothing here was copied from it."""
    assert AP.digests()["A"] == {"k1": "5f4f1e210aa7f7ea", "k2": "0aa687b3be6b6a26",
                                 "k3": "b99a1fd5366f507f", "k4": "97ee8c80330b164a"}


def test_B_is_A_plus_the_bound_and_nothing_else():
    d = AP.assembly_diff()
    assert all(r["B_is_A_plus_bound"] for r in d["per_task"].values())
    assert d["delta_is_constant"], "the bound is not the only thing that differs"
    assert all(r["delta_bytes"] == d["bound_bytes"] for r in d["per_task"].values())


def test_the_two_policies_have_different_prompt_digests():
    """If they did not, A3 would be one policy run sixteen times."""
    dg = AP.digests()
    assert all(dg["A"][t] != dg["B"][t] for t in AP.TASKS)
    assert len(set(dg["A"].values()) | set(dg["B"].values())) == 8


def test_the_assembly_diff_can_fail():
    """The control. A check that holds whatever the registration says is not a check."""
    reg = AP.registration()
    reg["consult"] = reg["consult"] + "\n\nan unregistered extra sentence."
    d = AP.assembly_diff(reg)
    assert not d["holds"]
    assert any("not byte-identical to calib-v3" in f for f in d["failures"])


def test_an_empty_bound_is_refused_as_one_policy():
    reg = AP.registration()
    reg["consult_bound"] = ""
    d = AP.assembly_diff(reg)
    assert not d["holds"]
    assert any("assemble to the same bytes" in f for f in d["failures"])


# ---------------------------------------------------------------------------------------
# THE LAUNCH LEDGER. A started process can consume tokens and leave nothing else.
# ---------------------------------------------------------------------------------------

import a3_ledger as L                                                      # noqa: E402


def test_a_launch_with_no_trace_is_unresolved_not_absent(tmp_path):
    """The reported reproduction. `saved_consumption` keyed on `trace.jsonl` and returned
    `[]` for a launch that had spent tokens -- "no trace" read as "not started"."""
    L.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45, wall_clock_s=600,
                  prompt_digest="5f4f1e210aa7f7ea")
    assert not (P.arm_run_dir(tmp_path, "k1", 1, "A") / "trace.jsonl").exists()
    led = L.read(tmp_path)
    assert led.started == ["k1/A/1"]
    assert led.unresolved == ["k1/A/1"]
    assert led.consumption_certain is False
    assert P.saved_consumption(tmp_path) == ["k1/A/1"]


def test_a_cell_never_launched_is_absent_not_unresolved(tmp_path):
    """The other half. Absent is missing COVERAGE; unresolved is a row that spent an unknown
    amount. Collapsing them in either direction loses something different."""
    led = L.read(tmp_path)
    assert led.started == [] and led.unresolved == []
    assert L.fatal_stop(tmp_path) is None


def test_an_interrupted_launch_stops_the_next_pair(tmp_path):
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={**R.cheaper_B(),
                                       (first, 1, "B"): {"artifacts": False}})
    gates = rep["launch_gate"]
    assert gates[0]["may_start"] is True
    assert gates[1]["may_start"] is False
    assert gates[1]["stopping_reason"] == "unresolved_accounting"
    assert gates[1]["gate"] == "fatal"
    assert rep["further_launches_permitted"] is False


def test_an_interrupted_A_does_not_authorise_its_own_pairs_B(tmp_path):
    """The gate separation. A pair is ADMITTED on budget, once, before it starts. Whether
    its second member may launch is a FATAL-STOP question asked again, after the first --
    reserving a pair does not authorise launching its B once A lost its accounting."""
    L.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45, wall_clock_s=600,
                  prompt_digest="a")                      # launched, never accounted
    stop = L.fatal_stop(tmp_path)
    assert stop and stop["stopping_reason"] == "unresolved_accounting"
    # the budget alone would happily admit: nothing is known to have been spent
    led = L.read(tmp_path)
    assert led.tokens_known + L.PAIR_RESERVATION <= L.SOFT_LAUNCH_THRESHOLD
    # ... and the pair gate still refuses, on the fatal gate rather than the budget one
    g = L.may_admit_pair(tmp_path, "k1", 1)
    assert g["may_start"] is False and g["gate"] == "fatal"


def test_a_recorded_refusal_is_a_fatal_stop(tmp_path):
    """A boundary or environment-gate refusal is not an arm-run that spent nothing."""
    L.mark_refusal(tmp_path, "k1", 1, "A", "boundary not demonstrated",
                   {"required_unresolved": ["cache_shadow_unreadable"]})
    stop = L.fatal_stop(tmp_path)
    assert stop and stop["stopping_reason"] == "refused"
    assert L.may_admit_pair(tmp_path, "k2", 1)["may_start"] is False


def test_an_allowance_cannot_clear_an_unresolved_row(tmp_path):
    """§7: never covered by an allowance written to unblock it. A2-R's ledger honours
    `resolution.json`; A3's refuses it and keeps the stop."""
    L.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45, wall_clock_s=600,
                  prompt_digest="a")
    (P.arm_run_dir(tmp_path, "k1", 1, "A") / "resolution.json").write_text(
        '{"tokens": 1500000, "basis": "assumed from the mean"}')
    led = L.read(tmp_path)
    assert led.unresolved == ["k1/A/1"]
    assert led.rejected_allowances and "REFUSED" in led.rejected_allowances[0]
    assert L.fatal_stop(tmp_path)["stopping_reason"] == "unresolved_accounting"


def test_the_budget_gate_is_monetary_independent_and_reserves_a_pair(tmp_path):
    """Tokens, not dollars: the CLI's cost field has no provenance on this model. The
    reservation is set aside BEFORE a pair is admitted."""
    import json as _j
    for i, (t, a, pol) in enumerate([("k1", 1, "A"), ("k1", 1, "B")]):
        d = P.arm_run_dir(tmp_path, t, a, pol)
        L.mark_launch(tmp_path, t, a, pol, identity={}, max_turns=45, wall_clock_s=600,
                      prompt_digest="a")
        (d / "record.json").write_text(_j.dumps({"usage": R.usage_block(13_000_000)}))
    led = L.read(tmp_path)
    assert led.tokens_known == 2 * 13_000_000 and led.unresolved == []
    g = L.may_admit_pair(tmp_path, "k1", 2)
    assert g["may_start"] is False and g["gate"] == "budget"
    assert g["stopping_reason"] == "soft_threshold"
    assert "reserved" in g["reason"]


def test_the_ledger_and_the_normaliser_read_usage_by_one_rule(tmp_path):
    """A usage block of four nulls -- what the runner writes when the envelope carried none
    -- must not read as a measurement in one place and unknown in the other."""
    import json as _j
    d = P.arm_run_dir(tmp_path, "k1", 1, "A")
    L.mark_launch(tmp_path, "k1", 1, "A", identity={}, max_turns=45, wall_clock_s=600,
                  prompt_digest="a")
    (d / "record.json").write_text(_j.dumps({"usage": {k: None for k in
                                                       ("input_tokens", "output_tokens",
                                                        "cache_read_input_tokens",
                                                        "cache_creation_input_tokens")}}))
    assert L.read(tmp_path).unresolved == ["k1/A/1"]


def test_a_launched_cell_with_no_artifacts_is_still_a_row(tmp_path):
    """It must reach the decision as an unresolved row, not vanish from coverage."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={**R.cheaper_B(),
                                       (first, 1, "B"): {"artifacts": False}})
    ids = {n["dir"].split("/")[-4:][0] for n in rep["normalisation"]}
    assert len(rep["normalisation"]) == 2          # the pair that ran, both members
    assert any(n.get("consumption") == "UNRESOLVED" for n in rep["normalisation"])


# ---------------------------------------------------------------------------------------
# THE TWO MEASUREMENT MAPPINGS.
# ---------------------------------------------------------------------------------------

def test_information_arriving_only_after_the_edit_is_not_delivered(tmp_path):
    """The registered measure is delivery BEFORE the first source edit. Concatenating every
    consultation result and asking whether the body appears anywhere credits a memory
    retrieved after the edit it was supposed to inform."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"deliver_late": True, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000},
        (first, 2, "B"): {"deliver_late": True, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000}})
    fact = rep["dimensions"]["information"]["per_task"][first]["facts"][f"{first}-mechanism"]
    assert fact["A"] is True and fact["B"] is False
    assert rep["dimensions"]["information"]["per_task"][first]["state"] == "fail"
    assert rep["decision"] == "reject"


def test_delivery_with_no_locatable_edit_is_unresolved_not_delivered(tmp_path):
    """Delivered, but against what? If the edit cannot be located the ORDER is unknown, and
    unknown is not credit."""
    calls = P.parse.__self__ if False else None                    # (parse is a function)
    trace = R._trace("k1", "A", deliver=["k1-m-primary"], searches=1, fetches=1, edit=False)
    f = tmp_path / "t.jsonl"; f.write_text(trace)
    parsed = P.parse(f)
    got = P.facts_delivered(parsed["calls"], {"k1-mechanism": ["k1-m-primary"]},
                            R.BODIES, "k1")
    assert got["k1-m-primary"] is None


def test_a_body_never_delivered_is_false_even_with_no_edit(tmp_path):
    """Ordering only arises for something that arrived. Nothing arrived here."""
    trace = R._trace("k1", "A", deliver=[], searches=1, fetches=0, edit=False)
    f = tmp_path / "t.jsonl"; f.write_text(trace)
    got = P.facts_delivered(P.parse(f)["calls"], {"k1-mechanism": ["k1-m-primary"]},
                            R.BODIES, "k1")
    assert got["k1-m-primary"] is False


def test_an_offline_pass_the_agent_never_ran_is_not_required_work(tmp_path):
    """The second control the review asked for. `E1_regression_discriminates` applies the
    patch to three trees of its own and runs pytest AFTERWARDS -- that is evidence about the
    submitted test, not an execution event. §5.3 asks for both."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"ran_test": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000},
        (first, 2, "B"): {"ran_test": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000}})
    b = [n for n in rep["normalisation"]
         if f"run-{first}/attempt1/policy-B" in n["dir"]][0]
    assert b["E1"] == "pass"                       # the offline probe is satisfied
    assert b["required_work"]["observed_execution"] is False
    assert b["required_work"]["criterion"] == "fail"
    assert rep["dimensions"]["required_work"]["per_task"][first]["state"] == "fail"
    assert rep["decision"] == "reject"


def test_the_three_observations_are_reported_separately(clean):
    n = clean["normalisation"][0]["required_work"]
    assert n["offline_discrimination"] == "pass"
    assert n["observed_execution"] is True
    assert n["reviewed_relevance"] == "unreviewed"
    assert n["relevance_is_in_the_criterion"] is False


def test_relevance_is_never_machine_settled(clean):
    """A launch may not substitute a machine verdict for a review that has not happened."""
    assert all(n["required_work"]["reviewed_relevance"] == "unreviewed"
               for n in clean["normalisation"])


def test_the_production_fact_mapping_is_frozen_and_loaded():
    """`facts-a3.json` is declared before launch and LOADED. Computing the equivalence
    classes at scoring time would let them move after the numbers exist."""
    detail = P.registered_fact_detail()
    facts = P.registered_facts()
    assert set(facts) == set(A3_PLAN.tasks)
    labels = _json.loads((BENCH / "results-dev-m1-r2.json").read_text())["labels"]
    for task, fs in detail["facts"].items():
        for fid, f in fs.items():
            assert f["members"], fid
            for m in f["members"]:
                assert m in labels[task]["useful"], (fid, m)
            assert f["claim"] and f["basis"]
    # the equivalence machinery is exercised by at least one real class, and the singletons
    # are declared as singletons rather than padded
    red = [fid for fs in detail["facts"].values() for fid, f in fs.items() if f["redundant"]]
    assert len(red) >= 1, "no genuinely redundant class: the machinery would be vacuous"
    assert any(not f["redundant"] for fs in detail["facts"].values() for f in fs.values())


def test_the_k4_fact_carries_the_m02_flag():
    detail = P.registered_fact_detail()
    k4 = detail["facts"]["k4"]["k4-help-option-is-constructed-per-call-and-compared-by-object"]
    assert k4.get("flagged") == "m02"
    assert "unaided" in k4["basis"]


# ---------------------------------------------------------------------------------------
# GAP 3 -- test execution was overcredited. `echo pytest`, collection-only runs and fully
# deselected runs all counted, and all three exit 0.
# ---------------------------------------------------------------------------------------

ADDED = ["tests/test_pkg.py::test_add_one_adds_one"]


def _bash(command, result="", is_error=False):
    return {"name": "Bash", "input": {"command": command}, "result": result,
            "is_error": is_error, "resolved_at": 1, "issued_at": 0, "index": 0}


@pytest.mark.parametrize("command,result,expected", [
    # -- mentioned, never invoked ---------------------------------------------------
    ("echo pytest", "pytest", False),
    ("grep -rn pytest .", "tests/test_pkg.py: import pytest", False),
    ('echo "next: run pytest" && ls', "next: run pytest", False),
    # -- invoked, but nothing executed ----------------------------------------------
    ("pytest tests/test_pkg.py --collect-only", "collected 27 items", False),
    ("pytest tests/test_pkg.py --co", "collected 27 items", False),
    ("pytest tests/test_pkg.py -k nomatch", "2 deselected in 0.01s", False),
    ("pytest tests/test_pkg.py", "no tests ran in 0.01s", False),
    ("pytest tests/test_pkg.py", "3 skipped in 0.01s", False),
    # -- invoked over something else -------------------------------------------------
    ("pytest tests/other_test.py -q", "5 passed in 0.02s", False),
    # -- genuinely executed ----------------------------------------------------------
    ("pytest tests/test_pkg.py -q", "3 failed, 24 passed in 0.06s", True),
    ("PYTHONPATH=src python -m pytest tests -q", "1 passed in 0.03s", True),
    ("cd repo && pytest tests -q", "2 passed in 0.01s", True),
    ("pytest tests/test_pkg.py -q", "1 passed, 2 deselected in 0.02s", True),
    # -- invoked, outcome unreadable -------------------------------------------------
    ("pytest tests/test_pkg.py -q", "", None),
    ("pytest tests/test_pkg.py -q", "something unrecognisable", None),
    # -- the interpreter form the protocol PRESCRIBES --------------------------------
    # `$A2_PYTHON` is uppercase and usually quoted, and `pytest` sits after `-m` rather
    # than at a command position, so the prescribed command earned no credit at all.
    ('PYTHONPATH=src "$A2_PYTHON" -m pytest tests/test_pkg.py -q', "1 passed in 0.03s", True),
    ("PYTHONPATH=src $A2_PYTHON -m pytest tests/test_pkg.py -q", "1 passed in 0.03s", True),
    ("PYTHONPATH=src ${A2_PYTHON} -m pytest tests/test_pkg.py -q", "1 passed in 0.03s", True),
    ('"$A2_PYTHON" -m pytest tests/test_pkg.py::test_add_one_adds_one', "1 passed", True),
    # -- invoked over the added test's FILE, but never over the added test ------------
    ("pytest tests/test_pkg.py::test_something_else -q", "1 passed in 0.02s", False),
    ("pytest tests/test_pkg.py --deselect tests/test_pkg.py::test_add_one_adds_one",
     "4 passed, 1 deselected in 0.03s", False),
    ("pytest tests/test_pkg.py --deselect=tests/test_pkg.py::test_add_one_adds_one",
     "4 passed, 1 deselected in 0.03s", False),
    # -- collection failed, so nothing in that module reached a verdict ---------------
    ("pytest tests/test_pkg.py -q",
     "ERROR collecting tests/test_pkg.py\nImportError: cannot import x\n1 error in 0.03s",
     False),
    # -- a collection error beside real verdicts does not settle THIS test ------------
    ("pytest tests -q",
     "ERROR collecting tests/test_pkg.py\n5 passed, 1 error in 0.10s", None),
])
def test_execution_credit(command, result, expected):
    assert P.observed_test_execution([_bash(command, result)], ADDED) is expected


def test_an_errored_pytest_call_is_unresolved_not_absent():
    calls = [_bash("pytest tests/test_pkg.py -q", "boom", is_error=True)]
    assert P.observed_test_execution(calls, ADDED) is None


def test_no_added_test_means_no_execution_to_observe():
    assert P.observed_test_execution([_bash("pytest tests -q", "1 passed")], []) is False


def test_the_summary_reader_uses_pytest_output_not_the_exit_status():
    """All three overcredited shapes exit 0, so the exit status cannot separate them."""
    assert P._tests_actually_ran("1 passed in 0.1s") is True
    assert P._tests_actually_ran("2 deselected in 0.1s") is False
    assert P._tests_actually_ran("no tests ran in 0.1s") is False
    assert P._tests_actually_ran("collected 0 items") is False
    assert P._tests_actually_ran("") is None


def test_the_command_is_read_from_the_command_field_not_the_json_envelope():
    """Matching `json.dumps(input)` put a quote before a command STARTING with pytest, so a
    command-position anchor could never fire on the ordinary case."""
    assert P._command_text(_bash("pytest -q")) == "pytest -q"
    assert P._command_text({"input": {"file_path": "a.py"}}).startswith("{")


def test_overcredit_changes_the_decision(tmp_path):
    """End to end: a B that only ever ECHOED pytest fails required work, where before it
    would have passed."""
    first = A3_PLAN.tasks[0]
    rep = R.build(tmp_path, overrides={
        **R.cheaper_B(),
        (first, 1, "B"): {"ran_test": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000},
        (first, 2, "B"): {"ran_test": False, "fetches": 1,
                          "deliver": [f"{first}-m-primary"], "total_tokens": 80_000}})
    assert rep["dimensions"]["required_work"]["per_task"][first]["state"] == "fail"


def test_an_unreviewed_production_sweep_cannot_be_accepted(tmp_path):
    """The gate, through the files: omit the review artifact and acceptance is withheld
    while nothing fails."""
    rep = R.build(tmp_path, overrides={
        (t, a, "B"): {**R.cheaper_B()[(t, a, "B")], "reviewed": None}
        for t in A3_PLAN.tasks for a in (1, 2)})
    assert rep["decision"] == "indeterminate"
    assert rep["relevance"]["blocks_acceptance"] is True
    assert len(rep["relevance"]["unreviewed_arm_runs"]) == 8
    assert rep["failed"] == []


def test_the_review_artifact_is_read_from_disk(clean):
    assert clean["relevance"]["reviewed"] == 16
    assert clean["relevance"]["blocks_acceptance"] is False
    assert all(n.get("relevance_reviewed") is True for n in clean["normalisation"])
