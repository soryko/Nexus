"""The A3 decision table, exercised against synthetic outcomes. Model-free.

Every check below is a scenario the FIRST draft would have decided wrongly, plus the controls
that stop the repair from over-correcting. Synthetic throughout: no arm-run has been run, no
budget is approved, and nothing here is evidence about the policy.

    <venv>/bin/python -m pytest benchmarks/agent/test_a3_decision.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

from a3_decision import (A3_PLAN, ArmRun, Plan, Thresholds, bound_compliance,  # noqa: E402
                         consumption, correctness, cost, decide, information,
                         required_work, run_id, stale_advice, validity)

TASKS = ("k1", "k2", "k3", "k4")
FACTS = {t: {f"{t}-mechanism": [f"{t}-m-primary", f"{t}-m-equivalent"]} for t in TASKS}


def run(task, policy, attempt=1, *, ok=True, work="pass", fact=True,
        exposed=False, adopted=False, consult=1000, total=100_000, **kw):
    """A resolved arm-run with everything measured, so each test varies one thing."""
    # EVERY member of the fact's equivalence class is recorded. A missing member is not
    # "not delivered", it is "not measured", and the table treats it as unresolved -- so a
    # helper that populated only the primary would make every scenario indeterminate for a
    # reason that has nothing to do with the scenario.
    delivered = {f"{task}-m-primary": fact, f"{task}-m-equivalent": False if fact else fact}
    return ArmRun(task=task, policy=policy, attempt=attempt, functional_pass=ok,
                  required_test_work=work, facts_delivered=delivered,
                  stale_exposed=exposed, stale_adopted=adopted,
                  consultation_calls={"search": 1, "get": 2},
                  delivered_bytes=consult * 4, delivered_text_tokens=consult,
                  token_counting_method="harness-tiktoken-o200k", provider_total_tokens=total,
                  accounting_resolved=True, bound_respected=(True if policy == "B" else None),
                  # `run()` is "everything measured", and relevance is part of everything:
                  # left unreviewed it blocks acceptance, which would make every scenario
                  # below indeterminate for a reason unrelated to the scenario. The gate
                  # itself is exercised in its own section.
                  relevance_reviewed=kw.pop("relevance_reviewed", True),
                  **kw)


def clean_sweep(**over):
    """16 arm-runs: 4 tasks x 2 policies x 2 attempts, B cheaper, nothing lost."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, consult=1000, total=100_000))
            runs.append(run(t, "B", a, consult=over.get("b_consult", 500),
                            total=over.get("b_total", 85_000)))
    return runs


def test_the_clean_case_is_accepted():
    r = decide(clean_sweep(), FACTS)
    assert r["decision"] == "accept_for_further_development", r["reason"]
    assert r["states"] == {k: "hold" for k in r["states"]}


# --------------------------------------------------------------------------------------
# Defect 1: required test work was measured and then absent from acceptance.
# --------------------------------------------------------------------------------------

