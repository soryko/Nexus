"""Compute the A2 calibration report from the saved records, and apply `freeze-calib-a2` §5.

No model is invoked, no fixture is built, nothing is re-run. Every figure here comes from
files the sweep already wrote.

`run_calibration.py` names this program in its docstring and has done since before it
existed. It did not exist through the whole of the v2 sweep, so §5 was never applied by
anything; it was applied by hand, in prose, and the prose got it wrong in one specific way:

    a ceiling being ELIGIBLE for the rule is not the ceiling QUALIFYING under it.

§5 has two separate gates, and the second is the one that selects:

  eligibility   all 12 arm-runs at that ceiling present, scored, under one configuration.
                A ceiling with missing cells is *not eligible*, and fewer than two eligible
                ceilings is itself "no ceiling selected" -- a grid of one point cannot show
                that a lower ceiling was insufficient.
  qualification the lowest ELIGIBLE ceiling whose pooled truncation rate over those 12
                arm-runs is <= 20%, provided its correctness is no worse than at any lower
                eligible ceiling. **Two eligible ceilings that both truncate above 20% select
                nothing.** That is the registered outcome, not a gap in it.

Three scopes, kept apart on purpose, because conflating them is what produced a 36 that
should have been 27:

  accounting    every invocation whose consumption belongs to this calibration, INCLUDING
                quarantined and void ones. Delegated whole to `run_calibration.consumed`,
                so there is exactly one implementation of the ledger. A quarantined row's
                tokens were still spent.
  analysis      only cells whose recorded identity matches the launch record. Identity, not
                the directory name: `v1-c30-unrepaired-sandbox` and
                `v2-void-forwarder-down-*` are excluded because their records say so --
                the first records no identity at all, the second a different
                `harness_revision` -- and a directory renamed tomorrow is excluded by the
                same test.
  reconstruction what a trace can be made to say about an arm-run with no terminal envelope.
                Reported by `reconcile_usage.py`, never added to `tokens_known`, and never
                written back into a record.

Usage:  report_calibration.py <scratch> [--launch <LAUNCH-A2.md>] [--json <out.json>]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import run_calibration as RC                                            # noqa: E402

ARMS = ("baseline", "nexus", "notes")
TASKS = ("k1", "k2", "k3", "k4")
CEILINGS = (30, 45, 60)
ARM_RUNS_PER_CEILING = len(TASKS) * len(ARMS)          # 12, as registered in §3
TRUNCATION_THRESHOLD = 0.20                            # §5, chosen before any number existed
DEFAULT_LAUNCH = BENCH.parent.parent / "LAUNCH-A2.md"

# The identity fields a row must match to be poolable with the frozen launch. `max_turns` and
# `config_digest` are per-ceiling and `prompt_digest` per-task, so they are checked against
# the row's own ceiling and task rather than against a single constant.
SHARED_IDENTITY = ("config_version", "product_revision", "harness_revision",
                   "corpus_digest_registered", "schedule_digest")


# --------------------------------------------------------------------------- launch record
def parse_launch_record(path: Path) -> dict:
    """The frozen identity, read from the launch record rather than assumed.

    LAUNCH-A2.md is deliberately outside the measured tree -- `product_revision` and
    `harness_revision` are the last commits touching `src/nexus_memory` and
    `benchmarks/agent`, so a record kept in either would move the identifiers it records
    every time it was written. That makes it the one authority on what a row must match, and
    the reason this reads it instead of hardcoding the digests: a hardcoded copy here would
    be a second authority that could drift from the first in silence.
    """
    text = path.read_text()
    out: dict = {"ceilings": {}, "prompts": {}}

    scalars = {"product_revision": "product_revision", "harness_revision": "harness_revision",
               "config_version": "config_version", "schedule_digest": "schedule_digest",
               "corpus_digest": "corpus_digest_registered"}
    for row in re.finditer(r"^\|\s*`([a-z_]+)`[^|]*\|\s*`([^`]+)`\s*\|\s*$", text, re.M):
        if (field := scalars.get(row.group(1))):
            out[field] = row.group(2)

    for row in re.finditer(r"^\|\s*(\d+)\s*\|\s*`([0-9a-f]+)`\s*\|\s*(\d+)\s*\|\s*$", text, re.M):
        out["ceilings"][int(row.group(1))] = {"config_digest": row.group(2),
                                              "max_turns": int(row.group(3))}

    for row in re.finditer(r"^\|\s*(k\d)\s*\|\s*`([0-9a-f]+)`\s*\|\s*$", text, re.M):
        out["prompts"][row.group(1)] = row.group(2)

    missing = [f for f in (*SHARED_IDENTITY,) if not out.get(f)]
    if missing or not out["ceilings"] or not out["prompts"]:
        raise ValueError(f"{path}: launch record is missing {missing or 'its digest tables'}; "
                         f"it is the authority on identity and cannot be guessed at")
    return out


def identity_mismatch(ident: dict | None, task: str, ceiling: int, launch: dict) -> list[str]:
    """-> the reasons this row may not be pooled with the frozen launch, or []."""
    if not ident:
        return ["records no identity at all"]
    why = [f"{f}={ident.get(f)!r} != {launch[f]!r}"
           for f in SHARED_IDENTITY if ident.get(f) != launch[f]]
    if (exp := launch["ceilings"].get(ceiling)):
        if ident.get("config_digest") != exp["config_digest"]:
            why.append(f"config_digest={ident.get('config_digest')!r} != "
                       f"{exp['config_digest']!r}")
        if ident.get("max_turns_applied") != exp["max_turns"]:
            why.append(f"max_turns_applied={ident.get('max_turns_applied')!r} != "
                       f"{exp['max_turns']}")
    else:
        why.append(f"ceiling {ceiling} is not in the launch record")
    if (exp_prompt := launch["prompts"].get(task)) and ident.get("prompt_digest") != exp_prompt:
        why.append(f"prompt_digest={ident.get('prompt_digest')!r} != {exp_prompt!r}")
    return why


# --------------------------------------------------------------------------- reading cells
def read_cells(scratch: Path, launch: dict) -> tuple[list[dict], list[dict]]:
    """-> (in-scope cells, excluded rows with the reason each was excluded).

    One cell is one (ceiling, task, arm) arm-run. `truncated` and `scored` are read from the
    terminal classification the runner already wrote -- this re-derives neither, because
    `terminal_status.classify` is the registered classifier and a second opinion here would
    be a second classifier.

    `scored` (the functional verdict, with `passed`) and `terminal.scored` (whether the
    arm-run is admissible at all) are different fields with the same name in different
    places. Reading one for the other is why the distinction is spelled out in both the
    closeout and here: a run that terminated cleanly can still have failed every check.
    """
    cells: list[dict] = []
    excluded: list[dict] = []

    for rec in sorted(scratch.glob("*/run-*/attempt*/records.json")):
        try:
            data = json.loads(rec.read_text())
        except ValueError as exc:
            excluded.append({"row": str(rec.parent), "reasons": [f"will not parse: {exc}"]})
            continue

        task = data.get("task")
        ident = data.get("identity")
        ceiling = (ident or {}).get("max_turns_applied") or data.get("max_turns")
        why = identity_mismatch(ident, task, ceiling, launch)
        rows = data.get("records", [])
        if rows and not any((r.get("terminal") or {}).get("scored") for r in rows):
            why.append("no arm-run in the row was scored (row measured nothing)")
        if why:
            excluded.append({"row": str(rec.parent), "task": task, "ceiling": ceiling,
                             "arm_runs": len(rows), "reasons": why})
            continue

        for r in rows:
            term = r.get("terminal") or {}
            func = r.get("scored") or {}
            usage = r.get("usage") or {}
            cells.append({
                "ceiling": ceiling, "task": task, "arm": r.get("arm"),
                "terminal": term.get("terminal"),
                "scored": bool(term.get("scored")),
                "truncated": bool(term.get("truncated")),
                "passed": func.get("passed"),
                "tokens": RC.usage_tokens(usage),
                "usage": {k: usage.get(k) for k in RC.TOKEN_FIELDS},
                "num_turns": (r.get("result") or {}).get("num_turns"),
                "tool_calls": len(r.get("tool_calls") or []),
                "permission_denials": len(r.get("permission_denials") or []),
                "wall_clock_s": r.get("wall_clock_s"),
                "patch_touches_src": r.get("patch_touches_src"),
                "row": str(rec.parent),
            })
    return cells, excluded


# --------------------------------------------------------------------------- §5
def ceiling_table(cells: list[dict]) -> dict:
    """Per-ceiling coverage, terminals, correctness and tokens -- figures only, no verdict."""
    table: dict[int, dict] = {}
    for c in CEILINGS:
        mine = [x for x in cells if x["ceiling"] == c]
        present = {(x["task"], x["arm"]) for x in mine}
        missing = [f"{t}/{a}" for t in TASKS for a in ARMS if (t, a) not in present]
        terminals: dict[str, int] = {}
        for x in mine:
            terminals[x["terminal"]] = terminals.get(x["terminal"], 0) + 1
        known = [x["tokens"] for x in mine if x["tokens"] is not None]
        table[c] = {
            "arm_runs": len(mine),
            "expected": ARM_RUNS_PER_CEILING,
            "complete": len(mine) == ARM_RUNS_PER_CEILING and not missing,
            "missing_cells": missing,
            "scored": sum(1 for x in mine if x["scored"]),
            "terminals": dict(sorted(terminals.items())),
            "truncated": sum(1 for x in mine if x["truncated"]),
            "passed": sum(1 for x in mine if x["passed"] is True),
            "tokens_accounted": sum(known),
            "arm_runs_without_usage": sum(1 for x in mine if x["tokens"] is None),
        }
    return table


def apply_selection_rule(table: dict) -> dict:
    """§5, exactly as registered, against the figures in `table`.

    Deliberately says *why* a ceiling did not win, because the three reasons are different
    outcomes and only one of them is a gap in the measurement:

      ineligible  coverage is incomplete -- more measurement could change this
      fails       complete, measured, and above the threshold -- more measurement of THIS
                  configuration cannot change it; §5 says revise the workload or the agent
      passed over a lower ceiling already qualified
    """
    verdict: dict[int, dict] = {}
    eligible = []
    for c in sorted(CEILINGS):
        row = table.get(c) or {}
        runs, scored = row.get("arm_runs", 0), row.get("scored", 0)
        if not row.get("complete") or scored != ARM_RUNS_PER_CEILING:
            verdict[c] = {"status": "ineligible",
                          "why": (f"coverage {runs}/{ARM_RUNS_PER_CEILING} arm-runs, "
                                  f"{scored} scored; missing "
                                  f"{', '.join(row.get('missing_cells') or []) or 'none'}")}
            continue
        eligible.append(c)
        verdict[c] = {"status": "eligible",
                      "truncation_rate": row["truncated"] / ARM_RUNS_PER_CEILING,
                      "correctness": row["passed"] / ARM_RUNS_PER_CEILING}

    # "If that leaves fewer than two eligible ceilings, the outcome is no ceiling selected:
    # a grid of one point cannot show that a lower ceiling was insufficient."
    if len(eligible) < 2:
        return {"selected": None, "eligible": eligible, "per_ceiling": verdict,
                "reason": (f"fewer than two eligible ceilings ({len(eligible)}); §5 declines "
                           f"to select from a grid that cannot show a lower ceiling "
                           f"insufficient")}

    selected = None
    for c in eligible:
        rate = verdict[c]["truncation_rate"]
        if rate > TRUNCATION_THRESHOLD:
            verdict[c]["status"] = "fails_truncation"
            verdict[c]["why"] = (f"truncation {rate:.1%} over {ARM_RUNS_PER_CEILING} arm-runs "
                                 f"exceeds the registered {TRUNCATION_THRESHOLD:.0%}")
            continue
        # "provided its correctness at that ceiling is no worse than at any lower ceiling."
        # Compared against lower ELIGIBLE ceilings only: a partial ceiling's correctness is
        # over a different number of arm-runs and is not a floor anything can be held to.
        worse = [(lo, verdict[lo]["correctness"]) for lo in eligible
                 if lo < c and verdict[lo]["correctness"] > verdict[c]["correctness"]]
        if worse:
            verdict[c]["status"] = "fails_correctness"
            verdict[c]["why"] = ("correctness "
                                 f"{verdict[c]['correctness']:.1%} is below ceiling "
                                 f"{worse[0][0]}'s {worse[0][1]:.1%}; §5 refuses a ceiling "
                                 f"that buys exploration by lowering tasks solved")
            continue
        verdict[c]["status"] = "selected"
        selected = c
        break

    for c in eligible:
        if selected is not None and c > selected and verdict[c]["status"] == "eligible":
            verdict[c] = {**verdict[c], "status": "not_reached",
                          "why": f"ceiling {selected} qualified first; §5 selects the lowest"}

    reason = ("selected" if selected is not None else
              "no eligible ceiling met the registered truncation threshold; §5: no ceiling "
              "is selected and A2 does not proceed to a held-out registration")
    return {"selected": selected, "eligible": eligible, "per_ceiling": verdict,
            "reason": reason}


# --------------------------------------------------------------------------- report
def build(scratch: Path, launch_path: Path) -> dict:
    launch = parse_launch_record(launch_path)
    cells, excluded = read_cells(scratch, launch)
    table = ceiling_table(cells)
    selection = apply_selection_rule(table)
    ledger = RC.consumed(scratch)        # accounting scope: quarantined rows included
    return {"scratch": str(scratch), "launch_record": str(launch_path),
            "frozen_identity": launch, "cells": cells, "excluded_rows": excluded,
            "per_ceiling": table, "selection": selection, "ledger": ledger,
            "cap_tokens": RC.CAP_TOKENS,
            "budget_overshoot_tokens": max(0, ledger["tokens_budgeted"] - RC.CAP_TOKENS)}


def render(rep: dict) -> str:
    out: list[str] = []
    w = out.append
    table, sel, led = rep["per_ceiling"], rep["selection"], rep["ledger"]

    w("=" * 96)
    w("A2 BUDGET CALIBRATION -- report from the saved records (no model invoked)")
    w("=" * 96)
    w(f"scratch       {rep['scratch']}")
    w(f"launch record {rep['launch_record']}")
    w(f"identity      harness {rep['frozen_identity']['harness_revision'][:12]}  "
      f"product {rep['frozen_identity']['product_revision'][:12]}  "
      f"{rep['frozen_identity']['config_version']}")

    w("\nCOVERAGE AND TERMINALS (analysis scope: identity matches the launch record)")
    w(f"  {'ceiling':<8}{'cells':<9}{'scored':<8}{'truncated':<22}{'correct':<10}"
      f"{'tokens accounted':>18}")
    for c in CEILINGS:
        r = table[c]
        runs = r["arm_runs"]
        cover = f"{runs}/{r['expected']}"
        trunc = f"{r['truncated']}/{runs} ({r['truncated'] / runs:.0%})" if runs else "-"
        right = f"{r['passed']}/{runs}" if runs else "-"
        w(f"  {c:<8}{cover:<9}{r['scored']:<8}{trunc:<22}{right:<10}"
          f"{r['tokens_accounted']:>18,}")
        w(f"           terminals: {r['terminals'] or '{}'}"
          + (f"   missing: {', '.join(r['missing_cells'])}" if r["missing_cells"] else "")
          + (f"   arm-runs with no usage: {r['arm_runs_without_usage']}"
             if r["arm_runs_without_usage"] else ""))

    w("\nSELECTION RULE (freeze-calib-a2 §5, applied unchanged)")
    for c in CEILINGS:
        v = sel["per_ceiling"][c]
        line = f"  ceiling {c}: {v['status'].upper()}"
        if "truncation_rate" in v:
            line += (f"   truncation {v['truncation_rate']:.1%}"
                     f"   correctness {v['correctness']:.1%}")
        w(line)
        if v.get("why"):
            w(f"           {v['why']}")
    w(f"\n  ELIGIBLE: {sel['eligible'] or 'none'}")
    w(f"  SELECTED: {sel['selected'] if sel['selected'] is not None else 'NO CEILING'}")
    w(f"  {sel['reason']}")

    w("\nRESOURCE LEDGER (accounting scope: every invocation, quarantined rows included)")
    w(f"  tokens_known (measured)          {led['tokens_known']:>14,}")
    w(f"  tokens_allowance (assumed)       {led['tokens_allowance']:>14,}")
    w(f"  tokens_budgeted (charge)         {led['tokens_budgeted']:>14,}")
    w(f"  cap                              {rep['cap_tokens']:>14,}")
    w(f"  budget_overshoot_tokens          {rep['budget_overshoot_tokens']:>14,}")
    w(f"  arm-runs with any evidence       {led['arm_runs']:>14,}")
    w(f"  unresolved arm-runs              {led['unresolved']:>14,}")
    for u in led["unresolved_arm_runs"]:
        w(f"      {u}")
    w(f"  consumption_certain              {str(led['consumption_certain']):>14}")
    if not led["consumption_certain"]:
        w("      overshoot of CONSUMPTION is not computable while an allowance is carried or")
        w("      an arm-run is unresolved. The charge above is exact; consumption is not.")

    w("\nEXCLUDED FROM ANALYSIS (still counted in the ledger above)")
    for e in rep["excluded_rows"]:
        w(f"  {Path(e['row']).relative_to(rep['scratch'])}  ({e.get('arm_runs', '?')} arm-runs)")
        for r in e["reasons"]:
            w(f"      {r}")
    w("=" * 96)
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    scratch = Path(argv[1])
    launch = Path(argv[argv.index("--launch") + 1]) if "--launch" in argv else DEFAULT_LAUNCH
    rep = build(scratch, launch)
    print(render(rep))
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"\nfull record -> {out}")
    # A report that selected nothing is a successful report of a negative result, not a
    # failure of the reporter. Exit 0 either way; the verdict is in the text.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
