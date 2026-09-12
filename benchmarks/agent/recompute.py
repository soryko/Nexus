"""Recompute every reported figure from the saved traces. No model is invoked."""
from __future__ import annotations
import json, sys
from pathlib import Path
from score_compliance import score
from terminal_status import classify

BENCH = Path(__file__).parent
PY_CLICK = sys.argv[1]
RUNS = [("d1", "run-dev-a1-attempt4"), ("d2", "run-dev-d2"),
        ("d3", "run-dev-d3"), ("d4", "run-dev-d4")]
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
            "task": task, "arm": arm,
            "terminal": term["terminal"], "scored": term["scored"],
            "truncated": term["truncated"],
            "functional": (rec["scored"]["passed"] if term["scored"] else None),
            "compliance": c["compliance"], "failed": c["failed"],
            "regression_probe": c["regression_probe"],
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
hdr = f"{'task':4} {'arm':9} {'term':10} {'func':5} {'compl':6} {'orig':18} {'amended':8} {'reg-probe':22} failed"
print(hdr); print("-"*len(hdr))
for r in rows:
    rp = r["regression_probe"]
    probe = ("fails pre-fix" if rp.get("fails_prefix") else
             "PASSES pre-fix" if rp.get("applicable") else f"n/a: {rp.get('reason','')[:16]}")
    print(f"{r['task']:4} {r['arm']:9} {r['terminal']:10} "
          f"{('pass' if r['functional'] else 'fail' if r['functional'] is not None else '-'):5} "
          f"{r['compliance']:6} {r['outcome_original_rule']:18} {r['outcome_amended_rule']:8} "
          f"{probe:22} {','.join(x.replace('_',' ')[:18] for x in r['failed'])}")
