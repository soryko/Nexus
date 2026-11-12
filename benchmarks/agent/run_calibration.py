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

A separately registered sweep drives this same loop with its own `Scope` -- its own threshold,
summary name, configuration locator and vocabulary -- rather than a copy of it. See
`run_a2r.py`. A copied ledger is a second implementation of the one thing whose defects are
invisible from a summary.

Usage:  run_calibration.py <scratch> [--ceilings 30,45,60] [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import identity as ident                                                # noqa: E402
import schedule as sched                                                # noqa: E402

CEILINGS = (30, 45, 60)
CONFIG_VERSION = "calib-v3"   # v1: unrepaired sandbox. v2: `python3` meant a different
                              # interpreter to the agent than to the gate. None are pooled.

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


@dataclass(frozen=True)
class Scope:
    """What a sweep is charged against, what its summary is called, and what it is called.

    The calibration is CLOSED. A separately registered sweep must not be charged against its
    cap, must not overwrite its summary, and must not be described in its vocabulary -- but it
    must use THIS accounting rather than a copy, because a second implementation of the ledger
    is a second set of the defects this one already has tests for.

    `cap_tokens=None` means "read the module-level `CAP_TOKENS` when the sweep runs", which is
    what the calibration does and what lets a test substitute a small cap.

    `soft` changes nothing the driver enforces -- the check is identical, and overshoot by at
    most one row is possible either way. It changes what the summary CALLS the number, so a
    reader is not told a threshold bounded something it cannot bound.
    """
    name: str = "calibration"
    cap_tokens: int | None = None
    # Where this sweep's per-ceiling configuration lives. `None` means the module function,
    # which names `calib-config-<n>.json`. A second sweep MUST NOT share those filenames: the
    # v2 files are the closed calibration's frozen identity, `preflight-a2.py` checks their
    # digests against `LAUNCH-A2.md`, and writing a v3 configuration over one of them would
    # destroy the provenance of a sweep that has already been published.
    config_locator: object = None
    cap_label: str = "cap"
    soft: bool = False
    summary_name: str = "calibration-summary.json"
    partial_note: str = ("The grid is PARTIAL: §5 forbids selecting a ceiling from unequal "
                         "coverage.")

    def cap(self) -> int:
        return CAP_TOKENS if self.cap_tokens is None else self.cap_tokens


CALIBRATION = Scope()

SCHEDULE = BENCH / "schedule-calib-a2.json"
TASKS = BENCH / "tasks-calib-a2.json"
CLONE = "/Users/soko/Cerebros/nexus-a1-fixtures/click"


def config_for(ceiling: int) -> Path:
    """Where this ceiling's configuration lives. Indirected so the deterministic tests can
    point at self-contained temporary configs instead of this host's `calib-config-*.json`,
    which do not exist in CI and carry per-host absolute paths."""
    return BENCH / f"calib-config-{ceiling}.json"


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")


def usage_tokens(u) -> int | None:
    """-> the tokens this usage block accounts for, or None when it accounts for nothing.

    None means UNKNOWN. It never means zero, and it is never a partial sum: a usage block with
    one usable field and one corrupt one is not two thirds of a measurement.

    The emptiness test this replaces was `if not u`, and the runner does not write an empty
    dict. It writes `{k: (envelope usage).get(k) for k in TOKEN_FIELDS}` whether or not the
    envelope carried usage at all, so a missing envelope produces a NON-EMPTY dict of four
    nulls -- which `if not u` admits and `None + None` then raises TypeError on. That is the
    one path that writes calibration-summary.json, so a missing envelope destroyed the
    accounting exactly when the accounting was the thing that mattered.
    """
    if not isinstance(u, dict) or not u:
        return None
    total, measured = 0, False
    for k in TOKEN_FIELDS:
        v = u.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return None
        total += v
        measured = True
    return total if measured else None


# `attempt<n>`, optionally carrying the suffix `preserve_partial` appends when it moves an
# interrupted attempt aside. Both name attempt <n>; only the canonical number is its identity.
ATTEMPT_DIR = re.compile(r"attempt(\d+)(?:\.partial-\d{8}T\d{6}Z)?")


def read_allowance(path: Path) -> tuple[int | None, str | None]:
    """-> (tokens, None) for a usable allowance, (None, why) for one that may not be used.

    An allowance is an ASSUMPTION standing in for a measurement that could not be recovered,
    and it is the only thing that can clear an unresolved arm-run and let the sweep continue.
    That makes it the cheapest way to make the budget say whatever one likes, so it is
    validated like an input and not read like a note: an integer number of tokens that is not
    negative and not a bool, the arm-run it stands for named and matching the directory it
    sits in, and a written basis. `-100` tokens, `true`, and an allowance filed against
    another arm were all accepted before this.
    """
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return None, f"{path}: will not parse ({type(exc).__name__})"
    if (kind := data.get("kind")) != "conservative_allowance":
        return None, (f"{path}: kind {kind!r} is not a conservative_allowance, so it accounts "
                      f"for nothing; the arm-run it sits in is still outstanding")
    v = data.get("tokens_allowance")
    if isinstance(v, bool) or not isinstance(v, int):
        return None, f"{path}: tokens_allowance {v!r} is not an integer number of tokens"
    if v < 0:
        return None, (f"{path}: tokens_allowance {v} is negative; an allowance stands in for "
                      f"consumption, and no run consumed less than nothing")
    # <scratch>/<ceiling>/run-<task>/attempt<n>/arms/<arm>/resolution.json -- where <n> may
    # carry the `.partial-<timestamp>` suffix `preserve_partial` appends. Stripping the word
    # `attempt` and taking the rest read that suffix as part of the attempt NUMBER, so a
    # correctly filed allowance was refused the moment its interrupted attempt was preserved:
    # the two functions disagreed about what a directory name means, and preservation runs
    # first. The directory format is validated rather than the identity check weakened.
    attempt_dir = path.parents[2].name
    if not (m := ATTEMPT_DIR.fullmatch(attempt_dir)):
        return None, f"{path}: {attempt_dir!r} is not an attempt directory"
    run_dir = path.parents[3].name
    if not run_dir.startswith("run-"):
        return None, f"{path}: {run_dir!r} is not a task directory"
    want = {"arm": path.parent.name, "attempt": m.group(1), "task": run_dir[len("run-"):]}
    for field, expected in want.items():
        got = data.get(field)
        if got is None:
            return None, f"{path}: does not say which {field} it stands for"
        if str(got) != expected:
            return None, (f"{path}: filed against {field} {got!r}, but sits in the directory "
                          f"of {field} {expected!r}")
    # The one written by hand carries its basis as a list of lines. Either spelling is a
    # basis; neither an absent one nor a blank one is.
    basis = data.get("basis")
    text = "".join(basis) if isinstance(basis, list) else (basis if isinstance(basis, str) else "")
    if not text.strip():
        return None, f"{path}: states no basis, so the figure is not reviewable"
    return v, None


def consumed(scratch: Path) -> dict:
    """Every token this calibration is KNOWN to have consumed, what it has ASSUMED, and what
    it cannot account for at all. Each arm-run is resolved exactly once.

    Sources, most durable first: each arm's own `record.json`, written the moment that arm
    finishes; the row's `records.json`; the arm's `trace.jsonl`; and a bare `launched.json`,
    which proves an arm-run started and nothing about what it spent. The first source that
    yields a usable usage block is the measurement; the rest are the same arm-run seen again,
    not another one. An arm-run found in none of them with a usable usage block is
    `unresolved` -- it is never counted as zero.

    A written allowance is applied only where no measurement exists. The order used to be the
    other way round and inconsistently applied, so one recovered arm-run was charged
    allowance + measurement and counted as two runs when the measurement came from
    `record.json`, and had its measurement silently DISCARDED when the same measurement came
    from `records.json` or a trace. The same recovered consumption produced three different
    budget decisions depending on which file it was recovered from.

    The glob matches ANY top-level directory, not `c<ceiling>` only: quarantining rows by
    renaming their directory once dropped 6 425 690 consumed tokens from this sum.

    `usd_unprovenanced` is recorded and used for nothing. runner-a1 section 3 established the
    CLI's dollar field has no provenance on this model.
    """
    measured: dict[str, int] = {}          # arm-run -> tokens actually recorded
    started: set[str] = set()              # arm-run with evidence it ran at all
    allowances: dict[str, int] = {}        # arm-run -> assumed tokens, where none were found
    superseded: list[str] = []             # allowance overtaken by a recovered measurement
    rejected: list[str] = []               # allowance that may not be used, and why
    usd = 0.0

    def resolve(armdir: Path, tokens: int | None) -> bool:
        """-> True when this is the first usable measurement for that arm-run."""
        key = str(armdir)
        started.add(key)
        if key in measured or tokens is None:
            return False
        measured[key] = tokens
        return True

    for f in sorted(scratch.glob("*/run-*/attempt*/arms/*/record.json")):
        rec = (json.loads(f.read_text()).get("record") or {})
        if resolve(f.parent, usage_tokens(rec.get("usage"))):
            usd += (rec.get("result") or {}).get("total_cost_usd") or 0.0

    for f in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        for r in json.loads(f.read_text()).get("records", []):
            if resolve(f.parent / "arms" / r.get("arm", "?"), usage_tokens(r.get("usage"))):
                usd += (r.get("result") or {}).get("total_cost_usd") or 0.0

    for tr in sorted(scratch.glob("*/run-*/attempt*/arms/*/trace.jsonl")):
        env = None
        for line in tr.open(errors="replace"):
            if '"type":"result"' in line[:40] or '"type": "result"' in line[:40]:
                env = json.loads(line)
        if resolve(tr.parent, usage_tokens((env or {}).get("usage"))):
            usd += (env or {}).get("total_cost_usd") or 0.0

    # An arm with a launch marker but no terminal accounting CONSUMED AN UNKNOWN AMOUNT. The
    # runner buffers the whole trace until the subprocess returns, so an interruption inside
    # that window leaves the marker and nothing else -- the window that lost k4.
    for mk in sorted(scratch.glob("*/run-*/attempt*/arms/*/launched.json")):
        resolve(mk.parent, None)

    # Assumption last, and only where measurement found nothing. `resolution.json` says what
    # was assumed and on what basis; its tokens are carried in `tokens_allowance`, kept apart
    # from `tokens_known` so that no figure mixes a measurement with an assumption.
    for res in sorted(scratch.glob("*/run-*/attempt*/arms/*/resolution.json")):
        tokens, why = read_allowance(res)
        key = str(res.parent)
        if why:
            # A refused allowance does not merely fail to resolve: it is somebody's statement
            # that an arm-run happened here. It leaves the arm-run OUTSTANDING, which blocks,
            # rather than leaving the accounting silently complete. Refusing one used to make
            # it disappear along with the arm-run it named.
            rejected.append(why)
            started.add(key)
            continue
        started.add(key)
        if key in measured:
            superseded.append(key)     # kept for audit; a measurement is not topped up
        else:
            allowances[key] = tokens

    unresolved = sorted(started - set(measured) - set(allowances))
    known, assumed = sum(measured.values()), sum(allowances.values())
    return {"tokens_known": known,
            "tokens_allowance": assumed,
            "tokens_budgeted": known + assumed,
            "allowance_arm_runs": sorted(allowances),
            "allowance_superseded_arm_runs": sorted(superseded),
            "allowance_rejected": rejected,
            "usd_unprovenanced": round(usd, 4), "arm_runs": len(started),
            "unresolved_arm_runs": unresolved, "unresolved": len(unresolved),
            # What the budget is charged is known exactly. What was CONSUMED is known exactly
            # only when nothing is outstanding and nothing was assumed.
            "consumption_certain": not unresolved and not allowances}


def row_verdicts(records: Path) -> tuple[int, int]:
    """-> (scored, total) arm-runs in a row that completed.

    `terminal_status` already classifies every arm-run, and protocol-a1 §10 EXCLUDES
    `env_fail` and `unknown` from scored sets: a harness, auth or transport fault is not a
    result. The driver never read that verdict. So a row in which no arm reached the model at
    all was a completed row -- it had a records.json, its usage blocks were honest zeros, and
    nothing was unresolved. On 2026-09-14 the forwarder was not running, every one of 36
    arm-runs returned `API Error: Connection refused`, and the sweep reported
    `"stopping_reason": "completed"` and exited 0 having measured nothing.
    """
    recs = json.loads(records.read_text()).get("records", [])
    return sum(1 for r in recs if (r.get("terminal") or {}).get("scored")), len(recs)


def grid_verdicts(scratch: Path) -> dict:
    """What the grid has actually MEASURED, as against what it has written files about."""
    scored = excluded = 0
    barren: list[str] = []
    for rec in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        try:
            s, t = row_verdicts(rec)
        except (ValueError, KeyError):
            continue
        scored += s
        excluded += t - s
        if t and not s:
            barren.append(str(rec.parent))
    return {"scored_arm_runs": scored, "excluded_arm_runs": excluded,
            "rows_with_no_scored_arm_run": barren}


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
        # `usage_tokens`, not an inline sum: the runner's missing-usage record is four nulls
        # in a non-empty dict, and summing it raised TypeError here as well. An arm whose
        # usage is unknown contributes nothing to the largest row OBSERVED -- the row is then
        # a lower bound on itself, which is the honest reading and the conservative one, since
        # `unresolved` blocks the sweep before this figure is used to start anything.
        row = sum(usage_tokens(r.get("usage")) or 0 for r in data.get("records", []))
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


def expected_identity(cfg, task: str, plan, ceiling: int) -> dict:
    """What a row produced by THIS configuration, for THIS task, must record.

    A thin call into `identity.expected`, which `run_identity` in run_arms_isolated also
    calls. There used to be a second implementation here, and it hashed a different thing:
    the producer hashed the RESOLVED configuration, this hashed the raw file, so an unchanged
    configuration produced a row this check then refused -- a legitimate interrupted sweep
    could not be resumed. The prompt digest was required to be present and never compared
    against anything, so a changed prompt at the same filename resumed silently; the
    configuration mismatch was what hid that, by refusing every real row first.
    """
    return ident.expected(cfg, task, plan.schedule_digest, ceiling)


# Re-exported so a reader (and the counterexamples) find the pooling fields where the check is.
IDENTITY_FIELDS = ident.FIELDS


def incompatible(records: Path, cfg, task: str, plan, ceiling: int | None = None) -> str | None:
    """-> a reason this existing row may not be pooled with the current configuration."""
    data = json.loads(records.read_text())
    want = expected_identity(cfg, task, plan,
                             ceiling if ceiling is not None else cfg.max_turns)
    return ident.incompatible(data.get("identity") or {}, want)


def preserve_partial(attempt_dir: Path) -> Path | None:
    """Move an interrupted attempt aside instead of letting the rerun overwrite its traces.

    An absent records.json used to send the row straight back through the same paths, so the
    rerun overwrote the traces of arm-runs that had already consumed. Evidence of spend is not
    something to reclaim disk space with.
    """
    if not attempt_dir.exists() or (attempt_dir / "records.json").exists():
        return None
    # Launch evidence counts, not just traces. An attempt interrupted before its first trace
    # was written used to be left in place and then overwritten by its own rerun, destroying
    # the only record that an arm had been started at all.
    evidence = (any(attempt_dir.glob("arms/*/trace.jsonl"))
                or any(attempt_dir.glob("arms/*/launched.json"))
                or any(attempt_dir.glob("arms/*/record.json"))
                or (attempt_dir / "launch.json").exists())
    if not evidence:
        return None
    dest = attempt_dir.with_name(
        attempt_dir.name + ".partial-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    attempt_dir.rename(dest)
    return dest


def main(argv: list[str], scope: Scope = CALIBRATION) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    dry = "--dry-run" in argv
    ceilings = ([int(x) for x in argv[argv.index("--ceilings") + 1].split(",")]
                if "--ceilings" in argv else list(CEILINGS))
    plan = sched.load(SCHEDULE)
    python = sys.executable
    cap = scope.cap()

    print(f"{scope.name}: schedule {plan.schedule_digest[:16]} seed {plan.seed}")
    print(f"ceilings {ceilings}  |  {len(plan.rows)} rows each  |  "
          f"{len(plan.rows) * 3 * len(ceilings)} arm-runs  |  "
          f"{scope.cap_label} {cap:,} tokens")
    if dry:
        for c in ceilings:
            for row in plan.rows:
                print(f"  [dry] ceiling {c}  {row['task']} attempt {row['attempt']}  "
                      f"order {'->'.join(row['order'])}")
        return 0

    stopped, errored, blocked, instrument = None, False, None, None
    for ceiling in ceilings:
        root = scratch / f"c{ceiling}"
        config = (scope.config_locator or config_for)(ceiling)
        if not config.exists():
            raise SystemExit(f"no config for ceiling {ceiling}: {config}")
        # The filename does not establish the ceiling. Check what the config actually says,
        # and that it declares the configuration version this run is pooling under.
        # Loaded through `a1_config`, not `json.loads`, so the identity this check compares
        # against is built from the same resolved configuration the runner will record --
        # defaults included. Reading the raw file here is what made resume refuse its own
        # unchanged configuration.
        cfg = a1_config.load(config)
        if cfg.max_turns != ceiling:
            raise SystemExit(f"{config.name} declares max_turns={cfg.max_turns}, "
                             f"not the requested grid point {ceiling}")
        if cfg.config_version != CONFIG_VERSION:
            raise SystemExit(f"{config.name} is config_version "
                             f"{cfg.config_version!r}, not {CONFIG_VERSION!r}; "
                             f"rows from different configurations are not pooled")
        for row in plan.rows:
            task, attempt = row["task"], row["attempt"]
            attempt_dir = root / f"run-{task}" / f"attempt{attempt}"
            out = attempt_dir / "records.json"
            if out.exists():
                why = incompatible(out, cfg, task, plan, ceiling)
                if why:
                    raise SystemExit(
                        f"{out} was produced under a different configuration ({why}). "
                        f"Refusing to resume: rows from different configurations are not "
                        f"pooled. Move it aside or run into a fresh scratch directory.")
                scored, total = row_verdicts(out)
                if total and not scored:
                    # Stop through the summary, not out of main(): the same lesson as the
                    # unresolved path. Resuming over this row would treat an instrument
                    # failure as a result, and its identity matches, so nothing else refuses.
                    instrument = (ceiling, task, total)
                    print(f"  [REFUSED] ceiling {ceiling} {task}: records.json exists and NOT "
                          f"ONE of its {total} arm-runs is scored. Move it aside; it is not a "
                          f"completed row.")
                    break
                print(f"  [skip] ceiling {ceiling} {task}: records.json exists, identity "
                      f"matches, {scored}/{total} arm-runs scored")
                continue

            # A row whose records.json is absent but whose arm directories exist was
            # interrupted. Re-running it into the same paths would overwrite its traces --
            # evidence of consumption that already happened. Move it aside instead.
            preserved = preserve_partial(attempt_dir)
            if preserved:
                print(f"  [preserved] partial attempt moved to {preserved.name}")

            before = consumed(scratch)
            if before["unresolved"]:
                # Stop, but stop THROUGH the summary. This used to raise out of main(), so the
                # one state that most needs a durable record -- consumption that cannot be
                # accounted for -- was the one state that wrote no calibration-summary.json.
                refused = ("\n  allowances that may not be used:\n    "
                           + "\n    ".join(before["allowance_rejected"])
                           if before["allowance_rejected"] else "")
                blocked = (
                    f"{before['unresolved']} arm-run(s) have no usable usage record, so "
                    f"consumption so far is only a lower bound and the budget cannot be "
                    f"enforced. Reconcile them or assign a documented allowance before "
                    f"continuing:\n    " + "\n    ".join(before["unresolved_arm_runs"])
                    + refused)
                break
            reserve = row_reserve(scratch, ceiling)
            # PRE-launch: do not START a row without room for one of its size.
            if before["tokens_budgeted"] + reserve > cap:
                stopped = (ceiling, task, before["tokens_budgeted"], reserve)
                break

            # Durable record of the invocation BEFORE it starts, so a row that dies without
            # an envelope is still identifiable as having been launched.
            attempt_dir.mkdir(parents=True, exist_ok=True)
            (attempt_dir / "launch.json").write_text(json.dumps({
                "run_id": f"{CONFIG_VERSION}-c{ceiling}-{task}-a{attempt}-"
                          f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                "config_version": CONFIG_VERSION, "ceiling_requested": ceiling,
                "max_turns_declared": cfg.max_turns,
                "schedule_digest": plan.schedule_digest,
                "corpus_digest": cfg.corpus_digest or None,
                "prompts": str(cfg.bench_path("prompts")),
                "prompt_digest": ident.prompt_digest(cfg, task),
                "config_digest": ident.config_digest(cfg),
                "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, indent=1) + "\n")

            print(f"\n=== ceiling {ceiling}  {task} attempt {attempt}  "
                  f"({before['tokens_budgeted']:,} of {cap:,} tokens charged, "
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
            # A row can exit 0 having measured nothing: `env_fail` is a per-arm terminal
            # classification, not a runner failure. If no arm in the row is scored, the
            # instrument is down -- continuing writes eleven more rows of the same and calls
            # the result `completed`.
            scored, total = row_verdicts(out) if out.exists() else (0, 0)
            print(f"    {scored}/{total} arm-runs scored")
            if total and not scored:
                instrument = (ceiling, task, total)
                print(f"    NOT ONE arm-run in this row is scored: every terminal is "
                      f"excluded (harness, auth or transport). Stopping.")
                break

        # A failed row stops the SWEEP, not just this ceiling. The inner `break` left the
        # outer loop free to start the next ceiling, so one broken runner launched three
        # ceilings in a row and the driver still exited 0.
        if stopped or errored or blocked or instrument:
            break

    total = consumed(scratch)
    verdicts = grid_verdicts(scratch)
    # What the budget is CHARGED is always known: it is measurement plus assumption, and both
    # are recorded. What was CONSUMED is known only when nothing is outstanding and nothing
    # was assumed -- an allowance clears the blocker, it does not establish a fact.
    over = max(0, total["tokens_budgeted"] - cap)
    summary = {
        "scope": scope.name,
        "cap_tokens": cap,
        # A soft threshold is a launch check, not a bound. It is recorded under its own name
        # so nothing downstream reads `cap_tokens` as a maximum that was enforced.
        "cap_is_soft": scope.soft,
        "config_version": CONFIG_VERSION,
        "tokens_known": total["tokens_known"],
        "tokens_allowance": total["tokens_allowance"],
        "tokens_budgeted": total["tokens_budgeted"],
        "allowance_arm_runs": total["allowance_arm_runs"],
        "allowance_superseded_arm_runs": total["allowance_superseded_arm_runs"],
        "allowance_rejected": total["allowance_rejected"],
        "arm_runs": total["arm_runs"],
        # Files written is not the same quantity as measurements taken. A grid can be full of
        # complete, parseable, honestly-zero rows and contain no measurement at all.
        **verdicts,
        "unresolved_arm_runs": total["unresolved_arm_runs"],
        "usd_unprovenanced": total["usd_unprovenanced"],
        "row_reserve_used": {str(c): row_reserve(scratch, c) for c in ceilings},
        # Read off the FINAL ledger, not off `blocked`. `blocked` is set by the NEXT row's
        # pre-launch check, and after the last row of the last ceiling there is no next row --
        # nor is there one when a resume skips every row. An unaccounted sweep therefore
        # reported `completed` and exited 0 in exactly the two cases where nothing downstream
        # would ever look again.
        "stopping_reason": ("gate_or_error" if errored else
                            "unresolved_accounting" if total["unresolved"] else
                            "instrument_fault" if instrument else
                            "budget" if stopped else "completed"),
        "stopped_at": None if not stopped else
            {"ceiling": stopped[0], "task": stopped[1],
             "tokens_before": stopped[2], "row_reserve": stopped[3]},
        # Two different questions, and the second is the one that used to be answered wrongly.
        # By how much does the CHARGE exceed the cap: always answerable, since the charge is
        # measurement plus written allowance. By how much did actual CONSUMPTION exceed it:
        # answerable only when nothing is outstanding and nothing was assumed. An allowance
        # cleared `unresolved`, and `overshoot_certain` read that as certainty about spend.
        "budget_overshoot_tokens": over,
        "overshoot_tokens": (over if total["consumption_certain"] else None),
        "overshoot_certain": total["consumption_certain"],
        "consumption_certain": total["consumption_certain"],
        "schedule_digest": plan.schedule_digest, "seed": plan.seed,
        "written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (scratch / scope.summary_name).write_text(json.dumps(summary, indent=1) + "\n")
    # The summary is written on every path, including failure -- and failure exits nonzero, so
    # a wrapper cannot mistake a broken sweep for a finished one.
    print(f"\ncharged {total['tokens_budgeted']:,} of {cap:,} tokens over "
          f"{total['arm_runs']} arm-runs "
          f"({total['tokens_known']:,} measured + {total['tokens_allowance']:,} allowance)")
    print(f"{verdicts['scored_arm_runs']} arm-run(s) scored, "
          f"{verdicts['excluded_arm_runs']} excluded (harness, auth or transport faults)")
    if total["allowance_arm_runs"]:
        print(f"{len(total['allowance_arm_runs'])} arm-run(s) are carried by a written "
              f"allowance, not a measurement. The charge is exact; consumption is not.")
    for why in total["allowance_rejected"]:
        print(f"ALLOWANCE REFUSED: {why}")
    if total["unresolved"]:
        print(f"UNRESOLVED: {total['unresolved']} arm-run(s) have no usable usage record. "
              f"Total consumption is a LOWER BOUND and overshoot cannot be computed.")
        for u in total["unresolved_arm_runs"]:
            print(f"    {u}")
    elif over:
        print(f"OVERSHOT by {over:,} tokens: a row cannot be interrupted part-way, so "
              f"overshoot by at most one row is possible by design.")
    if stopped:
        print(f"STOPPED AT BUDGET before ceiling {stopped[0]} {stopped[1]}. "
              f"{scope.partial_note}")
    if errored:
        print("A ROW DID NOT COMPLETE. The sweep stopped; no further ceiling was started.")
        return 1
    if blocked:
        print(f"\nSTOPPED BEFORE THE NEXT ROW: {blocked}")
        return 1
    if instrument:
        # Driven by THIS sweep's own refusal, not by the scratch-wide scan. Quarantined rows
        # from a void run stay in the scratch on purpose -- the accounting must keep counting
        # them -- and a historical barren row must not make every later summary say the
        # instrument is down.
        where = f"ceiling {instrument[0]} {instrument[1]}"
        print(f"\nNOTHING WAS MEASURED in at least one row ({where}). Every arm-run there is "
              f"an EXCLUDED terminal -- a harness, auth or transport fault, not a result. "
              f"{verdicts['scored_arm_runs']} arm-run(s) scored, "
              f"{verdicts['excluded_arm_runs']} excluded across the grid. This is NOT a "
              f"completed {scope.name}.")
        return 1
    if total["unresolved"]:
        # Reached when the unaccounted arm-run is in the LAST row, or when a resume skipped
        # every row: no pre-launch check followed it, so nothing set `blocked`. The ledger is
        # the authority on whether this sweep is accounted for, and it is not.
        print(f"\nTHE SWEEP IS NOT ACCOUNTED FOR: it ran to its last row, but the "
              f"arm-run(s) above have no usable usage record. This is NOT a completed "
              f"{scope.name}.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
