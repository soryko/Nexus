"""A3's acceptance criteria, as a deterministic decision table. Model-free.

The criteria in the first draft could not be applied without judgement calls at the moment of
reading the numbers, which is the moment judgement is worth least. Six defects, each fixed by
a rule below:

  1. required test work was MEASURED and then absent from acceptance -- so a policy could be
     accepted while losing the work the task asked for;
  2. correctness was POOLED across tasks, letting a loss on one be offset by a gain on
     another. It is per task here, and a pooled total is reported but decides nothing;
  3. every `useful` memory was treated as a required fact whose IDENTITY had to be preserved,
     so delivering an equivalent memory counted as a loss. Facts are predeclared, and an
     equivalence class satisfies one;
  4. delivering refuted information was counted as harm. Exposure and ADOPTION are separate
     measures here: reading a stale memory and rejecting it is the behaviour we want;
  5. failing C1 meant "rejected" in one paragraph and "indeterminate" in another;
  6. an observed-spread clause made C1 void for a task depending on the numbers it was
     judging -- an outcome-dependent exception, removed.

The 40% / 10% thresholds are ENGINEERING PREFERENCES. Two attempts per cell cannot establish
that a threshold exceeds stochastic variation, so no such claim is made and the spread-based
exception is gone.

Dimensions, each returning `hold`, `fail` or `indeterminate`:

  correctness     no task has fewer functional passes under B than under A
  required_work   no task loses verified relevant regression-test compliance; unresolved
                  evidence stays unresolved and never counts as compliance
  information     predeclared task-relevant facts are preserved, satisfied by any member of
                  the fact's equivalence class
  stale_adoption  incorrect ADOPTION does not increase; exposure is reported, not judged
  cost            the registered thresholds, applied to every arm-run including failures

Validity is established BEFORE any of them. `validity()` asks whether the records are the
registered experiment at all; the dimensions ask what it showed. A sweep that did not run
cannot be accepted, and a record set with a duplicated or unplanned identity is not evidence
about the policy at all -- the A3 result identity is `(task, attempt, policy)`, because both
policies run the *nexus* arm and `(task, arm)` would collide them.

Missing OUTCOME evidence and missing CONSUMPTION are also separated. The first makes a
dimension indeterminate and the sweep may continue; the second means the sweep has lost track
of what it spent, and §7 stops it -- `further_launches_permitted` keys on that and nothing
else.

  decision        record set corrupt          -> invalid (no criterion applied)
                  any established failure     -> reject
                  coverage partial            -> indeterminate (acceptance unavailable)
                  any indeterminate dimension -> indeterminate
                  else                        -> accept_for_further_development
"""
from __future__ import annotations

from dataclasses import dataclass, field

HOLD, FAIL, INDETERMINATE = "hold", "fail", "indeterminate"

#: Every consultation tool call counted. `status` is counted too: it is a consultation of the
#: memory service and a bounded policy that leans on it is still consulting.
CONSULT_TOOLS = ("status", "search", "get", "history")

#: Cost is reported in three units that are NOT interchangeable, because "consultation
#: tokens" alone is not an operational definition. Tool-result text measures delivered
#: context volume; it is not attributable provider spend, since retrieved content reappears
#: in later requests and cache accounting differs.
COST_UNITS = (
    "calls",                  # per tool in CONSULT_TOOLS
    "delivered_bytes",        # bytes of tool-result text
    "delivered_text_tokens",  # under a NAMED counting method, recorded per run
    "provider_total_tokens",  # the envelope's own categories, failures included
)


