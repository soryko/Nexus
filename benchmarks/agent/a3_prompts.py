"""A3's two prompts, assembled and VERIFIED rather than asserted.

Both A3 policies run the nexus arm. They differ in one appended paragraph, and §2 of the
registration holds everything else fixed -- body, tail, environment block, `capture_instruction`.
That is a claim about bytes, so it is checked as one:

  * each task's A prompt must be BYTE-IDENTICAL to the prompt `calib-v3` assembles, so an A
    arm-run is given exactly what a v3 or A2-R nexus arm-run was given;
  * each task's B prompt must be its A prompt plus `consult_bound`, and differ in nothing
    else.

`identity.prompt_digest` cannot do this: it reads one `consult` from the registration and
has no notion of a policy. Rather than teach the shared harness about policies -- §2 holds
the runner fixed -- the policy-aware assembly lives here, over the same `task_set.assemble`
the runner uses for the A half.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import task_set                                                            # noqa: E402

A3_PROMPTS = BENCH / "prompts-a3.json"
V3_PROMPTS = BENCH / "prompts-calib-a2.json"
POLICIES = ("A", "B")
TASKS = ("k1", "k2", "k3", "k4")


def registration(path: Path = A3_PROMPTS) -> dict:
    return json.loads(Path(path).read_text())


def assemble(task: str, policy: str, reg: dict | None = None) -> str:
    """body + tail + environment + consult [+ bound]. One code path for both policies."""
    if policy not in POLICIES:
        raise SystemExit(f"unknown policy {policy!r}; A3 registers {POLICIES}")
    reg = reg or registration()
    spec = task_set.require(reg, task, ("development",), "a3")
    base = task_set.assemble(reg, spec)
    return base + reg["consult_bound"] if policy == "B" else base


def digest(task: str, policy: str, reg: dict | None = None) -> str:
    return hashlib.sha256(assemble(task, policy, reg).encode()).hexdigest()[:16]


def digests(reg: dict | None = None) -> dict[str, dict[str, str]]:
    reg = reg or registration()
    return {p: {t: digest(t, p, reg) for t in TASKS} for p in POLICIES}


def assembly_diff(reg: dict | None = None) -> dict:
    """§2's precondition, measured. Refuses rather than reporting a soft warning."""
    reg = reg or registration()
    v3 = json.loads(V3_PROMPTS.read_text())
    bound = reg["consult_bound"]
    rows, failures = {}, []
    for t in TASKS:
        a, b = assemble(t, "A", reg), assemble(t, "B", reg)
        v3_prompt = task_set.assemble(v3, task_set.require(v3, t, ("development",), "a3"))
        same_as_v3 = a == v3_prompt
        differs_by_exactly_the_bound = b == a + bound
        rows[t] = {"A_matches_calib_v3": same_as_v3,
                   "B_is_A_plus_bound": differs_by_exactly_the_bound,
                   "A_bytes": len(a.encode()), "B_bytes": len(b.encode()),
                   "delta_bytes": len(b.encode()) - len(a.encode()),
                   "A_digest": digest(t, "A", reg), "B_digest": digest(t, "B", reg)}
        if not same_as_v3:
            failures.append(f"{t}: the A prompt is not byte-identical to calib-v3's")
        if not differs_by_exactly_the_bound:
            failures.append(f"{t}: the B prompt is not the A prompt plus the bound")
    # every A digest must differ from its B digest, or the two policies are one policy
    for t in TASKS:
        if rows[t]["A_digest"] == rows[t]["B_digest"]:
            failures.append(f"{t}: A and B assemble to the same bytes")
    return {"holds": not failures, "failures": failures, "per_task": rows,
            "bound_bytes": len(bound.encode()),
            "delta_is_constant": len({r["delta_bytes"] for r in rows.values()}) == 1}


if __name__ == "__main__":
    d = assembly_diff()
    for t, r in d["per_task"].items():
        print(f"{t}  A {r['A_digest']}  B {r['B_digest']}  "
              f"+{r['delta_bytes']} bytes  v3-identical={r['A_matches_calib_v3']}")
    print(f"\nassembly diff: {'HOLDS' if d['holds'] else 'FAILS'}"
          f"   bound {d['bound_bytes']} bytes, constant delta={d['delta_is_constant']}")
    for f in d["failures"]:
        print(f"  {f}")
    raise SystemExit(0 if d["holds"] else 1)
