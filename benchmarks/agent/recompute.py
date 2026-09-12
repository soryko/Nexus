"""Recompute every reported figure from the saved traces. No model is invoked.

The recomputed record keeps EVERY check's verdict, not just the ratio and the failures. It
used to drop `checks`, so `recomputed.json` carried `checks: null` -- which meant a reader
could see `4/5` and had no way to learn whether the sixth check was not applicable to that
arm, unsettled by the trace, or an instrument that did not run. Those are three different
measurements and the ratio is identical under all three. The scorer version is stored beside
them, because a ratio is not comparable across versions.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from score_compliance import score
from terminal_status import classify

BENCH = Path(__file__).parent
PY_CLICK = sys.argv[1]
# `run-dev-d4-isolated` is the enforced-boundary re-run of d4 with the stale advice
# unlabelled. Its scores lived in a separate `compliance-d4-isolated.json` produced by hand
# and were therefore still on the old scorer; it is recomputed here with everything else.
RUNS = [("d1", "run-dev-a1-attempt4"), ("d2", "run-dev-d2"),
        ("d3", "run-dev-d3"), ("d4", "run-dev-d4"),
        ("d4", "run-dev-d4-isolated")]
PRISTINE = {"d1": "d1", "d2": "d2", "d3": "d3", "d4": "d4"}

rows = []
for task, d in RUNS:
    run = BENCH / d
    data = json.loads((run / "records.json").read_text())
    pristine = Path(json.loads((run / "fixtures.json").read_text())[0]["path"])
    for rec in data["records"]:
        arm = rec["arm"]
        term = classify(rec)
        c = score(run, task, arm, pristine, PY_CLICK)
        rows.append({
            "task": task, "run": d, "arm": arm,
            "terminal": term["terminal"], "scored": term["scored"],
            "truncated": term["truncated"],
            "functional": (rec["scored"]["passed"] if term["scored"] else None),
            "scorer_version": c["scorer_version"],
            "checks": c["checks"],
            "compliance": c["compliance"], "failed": c["failed"],
            "passed": c["passed"], "unknown": c["unknown"],
            "not_applicable": c["not_applicable"],
            "unknown_count": c["unknown_count"],
            "not_applicable_count": c["not_applicable_count"],
            "instrument_errors": c["instrument_errors"],
            "regression_probe": c["regression_probe"],
            "consultation": c["consultation"],
            "final_reply": c["final_reply"],
            "trace_health": c["trace_health"],
            "outcome_original_rule": (
                "excluded (env_fail)" if not term["scored"]
                else "fail (truncated)" if term["truncated"]
                else ("pass" if rec["scored"]["passed"] else "fail")),
            "outcome_amended_rule": (
                "excluded (env_fail)" if not term["scored"]
                else ("pass" if rec["scored"]["passed"] else "fail")),
        })
(BENCH / "recomputed.json").write_text(json.dumps(rows, indent=1))
# keep the per-run artifact in step with the recomputation rather than beside it
(BENCH / "compliance-d4-isolated.json").write_text(json.dumps(
    [r for r in rows if r["run"] == "run-dev-d4-isolated"], indent=1))
hdr = (f"{'run':22} {'arm':9} {'term':10} {'func':5} {'compl':6} {'unk':3} {'n/a':3} "
       f"{'ierr':4} {'orig':18} {'amended':8} {'base':8} {'pre-fix':9} {'cand':9} "
       f"{'discr':5} failed")
print(hdr); print("-"*len(hdr))
for r in rows:
    rp = r["regression_probe"]
    if not rp.get("applicable"):
        base = pre = cand = "-"
        disc = f"n/a"
    else:
        base = rp["baseline"]["verdict"][:8]
        pre = rp["prefix"]["verdict"][:9]
        cand = rp["candidate"]["verdict"][:9]
        disc = "yes" if rp.get("discriminates") else "NO"
    print(f"{r['run']:22} {r['arm']:9} {r['terminal']:10} "
          f"{('pass' if r['functional'] else 'fail' if r['functional'] is not None else '-'):5} "
          f"{r['compliance']:6} {r['unknown_count']:<3} {r['not_applicable_count']:<3} "
          f"{len(r['instrument_errors']):<4} "
          f"{r['outcome_original_rule']:18} {r['outcome_amended_rule']:8} "
          f"{base:8} {pre:9} {cand:9} {disc:5} "
          f"{','.join(x.replace('_',' ')[:20] for x in r['failed'])}")
    if not rp.get("applicable"):
        print(f"{'':>60}   probe n/a: {rp.get('reason','')}")
