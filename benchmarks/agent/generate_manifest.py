"""Derive the attempt manifest from the preserved records. Never hand-written.

The reports disagreed with each other -- one said "nine runs", another said d1 took four
attempts -- because "run" was used for two different things: a task's set of three arms, and
an individual arm's execution. This counts both, from the records themselves, and says which
attempts each results table draws on.
"""
from __future__ import annotations

import json
from pathlib import Path

BENCH = Path(__file__).parent
# attempt -> (directory, why it ended up as it did, which report uses it)
ATTEMPTS = [
    ("d1", 1, "run-dev-a1-attempt1", "no network isolation; corpus invisible (scope)", "results-dev-a1.md"),
    ("d1", 2, "run-dev-a1-attempt2", "corpus invisible (scope); superseded", "results-dev-a1-run2.md §5"),
    ("d1", 3, "run-dev-a1-attempt3", "corpus invisible (config overwrite); superseded", "results-dev-a1-run2.md §5"),
    ("d1", 4, "run-dev-a1-attempt4", "all gates passing; memory delivered", "results-dev-a1-run2.md §3"),
    ("d2", 1, "run-dev-d2", "all gates passing; memory delivered", "results-dev-d2-d3.md"),
    ("d3", 1, "run-dev-d3", "all gates passing; memory delivered", "results-dev-d2-d3.md"),
    ("d4", 1, "run-dev-d4", "outdated-memory task; stale advice was LABELLED outdated", "results-dev-d4.md"),
    ("d4", 2, "run-dev-d4-isolated", "enforced sandbox boundary; stale advice UNLABELLED", "results-recomputed.md §8"),
]


def main() -> int:
    rows, total_arms, delivered_arms = [], 0, 0
    for task, attempt, d, note, report in ATTEMPTS:
        p = BENCH / d / "records.json"
        if not p.exists():
            rows.append((task, attempt, d, "NOT PRESENT", "-", note, report))
            continue
        rec = json.loads(p.read_text())
        arms = rec["records"]
        total_arms += len(arms)
        vis = [(r.get("memory_visibility") or {}).get("visible") for r in arms
               if r["arm"] == "nexus"]
        seen = vis[0] if vis else None
        if seen:
            delivered_arms += len(arms)
        passed = sum(1 for r in arms if r["scored"]["passed"])
        rows.append((task, attempt, d, f"{len(arms)} arms", f"{passed} pass", note, report))

    w = "| task | attempt | directory | arm-runs | functional | disposition | reported in |"
    print(w)
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | `{r[2]}` | {r[3]} | {r[4]} | {r[5]} | `{r[6]}` |")
    print(f"\nattempts: {len(rows)}   arm-runs total: {total_arms}   "
          f"arm-runs with memory actually delivered: {delivered_arms}")
    print("\nExecuted but NOT counted above, because they produced no records file:")
    print("  * one aborted attempt between d1 attempts 1 and 2, stopped by the egress")
    print("    control before any model call -- no arms ran, no tokens spent.")
    print("  * two crashed starts of the d4 isolated run. Each completed its BASELINE arm")
    print("    (tokens spent) and then died on a dangling variable left by an edit to the")
    print("    runner -- `blocked`, then `vis`. Partial output was discarded and the")
    print("    attempt restarted from a fresh fixture. Recorded so the spend is visible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