def test_losing_required_test_work_is_not_acceptable():
    """The draft measured this and then never consulted it, so a policy could be accepted
    while losing the work the tail explicitly asks for."""
    runs = [r for r in clean_sweep() if not (r.task == "k2" and r.policy == "B")]
    runs += [run("k2", "B", 1, work="fail", consult=500, total=85_000),
             run("k2", "B", 2, work="fail", consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["states"]["required_work"] == "fail"
    assert r["decision"] == "reject"
    assert "required_work" in r["failed"]


def test_unresolved_test_work_is_indeterminate_and_never_compliance():
    runs = [r for r in clean_sweep() if not (r.task == "k3" and r.policy == "B")]
    runs += [run("k3", "B", 1, work="unresolved", consult=500, total=85_000),
             run("k3", "B", 2, work="pass", consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["states"]["required_work"] == "indeterminate"
    assert r["decision"] == "indeterminate"


# --------------------------------------------------------------------------------------
# Defect 2: pooled correctness let a loss on one task be offset elsewhere.
# --------------------------------------------------------------------------------------

def test_a_loss_on_one_task_is_not_offset_by_a_gain_on_another():
    """The scenario the pooled rule gets wrong: B loses both of k1's passes and gains both
    of k4's. Pooled, 8 == 8 and C2 held. Per task, k1 lost and the policy is rejected."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, ok=(t != "k4"), consult=1000, total=100_000))
            runs.append(run(t, "B", a, ok=(t != "k1"), consult=500, total=85_000))
    pooled = correctness(runs)["pooled_and_not_deciding"]
    assert pooled["A"] == pooled["B"] == 6          # identical pooled totals
    r = decide(runs, FACTS)
    assert r["dimensions"]["correctness"]["per_task"]["k1"]["state"] == "fail"
    assert r["dimensions"]["correctness"]["per_task"]["k4"]["state"] == "hold"
    assert r["decision"] == "reject"


def test_an_unresolved_arm_run_makes_correctness_indeterminate_not_passed():
    runs = [r for r in clean_sweep() if not (r.task == "k1" and r.policy == "B")]
    runs += [run("k1", "B", 1, ok=None, consult=500, total=85_000),
             run("k1", "B", 2, ok=True, consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["dimensions"]["correctness"]["per_task"]["k1"]["state"] == "indeterminate"
    assert r["decision"] == "indeterminate"


def test_gaining_correctness_is_not_a_failure():
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, ok=False, consult=1000, total=100_000))
            runs.append(run(t, "B", a, ok=True, consult=500, total=85_000))
    assert decide(runs, FACTS)["states"]["correctness"] == "hold"


# --------------------------------------------------------------------------------------
# Defect 3: every useful memory was a required fact, and its IDENTITY had to survive.
# --------------------------------------------------------------------------------------

def test_an_equivalent_memory_satisfies_the_fact():
    """B reaches the redundant memory carrying the same claim rather than the one A reached.
    Nothing was lost. C3 as drafted rejected this, because it compared memory ids."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            ra = run(t, "A", a, consult=1000, total=100_000)
            rb = run(t, "B", a, consult=500, total=85_000)
            if t == "k2":
                rb = ArmRun(**{**rb.__dict__,
                               "facts_delivered": {"k2-m-primary": False,
                                                   "k2-m-equivalent": True}})   # redundant
            runs += [ra, rb]
    r = decide(runs, FACTS)
    assert r["dimensions"]["information"]["per_task"]["k2"]["state"] == "hold"
    assert r["decision"] == "accept_for_further_development"


def test_a_fact_delivered_under_A_and_lost_under_B_fails():
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, consult=1000, total=100_000))
            runs.append(run(t, "B", a, fact=(t != "k2"), consult=500, total=85_000))
    r = decide(runs, FACTS)
    assert r["dimensions"]["information"]["per_task"]["k2"]["state"] == "fail"
    assert r["decision"] == "reject"


def test_a_fact_neither_arm_delivered_is_not_a_loss():
    """B cannot be blamed for not reaching what A never reached either."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, fact=(t != "k3"), consult=1000, total=100_000))
            runs.append(run(t, "B", a, fact=(t != "k3"), consult=500, total=85_000))
    r = decide(runs, FACTS)
    assert r["dimensions"]["information"]["per_task"]["k3"]["state"] == "hold"


def test_necessity_is_never_claimed():
    r = decide(clean_sweep(), FACTS)
    assert any("NECESSARY" in n for n in r["not_established"])


# --------------------------------------------------------------------------------------
# Defect 4: delivering refuted information was counted as harm.
# --------------------------------------------------------------------------------------

def test_reading_and_rejecting_stale_advice_is_not_harm():
    """B is exposed to the refuted memory in every run and adopts it in none. The consult
    block asks for exactly this -- verify prior notes against current code -- so counting
    delivery as harm would penalise compliance."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, exposed=False, adopted=False,
                            consult=1000, total=100_000))
            runs.append(run(t, "B", a, exposed=True, adopted=False,
                            consult=500, total=85_000))
    r = decide(runs, FACTS)
    assert r["states"]["stale_advice"] == "hold"
    assert r["decision"] == "accept_for_further_development"
    sa = r["dimensions"]["stale_advice"]
    assert sa["exposure_reported_not_judged"]["B"]["true"] == 8
    assert sa["adoption"]["B"]["true"] == 0


def test_adopting_stale_advice_more_often_is_harm():
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, exposed=True, adopted=False,
                            consult=1000, total=100_000))
            runs.append(run(t, "B", a, exposed=True, adopted=(t == "k1"),
                            consult=500, total=85_000))
    r = decide(runs, FACTS)
    assert r["states"]["stale_advice"] == "fail"
    assert r["decision"] == "reject"


