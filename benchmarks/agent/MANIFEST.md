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

## Running these

See `results-review-3.md`, section "Running the pieces". Note in particular that
`test_compliance_counterexamples.py` is a script and is NOT covered by the Nexus
pytest suite, and that it needs an interpreter with a working `pyexpat`.