@dataclass(frozen=True)
class ArmRun:
    """One arm-run. `None` means unresolved and is never silently read as a zero or a pass."""
    task: str
    policy: str                              # "A" (current) or "B" (bounded)
    attempt: int
    functional_pass: bool | None = None
    #: did the patch add a test node AND did the arm-run execute it
    required_test_work: str = "unresolved"   # "pass" | "fail" | "unresolved"
    #: predeclared fact id -> delivered before the first source edit
    facts_delivered: dict[str, bool | None] = field(default_factory=dict)
    stale_exposed: bool | None = None        # a refuted memory was delivered
    stale_adopted: bool | None = None        # the patch or a reproduction followed it
    consultation_calls: dict[str, int] = field(default_factory=dict)
    delivered_bytes: int | None = None
    delivered_text_tokens: int | None = None
    token_counting_method: str | None = None
    provider_total_tokens: int | None = None
    accounting_resolved: bool = True
    #: B only: did the run stay inside "one search, at most three fetches". RECORDED, never
    #: used to exclude the run -- a policy that is not followed is a result about the policy.
    bound_respected: bool | None = None
    #: Did a HUMAN review find the added test relevant to the reported defect, rather than
    #: merely failing in the predicted way? `None` = not reviewed. No machine settles this;
    #: `score_compliance` lists it under `unsettled_by_machine` and always has.
    relevance_reviewed: bool | None = None

    def consult_total(self) -> int:
        return sum(self.consultation_calls.get(t, 0) for t in CONSULT_TOOLS)


@dataclass(frozen=True)
class Thresholds:
    """Engineering preferences, registered in advance and applied without exception."""
    consultation_token_drop: float = 0.40
    total_token_drop: float = 0.10


def _verdict(results: list[str]) -> str:
    if FAIL in results:
        return FAIL
    if INDETERMINATE in results:
        return INDETERMINATE
    return HOLD


def _by_cell(runs: list[ArmRun]) -> dict[tuple[str, str], list[ArmRun]]:
    cells: dict[tuple[str, str], list[ArmRun]] = {}
    for r in runs:
        cells.setdefault((r.task, r.policy), []).append(r)
    return cells


#: What the bounds below are, said once, and carried in every row that reports them.
BOUNDS_KIND = ("possible-count bounds: the smallest and largest count each policy could have "
               "had, given that an unresolved arm-run is neither a pass nor a failure. NOT a "
               "confidence interval -- nothing here is estimated from a distribution, no "
               "sampling model is assumed, and the width says how much evidence is missing, "
               "not how much the measurement varies")


def _possible_count_bounds(a_pass: int, a_unresolved: int, b_pass: int, b_unresolved: int,
                           what: str) -> dict:
    """Compare two counts when some observations are unresolved, without guessing them.

    An unresolved arm-run is not a failure and not a pass, so each policy's count is known
    only to lie between two integers: its resolved passes (every unresolved run went against
    it) and its resolved passes plus its unresolved runs (every one went for it). Those two
    integers are the POSSIBLE-COUNT BOUNDS.

    They are not a confidence interval and the earlier name `_interval` invited that reading.
    A confidence interval is an estimate under a sampling model; these bounds are arithmetic
    over observations that exist and observations that are missing. Two attempts per cell
    could not support the former, and nothing here computes one.

    The rule, applied per task, for the requirement that B does at least as well as A:

        B's maximum possible count < A's minimum possible count   -> fail
        B's minimum possible count >= A's maximum possible count  -> hold
        otherwise                                                 -> indeterminate

    "B scored fewer" and "B lost" are different claims; the first draft conflated them and a
    single unresolved run read as a loss.
    """
    b_max, b_min = b_pass + b_unresolved, b_pass
    a_max, a_min = a_pass + a_unresolved, a_pass
    row = {"A": a_pass, "B": b_pass,
           "A_unresolved": a_unresolved, "B_unresolved": b_unresolved,
           "A_possible_min": a_min, "A_possible_max": a_max,
           "B_possible_min": b_min, "B_possible_max": b_max,
           "bounds_kind": BOUNDS_KIND}
    if b_max < a_min:
        return {**row, "state": FAIL,
                "reason": f"{what}: B's maximum possible count is below A's minimum "
                          f"({b_max} < {a_min}), so B loses even counting every unresolved "
                          f"run in its favour"}
    if b_min >= a_max:
        return {**row, "state": HOLD}
    return {**row, "state": INDETERMINATE,
            "reason": f"{what}: the possible-count bounds overlap "
                      f"(B {b_min}-{b_max} vs A {a_min}-{a_max}), so the comparison is not "
                      f"settled either way"}


def run_id(r: ArmRun) -> tuple[str, int, str]:
    """The result identity: (task, attempt, policy).

    NOT (task, arm). Both A3 policies run the *nexus* arm -- they differ in one paragraph of
    the prompt and in nothing else -- so an identity keyed on the arm collides A with B at
    every task and attempt, and 16 arm-runs would land in 8 slots with the second of each
    pair overwriting the first. The on-disk layout has the same hazard: `run_arms_isolated`
    writes to `run-<task>/attempt<n>/arms/<arm>/`, which is one directory for both policies.
    A3 varies a dimension the historical harness did not have.
    """
    return (r.task, r.attempt, r.policy)


