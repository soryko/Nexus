| task | attempt | directory | arm-runs | functional | disposition | reported in |
| --- | --- | --- | --- | --- | --- | --- |
| d1 | 1 | `run-dev-a1-attempt1` | 3 arms | 3 pass | no network isolation; corpus invisible (scope) | `results-dev-a1.md` |
| d1 | 2 | `run-dev-a1-attempt2` | 3 arms | 3 pass | corpus invisible (scope); superseded | `results-dev-a1-run2.md §5` |
| d1 | 3 | `run-dev-a1-attempt3` | 3 arms | 3 pass | corpus invisible (config overwrite); superseded | `results-dev-a1-run2.md §5` |
| d1 | 4 | `run-dev-a1-attempt4` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-a1-run2.md §3` |
| d2 | 1 | `run-dev-d2` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-d2-d3.md` |
| d3 | 1 | `run-dev-d3` | 3 arms | 3 pass | all gates passing; memory delivered | `results-dev-d2-d3.md` |
| d4 | 1 | `run-dev-d4` | 3 arms | 3 pass | all gates passing; outdated-memory task | `results-dev-d4.md` |

attempts: 7   arm-runs total: 21   arm-runs with memory actually delivered: 12

Also executed and NOT counted above: one aborted attempt between d1 attempts 1
and 2, stopped by the egress control before any model call (no arms ran, no
tokens spent). It produced no records file and is recorded here only.
