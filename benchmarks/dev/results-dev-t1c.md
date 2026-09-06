# T1-C — performance confirmation for `exact` and unchanged `stem`

Development data. **Nothing here is a v2 evaluation result.**

| | |
| --- | --- |
| Registered in | [v2-development-queries.md](../eval/v2-development-queries.md#t1-c--performance-confirmation-registered-2026-09-06) — schedule and decision rule, both fixed before this ran |
| Pinned revision | `f82138a`, working tree clean at start (recorded in the result file) |
| Interpreter | `.venv-sqlite/bin/python`, SQLite **3.53.4** |
| Machine record | `results-dev-t1c.json` — every block, and every individual query duration |
| Scope | A **measurement amendment**. Two already-registered profiles, re-measured. No new configuration, no configuration-search slot consumed. |

## Verdict — `stem` clears the registered rule

The rule, registered before the run: within each order, take each profile's worst block p95. `stem` passes only if it is **≤50 ms and ≤1.5× `exact` in both orders at both fixture sizes** — four cells, all of which must hold.

| Fixture | Order | `exact` worst-block p95 | `stem` worst-block p95 | Ratio | ≤50 ms | ≤1.5× |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 1,000 | `exact→stem` | 7.4948 ms | 8.3850 ms | 1.1188× | pass | pass |
| 1,000 | `stem→exact` | 7.5304 ms | 8.9346 ms | 1.1865× | pass | pass |
| 10,000 | `exact→stem` | 18.0782 ms | 20.8957 ms | 1.1559× | pass | pass |
| 10,000 | `stem→exact` | 19.0476 ms | 18.5715 ms | 0.9750× | pass | pass |

**4 of 4 cells pass.** The schedule ran once, complete, with no early stopping, no dropped blocks and no retries.

The verdict also holds under the stricter of the two defensible statistics. Computing the ratio **within each pair** — never comparing blocks measured minutes apart — the worst of all twelve pairs is **1.3611×** (10,000, `exact→stem`, pair 5). Both readings clear 1.5×; neither clears it by a large margin.

The paired statistic is stricter as arithmetic, not as luck. For positive paired block p95s `s_i` (`stem`) and `e_i` (`exact`),

> max(s_i) / max(e_i)  ≤  max(s_i / e_i),

because if every pair satisfies `s_i ≤ r·e_i` then the maxima satisfy `max(s_i) ≤ r·max(e_i)` as well. Selecting each profile's maximum independently can therefore **mask a bad pair** — which is exactly what the original 1.8561× did, against a 3.35× paired gap on the very block it drew from. And because both statistics are computed from the same 4,800 durations, their agreement is a **sensitivity check on one dataset, not independent replication**.

## What the original 1.8561× actually was

T1's per-block figures, which the original report did not print, identify it.

| T1, forward order, 1,000 memories | block 1 | block 2 | block 3 | worst |
| --- | ---: | ---: | ---: | ---: |
| `exact` | 7.7502 | 6.4850 | **13.9946** | 13.9946 |
| `stem` | **25.9749** | 9.7370 | 12.2273 | 25.9749 |

The registered statistic takes each profile's worst block independently and then divides. Here that **compared two different blocks** — `stem`'s block 1 against `exact`'s block 3 — which ran **different query sets**. It is not a paired comparison, and a ratio of two independently selected maxima compounds two tail draws.

Underneath that, there is a real paired anomaly. On block 1's queries, which both profiles ran, T1 measured `stem` at 25.9749 ms against `exact` at 7.7502 ms — a **3.35× gap on identical queries**. T1-C's measured block is **exactly T1's block 1** (verified: the query generator is seeded identically, so T1-C's 250 queries are T1's first 250). Across six fresh blocks on those same queries, `stem`'s worst is **8.9346 ms**.

**The spike did not recur; its cause remains unresolved.** One candidate mechanism is a first-touch cost on the very first stem measurement of that run, not absorbed by the 50 warm-up queries — opening a copied `exact` fixture under `stem` rebuilds the FTS index, which `exact` does not do. That mechanism is plausible but untested by this measurement: **every T1-C block rebuilds the same index, and none of the twelve spiked**, so a design that reproduces the supposed cause without reproducing the effect cannot be what identifies it. Failure to reproduce a spike shows non-recurrence, nothing more. Neither cache causation nor an absence of production impact was established here. The original report claimed run position explained the failure without measuring it; that claim was not licensed by the evidence then, and the promotion built on it was correctly withdrawn.

Separately, the query mix explains the absolute drop from T1: T1's `exact` worst at 1,000 was 13.9946 ms, drawn from block 3, whose heavier query set T1-C does not use. On block 1's queries T1 measured `exact` at 7.7502 and 8.4340 ms in the two orders, against T1-C's 7.4948 and 7.5304. Those agree. **T1-C is not comparable to T1 in absolute terms** — different query mix, a fresh database copy and service per block — and no such comparison is made here.

## Per-block results

| Fixture | Pair | Order | `exact` p95 | `stem` p95 | Pair ratio |
| --- | ---: | --- | ---: | ---: | ---: |
| 1,000 | 1 | `exact→stem` | 7.4948 | 8.3850 | 1.1188 |
| 1,000 | 2 | `stem→exact` | 6.8033 | 8.9346 | 1.3133 |
| 1,000 | 3 | `exact→stem` | 6.7859 | 6.5661 | 0.9676 |
| 1,000 | 4 | `stem→exact` | 6.3128 | 5.9082 | 0.9359 |
| 1,000 | 5 | `exact→stem` | 6.2510 | 7.7571 | 1.2409 |
| 1,000 | 6 | `stem→exact` | 7.5304 | 8.1322 | 1.0799 |
| 10,000 | 1 | `exact→stem` | 18.0782 | 16.4836 | 0.9118 |
| 10,000 | 2 | `stem→exact` | 13.9602 | 18.5715 | 1.3303 |
| 10,000 | 3 | `exact→stem` | 17.0760 | 20.8138 | 1.2189 |
| 10,000 | 4 | `stem→exact` | 19.0476 | 17.1444 | 0.9001 |
| 10,000 | 5 | `exact→stem` | 15.3517 | 20.8957 | 1.3611 |
| 10,000 | 6 | `stem→exact` | 17.1256 | 16.7680 | 0.9791 |

Pairs are interleaved `EF, FE, EF, FE, EF, FE`, not run as two consecutive campaigns, so order is not confounded with drift in machine state.

**No consistent first-position penalty is apparent in this schedule.** `stem` measured first in six pairs and second in six; the pair ratio exceeds 1.2× in **two `stem→exact` pairs and three `exact→stem` pairs** — five pairs, not three of each. Twelve pairs cannot separate position from anything else at that margin. Interleaving removes the confound between order and one-directional drift; it does not eliminate every time-dependent influence, and the ±35% spread is not attributed here.

## What this measurement does not establish

- **It does not remove the spread.** Per-pair ratios range 0.9001× to 1.3611×. `stem` clears a 1.5× gate against that spread, but not with room to spare, and a rerun could plausibly produce a pair above 1.4×.
- **The registered statistic still selects its maxima independently.** Worst-block-per-profile-then-ratio is the rule that was registered and the rule that was applied; it is also the rule that produced 1.8561× while a 3.35× paired gap sat inside the same blocks. It passed here because no extreme block was drawn, not because the statistic was repaired. The paired cross-check bounds that masking on **this** data; it is not a second measurement of it.
- **It says nothing about writes.** Writes were excluded by design. `stem`'s write and storage results stand on the T1 measurement.
- **It says nothing about `dual` or `split`.** Neither was re-measured; neither needed to be.
- **Two properties were recorded rather than corrected**: every block uses the same 200 measured queries, so block spread reflects machine state rather than query mix; and opening a copied fixture under `stem` rebuilds the FTS index while `exact` does not, leaving the profiles with different page-cache state before warm-up. The second is a **candidate** mechanism for the original spike, not a demonstrated one, and this measurement says nothing about whether it has a production analogue.
- **It does not explain the original spike.** T1's 25.9749 ms block remains unexplained. What is established is that it did not recur across six fresh blocks on the same queries.

## Harness correction applied before this ran

The two T1 harnesses enforced different delivery budgets: the performance harness counted body bytes only against the 8,192-byte limit, while the quality harness counted content plus serialized provenance. They therefore timed and gated different delivery paths.

Both now call one label-free budgeted function, [`budgeted_retrieval.retrieve`](budgeted_retrieval.py), with provenance counted. Grading, miss attribution and the over-budget diagnostic probe run outside it. Re-running the quality harness under the shared function reproduces `results-dev-t1.json` with **every per-query field, per-class recall, delivered-byte total and index size identical** — only `build_commit` differs.

**This correction does not establish the cause of the timing swing**, and is not offered as one. It was made because a measurement that does not time the path the gate governs is not evidence either way.

## Harness correction applied after this ran

The harness rounded percentiles to 4 decimal places and the ratio to 4 decimal places **before** comparing them to the gates. Percentiles and ratios are now compared unrounded, and rounding is left to this report. No T1 or T1-C figure sits near enough to a gate for this to change any outcome — the closest cell is 1.1865× against 1.5× — so nothing above is re-decided. The change binds T2 and T3.

## Consequence

`stem` is **promoted on this evidence**, and T2's baseline is pinned `stem`. The original failure and both original records — `results-dev-t1-perf.json` and `results-dev-t1-perf-reversed.json` — are preserved unchanged, as is the record that the first promotion was withdrawn.
