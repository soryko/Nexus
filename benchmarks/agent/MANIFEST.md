| task | attempt | directory | arm-runs | functional | disposition | reported in |
| --- | --- | --- | --- | --- | --- | --- |
| d1 | 1 | `run-dev-a1-attempt1` | 3 arms | 3 pass | no network isolation; corpus invisible (scope) | `results-dev-a1.md` |
| d1 | 2 | `run-dev-a1-attempt2` | 3 arms | 3 pass | corpus invisible (scope); superseded | `results-dev-a1-run2.md §5` |
| d1 | 3 | `run-dev-a1-attempt3` | 3 arms | 3 pass | corpus invisible (config overwrite); superseded | `results-dev-a1-run2.md §5` |
| d1 | 4 | `run-dev-a1-attempt4` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-a1-run2.md §3` |
| d2 | 1 | `run-dev-d2` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-d2-d3.md` |
| d3 | 1 | `run-dev-d3` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-d2-d3.md` |
| d4 | 1 | `run-dev-d4` | 3 arms | 3 pass | outdated-memory task; stale advice was LABELLED outdated | `results-dev-d4.md` |
| d4 | 2 | `run-dev-d4-isolated` | 3 arms | 3 pass | enforced sandbox boundary; stale advice UNLABELLED | `results-recomputed.md §8` |

attempts: 8   arm-runs total: 24   arm-runs with memory actually delivered: 15

Executed but NOT counted above, because they produced no records file:
  * one aborted attempt between d1 attempts 1 and 2, stopped by the egress
    control before any model call -- no arms ran, no tokens spent.
  * two crashed starts of the d4 isolated run. Each completed its BASELINE arm
    (tokens spent) and then died on a dangling variable left by an edit to the
    runner -- `blocked`, then `vis`. Partial output was discarded and the
    attempt restarted from a fresh fixture. Recorded so the spend is visible.

## Not an arm-run

`smoke-a1/` holds one integrated smoke test of the boundary repaired after the
review of 2a966c1: a model response, a repository read, an edit and a Nexus
retrieval, on a d1 fixture with no task in the prompt. It produces no compliance
figure, no functional verdict and no row in the table above, and its record says
so in its first field. It exists because `claude --version` and
`nexus-memory --help` cannot establish that the request/tool loop survives the
restrictions -- and on its first two executions it found two boundary defects
that every model-free control had passed. See `results-review-3.md`.

Two earlier executions of it were discarded and are not preserved. The first
died at 0.4 s on `EPERM ... open '/tmp/claude-501'` before any token was spent.
The second completed (6 127 in / 1 726 out) with every Bash call refused, which
is how the second defect was found. Both are reported in
`results-review-3.md §7`; the run kept here is the third, after both repairs.
The diagnosis between them cost nothing: it ran against the loopback stub
upstream added for the forwarder's acceptance control, not against the provider.

## Held-out registration

`freeze-heldout-a1.md`, frozen 2026-09-12 before any held-out task ran, fills
four of `protocol-a1` §15's six open slots: the selection rule and the three
disjoint task sets (development `d1`-`d4`, capture `c1`-`c2`, held-out
`h1`-`h4`), the arms as executed, the analysis, and the budget. The execution
schedule is `schedule-heldout-a1.json`, digest `1a960a7465fa36a0`.

Slot 3, the held-out memory corpus, stays open on purpose: it is produced by a
prior-session run on `c1`/`c2` that never sees `h1`-`h4`, so it cannot exist
before the tasks are frozen. Required-evidence completeness is deferred from A1
and will not be reported.

## Running these

See `results-review-3.md`, section "Running the pieces", and
`results-preparation.md`. Note in particular that
`test_compliance_counterexamples.py` and `verify_preparation.py` are scripts and
are NOT covered by the Nexus pytest suite, and that both need an interpreter with
a working `pyexpat`.

Paths and ceilings live in `a1-config.json`, which is per host and gitignored;
`a1-config.example.json` is the committed template. `python3 a1_config.py`
validates it and names every problem in one pass.

Arm order comes from a schedule frozen before execution (`schedule.py`), not from
a seed re-drawn inside each run. The eight runs above predate it and all record
the same order; that is the defect, not a finding.
