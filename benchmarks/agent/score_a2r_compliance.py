"""Run `a1-scorer-4` over a calibration-layout sweep and write the artifact the reporter reads.

`run_arms_isolated` does not score requirement compliance -- it is a separate offline pass --
and until now there was no way to run that pass over a sweep in this layout. `recompute.py`
drives the same scorer but is hard-wired to A1's development runs: fixed task list, fixed run
directories, fixed output path. So step 4 of the launch plan, "run the separate compliance
scorer", had no path at all, and `assess_a2r.py` would have reported `not_scored` forever.

Model-free. It re-reads saved patches and traces and runs the registered regression probe
against each task's own pristine tree. Nothing is invoked and nothing is spent.

Two things it refuses rather than guesses:

  a missing pristine tree   `regression_discriminates` needs the task's unmodified checkout.
                            In this layout it lives at `run-<task>/base/<task>`, recorded in
                            `run-<task>/base/fixtures.json`, INSIDE the sweep's own scratch --
                            unlike A1's, whose `fixtures.json` names a path in a scratchpad
                            that no longer exists, which is why that suite's E1 check comes
                            back as an instrument error. If it is gone, the row says so.
  writing into another      `--out` defaults to the sweep's own root. The closed calibration's
  sweep's directory         artifacts are not to be added to; validate against it with an
                            explicit `--out` elsewhere.

Every check's verdict is kept, not just the ratio: 4/5 with one not-applicable, one unsettled,
and one instrument failure are three different measurements and the ratio is identical under
all three.

    python3 score_a2r_compliance.py <scratch> [--out <dir>] [--ceilings 45] [--python <exe>]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
# The artifact's field names come from the READER, so a rename cannot leave the writer
# emitting a key nothing looks for. That is the same class of defect as reading a record
# shape no runner writes, and it is cheaper to make impossible than to test for.
from assess_a2r import (COMPLIANCE_FILES, COMPLIANCE_RATIO,             # noqa: E402
                        COMPLIANCE_UNKNOWN)
from score_compliance import SCORER_VERSION, score                      # noqa: E402
from terminal_status import classify                                    # noqa: E402


def pristine_for(run_dir: Path, task: str) -> tuple[Path | None, str]:
    """-> (the task's unmodified checkout, why not).

    `run_dir` is `<scratch>/c<n>/run-<task>/attempt<n>`; the fixture sits two levels up under
    `base/`, where `build_fixture_for` put it.
    """
    base = run_dir.parent / "base"
    spec = base / "fixtures.json"
    if not spec.exists():
        return None, f"no fixtures.json at {spec}"
    try:
        rows = json.loads(spec.read_text())
    except (ValueError, OSError) as exc:
        return None, f"{spec}: {type(exc).__name__}"
    for row in rows if isinstance(rows, list) else []:
        if row.get("task") != task:
            continue
        p = Path(row.get("path", ""))
        if not p.is_dir():
            return None, f"the recorded pristine tree is gone: {p}"
        if not (p / ".git").exists():
            return None, f"{p} is not a git checkout"
        return p, ""
    return None, f"{spec} records no fixture for {task}"


def run(scratch: Path, ceilings: list[int] | None, python: str,
        notes_file: Path | None = None) -> list[dict]:
    # Resolved once, from the same configuration field `run_arms_isolated` seeded the notes
    # arm from -- not from a constant inside the scorer, which is how P2 came to be read
    # against the development rendering for four held-out notes arms.
    notes_file = notes_file or a1_config.load().bench_path("notes_file")
    rows: list[dict] = []
    pattern = "*/run-*/attempt*/records.json"
    for records in sorted(scratch.glob(pattern)):
        ceiling = int(re.sub(r"\D", "", records.parents[2].name) or 0)
        if ceilings and ceiling not in ceilings:
            continue
        data = json.loads(records.read_text())
        task = data.get("task") or records.parent.parent.name.replace("run-", "")
        run_dir = records.parent
        pristine, why = pristine_for(run_dir, task)
        for rec in data.get("records", []):
            arm = rec.get("arm") or "?"
            term = classify(rec)
            row = {"ceiling": ceiling, "task": task, "arm": arm,
                   "run_dir": str(run_dir),
                   "terminal": term["terminal"], "scored": term["scored"],
                   "truncated": term["truncated"],
                   "functional": (rec.get("scored") or {}).get("passed"),
                   "scorer_version": SCORER_VERSION}
            if pristine is None:
                # NOT a compliance failure. The scorer could not run, and says which part of
                # the instrument was missing.
                row.update({COMPLIANCE_RATIO: None, COMPLIANCE_UNKNOWN: None,
                            "scorer_ran": False, "why_not": why})
                rows.append(row)
                print(f"  {task}/{arm:9} SCORER DID NOT RUN: {why}")
                continue
            try:
                c = score(run_dir, task, arm, pristine, python, notes_file=notes_file)
            except Exception as exc:                       # the scorer, not the arm-run
                row.update({COMPLIANCE_RATIO: None, COMPLIANCE_UNKNOWN: None,
                            "scorer_ran": False,
                            "why_not": f"{type(exc).__name__}: {exc}"})
                rows.append(row)
                print(f"  {task}/{arm:9} SCORER ERROR: {type(exc).__name__}: {exc}")
                continue
            row.update({
                "scorer_ran": True,
                # The name `assess_a2r.read_compliance` reads, and the ratio `tally` computes
                # over the SETTLED checks only.
                COMPLIANCE_RATIO: c["compliance"],
                COMPLIANCE_UNKNOWN: c["unknown_count"],
                "not_applicable_count": c["not_applicable_count"],
                "passed": c["passed"], "failed": c["failed"],
                "unknown": c["unknown"], "not_applicable": c["not_applicable"],
                "instrument_errors": c["instrument_errors"],
                # Kept in full: a ratio without its per-check verdicts cannot be re-read.
                "checks": c["checks"],
                "regression_probe": c["regression_probe"],
                "unsettled_by_machine": c["unsettled_by_machine"],
            })
            rows.append(row)
            errs = c["instrument_errors"]
            flag = f"  (instrument: {', '.join(errs)})" if errs else ""
            ratio = c["compliance"]
            unknown = c["unknown_count"]
            na = c["not_applicable_count"]
            print(f"  {task}/{arm:9} {ratio:>5}  unknown {unknown}  n/a {na}{flag}")
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    if not scratch.is_dir():
        raise SystemExit(f"no scratch at {scratch}")
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else scratch
    ceilings = ([int(x) for x in argv[argv.index("--ceilings") + 1].split(",")]
                if "--ceilings" in argv else None)
    python = (argv[argv.index("--python") + 1] if "--python" in argv
              else a1_config.load().pytest_python)

    print(f"requirement compliance scorer {SCORER_VERSION} over {scratch}")
    print(f"  regression probe interpreter: {python}")
    rows = run(scratch, ceilings, python)
    if not rows:
        raise SystemExit("no arm-runs found; nothing scored")

    out.mkdir(parents=True, exist_ok=True)
    dest = out / COMPLIANCE_FILES[0]
    dest.write_text(json.dumps(rows, indent=1) + "\n")

    ran = [r for r in rows if r["scorer_ran"]]
    print(f"\n{len(ran)} of {len(rows)} arm-run(s) scored -> {dest}")
    if len(ran) != len(rows):
        print("Rows the scorer could not run on are recorded with `scorer_ran: false` and a "
              "reason. They are NOT compliance failures.")
    instr = [r for r in ran if r.get("instrument_errors")]
    if instr:
        print(f"{len(instr)} scored row(s) carry an instrument error; their ratio is over "
              f"fewer settled checks, which is why the unknown count travels with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