def test_unresolved_adoption_is_indeterminate():
    runs = [r for r in clean_sweep() if not (r.task == "k4" and r.policy == "B")]
    runs += [run("k4", "B", 1, adopted=None, consult=500, total=85_000),
             run("k4", "B", 2, adopted=False, consult=500, total=85_000)]
    assert decide(runs, FACTS)["states"]["stale_advice"] == "indeterminate"


# --------------------------------------------------------------------------------------
# Defect 5: failing the cost criterion meant reject in one paragraph and indeterminate in
# another. Defect 6: an outcome-dependent spread exception.
# --------------------------------------------------------------------------------------

def test_a_saving_below_threshold_rejects_once_and_deterministically():
    r = decide(clean_sweep(b_consult=900, b_total=99_000), FACTS)
    assert r["states"]["cost"] == "fail"
    assert r["decision"] == "reject"
    assert "cost" in r["failed"] and "cost" not in r["indeterminate"]


def test_consultation_falls_but_total_does_not():
    """The half-and-half case the draft called indeterminate in one place and rejected in
    another. It is one verdict here: the criterion is not met."""
    r = decide(clean_sweep(b_consult=400, b_total=99_500), FACTS)
    assert r["states"]["cost"] == "fail"
    assert r["decision"] == "reject"


def test_a_bounded_policy_that_costs_more_in_total_still_fails_cost():
    """Registered in advance as possible: stopping consultation early can send the agent
    into repository investigation it would otherwise have skipped."""
    r = decide(clean_sweep(b_consult=100, b_total=130_000), FACTS)
    assert r["states"]["cost"] == "fail"
    assert r["dimensions"]["cost"]["total_drop"] < 0


def test_no_spread_based_exception_exists():
    """The removed clause: C1 was void for a task whose own two A attempts differed by more
    than 40%, which made the criterion depend on the numbers it was judging. Two A attempts
    that differ wildly must now still be measured."""
    runs = []
    for t in TASKS:
        runs += [run(t, "A", 1, consult=200, total=100_000),
                 run(t, "A", 2, consult=1800, total=100_000),
                 run(t, "B", 1, consult=500, total=85_000),
                 run(t, "B", 2, consult=500, total=85_000)]
    c = cost(runs)
    assert c["state"] in ("hold", "fail")
    assert "void" not in str(c)


def test_the_threshold_is_declared_a_preference_not_a_finding():
    c = cost(clean_sweep())
    assert "engineering preference" in c["threshold_basis"]
    assert "stochastic variation" in c["threshold_basis"]


# --------------------------------------------------------------------------------------
# Cost units and accounting.
# --------------------------------------------------------------------------------------

def test_failures_are_counted_in_cost():
    """'Including failures' has to mean the tokens too. A failed arm-run's spend is spend."""
    runs = clean_sweep()
    runs = [r for r in runs if not (r.task == "k1" and r.policy == "B" and r.attempt == 1)]
    runs.append(run("k1", "B", 1, ok=False, consult=5000, total=400_000))
    c = cost(runs)
    assert c["delivered_text_tokens"]["B"] == 500 * 7 + 5000
    assert c["provider_total_tokens"]["B"] == 85_000 * 7 + 400_000


def test_unresolved_accounting_is_indeterminate_not_excluded():
    """Dropping the run would flatter whichever arm it fell in."""
    runs = [r for r in clean_sweep() if not (r.task == "k1" and r.policy == "B"
                                             and r.attempt == 1)]
    runs.append(ArmRun(task="k1", policy="B", attempt=1, functional_pass=True,
                       required_test_work="pass", facts_delivered={"k1-m-primary": True},
                       stale_exposed=False, stale_adopted=False,
                       accounting_resolved=False))
    c = cost(runs)
    assert c["state"] == "indeterminate"
    assert c["unresolved_arm_runs"] == ["k1/B/1"]
    assert decide(runs, FACTS)["decision"] == "indeterminate"


def test_a_run_without_a_named_counting_method_cannot_be_counted():
    runs = [r for r in clean_sweep() if not (r.task == "k2" and r.policy == "A"
                                             and r.attempt == 1)]
    runs.append(ArmRun(task="k2", policy="A", attempt=1, functional_pass=True,
                       required_test_work="pass", facts_delivered={"k2-m-primary": True},
                       stale_exposed=False, stale_adopted=False,
                       delivered_text_tokens=1000, provider_total_tokens=100_000,
                       token_counting_method=None))
    assert cost(runs)["state"] == "indeterminate"


