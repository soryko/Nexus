"""Drive the A2 budget calibration: the same 4 tasks and 3 arms at each of 3 turn ceilings.

Registered in `freeze-calib-a2.md`. This runs it and nothing else -- it selects no ceiling,
computes no figure, and reports no contrast between arms. `report_calibration.py` applies the
selection rule to what this produces.

Four things it is responsible for:

  budget     36 000 000 TOKENS, not dollars -- runner-a1 section 3 established the CLI's cost
             field has no provenance on this model. Checked BEFORE a row starts, against the
             largest row yet seen at that ceiling, so a row is never begun without room for
             one of its size. Consumption that cannot be accounted for is `unresolved` and
             BLOCKS continuation; it is never counted as zero.
  identity   every row records product and harness revisions, configuration version and
             digest, prompt and schedule digests, and the ceiling AS APPLIED. On resume an
             existing row whose identity does not match is REFUSED, not skipped.
  order      ceilings ascending, so a budget stop costs the most expensive cell. Within a
             ceiling, rows come from the frozen schedule in its own order.
  isolation  each ceiling writes to its own scratch subtree, and an interrupted attempt is
             moved aside rather than overwritten by its own rerun.

The environment gate is NOT here: it runs inside `run_arms_isolated`, per arm, against the
profile and environment that arm actually uses.

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
    """Every token this calibration is KNOWN to have consumed, plus what it cannot account for.

    Three sources, most durable first: each arm's own `record.json`, written the moment that
    arm finishes; the row's `records.json`; the arm's `trace.jsonl`. An arm found in none of
    them, or found with no usage block, is counted in `unresolved` -- it is never counted as
    zero. The earlier version returned 0 tokens for a trace with no terminal envelope and 0
    tokens for a record with no usage, both silently.

    The glob matches ANY top-level directory, not `c<ceiling>` only: quarantining rows by
    renaming their directory once dropped 6 425 690 consumed tokens from this sum.

    `usd_unprovenanced` is recorded and used for nothing. runner-a1 section 3 established the
    CLI's dollar field has no provenance on this model.
    """
    def toks(u: dict) -> int | None:
        if not u:
            return None
        return (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0) + u.get("output_tokens", 0))

    tokens, usd, runs = 0, 0.0, 0
    unresolved: list[str] = []
    seen: set[Path] = set()

    for f in sorted(scratch.glob("*/run-*/attempt*/arms/*/record.json")):
        data = json.loads(f.read_text())
        t = toks((data.get("record") or {}).get("usage") or {})
        seen.add(f.parent)
        runs += 1
        if t is None:
            unresolved.append(str(f.parent))
        else:
            tokens += t
            usd += ((data.get("record") or {}).get("result") or {}).get("total_cost_usd") or 0.0

    for f in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        data = json.loads(f.read_text())
        for r in data.get("records", []):
            armdir = f.parent / "arms" / r.get("arm", "?")
            if armdir in seen:
                continue
            seen.add(armdir)
            runs += 1
            t = toks(r.get("usage") or {})
            if t is None:
                unresolved.append(str(armdir))
            else:
                tokens += t
                usd += (r.get("result") or {}).get("total_cost_usd") or 0.0

    for tr in sorted(scratch.glob("*/run-*/attempt*/arms/*/trace.jsonl")):
        if tr.parent in seen:
            continue
        seen.add(tr.parent)
        runs += 1
        env = None
        for line in tr.open(errors="replace"):
            if '"type":"result"' in line[:40] or '"type": "result"' in line[:40]:
                env = json.loads(line)
        t = toks((env or {}).get("usage") or {})
        if t is None:
            unresolved.append(str(tr.parent))      # started, no envelope: consumption unknown
        else:
            tokens += t
            usd += (env or {}).get("total_cost_usd") or 0.0

    return {"tokens_known": tokens, "usd_unprovenanced": round(usd, 4), "arm_runs": runs,
            "unresolved_arm_runs": unresolved, "unresolved": len(unresolved)}


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


def incompatible(records: Path, cfg_json: dict, plan) -> str | None:
    """-> a reason this existing row may not be pooled with the current configuration.

    `CONFIG_VERSION` used to appear only at its own definition: the resume path skipped any
    existing records.json without ever asking what produced it, so a v1 row and a v2 row in
    one tree were indistinguishable to the driver that was about to average them.
    """
    data = json.loads(records.read_text())
    ident = data.get("identity") or {}
    checks = [
        ("config_version", ident.get("config_version"), cfg_json.get("config_version")),
        ("max_turns", data.get("max_turns"), cfg_json.get("max_turns")),
        ("schedule_digest", data.get("schedule_digest"), plan.schedule_digest),
        ("corpus_digest", data.get("corpus_digest_registered"), cfg_json.get("corpus_digest")),
    ]
    bad = [f"{k}: recorded {a!r} != requested {b!r}" for k, a, b in checks if a != b]
    if not ident:
        bad.append("no identity block: predates configuration recording")
    return "; ".join(bad) or None


def preserve_partial(attempt_dir: Path) -> Path | None:
    """Move an interrupted attempt aside instead of letting the rerun overwrite its traces.

    An absent records.json used to send the row straight back through the same paths, so the
    rerun overwrote the traces of arm-runs that had already consumed. Evidence of spend is not
    something to reclaim disk space with.
    """
    if not attempt_dir.exists() or (attempt_dir / "records.json").exists():
        return None
    if not any(attempt_dir.glob("arms/*/trace.jsonl")):
        return None
    dest = attempt_dir.with_name(
        attempt_dir.name + ".partial-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    attempt_dir.rename(dest)
    return dest


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

    stopped, errored = None, False
    for ceiling in ceilings:
        root = scratch / f"c{ceiling}"
        config = BENCH / f"calib-config-{ceiling}.json"
        if not config.exists():
            raise SystemExit(f"no config for ceiling {ceiling}: {config}")
        # The filename does not establish the ceiling. Check what the config actually says,
        # and that it declares the configuration version this run is pooling under.
        cfg_json = json.loads(config.read_text())
        if cfg_json.get("max_turns") != ceiling:
            raise SystemExit(f"{config.name} declares max_turns={cfg_json.get('max_turns')}, "
                             f"not the requested grid point {ceiling}")
        if cfg_json.get("config_version") != CONFIG_VERSION:
            raise SystemExit(f"{config.name} is config_version "
                             f"{cfg_json.get('config_version')!r}, not {CONFIG_VERSION!r}; "
                             f"rows from different configurations are not pooled")
        for row in plan.rows:
            task, attempt = row["task"], row["attempt"]
            attempt_dir = root / f"run-{task}" / f"attempt{attempt}"
            out = attempt_dir / "records.json"
            if out.exists():
                why = incompatible(out, cfg_json, plan)
                if why:
                    raise SystemExit(
                        f"{out} was produced under a different configuration ({why}). "
                        f"Refusing to resume: rows from different configurations are not "
                        f"pooled. Move it aside or run into a fresh scratch directory.")
                print(f"  [skip] ceiling {ceiling} {task}: records.json exists, identity matches")
                continue

            # A row whose records.json is absent but whose arm directories exist was
            # interrupted. Re-running it into the same paths would overwrite its traces --
            # evidence of consumption that already happened. Move it aside instead.
            preserved = preserve_partial(attempt_dir)
            if preserved:
                print(f"  [preserved] partial attempt moved to {preserved.name}")

            before = consumed(scratch)
            if before["unresolved"]:
                raise SystemExit(
                    f"{before['unresolved']} arm-run(s) have no usable usage record, so "
                    f"consumption so far is only a lower bound and the budget cannot be "
                    f"enforced. Reconcile them or assign a documented allowance before "
                    f"continuing:\n    " + "\n    ".join(before["unresolved_arm_runs"]))
            reserve = row_reserve(scratch, ceiling)
            # PRE-launch: do not START a row without room for one of its size.
            if before["tokens_known"] + reserve > CAP_TOKENS:
                stopped = (ceiling, task, before["tokens_known"], reserve)
                break

            # Durable record of the invocation BEFORE it starts, so a row that dies without
            # an envelope is still identifiable as having been launched.
            attempt_dir.mkdir(parents=True, exist_ok=True)
            (attempt_dir / "launch.json").write_text(json.dumps({
                "run_id": f"{CONFIG_VERSION}-c{ceiling}-{task}-a{attempt}-"
                          f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                "config_version": CONFIG_VERSION, "ceiling_requested": ceiling,
                "max_turns_declared": cfg_json.get("max_turns"),
                "schedule_digest": plan.schedule_digest,
                "corpus_digest": cfg_json.get("corpus_digest"),
                "prompts": cfg_json.get("prompts"),
                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, indent=1) + "\n")

            print(f"\n=== ceiling {ceiling}  {task} attempt {attempt}  "
                  f"({before['tokens_known']:,} of {CAP_TOKENS:,} tokens known, "
                  f"reserving {reserve:,} for this row)  "
                  f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")

            if not (root / f"run-{task}" / "base" / task).exists():
                build_fixture_for(task, root, python)

            # The environment gate runs inside run_arms_isolated, per arm, against the
            # profile and environment that arm will actually use, and refuses there. A gate
            # here would test a throwaway profile built by this file instead of the real one.

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
            if done.returncode != 0:
                errored = True
                print(f"    row did not complete; stopping rather than spending the next one")
                break

        if stopped:
            break

    total = consumed(scratch)
    over = max(0, total["tokens_known"] - CAP_TOKENS)
    summary = {
        "cap_tokens": CAP_TOKENS,
        "config_version": CONFIG_VERSION,
        "tokens_known": total["tokens_known"],
        "arm_runs": total["arm_runs"],
        "unresolved_arm_runs": total["unresolved_arm_runs"],
        "usd_unprovenanced": total["usd_unprovenanced"],
        "row_reserve_used": {str(c): row_reserve(scratch, c) for c in ceilings},
        "stopping_reason": ("budget" if stopped else
                            "gate_or_error" if errored else "completed"),
        "stopped_at": None if not stopped else
            {"ceiling": stopped[0], "task": stopped[1],
             "tokens_before": stopped[2], "row_reserve": stopped[3]},
        # Overshoot is only a number when consumption is fully accounted. With an unresolved
        # arm-run outstanding the true total is unknown and so is any excess over the cap.
        "overshoot_tokens": (over if not total["unresolved"] else None),
        "overshoot_certain": not total["unresolved"],
        "schedule_digest": plan.schedule_digest, "seed": plan.seed,
        "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (scratch / "calibration-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"\nknown consumption {total['tokens_known']:,} of {CAP_TOKENS:,} tokens over "
          f"{total['arm_runs']} arm-runs")
    if total["unresolved"]:
        print(f"UNRESOLVED: {total['unresolved']} arm-run(s) have no usable usage record. "
              f"Total consumption is a LOWER BOUND and overshoot cannot be computed.")
        for u in total["unresolved_arm_runs"]:
            print(f"    {u}")
    elif over:
        print(f"OVERSHOT by {over:,} tokens: a row cannot be interrupted part-way, so "
              f"overshoot by at most one row is possible by design.")
    if stopped:
        print(f"STOPPED AT BUDGET before ceiling {stopped[0]} {stopped[1]}. The grid is "
              f"PARTIAL: §5 forbids selecting a ceiling from unequal coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