@dataclass(frozen=True)
class Plan:
    """The registered design. Coverage is checked against THIS, not against what arrived."""
    tasks: tuple[str, ...] = ("k1", "k2", "k3", "k4")
    policies: tuple[str, ...] = ("A", "B")
    attempts: int = 2

    def cells(self) -> list[tuple[str, int, str]]:
        return [(t, a, p) for t in self.tasks
                for a in range(1, self.attempts + 1) for p in self.policies]

    def pairs(self) -> list[tuple[str, int]]:
        return [(t, a) for t in self.tasks for a in range(1, self.attempts + 1)]


A3_PLAN = Plan()


def _consumption_unresolved(runs: list[ArmRun]) -> list[str]:
    """Arm-runs whose CONSUMPTION is not accounted for. One rule, used by both readers."""
    return [f"{r.task}/{r.policy}/{r.attempt}" for r in runs
            if not r.accounting_resolved or r.delivered_text_tokens is None
            or r.provider_total_tokens is None or not r.token_counting_method]


def validity(runs: list[ArmRun], plan: Plan = A3_PLAN) -> dict:
    """Is this evidence the registered experiment at all? Answered BEFORE the criteria.

    An acceptance criterion applied to a sweep that did not run reports a verdict about
    something that does not exist. Three ways that happens:

      missing      a planned arm-run is absent -- the sweep stopped at the soft threshold,
                   or a row refused. Legitimate, and §7 requires it be reported as partial;
      duplicate    two records share one (task, attempt, policy). Under the historical
                   (task, arm) identity this is what A and B would have done to each other;
      unplanned    a record outside the registered design, which is not this experiment.

    The first is PARTIAL and the other two are CORRUPT, and they are not interchangeable.

    Partial coverage still carries evidence: B losing a task that DID run is real, and a
    sweep stopping early does not unmake it, so an established failure still rejects. What
    partial coverage forbids is ACCEPTANCE -- it cannot be read as a complete comparison.

    A corrupt record set carries none. If two records share an identity the counts they feed
    are not counts of anything: a duplicated A row inflates A's passes and "B has fewer"
    becomes an artifact of the bookkeeping. `decide` therefore applies no acceptance
    criterion at all to a corrupt set and returns `invalid` -- an earlier version of this
    function rejected the POLICY for a collision in the FILES, a verdict about the wrong
    thing.
    """
    seen: dict[tuple[str, int, str], int] = {}
    for r in runs:
        seen[run_id(r)] = seen.get(run_id(r), 0) + 1
    planned = set(plan.cells())
    missing = sorted(planned - set(seen))
    duplicate = sorted(k for k, n in seen.items() if n > 1)
    unplanned = sorted(set(seen) - planned)
    corrupt = bool(duplicate or unplanned)
    complete = not (missing or corrupt)
    covered_pairs = sorted({(t, a) for (t, a, _) in seen}
                           & {(t, a) for (t, a) in plan.pairs()
                              if all((t, a, p) in seen for p in plan.policies)})
    return {"state": HOLD if complete else FAIL,
            "complete_coverage": complete,
            # PARTIAL and CORRUPT are different failures with different consequences, and
            # collapsing them is what made a bookkeeping collision reject the POLICY.
            "partial": bool(missing) and not corrupt,
            "corrupt": corrupt,
            "planned_arm_runs": len(planned),
            "distinct_identities": len(seen),
            "records": len(runs),
            "missing": [f"{t}/{p}/{a}" for (t, a, p) in missing],
            "duplicate_identities": [f"{t}/{p}/{a}" for (t, a, p) in duplicate],
            "unplanned": [f"{t}/{p}/{a}" for (t, a, p) in unplanned],
            "complete_pairs": [f"{t}/attempt{a}" for (t, a) in covered_pairs],
            "identity_is": "(task, attempt, policy)",
            "reason": "" if complete else
                      f"{len(missing)} missing, {len(duplicate)} duplicate, "
                      f"{len(unplanned)} unplanned against a {len(planned)}-arm-run design"}