def test_mixed_counting_methods_are_indeterminate():
    """Two methods are two units. Summing them produces a number with no denominator."""
    runs = clean_sweep()
    runs[0] = ArmRun(**{**runs[0].__dict__, "token_counting_method": "provider-usage-field"})
    c = cost(runs)
    assert c["state"] == "indeterminate"
    assert len(c["counting_methods"]) == 2


def test_the_three_cost_units_are_reported_separately():
    c = cost(clean_sweep())
    assert set(c["calls"]["A"]) == {"status", "search", "get", "history"}
    assert c["delivered_bytes"]["A"] > 0
    assert c["delivered_text_tokens"]["A"] > 0
    assert c["provider_total_tokens"]["A"] > 0


def test_status_is_counted_as_a_consultation_call():
    r = ArmRun(task="k1", policy="B", attempt=1,
               consultation_calls={"status": 1, "search": 1, "get": 3, "history": 2})
    assert r.consult_total() == 7


# --------------------------------------------------------------------------------------
# Prompt-bound violations.
# --------------------------------------------------------------------------------------

def test_a_run_that_broke_the_bound_is_counted_and_kept():
    """Excluding it would select the sample on the outcome being measured -- and an
    instruction the model does not follow is a result about the instruction."""
    runs = [r for r in clean_sweep() if not (r.task == "k2" and r.policy == "B")]
    violated = ArmRun(**{**run("k2", "B", 1, consult=1000, total=100_000).__dict__,
                         "bound_respected": False})
    runs += [violated, run("k2", "B", 2, consult=500, total=85_000)]
    bc = bound_compliance(runs)
    assert bc["violated"] == 1
    assert bc["excluded_from_any_dimension"] == 0
    # and its tokens are still in the cost totals
    assert cost(runs)["delivered_text_tokens"]["B"] == 500 * 6 + 1000 + 500


def test_bound_compliance_decides_nothing_on_its_own():
    runs = [ArmRun(**{**r.__dict__, "bound_respected": False}) if r.policy == "B" else r
            for r in clean_sweep()]
    assert decide(runs, FACTS)["decision"] == "accept_for_further_development"


# --------------------------------------------------------------------------------------
# The decision rule itself.
# --------------------------------------------------------------------------------------

