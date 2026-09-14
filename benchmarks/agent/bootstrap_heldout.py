"""The §5 percentile bootstrap, computed as registration revision 1 required. No model runs.

freeze-heldout-a1 §5 registered BOTH of these, and they are not the same instruction:

    "A percentile bootstrap over tasks, 10 000 resamples, reported with the contributing
     task count."                                                   <- compute it and report it
    "With four tasks this interval does not support an inferential claim and none will be
     made from it."                                                 <- do not reason from it

The first release of results-heldout-a1.md omitted the first on the strength of the second.
Revision 2 §5 lists "the analysis and exclusion rules in §5" among what stands unchanged, so
r2 did not relax the requirement; the omission was a reporting deviation and is disclosed as
one. This computes the registered interval. The prohibition is not softened by having the
number: it prints beside it, and no difference is called established on it.

Resampling is over the four TASKS, not the 36 arm-runs -- §5's unit is the task, and three
attempts on one task are three measurements of that task.

Pass rates are read from the saved `scored` blocks under `a1-functional-2`. Nothing is
re-scored and no verdict moves; this reads what the frozen run already produced.

Usage:  bootstrap_heldout.py <heldout-run-dir> [--json out.json]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

BENCH = Path(__file__).parent
ARMS = ("baseline", "nexus", "notes")
CONTRASTS = (("nexus", "baseline"), ("notes", "baseline"), ("nexus", "notes"))
RESAMPLES = 10_000
SEED = 20260912          # the schedule's registered seed, reused so this is reproducible


def pass_rates(runroot: Path) -> dict[str, dict[str, float]]:
    """-> {task: {arm: pass-rate over its contributing attempts}}, per §5's collapsing rule."""
    tasks = [t["task"] for t in json.loads((BENCH / "tasks-heldout-a1.json").read_text())["tasks"]]
    out: dict[str, dict[str, float]] = {}
    for task in tasks:
        tally = {a: [0, 0] for a in ARMS}          # arm -> [passes, contributing attempts]
        for attempt in (1, 2, 3):
            rec = json.loads((runroot / f"run-{task}" / f"attempt{attempt}"
                              / "records.json").read_text())
            for r in rec["records"]:
                tally[r["arm"]][0] += bool(r["scored"]["passed"])
                tally[r["arm"]][1] += 1
        out[task] = {a: (p / n if n else float("nan")) for a, (p, n) in tally.items()}
    return out


def percentile_bootstrap(values: list[float], resamples=RESAMPLES, seed=SEED):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(resamples))
    return sum(values) / n, means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: bootstrap_heldout.py <heldout-run-dir> [--json out.json]", file=sys.stderr)
        return 2
    rates = pass_rates(Path(argv[1]))
    out = {"resamples": RESAMPLES, "seed": SEED, "unit": "task",
           "pass_rates": rates, "contrasts": {}}

    print(f"Percentile bootstrap, {RESAMPLES} resamples, unit = task, seed = {SEED}\n")
    print(f"{'contrast':20s} {'tasks':>5s} {'point':>8s} {'2.5%':>8s} {'97.5%':>8s}")
    for a, b in CONTRASTS:
        name = f"{a} - {b}"
        per_task = {t: rates[t][a] - rates[t][b] for t in rates}
        vals = list(per_task.values())
        point, lo, hi = percentile_bootstrap(vals)
        out["contrasts"][name] = {"contributing_tasks": len(vals), "point": point,
                                  "ci_lo": lo, "ci_hi": hi, "per_task": per_task}
        print(f"{name:20s} {len(vals):5d} {point:8.3f} {lo:8.3f} {hi:8.3f}")

    print("\nRegistered caveat, reproduced from freeze-heldout-a1 §5:")
    print("  'With four tasks this interval does not support an inferential claim and none")
    print("   will be made from it.'")
    print("\nWith one non-zero task contrast among four, the resampling distribution is")
    print("degenerate: the interval describes the arithmetic of four numbers, not a")
    print("population. It is reported because §5 required it, and read no further.")

    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
