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
