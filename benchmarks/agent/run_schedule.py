"""Drive the frozen schedule: every row, in order, once.

Step 4. `run_arms_isolated.py` runs ONE task-attempt; the schedule has twelve of them and
each is three arms, so a held-out evaluation is 36 arm-runs and up to six hours. Typing that
out twelve times is twelve chances to transpose a task and an attempt, and a transposition is
not visible in the result -- the records file says what it was told.

What this adds beyond a loop:

  resumable     a row whose `records.json` already exists is skipped, not re-run. A paid run
                that dies at row 8 resumes at row 8 rather than re-spending on seven.
  ordered       rows are taken from the schedule in its own order. No row is selected here.
  preserved     each row's stdout goes to its own file under the attempt directory, whole.
                The initialization defect that went undiagnosed for fourteen reruns was on
                disk the entire time; what hid it was a `tail -1`.
  accounted     a per-row summary is written at the end -- exit status, terminal
                classification per arm, and whether the store digest held -- so the run can
                be read without opening twelve records files.

It does not aggregate results and does not compute a figure. Reporting is `recompute.py` and
`delivered_context.py`, per task and per arm, never collapsed.

Usage:  run_schedule.py <scratch> <schedule.json> [--dry-run] [--only h1,h3] [--config c.json]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import schedule as sched                                                # noqa: E402


def row_output(scratch: Path, task: str, attempt: int) -> Path:
    return scratch / f"run-{task}" / f"attempt{attempt}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    scratch, schedule_path = Path(argv[1]), Path(argv[2])
    dry = "--dry-run" in argv
    only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
    config = argv[argv.index("--config") + 1] if "--config" in argv else None
    cfg = a1_config.load(config).require()

    plan = sched.load(schedule_path)
    rows = [r for r in plan.rows if only is None or r["task"] in only]

    print(f"schedule {schedule_path.name}  digest {plan.schedule_digest[:16]}  seed {plan.seed}")
    print(f"{len(rows)} row(s), {len(rows) * 3} arm-runs")
    if not cfg.corpus_digest:
        print("\nREFUSING: a1-config.json has no corpus_digest. The runner would then accept "
              "whatever store it is pointed at, and the one thing that catches a wrong corpus "
              "is a digest registered before the run. Set it from the freeze record.")
        return 1
    print(f"corpus {cfg.corpus_size} memories, digest {cfg.corpus_digest}, "
          f"master {cfg.store_master}\n")

    done, todo = [], []
    for row in rows:
        out = row_output(scratch, row["task"], row["attempt"])
        (done if (out / "records.json").exists() else todo).append(row)
    for row in done:
        print(f"  [skip] {row['task']} attempt {row['attempt']}: records.json already exists")
    if dry:
        for row in todo:
            print(f"  [would run] {row['task']} attempt {row['attempt']}: "
                  f"{' -> '.join(row['order'])}")
        return 0

    started = datetime.now(timezone.utc)
    summary = []
    for index, row in enumerate(todo, start=1):
        task, attempt = row["task"], row["attempt"]
        out = row_output(scratch, task, attempt)
        out.mkdir(parents=True, exist_ok=True)
        log = out / "run.log"
        print(f"[{index}/{len(todo)}] {task} attempt {attempt} "
              f"({' -> '.join(row['order'])}) -> {log}", flush=True)
        t0 = time.monotonic()
        with log.open("w") as handle:
            done_proc = subprocess.run(
                [sys.executable, str(BENCH / "run_arms_isolated.py"), str(scratch), task,
                 str(attempt), str(schedule_path)],
                stdout=handle, stderr=subprocess.STDOUT)
        elapsed = round(time.monotonic() - t0, 1)
        record = out / "records.json"
        entry = {"task": task, "attempt": attempt, "order": row["order"],
                 "exit": done_proc.returncode, "wall_clock_s": elapsed, "log": str(log)}
        if record.exists():
            data = json.loads(record.read_text())
            entry["arms"] = {r["arm"]: {
                "terminal": (r.get("terminal") or {}).get("verdict"),
                "checks": (r.get("scored") or {}).get("summary"),
                "store_unchanged": (r.get("store") or {}).get("unchanged")}
                for r in data["records"]}
            entry["master_store_unchanged"] = data.get("store_unchanged")
        else:
            entry["arms"] = None
            print(f"      no records.json: the row did not complete. Its log is preserved.",
                  flush=True)
        summary.append(entry)
        print(f"      exit={done_proc.returncode} in {elapsed}s"
              + (f"  {entry['arms']}" if entry["arms"] else ""), flush=True)

    out_path = scratch / "schedule-summary.json"
    out_path.write_text(json.dumps(
        {"schedule": str(schedule_path), "schedule_digest": plan.schedule_digest,
         "seed": plan.seed, "started_utc": started.isoformat(),
         "finished_utc": datetime.now(timezone.utc).isoformat(),
         "config": cfg.as_recorded(),
         "rows_skipped_already_done": [(r["task"], r["attempt"]) for r in done],
         "rows": summary}, indent=1))
    failed = [e for e in summary if e["exit"] != 0 or e["arms"] is None]
    print(f"\nsummary -> {out_path}")
    print(f"{len(summary) - len(failed)}/{len(summary)} rows completed")
    if failed:
        print("rows that did not complete, with their logs:")
        for e in failed:
            print(f"  {e['task']} attempt {e['attempt']}: exit={e['exit']}  {e['log']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
