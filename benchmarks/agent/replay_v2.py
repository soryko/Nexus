"""Replay the A2-R reporter over the 27 ACCEPTED v2 arm-runs, as an integration control.

This is not an experiment and spends nothing. It exists because `assess_a2r.py` was written
against a record shape no runner produces -- `record["functional"]`, `record["num_turns"]`,
`record["patch"]` -- and every test agreed with the reader because every fixture was invented
by the same hand. Replaying over rows the runner actually wrote is the one check that could
not agree by construction.

SCOPED DELIBERATELY. Only the three current-sweep ceiling directories are read. The scratch
also holds `v1-c30-unrepaired-sandbox/` and three `v2-void-forwarder-down-c*/` quarantines;
those arm-runs stay in the BUDGET, and they are not evidence about anything, so they are not
in this replay. The expected numbers below come from `CLOSEOUT-calib-a2.md` and
`DIAGNOSTIC-calib-a2-workflow.md`, both computed by other code from the same records.

What v2 CANNOT show, and must come back unknown rather than clean: `runtime_identity` did not
exist in v2, so every arm-run is `no_relevant_invocation` or `other_interpreter_only` and none
is evidence about the repair. `a1-scorer-4` was never run over this sweep, so compliance is
`not_scored`.

    python3 replay_v2.py [<scratch>]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import assess_a2r as A                                                  # noqa: E402

DEFAULT = Path("/Users/soko/Cerebros/nexus-a1-fixtures/calib-run")
CURRENT = ("c30", "c45", "c60")          # the accepted sweep; quarantines excluded by name

EXPECTED = {
    "arm_runs": 27,
    "functional_passes": 16,
    "passing_and_truncated_at_max_turns": 11,
    "repair_evidence_available": 0,      # v2 has no pinned interpreter to observe
}


def replay(scratch: Path) -> dict:
    """Each accepted ceiling read by NAME, never by globbing the scratch.

    `read_cells` globs `*/run-*/attempt*/records.json`, which is right for A2-R's own output
    directory and wrong here: this scratch also holds `v1-c30-unrepaired-sandbox/` and three
    `v2-void-forwarder-down-c*/` trees, and a glob would silently pool 45 quarantined arm-runs
    into a 27-arm-run control.
    """
    cells = []
    compliance = A.read_compliance(scratch)
    for ceiling in CURRENT:
        for records in sorted((scratch / ceiling).glob("run-*/attempt*/records.json")):
            data = json.loads(records.read_text())
            task = data.get("task") or records.parent.parent.name.replace("run-", "")
            for rec in data.get("records", []):
                arm = rec.get("arm") or "?"
                n = A.normalise(rec, records.parent / "arms" / arm, compliance, task, arm)
                cells.append({"ceiling": int(re.sub(r"\D", "", ceiling)), "task": task,
                              "arm": arm, **n,
                              "repair": A.repair_check(n, n["tool_calls"]),
                              "requirement": A.requirement_compliance(n, n["tool_calls"])})
    return {"scratch": str(scratch), "cells": cells}


def main(argv: list[str]) -> int:
    scratch = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not scratch.is_dir():
        raise SystemExit(f"no scratch at {scratch}")
    rep = replay(scratch)
    cells = rep["cells"]

    passes = [c for c in cells if c["functional"] == "pass"]
    fails = [c for c in cells if c["functional"] == "fail"]
    unmeasured = [c for c in cells if c["functional"] == A.UNKNOWN]
    trunc_passes = [c for c in passes if c["terminal"] == "max_turns"]
    with_patch = [c for c in cells if c["patch"] is not None]
    src_diff = [c for c in cells if c["requirement"]["source_diff"] == "yes"]
    repair_evidence = [c for c in cells if c["repair"]["relevant_invocation"]
                       and c["repair"]["pinned_invocations"]]
    not_scored = [c for c in cells if c["scorer_a1_4"] == A.NOT_SCORED]

    got = {"arm_runs": len(cells),
           "functional_passes": len(passes),
           "passing_and_truncated_at_max_turns": len(trunc_passes),
           "repair_evidence_available": len(repair_evidence)}

    print(f"replay of {scratch}")
    print(f"scoped to {', '.join(CURRENT)} — quarantined trees excluded, and they stay in "
          f"the budget\n")
    bad = []
    for k, want in EXPECTED.items():
        ok = got[k] == want
        print(f"  {'ok ' if ok else 'BAD'} {k:38s} {got[k]} (expected {want})")
        if not ok:
            bad.append(f"{k}: {got[k]} != {want}")

    print(f"\n      {'functional fails':38s} {len(fails)}")
    print(f"      {'functional unmeasured':38s} {len(unmeasured)}")
    print(f"      {'patch sidecar readable':38s} {len(with_patch)} of {len(cells)}")
    print(f"      {'final patch touches src/':38s} {len(src_diff)}")
    print(f"      {'a1-scorer-4 not_scored':38s} {len(not_scored)} of {len(cells)}")

    prov: dict[str, int] = {}
    for c in cells:
        prov[c["patch_provenance"]] = prov.get(c["patch_provenance"], 0) + 1
    print(f"      {'patch provenance':38s} "
          + ", ".join(f"{k} {v}" for k, v in sorted(prov.items())))
    states: dict[str, int] = {}
    for c in cells:
        states[c["repair"]["state"]] = states.get(c["repair"]["state"], 0) + 1
    print(f"      {'repair states (v2: none informative)':38s} "
          + ", ".join(f"{k} {v}" for k, v in sorted(states.items())))

    if len(passes) != EXPECTED["functional_passes"]:
        print("\nper-cell functional verdicts:")
        for c in cells:
            print(f"    c{c['ceiling']}/{c['task']}/{c['arm']:9} {c['functional']}")

    print()
    if bad:
        print("REPLAY FAILED — the reporter does not recover what other code computed from "
              "the same records:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("REPLAY OK — the reporter recovers the closeout's figures from the runner's own "
          "saved format.")
    print("v2 establishes nothing about the repair: it had no pinned interpreter to observe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
