"""The model-free integration rehearsal, as checks.

    frozen A/B schedule -> stubbed execution -> saved records -> REAL compliance scoring
                        -> normalisation -> decision report

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
