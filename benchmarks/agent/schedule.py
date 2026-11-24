"""One seeded generator over every task-attempt pair, drawn once and saved before execution.

`run_arms_isolated` built its order with `random.Random(SEED).shuffle(order)` -- a fresh
generator, from the same constant, inside each run. The seed was recorded, which made the
result look reproducible, and it was: reproducibly the same order everywhere. All eight saved
development runs record `baseline -> nexus -> notes`, across four tasks and four attempts.
Randomised arm order was a constant, and `protocol-a1` §12's repeated-trials clause rests on
it not being one.

The generator is drawn once here, advanced across the pairs in a fixed enumeration order, and
the whole schedule is written to disk BEFORE the first arm runs. Two properties follow that
per-run seeding could not give:

  reproducible   the same (seed, tasks, attempts, arms) yields the same schedule, on any
                 host, at any time -- checkable without running anything.
  varied         orders differ between pairs, because one generator keeps advancing. That is
                 the property the old code lacked, and it is asserted here rather than hoped
                 for: `counterbalance` reports how the orders actually came out.

Execution reads the schedule; it never re-derives an order. A run that cannot find its pair
in the frozen schedule stops, rather than inventing one.

Usage:  schedule.py build <out.json> --seed N --tasks h1,h2 --attempts 3 [--arms a,b,c]
        schedule.py show <schedule.json>
        schedule.py verify <schedule.json>        # re-derives it and compares
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ARMS = ("baseline", "nexus", "notes")
SCHEDULE_VERSION = 1


def build(tasks: list[str], attempts: int, seed: int,
          arms: tuple[str, ...] = ARMS) -> list[dict]:
    """The realised arm order for every (task, attempt), from ONE generator.

    Pairs are enumerated task-major and the generator is never reseeded, so pair k's order
    depends on every draw before it. Reseeding per task is exactly the defect this replaces.
    """
    rng = random.Random(seed)
    rows = []
    for task in tasks:
        for attempt in range(1, attempts + 1):
            order = list(arms)
            rng.shuffle(order)
            rows.append({"index": len(rows), "task": task, "attempt": attempt,
                         "order": order})
    return rows


def counterbalance(rows: list[dict], arms: tuple[str, ...] = ARMS) -> dict:
    """How the draw actually came out -- reported, never corrected.

    A seeded draw is not a balanced design and this does not pretend otherwise: it is
    evidence about THIS schedule, recorded before the run so that a later reader cannot be
    told the order was balanced when it was not. `distinct_orders == 1` is the failure the
    old harness had.
    """
    first = {a: sum(1 for r in rows if r["order"][0] == a) for a in arms}
    positions = {a: [sum(1 for r in rows if r["order"][i] == a) for i in range(len(arms))]
                 for a in arms}
    distinct = sorted({tuple(r["order"]) for r in rows})
    return {"pairs": len(rows),
            "distinct_orders": len(distinct),
            "orders_used": ["->".join(o) for o in distinct],
            "times_each_arm_ran_first": first,
            "position_counts": positions,
            "every_arm_appears_once_per_pair": all(
                sorted(r["order"]) == sorted(arms) for r in rows)}


def digest(rows: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class Schedule:
    seed: int
    tasks: list[str]
    attempts: int
    arms: list[str]
    rows: list[dict]
    frozen_utc: str
    schedule_digest: str
    schedule_version: int = SCHEDULE_VERSION

    @classmethod
    def create(cls, tasks: list[str], attempts: int, seed: int,
               arms: tuple[str, ...] = ARMS) -> Schedule:
        rows = build(tasks, attempts, seed, arms)
        return cls(seed=seed, tasks=list(tasks), attempts=attempts, arms=list(arms),
                   rows=rows, frozen_utc=datetime.now(timezone.utc).isoformat(),
                   schedule_digest=digest(rows))

    def to_json(self) -> dict:
        return {"schedule_version": self.schedule_version, "seed": self.seed,
                "tasks": self.tasks, "attempts": self.attempts, "arms": self.arms,
                "frozen_utc": self.frozen_utc, "schedule_digest": self.schedule_digest,
                "counterbalance": counterbalance(self.rows, tuple(self.arms)),
                "rows": self.rows}

    def order_for(self, task: str, attempt: int) -> list[str]:
        for r in self.rows:
            if r["task"] == task and r["attempt"] == attempt:
                return list(r["order"])
        raise KeyError(f"({task}, attempt {attempt}) is not in the frozen schedule; "
                       f"an order may not be invented at execution time")


def load(path: Path | str) -> Schedule:
    d = json.loads(Path(path).read_text())
    s = Schedule(seed=d["seed"], tasks=d["tasks"], attempts=d["attempts"], arms=d["arms"],
                 rows=d["rows"], frozen_utc=d["frozen_utc"],
                 schedule_digest=d["schedule_digest"],
                 schedule_version=d.get("schedule_version", 0))
    if digest(s.rows) != s.schedule_digest:
        raise SystemExit(f"{path}: rows do not match schedule_digest -- the schedule was "
                         f"edited after it was frozen")
    return s


def save(schedule: Schedule, path: Path) -> Path:
    Path(path).write_text(json.dumps(schedule.to_json(), indent=1))
    return Path(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("out")
    b.add_argument("--seed", type=int, required=True)
    b.add_argument("--tasks", required=True)
    b.add_argument("--attempts", type=int, required=True)
    b.add_argument("--arms", default=",".join(ARMS))
    for name in ("show", "verify"):
        sub.add_parser(name).add_argument("path")
    a = ap.parse_args()

    if a.cmd == "build":
        out = Path(a.out)
        if out.exists():
            raise SystemExit(f"{out} already exists; a frozen schedule is not overwritten")
        s = Schedule.create(a.tasks.split(","), a.attempts, a.seed,
                            tuple(a.arms.split(",")))
        save(s, out)
        print(f"frozen {len(s.rows)} pairs -> {out}   digest {s.schedule_digest[:16]}")
        a.path = a.out
        a.cmd = "show"

    s = load(a.path)
    if a.cmd == "verify":
        rebuilt = build(s.tasks, s.attempts, s.seed, tuple(s.arms))
        same = rebuilt == s.rows
        print(f"re-derived from (seed={s.seed}, tasks={s.tasks}, attempts={s.attempts}): "
              f"{'IDENTICAL' if same else 'DIFFERENT'}")
        return 0 if same else 1

    cb = counterbalance(s.rows, tuple(s.arms))
    print(f"seed {s.seed}   frozen {s.frozen_utc}   digest {s.schedule_digest[:16]}")
    print(f"{cb['pairs']} pairs, {cb['distinct_orders']} distinct orders: "
          f"{', '.join(cb['orders_used'])}")
    print(f"ran first: {cb['times_each_arm_ran_first']}")
    for r in s.rows:
        print(f"  {r['index']:>3}  {r['task']:<6} attempt {r['attempt']}  "
              f"{' -> '.join(r['order'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
