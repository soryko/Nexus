"""The saved-file path: frozen A/B schedule -> arm-run records -> scoring -> decision.

`test_a3_decision.py` exercises the decision table against synthetic `ArmRun` objects. That
is necessary and it is not sufficient, and the reason is on the record: the fix-leakage scan
had tests too, and it still published "none" on four tasks, because nothing exercised the
path from the FILES to the REPORT. Its defect lived in the join, where the tests were not.

So this module is the join, and `rehearse_a3.py` drives it end to end with no model:

    schedule -> stubbed execution -> saved records -> compliance scoring
             -> NORMALISATION -> decision report

Three things it exists to get right.

**The result identity is `(task, attempt, policy)`.** Both A3 policies run the *nexus* arm --
they differ in one paragraph of the prompt and nothing else -- so `(task, arm)` names eight
slots for sixteen arm-runs and the second of each pair overwrites the first. The historical
harness wrote `run-<task>/attempt<n>/arms/<arm>/`, which has the collision built into the
path. `arm_run_dir` puts the policy in the path, above `arms/`, so the existing scorer runs
over it unchanged and two policies can never land in one directory.

**The launch gate keys on CONSUMPTION, not on outcomes.** A pair whose functional result is
unresolved is a dimension going indeterminate; the sweep continues. A pair whose CONSUMPTION
is unresolved means the sweep does not know what it has spent, and §7 stops it. `may_start`
returns False for the second and True for the first.

**A bound violation is an observation.** `bound_respected` is computed here from the trace --
at most one search and at most three full-body fetches -- and recorded. It excludes nothing
from any dimension. Excluding those arm-runs would select the sample on the outcome being
measured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import score_compliance
from a3_decision import CONSULT_TOOLS, A3_PLAN, ArmRun, Plan, decide
from trace_parse import parse

#: Both policies run this arm. The policy is NOT an arm and never becomes one.
ARM = "nexus"

#: `search` is the bounded call; `get` and `history` return full bodies. `status` is a
#: consultation but fetches no body, so it is counted and does not spend a fetch.
FETCH_TOOLS = ("get", "history")

#: Counting methods, named. An arm-run records WHICH one produced its number, because two
#: methods are two units and their sum has no denominator.
def _utf8_div4(text: str) -> int:
    return (len(text.encode()) + 3) // 4


COUNTING_METHODS = {"harness-utf8-bytes-div4": _utf8_div4}
DEFAULT_COUNTING_METHOD = "harness-utf8-bytes-div4"


def arm_run_dir(root: Path, task: str, attempt: int, policy: str) -> Path:
    """The directory for one arm-run, distinct in all three coordinates.

    `<root>/run-<task>/attempt<n>/policy-<P>/arms/nexus`. The policy sits ABOVE `arms/`, so
    `score_compliance.score(<...>/policy-<P>, task, "nexus", ...)` reads the right tree with
    no change to the scorer.
    """
    return Path(root) / f"run-{task}" / f"attempt{attempt}" / f"policy-{policy}" / "arms" / ARM


def policy_run_dir(root: Path, task: str, attempt: int, policy: str) -> Path:
    """What the scorer is handed: the parent of `arms/`."""
    return arm_run_dir(root, task, attempt, policy).parent.parent


def consultation_calls(calls: list[dict]) -> dict[str, int]:
    """Every consultation tool, counted separately. `status` included."""
    out = {t: 0 for t in CONSULT_TOOLS}
    for c in calls:
        name = (c.get("name") or "")
        if name.startswith("mcp__nexus__"):
            tool = name.split("mcp__nexus__", 1)[1]
            if tool in out:
                out[tool] += 1
    return out


def bound_respected(calls: list[dict]) -> bool:
    """At most one search and at most three full-body fetches. RECORDED, never a filter."""
    n = consultation_calls(calls)
    return n["search"] <= 1 and sum(n[t] for t in FETCH_TOOLS) <= 3


def delivered_text(calls: list[dict]) -> tuple[int, str]:
    """Bytes of tool-result text returned by consultation calls, and that text.

    Delivered CONTEXT VOLUME. Not attributable provider spend: retrieved content reappears
    in later requests and cache accounting differs, which is why it is reported beside
    `provider_total_tokens` and never instead of it.
    """
    text = "".join((c.get("result") or "") for c in calls
                   if (c.get("name") or "").startswith("mcp__nexus__"))
    return len(text.encode()), text


def facts_delivered(text: str, facts: dict[str, list[str]],
                    bodies: dict[str, str]) -> dict[str, bool | None]:
    """EVERY member of every equivalence class, measured.

    A member left out of this dict is `unresolved` to the decision table, not "not
    delivered" -- and a helper that populated only the primary would make every comparison
    indeterminate for a reason that has nothing to do with the run.
    """
    out: dict[str, bool | None] = {}
    for members in facts.values():
        for mid in members:
            body = bodies.get(mid)
            out[mid] = None if body is None else \
                score_compliance.notes_content_delivered(text, [body], need=1)
    return out


def _required_test_work(scored: dict) -> str:
    """The scorer's executed check, mapped without inventing a pass.

    `E1_regression_discriminates` is the only requirement a machine settles here. UNKNOWN is
    an instrument failure or an unlocatable test, and it stays `unresolved`: it is not
    compliance, and A2-R's k1/nexus passed the hidden checks having added no test at all.
    """
    v = (scored.get("checks", {}).get("E1_regression_discriminates") or {}).get("verdict")
    return {"pass": "pass", "fail": "fail"}.get(v, "unresolved")


@dataclass(frozen=True)
class Normalised:
    """An `ArmRun` plus the evidence it was built from, so a reader can audit the mapping."""
    arm_run: ArmRun
    sources: dict


def normalise(root: Path, task: str, attempt: int, policy: str, *,
              facts: dict[str, list[str]], bodies: dict[str, str],
              pristine: Path, python: str, notes_file: Path,
              counting_method: str = DEFAULT_COUNTING_METHOD) -> Normalised:
    """One saved arm-run -> one `ArmRun`. Absent evidence becomes `None`, never a zero.

    Every field that cannot be read is unresolved. That is the whole point of the stage: a
    normaliser that defaults a missing number to 0 reports a saving, and a missing outcome
    to False reports a loss.
    """
    d = arm_run_dir(root, task, attempt, policy)
    t = parse(d / "trace.jsonl")
    calls = t["calls"]

    scored = score_compliance.score(policy_run_dir(root, task, attempt, policy),
                                    task, ARM, pristine, python, notes_file=notes_file)

    # functional and adoption are recorded by the runner and the trace reader; absent is
    # unresolved, and `.get` on a missing file must not become False.
    fn = d / "functional.json"
    functional = json.loads(fn.read_text()).get("passed") if fn.is_file() else None
    on = d / "outcomes.json"
    outcomes = json.loads(on.read_text()) if on.is_file() else {}

    rec = d / "record.json"
    record = json.loads(rec.read_text()) if rec.is_file() else {}
    usage = record.get("usage") or {}
    total = usage.get("total_tokens")
    # An accounting that is absent, or that the runner marked unreconciled, is unresolved.
    resolved = bool(record.get("accounting_resolved", rec.is_file() and total is not None))

    nbytes, text = delivered_text(calls)
    method = COUNTING_METHODS.get(counting_method)

    arm_run = ArmRun(
        task=task, policy=policy, attempt=attempt,
        functional_pass=functional,
        required_test_work=_required_test_work(scored),
        facts_delivered=facts_delivered(text, facts, bodies),
        stale_exposed=outcomes.get("stale_exposed"),
        stale_adopted=outcomes.get("stale_adopted"),
        consultation_calls=consultation_calls(calls),
        delivered_bytes=nbytes,
        delivered_text_tokens=method(text) if method else None,
        token_counting_method=counting_method if method else None,
        provider_total_tokens=total,
        accounting_resolved=resolved,
        # recorded for B; meaningless for A, which is not bounded
        bound_respected=bound_respected(calls) if policy == "B" else None,
    )
    return Normalised(arm_run, {
        "dir": str(d),
        "compliance": scored.get("compliance"),
        "E1": (scored.get("checks", {}).get("E1_regression_discriminates") or {}).get("verdict"),
        "functional_source": "functional.json" if fn.is_file() else "absent -> unresolved",
        "accounting_source": "record.json" if rec.is_file() else "absent -> unresolved",
        "consultation_calls": arm_run.consultation_calls,
        "bound_respected": arm_run.bound_respected,
    })


def collect(root: Path, required_facts: dict[str, dict[str, list[str]]],
            plan: Plan = A3_PLAN, **kw) -> tuple[list[ArmRun], list[dict]]:
    """Every arm-run the plan registers that has actually been saved.

    `required_facts` is indexed PER TASK here. Handing one task's equivalence classes to all
    four would leave the other three tasks' members unmeasured, and unmeasured is
    `unresolved` -- so every task but one would go indeterminate on information for a reason
    that has nothing to do with the policy. That is what the first draft of this function
    did, and the rehearsal is what showed it.

    A cell with no directory is NOT normalised into an unresolved row: it is absent, and
    `validity` reports it as missing coverage. Inventing a row for it would turn "did not
    run" into "ran and told us nothing", which are different facts.
    """
    runs, sources = [], []
    for task, attempt, policy in plan.cells():
        if not (arm_run_dir(root, task, attempt, policy) / "trace.jsonl").is_file():
            continue
        n = normalise(root, task, attempt, policy,
                      facts=required_facts.get(task, {}), **kw)
        runs.append(n.arm_run)
        sources.append(n.sources)
    return runs, sources


def saved_consumption(root: Path, plan: Plan = A3_PLAN) -> list[str]:
    """Consumption of every saved arm-run, read from `record.json` and NOTHING else.

    Deliberately independent of the compliance scorer. The launch gate asks whether the
    sweep knows what it has spent; coupling that to a scorer that applies patches and runs
    pytest would let an instrument failure in the OUTCOME half stop launches -- the exact
    conflation §7 separates -- besides re-scoring every finished arm-run before every pair.

    A record that is absent, carries no usage, or is marked unreconciled is unresolved. It
    is never read as zero: a row whose spend is unknown is not a row that spent nothing.
    """
    out = []
    for task, attempt, policy in plan.cells():
        d = arm_run_dir(root, task, attempt, policy)
        if not (d / "trace.jsonl").is_file():
            continue                                   # not started: absent, not unresolved
        rec = d / "record.json"
        record = json.loads(rec.read_text()) if rec.is_file() else {}
        total = (record.get("usage") or {}).get("total_tokens")
        if not record.get("accounting_resolved", rec.is_file() and total is not None) \
                or total is None:
            out.append(f"{task}/{policy}/{attempt}")
    return out


def may_start_from_disk(root: Path, task: str, attempt: int,
                        plan: Plan = A3_PLAN) -> dict:
    """The launch gate, over the saved records. §8's launch unit is the A/B PAIR."""
    unresolved = saved_consumption(root, plan)
    if unresolved:
        return {"may_start": False, "task": task, "attempt": attempt,
                "stopping_reason": "unresolved_accounting",
                "reason": f"consumption unresolved in {unresolved}; no new pair is started"}
    return {"may_start": True, "task": task, "attempt": attempt, "stopping_reason": None,
            "reason": "every saved arm-run's consumption is accounted for"}


def may_start(runs: list[ArmRun], task: str, attempt: int) -> dict:
    """The same gate over already-normalised runs, for a caller that has them in hand.

    Keys on CONSUMPTION alone. §7: an arm-run whose consumption cannot be accounted for
    stops the sweep -- the row stays unresolved, never zero, never covered by an allowance
    written to unblock it. An unresolved OUTCOME stops nothing.
    """
    from a3_decision import consumption
    c = consumption(runs)
    if not c["further_launches_permitted"]:
        return {"may_start": False, "task": task, "attempt": attempt,
                "stopping_reason": c["stopping_reason"],
                "reason": f"consumption unresolved in {c['unresolved_arm_runs']}; "
                          f"no new pair is started"}
    return {"may_start": True, "task": task, "attempt": attempt, "stopping_reason": None,
            "reason": "every saved arm-run's consumption is accounted for"}


def report(root: Path, required_facts: dict[str, dict[str, list[str]]],
           plan: Plan = A3_PLAN, **kw) -> dict:
    """The whole path, from the saved files to the decision."""
    runs, sources = collect(root, required_facts, plan, **kw)
    out = decide(runs, required_facts, plan=plan)
    out["normalisation"] = sources
    out["root"] = str(root)
    return out