def consumption(runs: list[ArmRun]) -> dict:
    """Whether every arm-run's CONSUMPTION is accounted for -- the launch gate.

    Kept separate from missing OUTCOME evidence on purpose, because the two have opposite
    consequences. A run whose functional result is unresolved costs nothing further: the
    sweep may continue and the dimension goes indeterminate. A run whose CONSUMPTION is
    unresolved means the sweep does not know what it has spent, and §7 stops it -- the row
    stays `unresolved`, never zero, never covered by an allowance written to unblock it.

    So `further_launches_permitted` keys on this and on nothing else. An indeterminate
    verdict does not stop a launch; an unaccounted token does.
    """
    unresolved = _consumption_unresolved(runs)
    return {"state": HOLD if not unresolved else INDETERMINATE,
            "resolved_arm_runs": len(runs) - len(unresolved),
            "unresolved_arm_runs": unresolved,
            "further_launches_permitted": not unresolved,
            "stopping_reason": None if not unresolved else "unresolved_accounting",
            "note": "missing outcome evidence does not stop a launch; unaccounted "
                    "consumption does, and the total is then a lower bound"}


def correctness(runs: list[ArmRun]) -> dict:
    """PER TASK. A loss on one task is not offset by a gain on another, so no pooled total
    decides anything -- it is reported beside the per-task rows and marked as not deciding."""
    cells, per_task = _by_cell(runs), {}
    for task in sorted({r.task for r in runs}):
        a, b = cells.get((task, "A"), []), cells.get((task, "B"), [])
        if not a or not b:
            per_task[task] = {"state": INDETERMINATE, "reason": "a cell has no arm-run"}
            continue
        per_task[task] = _possible_count_bounds(
            sum(1 for r in a if r.functional_pass is True),
            sum(1 for r in a if r.functional_pass is None),
            sum(1 for r in b if r.functional_pass is True),
            sum(1 for r in b if r.functional_pass is None),
            "functional passes")
    return {"state": _verdict([v["state"] for v in per_task.values()]), "per_task": per_task,
            "pooled_and_not_deciding": {
                "A": sum(1 for r in runs if r.policy == "A" and r.functional_pass),
                "B": sum(1 for r in runs if r.policy == "B" and r.functional_pass)}}


def required_work(runs: list[ArmRun]) -> dict:
    """No task loses verified relevant regression-test compliance.

    `unresolved` stays unresolved: it is not compliance, and it is not a loss either. A task
    whose only evidence is unresolved is indeterminate, not passed -- which is the defect
    that let a hidden-check pass with no test count as a finished task."""
    cells, per_task = _by_cell(runs), {}
    for task in sorted({r.task for r in runs}):
        a, b = cells.get((task, "A"), []), cells.get((task, "B"), [])
        if not a or not b:
            per_task[task] = {"state": INDETERMINATE, "reason": "a cell has no arm-run"}
            continue
        per_task[task] = _possible_count_bounds(
            sum(1 for r in a if r.required_test_work == "pass"),
            sum(1 for r in a if r.required_test_work == "unresolved"),
            sum(1 for r in b if r.required_test_work == "pass"),
            sum(1 for r in b if r.required_test_work == "unresolved"),
            "verified regression-test compliance")
    return {"state": _verdict([v["state"] for v in per_task.values()]), "per_task": per_task}


def information(runs: list[ArmRun], required_facts: dict[str, dict[str, list[str]]]) -> dict:
    """Predeclared task-relevant FACTS, not memory identities.

    `required_facts[task][fact_id]` is an EQUIVALENCE CLASS of memory ids: any member
    delivering the fact satisfies it, so a bounded policy that reaches a redundant memory
    carrying the same claim has lost nothing. The draft required the identities themselves and
    would have rejected that.

    These are facts declared task-relevant in advance. Nothing here calls them NECESSARY: that
    would need evidence that the task cannot be completed without them, which this design does
    not gather."""
    cells, per_task = _by_cell(runs), {}
    for task in sorted({r.task for r in runs}):
        facts = required_facts.get(task, {})
        a, b = cells.get((task, "A"), []), cells.get((task, "B"), [])
        rows = {}
        if not facts:
            per_task[task] = {"state": HOLD, "facts": {},
                              "note": "no fact predeclared for this task"}
            continue
        for fact, members in facts.items():
            def best(group):
                seen = [m.facts_delivered.get(i) for m in group for i in members]
                if any(v is True for v in seen):
                    return True
                if any(v is None for v in seen) or not seen:
                    return None
                return False
            da, db = best(a), best(b)
            if da is True and db is False:
                rows[fact] = {"state": FAIL, "A": da, "B": db,
                              "reason": "delivered under A, not under B"}
            elif da is None or db is None:
                rows[fact] = {"state": INDETERMINATE, "A": da, "B": db}
            else:
                rows[fact] = {"state": HOLD, "A": da, "B": db}
        per_task[task] = {"state": _verdict([r["state"] for r in rows.values()]),
                          "facts": rows}
    return {"state": _verdict([v["state"] for v in per_task.values()]), "per_task": per_task}


