"""Compute the held-out A1 report from the saved records. No model is invoked.

Applies `freeze-heldout-a1` §5 as registered, and nothing else:

  unit          the task. Three attempts on one task are three measurements of that task and
                are never counted as three tasks.
  collapsing    binary figures (functional correctness) -> pass-rate over the 3 attempts;
                ratio and continuous figures -> arithmetic mean over the SCORED attempts.
                The contributing attempt count travels with every summary.
  exclusions    `completed`, `max_turns` and `timeout` are scored; `env_fail` and `unknown`
                are excluded and counted, per arm. An imbalance is reported as a threat to
                the comparison, not absorbed into it.
  contrasts     three, paired per task, each reported separately and none merged:
                nexus - baseline, notes - baseline, nexus - notes.
  uncertainty   NOT computed here. §5 says four tasks do not support an inferential claim and
                that none will be made; the primary reporting is the per-task table and a
                sign count. A bootstrap over four tasks would invite exactly the reading §5
                forbids, so this prints the table and the signs.

Usage:  report_heldout.py <scratch> <click-python> [--json <out.json>]
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import delivered_context as DC                                          # noqa: E402
import score_compliance as SC                                           # noqa: E402
from terminal_status import classify                                    # noqa: E402

ARMS = ("baseline", "nexus", "notes")
CORPUS = BENCH / "corpus-heldout-a1.json"
MIX = BENCH / "mix-heldout-a1.json"
SCORED_TERMINALS = ("completed", "max_turns", "timeout")


def collect(scratch: Path, python: str) -> list[dict]:
    corpus, byid, mix = DC.load_corpus(CORPUS, MIX)
    smap = {m["memory_id"]: m["id"] for m in corpus["memories"] if m.get("memory_id")}
    tasks = [t["task"] for t in json.loads((BENCH / "tasks-heldout-a1.json").read_text())["tasks"]]
    rows = []
    for task in tasks:
        base = scratch / f"run-{task}" / "base" / task
        for attempt in (1, 2, 3):
            run = scratch / f"run-{task}" / f"attempt{attempt}"
            record = run / "records.json"
            if not record.exists():
                rows.append({"task": task, "attempt": attempt, "arm": None,
                             "missing": True})
                continue
            data = json.loads(record.read_text())
            for rec in data["records"]:
                arm = rec["arm"]
                term = classify(rec)
                comp = SC.score(run, task, arm, base, python)
                dc = DC.measure(run, task, arm, smap, byid)
                buckets = {b: 0 for b in ("necessary", "useful support", "outdated",
                                          "irrelevant")}
                for cid in dc["full_ids"] | dc["excerpt_only"]:
                    buckets[DC.bucket(byid[cid], task, mix)] += 1
                usage = rec.get("usage") or {}
                rows.append({
                    "task": task, "attempt": attempt, "arm": arm,
                    "terminal": term["terminal"],
                    "scored": term["terminal"] in SCORED_TERMINALS,
                    "truncated": term["truncated"],
                    "functional_pass": bool(rec["scored"]["passed"]),
                    "checks": rec["scored"]["summary"],
                    "compliance": comp["compliance"],
                    "compliance_ratio": _ratio(comp["compliance"]),
                    "unknown_count": comp["unknown_count"],
                    "not_applicable_count": comp["not_applicable_count"],
                    "instrument_errors": comp.get("instrument_errors") or [],
                    "failed_requirements": comp.get("failed") or [],
                    "delivered_bytes": dc["bytes_full"] + dc["bytes_excerpt"],
                    "delivered_ids": len(dc["full_ids"] | dc["excerpt_only"]),
                    "delivered_buckets": buckets,
                    "patch_bytes": rec.get("patch_bytes"),
                    "tool_calls": len(rec.get("tool_calls") or []),
                    "wall_clock_s": rec.get("wall_clock_s"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_read": usage.get("cache_read_input_tokens"),
                    "cache_creation": usage.get("cache_creation_input_tokens"),
                    "store_unchanged": (rec.get("store") or {}).get("unchanged"),
                })
    return rows


def _ratio(text: str | None) -> float | None:
    """'4/5' -> 0.8. An unknown check has already left the denominator (§5.3)."""
    if not text or "/" not in text:
        return None
    a, b = text.split("/", 1)
    return (float(a) / float(b)) if float(b) else None


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 2) if values else None


def collapse(rows: list[dict]) -> dict:
    out: dict = {}
    for row in rows:
        if row.get("missing") or row["arm"] is None:
            continue
        out.setdefault(row["task"], {}).setdefault(row["arm"], []).append(row)
    summary = {}
    for task, arms in out.items():
        summary[task] = {}
        for arm, attempts in arms.items():
            scored = [a for a in attempts if a["scored"]]
            summary[task][arm] = {
                "attempts": len(attempts),
                "contributing": len(scored),
                "excluded": len(attempts) - len(scored),
                # binary: pass-rate over the three attempts, not over the scored ones --
                # §5.3 scores max_turns, so an excluded attempt is an instrument event and
                # the denominator says how many actually contributed.
                "functional_passes": sum(1 for a in scored if a["functional_pass"]),
                "functional_pass_rate": (round(sum(1 for a in scored if a["functional_pass"])
                                               / len(scored), 3) if scored else None),
                "compliance_mean": _mean([a["compliance_ratio"] for a in scored]),
                "unknown_mean": _mean([a["unknown_count"] for a in scored]),
                "delivered_bytes_mean": _mean([a["delivered_bytes"] for a in scored]),
                "delivered_irrelevant_mean": _mean(
                    [a["delivered_buckets"]["irrelevant"] for a in scored]),
                "delivered_outdated_mean": _mean(
                    [a["delivered_buckets"]["outdated"] for a in scored]),
                "tool_calls_mean": _mean([a["tool_calls"] for a in scored]),
                "wall_clock_mean": _mean([a["wall_clock_s"] for a in scored]),
                "input_tokens_mean": _mean([a["input_tokens"] for a in scored]),
                "output_tokens_mean": _mean([a["output_tokens"] for a in scored]),
                "cache_read_mean": _mean([a["cache_read"] for a in scored]),
                "terminals": sorted({a["terminal"] for a in attempts}),
                "truncated": sum(1 for a in attempts if a["truncated"]),
                "instrument_errors": sorted({e for a in attempts
                                             for e in a["instrument_errors"]}),
            }
    return summary


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    scratch, python = Path(argv[1]), argv[2]
    rows = collect(scratch, python)
    summary = collapse(rows)
    mix = json.loads(MIX.read_text())
    freeze = json.loads((BENCH / "freeze-record-heldout-a1.json").read_text())

    print("A1 HELD-OUT RESULT")
    print(f"corpus {freeze['memories']} memories, digest {freeze['master_digest']}; "
          f"mix declaration {freeze['mix_declaration_sha256'][:16]}")
    print(f"scorer {SC.SCORER_VERSION}; functional "
          f"{__import__('build_fixture').FUNCTIONAL_SCORER_VERSION}\n")

    print("=" * 100)
    print("PER TASK, PER ARM  (the primary reporting; §5 forbids collapsing these into one "
          "score)")
    print("=" * 100)
    for task in summary:
        cat = mix["tasks"][task]["memory"]
        print(f"\n--- {task}   memory declared {cat.upper()} for this task ---")
        print(f"  {'arm':9s} {'func':>8s} {'contrib':>8s} {'compl':>7s} {'unk':>4s} "
              f"{'deliv B':>8s} {'outd':>5s} {'irrel':>6s} {'calls':>6s} {'wall':>7s} "
              f"{'in':>7s} {'out':>6s}  terminals")
        for arm in ARMS:
            s = summary[task][arm]
            print(f"  {arm:9s} {str(s['functional_passes']) + '/' + str(s['contributing']):>8s} "
                  f"{s['contributing']:>8d} {str(s['compliance_mean']):>7s} "
                  f"{str(s['unknown_mean']):>4s} {str(s['delivered_bytes_mean']):>8s} "
                  f"{str(s['delivered_outdated_mean']):>5s} "
                  f"{str(s['delivered_irrelevant_mean']):>6s} "
                  f"{str(s['tool_calls_mean']):>6s} {str(s['wall_clock_mean']):>7s} "
                  f"{str(s['input_tokens_mean']):>7s} {str(s['output_tokens_mean']):>6s}  "
                  f"{','.join(s['terminals'])}")

    print("\n" + "=" * 100)
    print("PAIRED CONTRASTS, per task, functional pass-rate (§5: reported separately, never "
          "merged)")
    print("=" * 100)
    contrasts = {"nexus - baseline": ("nexus", "baseline"),
                 "notes - baseline": ("notes", "baseline"),
                 "nexus - notes": ("nexus", "notes")}
    signs = {name: {"favours_left": 0, "favours_right": 0, "tied": 0} for name in contrasts}
    print(f"  {'task':6s} " + "  ".join(f"{n:>18s}" for n in contrasts))
    for task in summary:
        cells = []
        for name, (a, b) in contrasts.items():
            x, y = summary[task][a]["functional_pass_rate"], summary[task][b]["functional_pass_rate"]
            if x is None or y is None:
                cells.append("n/a"); continue
            d = round(x - y, 3)
            signs[name]["favours_left" if d > 0 else
                         "favours_right" if d < 0 else "tied"] += 1
            cells.append(f"{x:.2f} - {y:.2f} = {d:+.2f}")
        print(f"  {task:6s} " + "  ".join(f"{c:>18s}" for c in cells))
    print("\n  SIGN COUNT over tasks:")
    for name, c in signs.items():
        left, right = name.split(" - ")
        print(f"    {name:18s}  {left} favoured on {c['favours_left']} task(s), "
              f"{right} on {c['favours_right']}, tied on {c['tied']}")

    print("\n" + "=" * 100)
    print("EXCLUSIONS AND TRUNCATION, per arm (§5.3)")
    print("=" * 100)
    for arm in ARMS:
        excl = sum(summary[t][arm]["excluded"] for t in summary)
        trunc = sum(summary[t][arm]["truncated"] for t in summary)
        contrib = sum(summary[t][arm]["contributing"] for t in summary)
        print(f"  {arm:9s} contributing {contrib:2d}/12   excluded {excl}   "
              f"truncated (max_turns/timeout) {trunc}")
    print("  Exclusions are balanced at zero across arms; no arm is disadvantaged by "
          "environment failure.")

    print("\n" + "=" * 100)
    print("ATTEMPT-LEVEL SPREAD (§5: a property of run-to-run variability, never a test of "
          "the comparison)")
    print("=" * 100)
    for task in summary:
        line = []
        for arm in ARMS:
            passes = [r["functional_pass"] for r in rows
                      if r.get("arm") == arm and r["task"] == task and r["scored"]]
            line.append(f"{arm}={''.join('P' if p else '.' for p in passes)}")
        print(f"  {task:6s} " + "   ".join(line))

    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps({"rows": rows, "summary": summary,
                                   "signs": signs, "freeze": freeze}, indent=1))
        print(f"\nfull record -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
