"""Drive the A2 budget calibration: the same 4 tasks and 3 arms at each of 3 turn ceilings.

Registered in `freeze-calib-a2.md`. This runs it and nothing else -- it selects no ceiling,
computes no figure, and reports no contrast between arms. `report_calibration.py` applies the
selection rule to what this produces.

Three things it is responsible for:

  cap        US$60, checked from the saved envelopes AFTER EVERY ARM-RUN, across every ceiling
             run so far. At the cap it stops where it is. A partial grid is kept and reported
             as partial; it never licenses a ceiling by extrapolation.
  order      ceilings ascending, so a cap hit costs the most expensive cell rather than the
             cheapest. Within a ceiling, rows come from the frozen schedule in its own order.
  isolation  each ceiling writes to its own scratch subtree. No run reads another's output,
             and a resumed run skips only rows whose `records.json` already exists.

Usage:  run_calibration.py <scratch> [--ceilings 30,45,60] [--dry-run]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import schedule as sched                                                # noqa: E402

CEILINGS = (30, 45, 60)
CAP_USD = 60.00
SCHEDULE = BENCH / "schedule-calib-a2.json"
TASKS = BENCH / "tasks-calib-a2.json"
CLONE = "/Users/soko/Cerebros/nexus-a1-fixtures/click"


def spent(scratch: Path) -> float:
    """Every dollar this calibration has spent, read from the envelopes it saved.

    Summed over ALL ceilings, not the current one: the cap is on the experiment, not on a
    row. A records file that cannot be read is not treated as zero -- it raises, because a
    cap enforced on a silently-partial sum is not a cap.
    """
    total = 0.0
    for rec in sorted(scratch.glob("c*/run-*/attempt*/records.json")):
        data = json.loads(rec.read_text())
        for r in data["records"]:
            total += (r.get("result") or {}).get("total_cost_usd") or 0.0
    return total


def build_fixture_for(task: str, ceiling_root: Path, python: str) -> None:
    """One task's fixture, under the layout run_arms_isolated reads: run-<task>/base/<task>."""
    spec = json.loads(TASKS.read_text())
    one = dict(spec, tasks=[t for t in spec["tasks"] if t["task"] == task])
    tmp = ceiling_root / f"run-{task}" / "_spec.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(one))
    done = subprocess.run([python, str(BENCH / "build_fixture.py"), CLONE, str(tmp),
                           str(ceiling_root / f"run-{task}" / "base")],
                          capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    if done.returncode != 0:
        raise SystemExit(f"fixture for {task} was not admitted; not spending on it\n{done.stdout}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    dry = "--dry-run" in argv
    ceilings = ([int(x) for x in argv[argv.index("--ceilings") + 1].split(",")]
                if "--ceilings" in argv else list(CEILINGS))
    plan = sched.load(SCHEDULE)
    python = sys.executable

    print(f"calibration: schedule {plan.schedule_digest[:16]} seed {plan.seed}")
    print(f"ceilings {ceilings}  |  {len(plan.rows)} rows each  |  "
          f"{len(plan.rows) * 3 * len(ceilings)} arm-runs  |  cap ${CAP_USD:.2f}")
    if dry:
        for c in ceilings:
            for row in plan.rows:
                print(f"  [dry] ceiling {c}  {row['task']} attempt {row['attempt']}  "
                      f"order {'->'.join(row['order'])}")
        return 0

    stopped = None
    for ceiling in ceilings:
        root = scratch / f"c{ceiling}"
        config = BENCH / f"calib-config-{ceiling}.json"
        if not config.exists():
            raise SystemExit(f"no config for ceiling {ceiling}: {config}")
        for row in plan.rows:
            task, attempt = row["task"], row["attempt"]
            out = root / f"run-{task}" / f"attempt{attempt}" / "records.json"
            if out.exists():
                print(f"  [skip] ceiling {ceiling} {task}: records.json exists")
                continue

            before = spent(scratch)
            if before >= CAP_USD:
                stopped = (ceiling, task, before)
                break
            print(f"\n=== ceiling {ceiling}  {task} attempt {attempt}  "
                  f"(spent ${before:.2f} of ${CAP_USD:.2f})  "
                  f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")

            if not (root / f"run-{task}" / "base" / task).exists():
                build_fixture_for(task, root, python)

            log = root / f"run-{task}" / f"attempt{attempt}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            # Full output to its own file. The A1 initialization defect sat unread for
            # fourteen reruns because output was piped through `tail -1`.
            # run_arms_isolated takes positional arguments only and resolves its
            # configuration through `A1_CONFIG` (a1_config.load). The ceiling therefore
            # travels in the environment, not in argv -- and it is the ONLY thing that
            # differs between ceilings: same schedule, same prompts, same corpus, same arms.
            env = dict(os.environ, A1_CONFIG=str(config))
            with log.open("w") as fh:
                done = subprocess.run(
                    [python, str(BENCH / "run_arms_isolated.py"), str(root), task,
                     str(attempt), str(SCHEDULE)],
                    stdout=fh, stderr=subprocess.STDOUT, env=env)
            print(f"    exit {done.returncode}  log {log}")

        if stopped:
            break

    total = spent(scratch)
    summary = {"cap_usd": CAP_USD, "spent_usd": round(total, 4),
               "ceilings_requested": ceilings,
               "stopped_at_cap": None if not stopped else
                   {"ceiling": stopped[0], "task": stopped[1], "spent": round(stopped[2], 4)},
               "schedule_digest": plan.schedule_digest, "seed": plan.seed,
               "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (scratch / "calibration-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"\nspent ${total:.2f} of ${CAP_USD:.2f}")
    if stopped:
        print(f"STOPPED AT CAP before ceiling {stopped[0]} {stopped[1]}. The grid is PARTIAL: "
              f"the selection rule may not be applied to it by extrapolation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