def stale_advice(runs: list[ArmRun]) -> dict:
    """Exposure and adoption, measured separately.

    Only ADOPTION is a guardrail. Reading a refuted memory and rejecting it is the behaviour
    the consult block asks for -- "treat prior notes as potentially outdated and verify" --
    so counting delivery as harm would penalise compliance. Exposure is reported because it
    is the quantity the bound acts on, and it decides nothing on its own."""
    def count(policy, attr):
        vals = [getattr(r, attr) for r in runs if r.policy == policy]
        return {"true": sum(1 for v in vals if v is True),
                "unresolved": sum(1 for v in vals if v is None), "n": len(vals)}
    exp_a, exp_b = count("A", "stale_exposed"), count("B", "stale_exposed")
    ad_a, ad_b = count("A", "stale_adopted"), count("B", "stale_adopted")
    if ad_b["true"] > ad_a["true"]:
        state = FAIL
        reason = f"stale advice adopted in {ad_b['true']} B arm-runs vs {ad_a['true']} under A"
    elif ad_a["unresolved"] or ad_b["unresolved"]:
        state, reason = INDETERMINATE, "adoption unresolved in at least one arm-run"
    else:
        state, reason = HOLD, ""
    return {"state": state, "reason": reason,
            "adoption": {"A": ad_a, "B": ad_b},
            "exposure_reported_not_judged": {"A": exp_a, "B": exp_b}}


def cost(runs: list[ArmRun], thresholds: Thresholds = Thresholds()) -> dict:
    """Registered thresholds, applied to EVERY arm-run including failures.

    No exclusions: not a failed run, not a run that broke the bound, not an outlier. A run
    whose accounting is unresolved makes the dimension indeterminate rather than being
    dropped from the denominator, because dropping it would flatter whichever arm it fell in.

    `delivered_text_tokens` is a measure of delivered context volume under a named counting
    method, NOT attributable provider spend; `provider_total_tokens` is the envelope's own
    categories. Both are required, and a run that does not name its counting method cannot be
    counted."""
    unresolved = _consumption_unresolved(runs)
    methods = sorted({r.token_counting_method for r in runs if r.token_counting_method})

    def total(policy, attr):
        return sum(getattr(r, attr) or 0 for r in runs if r.policy == policy)

    ca, cb = total("A", "delivered_text_tokens"), total("B", "delivered_text_tokens")
    ta, tb = total("A", "provider_total_tokens"), total("B", "provider_total_tokens")
    consult_drop = (ca - cb) / ca if ca else None
    total_drop = (ta - tb) / ta if ta else None
    out = {"consultation_drop": consult_drop, "total_drop": total_drop,
           "delivered_text_tokens": {"A": ca, "B": cb},
           "provider_total_tokens": {"A": ta, "B": tb},
           "counting_methods": methods,
           "calls": {p: {t: sum(r.consultation_calls.get(t, 0)
                                for r in runs if r.policy == p) for t in CONSULT_TOOLS}
                     for p in ("A", "B")},
           "delivered_bytes": {p: sum(r.delivered_bytes or 0
                                      for r in runs if r.policy == p) for p in ("A", "B")},
           "thresholds": {"consultation": thresholds.consultation_token_drop,
                          "total": thresholds.total_token_drop},
           "threshold_basis": "engineering preference, registered in advance; two attempts "
                              "per cell cannot establish that it exceeds stochastic variation "
                              "and no such claim is made",
           "unresolved_arm_runs": unresolved}
    if len(methods) > 1:
        out["state"] = INDETERMINATE
        out["reason"] = f"arm-runs counted tokens by different methods: {methods}"
    elif unresolved:
        out["state"] = INDETERMINATE
        out["reason"] = f"{len(unresolved)} arm-run(s) without resolved accounting"
    elif consult_drop is None or total_drop is None:
        out["state"] = INDETERMINATE
        out["reason"] = "a baseline total is zero, so a drop cannot be computed"
    elif (consult_drop >= thresholds.consultation_token_drop
          and total_drop >= thresholds.total_token_drop):
        out["state"] = HOLD
    else:
        out["state"] = FAIL
        out["reason"] = (f"consultation drop {consult_drop:.3f} / total drop {total_drop:.3f} "
                         f"does not meet the registered thresholds")
    return out


