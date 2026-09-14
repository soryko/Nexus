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
import verify_arm_environment as VENV                                   # noqa: E402

CEILINGS = (30, 45, 60)
CONFIG_VERSION = "calib-v2"   # v1 ran against the unrepaired sandbox; the two are not pooled

# The budget is in TOKENS, not dollars. runner-a1 section 3 found "costBasis":"unknown" on this
# model and concluded the CLI's dollar field has no established provenance, so A1 quotes no
# cost and neither does this. Token counts come from the provider and are valid accounting.
#
# Scale: A1's 36 arm-runs at ceiling 30 consumed 20 050 240 input+cache-read tokens. This grid
# is 36 arm-runs across ceilings 30/45/60, where a higher ceiling consumes more per run because
# a truncated run spends its whole budget. 36 000 000 is that scale with headroom, and
# corresponds to the same amount of work the spending decision approved.
CAP_TOKENS = 36_000_000

# What one row (3 arm-runs) may consume before the cap is reached, so a row is not STARTED with
# too little allowance left. Seeded from A1's largest row and replaced by measurement as soon
# as this grid has produced a row at that ceiling.
INITIAL_ROW_RESERVE = 2_500_000
SCHEDULE = BENCH / "schedule-calib-a2.json"
TASKS = BENCH / "tasks-calib-a2.json"
CLONE = "/Users/soko/Cerebros/nexus-a1-fixtures/click"


def consumed(scratch: Path) -> dict:
    """Every token this calibration has consumed, read from what it saved.

    Counted from `records.json` where a row completed, and from the per-arm `trace.jsonl`
    where it did not -- an aborted or failed row still spent, and counting only completed rows
    would make the budget look smaller than it is. The dollar field is recorded for the record
    and is NOT used for enforcement.

    A file that cannot be read raises rather than counting as zero: a budget enforced on a
    silently-partial sum is not a budget.
    """
    # The glob matches ANY top-level directory, not `c<ceiling>` only. Quarantining the v1
    # rows by renaming their directory once made this sum silently drop 6 425 690 consumed
    # tokens -- a budget that stops counting when a directory is renamed is not a budget.
    tokens, usd, runs, from_trace = 0, 0.0, 0, 0
    counted: set[Path] = set()
    for rec in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(rec.read_text())
        for r in data["records"]:
            u = r.get("usage") or {}
            tokens += (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0) + u.get("output_tokens", 0))
            usd += (r.get("result") or {}).get("total_cost_usd") or 0.0
            runs += 1
            counted.add(rec.parent / "arms" / r["arm"] / "trace.jsonl")
    # Arm-runs whose row never completed: their tokens were still spent.
    for tr in sorted(scratch.glob("*/run-*/attempt*/arms/*/trace.jsonl")):
        if tr in counted:
            continue
        env = None
        for line in tr.open(errors="replace"):
            if line.startswith('{"type":"result"') or '"type": "result"' in line[:40]:
                env = json.loads(line)
        if not env:
            continue
        u = env.get("usage") or {}
        tokens += (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0) + u.get("output_tokens", 0))
        usd += env.get("total_cost_usd") or 0.0
        runs += 1
        from_trace += 1
    return {"tokens": tokens, "usd_unprovenanced": round(usd, 4), "arm_runs": runs,
            "from_incomplete_rows": from_trace}


def row_reserve(scratch: Path, ceiling: int) -> int:
    """The largest row observed at this ceiling, or the seed if none has run yet.

    This is what makes the budget a PRE-launch check rather than a post-hoc one. It is still
    not a hard ceiling on spend: an arm-run cannot be interrupted part-way, and tokens per turn
    are not bounded by `--max-turns`, so a single row can carry the total past the cap.
    Overshoot by at most one row is possible BY DESIGN and is recorded when it happens.
    """
    best = 0
    for rec in sorted(scratch.glob(f"c{ceiling}/run-*/attempt*/records.json")):
        data = json.loads(rec.read_text())
        row = sum((r.get("usage") or {}).get("input_tokens", 0)
                  + (r.get("usage") or {}).get("cache_read_input_tokens", 0)
                  + (r.get("usage") or {}).get("cache_creation_input_tokens", 0)
                  + (r.get("usage") or {}).get("output_tokens", 0)
                  for r in data["records"])
        best = max(best, row)
    return best or INITIAL_ROW_RESERVE


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


def _gate_arm(root: Path, task: str, attempt: int, config: Path, python: str) -> dict:
    """Build one throwaway baseline arm from this row's fixture and probe it. No model runs."""
    import shutil
    import a1_config
    import isolation
    base = root / f"run-{task}" / "base"
    arm = root / f"run-{task}" / f"attempt{attempt}" / "arms" / "_envcheck"
    if arm.exists():
        shutil.rmtree(arm)
    arm.mkdir(parents=True)
    shutil.copytree(base / task, arm / "repo", symlinks=True)
    cfg = a1_config.load(str(config)).require()
    deny = [base / "checks", Path(cfg.source_clone), BENCH]
    isolation.write_profile(arm / "sandbox.sb", arm / "repo", deny,
                            forwarder_port=cfg.forwarder_port)
    report = VENV.run(arm)
    shutil.rmtree(arm, ignore_errors=True)
    return report


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
          f"{len(plan.rows) * 3 * len(ceilings)} arm-runs  |  cap {CAP_TOKENS:,} tokens")
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

            before = consumed(scratch)
            reserve = row_reserve(scratch, ceiling)
            # PRE-launch: do not START a row without room for one of its size.
            if before["tokens"] + reserve > CAP_TOKENS:
                stopped = (ceiling, task, before["tokens"], reserve)
                break
            print(f"\n=== ceiling {ceiling}  {task} attempt {attempt}  "
                  f"({before['tokens']:,} of {CAP_TOKENS:,} tokens, "
                  f"reserving {reserve:,} for this row)  "
                  f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")

            if not (root / f"run-{task}" / "base" / task).exists():
                build_fixture_for(task, root, python)

            # The environment gate. A1 and the v1 rows were measured in a sandbox where
            # here-documents and the documented interpreter invocation silently did not work,
            # and nothing was looking: those failures appear in tool OUTPUT, not in
            # `permission_denials`. A gate that runs after the spend is a post-mortem, so this
            # runs before it, on this row's own fixture, and refuses rather than warns.
            gate = _gate_arm(root, task, attempt, config, python)
            (root / f"run-{task}" / f"attempt{attempt}-envcheck.json").write_text(
                json.dumps(gate, indent=1) + "\n")
            if not gate["all_passed"]:
                failed = [c["check"] for c in gate["checks"] if not c["passed"]]
                raise SystemExit(
                    f"environment gate FAILED for {task} at ceiling {ceiling}: {failed}\n"
                    f"  Not spending. Repair the environment and version the configuration; "
                    f"do not pool rows across configurations.")

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
