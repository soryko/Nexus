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

  decision        any established failure -> reject
                  else any indeterminate   -> indeterminate
                  else                     -> accept_for_further_development
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


def _interval(a_pass: int, a_unresolved: int, b_pass: int, b_unresolved: int,
              what: str) -> dict:
    """Compare two counts when some observations are unresolved, without guessing them.

    An unresolved arm-run is not a failure and not a pass, so the comparison is an INTERVAL,
    not a number. B is established behind only if it loses even in its own best case against
    A's worst; it is established level only if it holds in its worst case against A's best.
    Anything between those is indeterminate -- which is the point: a single unresolved run
    used to read as a loss, and "B scored fewer" is not the same claim as "B lost".
    """
    b_max, b_min = b_pass + b_unresolved, b_pass
    a_max, a_min = a_pass + a_unresolved, a_pass
    row = {"A": a_pass, "B": b_pass,
           "A_unresolved": a_unresolved, "B_unresolved": b_unresolved}
    if b_max < a_min:
        return {**row, "state": FAIL,
                "reason": f"{what}: B cannot reach A even if every unresolved run passed "
                          f"({b_max} < {a_min})"}
    if b_min >= a_max:
        return {**row, "state": HOLD}
    return {**row, "state": INDETERMINATE,
            "reason": f"{what}: unresolved evidence spans the comparison "
                      f"(B {b_min}-{b_max} vs A {a_min}-{a_max})"}


def correctness(runs: list[ArmRun]) -> dict:
    """PER TASK. A loss on one task is not offset by a gain on another, so no pooled total
    decides anything -- it is reported beside the per-task rows and marked as not deciding."""
    cells, per_task = _by_cell(runs), {}
    for task in sorted({r.task for r in runs}):
        a, b = cells.get((task, "A"), []), cells.get((task, "B"), [])
        if not a or not b:
            per_task[task] = {"state": INDETERMINATE, "reason": "a cell has no arm-run"}
            continue
        per_task[task] = _interval(
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
        per_task[task] = _interval(
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
    unresolved = [f"{r.task}/{r.policy}/{r.attempt}" for r in runs
                  if not r.accounting_resolved or r.delivered_text_tokens is None
                  or r.provider_total_tokens is None or not r.token_counting_method]
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
           thresholds: Thresholds = Thresholds()) -> dict:
    """The whole table, in one pass.

    Guardrails are correctness, required work, information and stale ADOPTION. Cost is a
    criterion too -- a change that does not pay is not adopted -- but it is not a guardrail:
    failing it rejects the policy without implying the policy did harm."""
    dims = {"correctness": correctness(runs),
            "required_work": required_work(runs),
            "information": information(runs, required_facts),
            "stale_advice": stale_advice(runs),
            "cost": cost(runs, thresholds)}
    guardrails = ("correctness", "required_work", "information", "stale_advice")
    states = {k: v["state"] for k, v in dims.items()}
    failed = sorted(k for k, s in states.items() if s == FAIL)
    unresolved = sorted(k for k, s in states.items() if s == INDETERMINATE)
    if failed:
        decision, why = "reject", f"established failure: {', '.join(failed)}"
    elif unresolved:
        decision, why = ("indeterminate",
                         f"insufficient decisive evidence: {', '.join(unresolved)}")
    else:
        decision, why = "accept_for_further_development", "every criterion satisfied"
    return {"decision": decision, "reason": why, "dimensions": dims, "states": states,
            "failed": failed, "indeterminate": unresolved,
            "guardrails": list(guardrails),
            "bound_compliance": bound_compliance(runs),
            "not_established": [
                "that the thresholds exceed stochastic variation -- two attempts per cell",
                "that any preserved fact is NECESSARY to the task",
                "anything about tasks outside k1-k4, which are exposed development tasks"]}


def render(report: dict) -> str:
    L = ["=" * 92, f"A3 decision: {report['decision'].upper()}", f"  {report['reason']}",
         "=" * 92, ""]
    for name, dim in report["dimensions"].items():
        L.append(f"{name:16} {dim['state']}"
                 + (f"   {dim['reason']}" if dim.get("reason") else ""))
        for task, row in (dim.get("per_task") or {}).items():
            extra = f"  A={row.get('A')} B={row.get('B')}" if "A" in row else ""
            L.append(f"    {task:4} {row['state']:14}{extra}"
                     + (f"  {row['reason']}" if row.get("reason") else ""))
    bc = report["bound_compliance"]
    L += ["", f"bound compliance (B): respected {bc['respected']}, violated {bc['violated']}, "
              f"unresolved {bc['unresolved']}; excluded {bc['excluded_from_any_dimension']}",
          "", "NOT ESTABLISHED"]
    L += [f"  - {n}" for n in report["not_established"]]
    return "\n".join(L)