def relevance(runs: list[ArmRun]) -> dict:
    """Whether the added tests have been REVIEWED for relevance, and what that gates.

    It is not in the required-work criterion and must not be: relevance is a judgement no
    machine makes, and folding an unreviewed row into a machine verdict would substitute
    the verdict for the review. But leaving it merely "reported" let a policy be ACCEPTED
    on tests nobody had read, which is the gap this closes.

    So it gates acceptance exactly as partial coverage does -- it can never cause a FAIL,
    and acceptance is unavailable until the review has happened. Rejection is unaffected: a
    task that lost is a task that lost whether or not its tests were read.
    """
    reviewed = [r for r in runs if r.relevance_reviewed is not None]
    unreviewed = sorted(f"{r.task}/{r.policy}/{r.attempt}" for r in runs
                        if r.relevance_reviewed is None)
    irrelevant = sorted(f"{r.task}/{r.policy}/{r.attempt}" for r in runs
                        if r.relevance_reviewed is False)
    return {"state": HOLD if not unreviewed else INDETERMINATE,
            "reviewed": len(reviewed), "total": len(runs),
            "unreviewed_arm_runs": unreviewed,
            "reviewed_not_relevant": irrelevant,
            "blocks_acceptance": bool(unreviewed),
            "in_the_required_work_criterion": False,
            "note": "no machine settles relevance; it gates ACCEPTANCE and never causes a "
                    "failure, and it never blocks a rejection"}


def bound_compliance(runs: list[ArmRun]) -> dict:
    """Reported, never a filter.

    A B arm-run that issued a second search violated the candidate policy. That is a finding
    ABOUT the policy -- an instruction the model did not follow is evidence the instruction is
    weak -- and excluding those runs would select the sample on the outcome being measured."""
    b = [r for r in runs if r.policy == "B"]
    return {"n": len(b),
            "respected": sum(1 for r in b if r.bound_respected is True),
            "violated": sum(1 for r in b if r.bound_respected is False),
            "unresolved": sum(1 for r in b if r.bound_respected is None),
            "excluded_from_any_dimension": 0,
            "note": "violations are counted and kept; no arm-run is excluded for breaking "
                    "the candidate policy"}


