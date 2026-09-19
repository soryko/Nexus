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

from a3_decision import (ArmRun, Thresholds, bound_compliance, correctness,  # noqa: E402
                         cost, decide, information, required_work, stale_advice)

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