def test_an_established_failure_outranks_an_indeterminate():
    runs = [r for r in clean_sweep() if not (r.task == "k1" and r.policy == "B")]
    runs += [run("k1", "B", 1, ok=False, consult=500, total=85_000),
             run("k1", "B", 2, ok=False, work="unresolved", consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["decision"] == "reject"
    assert r["failed"] and r["indeterminate"]


def test_indeterminate_is_a_real_outcome_and_is_named():
    """A's unresolved run spans the comparison here: if it passed, A had 2 and B has 1."""
    runs = [r for r in clean_sweep() if r.task != "k4"]
    runs += [run("k4", "A", 1, ok=True, consult=1000, total=100_000),
             run("k4", "A", 2, ok=None, consult=1000, total=100_000),
             run("k4", "B", 1, ok=True, consult=500, total=85_000),
             run("k4", "B", 2, ok=False, consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["dimensions"]["correctness"]["per_task"]["k4"]["state"] == "indeterminate"
    assert r["decision"] == "indeterminate"
    assert "correctness" in r["indeterminate"]


def test_an_unresolved_run_that_cannot_change_the_verdict_does_not_force_indeterminacy():
    """The other direction, so the repair does not over-correct into always-indeterminate.
    A has one pass and one unresolved run; B has two passes. Even if A's unresolved run had
    passed, A reaches 2 and B is not behind -- so the comparison IS established."""
    runs = [r for r in clean_sweep() if r.task != "k4"]
    runs += [run("k4", "A", 1, ok=True, consult=1000, total=100_000),
             run("k4", "A", 2, ok=None, consult=1000, total=100_000),
             run("k4", "B", 1, ok=True, consult=500, total=85_000),
             run("k4", "B", 2, ok=True, consult=500, total=85_000)]
    r = decide(runs, FACTS)
    assert r["dimensions"]["correctness"]["per_task"]["k4"]["state"] == "hold"
    assert r["decision"] == "accept_for_further_development"


def test_a_missing_cell_is_indeterminate_not_a_pass():
    runs = [r for r in clean_sweep() if r.task != "k3" or r.policy != "B"]
    r = decide(runs, FACTS)
    assert r["dimensions"]["correctness"]["per_task"]["k3"]["state"] == "indeterminate"
    assert r["decision"] == "indeterminate"


def test_cost_is_a_criterion_but_not_a_guardrail():
    r = decide(clean_sweep(), FACTS)
    assert "cost" not in r["guardrails"]
    assert set(r["guardrails"]) == {"correctness", "required_work", "information",
                                    "stale_advice"}


def test_the_render_names_the_decision_and_what_is_not_established():
    from a3_decision import render
    text = render(decide(clean_sweep(), FACTS))
    assert "ACCEPT_FOR_FURTHER_DEVELOPMENT" in text
    assert "NOT ESTABLISHED" in text
    assert "k1-k4" in text


def test_an_unmeasured_class_member_is_unresolved_not_undelivered():
    """The fail-closed direction, asserted directly: if a predeclared member was never
    measured, the fact is unresolved. Silently reading a missing key as `False` would invent
    a loss; reading it as `True` would invent a preservation."""
    runs = []
    for t in TASKS:
        for a in (1, 2):
            runs.append(run(t, "A", a, consult=1000, total=100_000))
            rb = run(t, "B", a, consult=500, total=85_000)
            if t == "k4":
                rb = ArmRun(**{**rb.__dict__, "facts_delivered": {"k4-m-primary": False}})
            runs.append(rb)
    r = decide(runs, FACTS)
    assert r["dimensions"]["information"]["per_task"]["k4"]["state"] == "indeterminate"
    assert r["decision"] == "indeterminate"


# ---------------------------------------------------------------------------------------
# EXPERIMENT VALIDITY -- established before any acceptance criterion is applied.
#
# None of the 33 checks above could fail if the validity gate did nothing: every one of them
# either builds a complete 16-arm-run sweep or asserts on a single dimension directly. A gate
# that no test can make fire is the same defect as the leakage scan that reported "none"
# without looking, so each check below fires it in one specific way.
# ---------------------------------------------------------------------------------------

def test_the_result_identity_is_task_attempt_policy_not_task_arm():
    """Both A3 policies run the nexus arm. `(task, arm)` collides them at every cell."""
    a = run("k1", "A", 1)
    b = run("k1", "B", 1)
    assert run_id(a) != run_id(b)
    assert run_id(a) == ("k1", 1, "A")
    # the identity the historical harness had: both policies are the same arm
    assert ("k1", "nexus") == ("k1", "nexus")          # the collision, stated
    assert len({run_id(r) for r in clean_sweep()}) == 16


def test_all_sixteen_planned_arm_runs_retain_distinct_identities():
    v = validity(clean_sweep())
    assert v["state"] == "hold"
    assert v["distinct_identities"] == v["planned_arm_runs"] == 16
    assert v["missing"] == [] and v["duplicate_identities"] == [] and v["unplanned"] == []


def test_partial_coverage_cannot_be_accepted_as_a_complete_comparison():
    """Everything that ran is clean; half the design did not run. That is not an accept."""
    half = [r for r in clean_sweep() if r.task in ("k1", "k2")]
    d = decide(half, FACTS)
    assert d["decision"] == "indeterminate"
    assert d["experiment_valid"] is False
    assert d["validity"]["partial"] is True
    assert len(d["validity"]["missing"]) == 8
    assert "did not run in full" in d["reason"]


def test_a_partial_sweep_still_rejects_an_established_failure():
    """Partial coverage blocks ACCEPTANCE, not rejection: B losing a task that ran is real
    evidence, and stopping the sweep early does not unmake it."""
    half = [r for r in clean_sweep() if r.task in ("k1", "k2")]
    half = [run(r.task, r.policy, r.attempt, ok=(r.policy == "A"))
            if r.task == "k1" else r for r in half]
    d = decide(half, FACTS)
    assert d["decision"] == "reject"
    assert "correctness" in d["failed"]


def test_a_duplicate_identity_is_invalid_and_no_criterion_is_applied():
    """A collision in the FILES is not a verdict about the POLICY. A duplicated A row
    inflates A's passes, so "B has fewer" would be an artifact of the bookkeeping -- which is
    exactly what an earlier version of `decide` rejected the policy for."""
    runs = clean_sweep()
    d = decide(runs + [runs[0]], FACTS)
    assert d["decision"] == "invalid"
    assert d["criteria_applied"] is False
    assert d["validity"]["corrupt"] is True
    assert d["validity"]["duplicate_identities"] == ["k1/A/1"]
    assert d["failed"] == [] and d["indeterminate"] == []


def test_an_unplanned_arm_run_is_invalid():
    d = decide(clean_sweep() + [run("k9", "B", 1)], FACTS)
    assert d["decision"] == "invalid"
    assert d["validity"]["unplanned"] == ["k9/B/1"]


def test_a_third_attempt_is_unplanned_against_the_registered_design():
    """The plan is two attempts. A third is not extra evidence, it is a different design."""
    d = decide(clean_sweep() + [run("k1", "A", 3)], FACTS)
    assert d["decision"] == "invalid"
    assert d["validity"]["unplanned"] == ["k1/A/3"]
    # ... and it IS valid under a plan that registered three
    v = validity(clean_sweep() + [run("k1", "A", 3)], Plan(attempts=3))
    assert v["unplanned"] == [] and v["partial"] is True      # the other 8 are now missing


# ---------------------------------------------------------------------------------------
# CONSUMPTION vs OUTCOME -- missing evidence of two kinds, with opposite consequences.
# ---------------------------------------------------------------------------------------

def test_unresolved_consumption_stops_further_launches():
    """§7: the sweep stops and the row stays unresolved, never zero, never covered by an
    allowance written to unblock it."""
    import dataclasses
    runs = clean_sweep()
    runs[0] = dataclasses.replace(runs[0], accounting_resolved=False)
    d = decide(runs, FACTS)
    assert d["further_launches_permitted"] is False
    assert d["consumption"]["stopping_reason"] == "unresolved_accounting"
    assert d["consumption"]["unresolved_arm_runs"] == ["k1/A/1"]


def test_an_unnamed_counting_method_is_unresolved_consumption_too():
    import dataclasses
    runs = clean_sweep()
    runs[0] = dataclasses.replace(runs[0], token_counting_method=None)
    assert decide(runs, FACTS)["further_launches_permitted"] is False


def test_unresolved_outcome_does_not_stop_further_launches():
    """The separation the review asked for. A functional result nobody could settle makes a
    DIMENSION indeterminate; it does not mean the sweep has lost track of what it spent."""
    import dataclasses
    runs = clean_sweep()
    runs[0] = dataclasses.replace(runs[0], functional_pass=None, required_test_work="unresolved")
    d = decide(runs, FACTS)
    assert d["further_launches_permitted"] is True
    assert d["consumption"]["unresolved_arm_runs"] == []
    assert d["consumption"]["state"] == "hold"


def test_consumption_is_computed_by_the_same_rule_cost_uses():
    """Two implementations of one contract disagree eventually; there is one here."""
    import dataclasses
    runs = clean_sweep()
    runs[3] = dataclasses.replace(runs[3], provider_total_tokens=None)
    assert run_id(runs[3]) == ("k1", 2, "B")
    assert (consumption(runs)["unresolved_arm_runs"]
            == cost(runs)["unresolved_arm_runs"] == ["k1/B/2"])


# ---------------------------------------------------------------------------------------
# POSSIBLE-COUNT BOUNDS -- not confidence intervals.
# ---------------------------------------------------------------------------------------

def test_the_bounds_are_named_possible_counts_and_disclaim_being_intervals():
    r = decide(clean_sweep(), FACTS)
    row = r["dimensions"]["correctness"]["per_task"]["k1"]
    assert row["A_possible_min"] == row["A_possible_max"] == 2
    assert "NOT a confidence interval" in row["bounds_kind"]
    assert any("confidence interval" in n for n in r["not_established"])


def test_the_three_way_rule_at_its_two_boundaries():
    """B_max < A_min -> fail; B_min >= A_max -> hold; anything between -> indeterminate."""
    import dataclasses
    # B loses even counting its unresolved run in its favour: B_max=1 < A_min=2
    runs = [r for r in clean_sweep() if r.task == "k1"]
    runs = [dataclasses.replace(r, functional_pass=(None if r.attempt == 1 else False))
            if r.policy == "B" else r for r in runs]
    k1 = correctness(runs)["per_task"]["k1"]
    assert k1["B_possible_max"] == 1 and k1["A_possible_min"] == 2 and k1["state"] == "fail"
    # one unresolved on each side: bounds overlap, so neither claim is settled
    runs2 = [dataclasses.replace(r, functional_pass=(None if r.attempt == 1 else True))
             for r in [x for x in clean_sweep() if x.task == "k1"]]
    k1b = correctness(runs2)["per_task"]["k1"]
    assert k1b["state"] == "indeterminate"
    assert k1b["B_possible_min"] == 1 and k1b["B_possible_max"] == 2


# ---------------------------------------------------------------------------------------
# RELEVANCE. Reported is not enough: it gates ACCEPTANCE.
# ---------------------------------------------------------------------------------------

def test_unreviewed_relevance_blocks_acceptance():
    """A policy must not be accepted on added tests nobody has read. The criterion cannot
    carry this -- no machine settles relevance -- so it gates acceptance instead."""
    import dataclasses
    runs = [dataclasses.replace(r, relevance_reviewed=None) for r in clean_sweep()]
    r = decide(runs, FACTS)
    assert r["decision"] == "indeterminate"
    assert r["relevance"]["blocks_acceptance"] is True
    assert len(r["relevance"]["unreviewed_arm_runs"]) == 16
    assert "reviewed for relevance" in r["reason"]


def test_relevance_never_causes_a_failure():
    """It can withhold acceptance and nothing else -- an unreviewed row is not a loss."""
    import dataclasses
    runs = [dataclasses.replace(r, relevance_reviewed=None) for r in clean_sweep()]
    r = decide(runs, FACTS)
    assert r["failed"] == []
    assert all(s != "fail" for s in r["states"].values())


def test_unreviewed_relevance_does_not_block_a_rejection():
    """A task that lost is a task that lost whether or not its tests were read."""
    import dataclasses
    runs = [dataclasses.replace(r, relevance_reviewed=None,
                                functional_pass=(r.policy == "A") if r.task == "k1"
                                else r.functional_pass)
            for r in clean_sweep()]
    r = decide(runs, FACTS)
    assert r["decision"] == "reject"
    assert "correctness" in r["failed"]


def test_a_review_finding_the_test_irrelevant_is_recorded_and_withholds_acceptance():
    """`False` is a completed review with an adverse result. It is reported, it does NOT
    silently become a failure of the required-work criterion -- and it withholds acceptance
    exactly as an absent review does.

    `blocks_acceptance` keyed off `unreviewed` alone, which read the wrong half of the
    review: marking a test irrelevant emptied that list, so an adverse conclusion was
    indistinguishable from a satisfied gate.
    """
    import dataclasses
    runs = clean_sweep()
    runs[1] = dataclasses.replace(runs[1], relevance_reviewed=False)
    r = decide(runs, FACTS)
    assert r["relevance"]["reviewed_not_relevant"] == ["k1/B/1"]
    assert r["relevance"]["blocks_acceptance"] is True
    assert r["decision"] == "indeterminate"
    assert "NOT relevant" in r["reason"]
    assert r["dimensions"]["required_work"]["state"] == "hold"


def test_every_added_test_reviewed_irrelevant_never_reaches_acceptance():
    """The reported case: A's tests relevant, every one of B's explicitly irrelevant. Each
    row IS reviewed, so `unreviewed` is empty -- and the old gate read an empty list as a
    satisfied gate and returned accept_for_further_development on tests a reviewer had just
    rejected."""
    import dataclasses
    runs = [dataclasses.replace(r, relevance_reviewed=(r.policy != "B"))
            for r in clean_sweep()]
    r = decide(runs, FACTS)
    assert r["relevance"]["unreviewed_arm_runs"] == []
    assert r["relevance"]["reviewed_not_relevant"], "B's rows must be recorded as adverse"
    assert r["relevance"]["blocks_acceptance"] is True
    assert r["decision"] == "indeterminate"
    assert r["decision"] != "accept_for_further_development"
    assert "correctness" not in r["failed"], "an adverse review must never cause a FAIL"


def test_relevance_is_not_in_the_required_work_criterion():
    r = decide(clean_sweep(), FACTS)
    assert r["relevance"]["in_the_required_work_criterion"] is False
    assert any("merely failing in the predicted way" in n for n in r["not_established"])