def decide(runs: list[ArmRun], required_facts: dict[str, dict[str, list[str]]],
           thresholds: Thresholds = Thresholds(), plan: Plan = A3_PLAN) -> dict:
    """The whole table, in one pass, with VALIDITY established first.

    Order matters. `validity` asks whether the evidence is the registered experiment; the
    criteria ask what the experiment showed. Applying the second without the first is how a
    half-finished sweep reports "accept".

    The wiring is asymmetric, deliberately. An established failure rejects whether or not
    coverage is complete -- B losing a task that ran is evidence, and a stopped sweep does
    not unmake it. But nothing is ACCEPTED on partial coverage: incomplete, duplicated or
    unplanned records make the outcome indeterminate and say which.

    Guardrails are correctness, required work, information and stale ADOPTION. Cost is a
    criterion too -- a change that does not pay is not adopted -- but it is not a guardrail:
    failing it rejects the policy without implying the policy did harm."""
    valid = validity(runs, plan)
    consumed = consumption(runs)
    rel = relevance(runs)
    dims = {"correctness": correctness(runs),
            "required_work": required_work(runs),
            "information": information(runs, required_facts),
            "stale_advice": stale_advice(runs),
            "cost": cost(runs, thresholds)}
    guardrails = ("correctness", "required_work", "information", "stale_advice")
    states = {k: v["state"] for k, v in dims.items()}
    failed = sorted(k for k, s in states.items() if s == FAIL)
    unresolved = sorted(k for k, s in states.items() if s == INDETERMINATE)
    if valid["corrupt"]:
        decision = "invalid"
        why = (f"the record set is not the registered experiment and no acceptance "
               f"criterion was applied to it: {valid['reason']}")
        failed, unresolved = [], []
    elif failed:
        decision, why = "reject", f"established failure: {', '.join(failed)}"
    elif valid["partial"]:
        decision = "indeterminate"
        why = (f"the registered experiment did not run in full, so acceptance is not "
               f"available: {valid['reason']}")
    elif rel["blocks_acceptance"]:
        decision = "indeterminate"
        why = (f"the added tests in {len(rel['unreviewed_arm_runs'])} arm-run(s) have not "
               f"been reviewed for relevance to the reported defect, so acceptance is not "
               f"available. No machine settles this, and a machine verdict may not stand "
               f"in for the review.")
    elif unresolved:
        decision, why = ("indeterminate",
                         f"insufficient decisive evidence: {', '.join(unresolved)}")
    else:
        decision, why = "accept_for_further_development", "every criterion satisfied"
    return {"decision": decision, "reason": why, "dimensions": dims, "states": states,
            "failed": failed, "indeterminate": unresolved,
            "validity": valid, "experiment_valid": valid["complete_coverage"],
            "criteria_applied": not valid["corrupt"],
            "consumption": consumed, "relevance": rel,
            "further_launches_permitted": consumed["further_launches_permitted"],
            "guardrails": list(guardrails),
            "bound_compliance": bound_compliance(runs),
            "not_established": [
                "that the thresholds exceed stochastic variation -- two attempts per cell",
                "anything from the possible-count bounds about sampling variability: they "
                "are arithmetic over missing observations, not confidence intervals",
                "that any preserved fact is NECESSARY to the task",
                "that an added test covers the defect rather than merely failing in the "
                "predicted way, wherever `relevance` reports it unreviewed",
                "anything about tasks outside k1-k4, which are exposed development tasks"]}


def render(report: dict) -> str:
    v, c = report["validity"], report["consumption"]
    L = ["=" * 92, f"A3 decision: {report['decision'].upper()}", f"  {report['reason']}",
         "=" * 92, "",
         f"experiment validity: {v['state']}   "
         f"{v['distinct_identities']}/{v['planned_arm_runs']} planned arm-runs, identity "
         f"{v['identity_is']}"]
    for k in ("missing", "duplicate_identities", "unplanned"):
        if v[k]:
            L.append(f"    {k}: {', '.join(v[k])}")
    L += [f"consumption: {c['state']}   {c['resolved_arm_runs']}/{report['validity']['records']}"
          f" accounted; further launches "
          f"{'permitted' if c['further_launches_permitted'] else 'STOPPED'}"
          + (f" ({c['stopping_reason']})" if c["stopping_reason"] else ""), ""]
    for name, dim in report["dimensions"].items():
        L.append(f"{name:16} {dim['state']}"
                 + (f"   {dim['reason']}" if dim.get("reason") else ""))
        for task, row in (dim.get("per_task") or {}).items():
            extra = f"  A={row.get('A')} B={row.get('B')}" if "A" in row else ""
            L.append(f"    {task:4} {row['state']:14}{extra}"
                     + (f"  {row['reason']}" if row.get("reason") else ""))
    bc = report["bound_compliance"]
    r = report["relevance"]
    L += ["", f"relevance: {r['reviewed']}/{r['total']} reviewed"
              + ("  -- ACCEPTANCE BLOCKED until reviewed" if r["blocks_acceptance"] else ""),
          "counts compared as POSSIBLE-COUNT BOUNDS, not confidence intervals",
          f"bound compliance (B): respected {bc['respected']}, violated {bc['violated']}, "
              f"unresolved {bc['unresolved']}; excluded {bc['excluded_from_any_dimension']}",
          "", "NOT ESTABLISHED"]
    L += [f"  - {n}" for n in report["not_established"]]
    return "\n".join(L)
