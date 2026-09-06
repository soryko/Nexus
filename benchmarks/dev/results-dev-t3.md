# T3 — evidence selection: three rejection rules under a frozen candidate generator

Development data. **Nothing here is a v2 evaluation result.**

| | |
| --- | --- |
| Registered in | [v2-development-queries.md](../eval/v2-development-queries.md#t3--predeclaration-registered-2026-09-06) and [t3-predeclaration.md](t3-predeclaration.md) — baseline, selections, estimates and decision rule fixed before any selection ran |
| Corpus | `corpus-dev2.json` · sha256 `73df79f8…40ff0736` |
| Baseline | pinned **`stem`, head-only candidate generation** (what T2 left standing), selection `all` |
| Quality record | `results-dev-t3.json` |
| Gate | aggregate delivered grade-0 bytes **≤9,092** (50% of 18,185), with no per-query grade-2 recall loss, no task-coverage loss, and grade-1-only support preserved |

Candidate generation was frozen throughout: every arm ran the same pool. Only the decision
of how much of that pool to deliver changed, and it read the pool's own BM25 scores and
nothing else.

## Result

| Selection | Grade-0 bytes | Reduction | Items delivered | Grade-2 bytes | Grade-1 bytes | Recall / coverage / support losses | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `all` (baseline) | 18,185 | — | 85 | 5,212 | 785 | — | — |
| `cutoff_25` | 12,988 | 28.58% | 66 | 5,212 | 785 | none | **fails** — short of 9,092 |
| `cutoff_40` | **8,140** | **55.24%** | 49 | 5,212 | 785 | none | **passes** |
| `dominant_top_2x` | 10,218 | 43.81% | 57 | 5,212 | 785 | none | **fails** — short of 9,092 |

**All three rules preserved everything they were required to preserve.** Grade-2 delivered
bytes are identical to the baseline in every arm — 5,212 — as are grade-1 bytes at 785. No
query lost grade-2 delivered recall, no query that received grade-2 evidence stopped
receiving it, and both grade-1-only queries (`dq14`, `dq18`) kept their support. The three
rules differ only in how much irrelevant volume they removed.

Registered estimates against measured values:

| Selection | Estimated reduction | Measured | Estimate error |
| --- | ---: | ---: | ---: |
| `cutoff_25` | ≈24% | 28.58% | understated by 4.6 points |
| `cutoff_40` | ≈55% | 55.24% | within a quarter of a point |
| `dominant_top_2x` | ≈38% | 43.81% | understated by 5.8 points |

The estimates were arithmetic on the baseline scores using an approximate per-item byte
size, and two of the three were low. The measured values replace them.

## Where the volume went

| Query | `all` | `cutoff_25` | `cutoff_40` | `dominant_top_2x` |
| --- | ---: | ---: | ---: | ---: |
| `dq03` "who approves a licensing window" | 5 / 1,131 | 1 / 0 | 1 / 0 | 1 / 0 |
| `dq20` "how fast does a bad segment clear" | 5 / 1,079 | 1 / 0 | 1 / 0 | 1 / 0 |
| `dq05` "ingest throttling" | 5 / 1,007 | 5 / 1,007 | 1 / 0 | 1 / 0 |
| `dq19` "4K encoding support" | 5 / 1,206 | 5 / 1,206 | 2 / 287 | 1 / 0 |
| `dq12` "subtitle translation vendor" (unanswerable) | 5 / 1,406 | 2 / 637 | 2 / 637 | 5 / 1,406 |
| `dq13` "what is our caption file format" | 5 / 1,132 | 5 / 1,132 | 5 / 1,132 | 5 / 1,132 |
| `dq06` "publishing schedule" | 5 / 1,178 | 5 / 1,178 | 5 / 1,178 | 5 / 1,178 |
| `dq18` turnaround *before it changed* | 5 / 1,140 | 5 / 1,140 | 5 / 1,140 | 5 / 1,140 |

*(items delivered / grade-0 bytes)*

`dq13` and `dq06` are untouched by every rule, which is the point: their answers sit at
delivered rank 5 and rank 2 with scores close to their neighbours, and a rule that cut them
would have lost the answer. `dq20`, where the top hit scores 15.29 against a 2.05 runner-up,
collapses to one item under all three. The rules agree about the easy cases and disagree
about the middle.

`dominant_top_2x` behaves differently in kind, not just in degree: it cuts `dq07`, `dq09`,
`dq10` and `dq19` to a single item — harder than `cutoff_40` does — while leaving `dq01`,
`dq12`, `dq14`, `dq15` and `dq23` completely untouched, because in those queries no hit is
twice the next. A rule keyed on the *shape* of the top of the distribution is all-or-nothing
per query; a rule keyed on a fraction of the top score is graded.

## What the pass is worth

`cutoff_40` clears the gate at 55.24% with nothing lost. It is also **the fitted variant**,
declared as such before the run: 0.40 is the largest round constant that preserves `dq13`,
whose answer sits at **0.401814** of its top hit — a margin of **0.0067 BM25 units, 0.45%**.

Three things follow, and the third is the one that matters.

- The 50% target is **reachable** on this corpus without losing anything: 55.24% measured,
  against a computed floor of 92.3% for any prefix rule that loses nothing.
- The two **unfitted** rules both fall short — 28.58% and 43.81% — while preserving
  everything. Nothing here shows that an unfitted rule can clear 50%.
- A constant tuned to one query's margin in a 24-query corpus, passing by 0.45% on that
  query, is **not evidence that the rule generalises**. A single answer sitting one place
  lower, or scoring a fraction less, would move `dq13` below the cutoff and turn the pass
  into a recall loss. The result establishes that the gate is achievable here and by what
  margin, not that `cutoff_40` is the rule to ship.

## Resource ceilings — `cutoff_40` against a fresh paired `exact`

Machine record: `results-dev-t2-perf-baseline-cutoff_40.json`. Same schedule as T2's
ceiling run: three arms — `anchor` (`exact`, no history, selection `all`),
`stage_baseline` (`stem`, no history, selection `all`) and `variant` (`stem`, no history,
`cutoff_40`) — in all six orders per fixture size, 1,000 and 10,000 memories, writes
measured in every block after that block's retrieval. Gates computed variant against
anchor; the third arm isolates what stemming costs from what selection costs.

| Fixture | Order | Retrieval p95 (anchor → variant) | Ratio | ≤50 ms | ≤1.5× | Record | Revise | Storage | Cell |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| 1,000 | `anchor→variant` | 7.5646 → 9.1280 ms | 1.2067× | pass | pass | 0.6991× | 0.6975× | 0.9595× | **pass** |
| 1,000 | `variant→anchor` | 7.6052 → 8.3772 ms | 1.1015× | pass | pass | 0.7353× | 0.7661× | 0.9595× | **pass** |
| 10,000 | `anchor→variant` | 17.9926 → 18.4069 ms | 1.0230× | pass | pass | 0.8202× | 1.2456× | 0.9823× | **pass** |
| 10,000 | `variant→anchor` | 16.2360 → 18.8607 ms | 1.1617× | pass | pass | 0.6536× | 0.6497× | 0.9823× | **pass** |

**4 of 4 cells pass, and the paired statistic agrees** — worst pair 1.4730× at 1,000 and
1.2275× at 10,000 for retrieval, 1.4166× worst for `revise`, against ≤1.5× and ≤2×. Unlike
T2, this verdict does not depend on which of the two readings is used.

Storage is below the anchor rather than above it (0.96× and 0.98×): selection adds no
persistent structure, and `stem`'s index is slightly smaller than `exact`'s. Writes are
untouched by selection; the spread around 1.0 is block noise in both directions.

**Selection's own effect on retrieval latency is not resolvable at this precision.** Across
the twelve block sets the variant measured a median 1.05× the stage baseline at 1,000 and
1.02× at 10,000, spanning 0.72×–1.41×. Delivering 49 items instead of 85 did not produce a
measurable speed-up here, and the perf workload is the synthetic query set rather than the
development corpus, so the number of items selection cuts there is not the number it cuts
above. What the run establishes is that `cutoff_40` costs nothing extra, not that it saves
time.

This run's anchor arm measured 16.2–18.0 ms at 10,000, close to T1-C's 19.0476 ms and far
from the 37.2549 ms seen during the T2 ceiling run. Why those two runs differed is still
unresolved; nothing here identifies it.

### Disclosures about how this run was made

- A **pilot** ran first at 1,000 memories only, to check the harness after selection was
  threaded through it. Its record is kept as `results-dev-t3-perf-pilot-1000.json` rather
  than deleted; it measured 2/2 cells passing and is not part of the verdict.
- The complete ceiling schedule ran **once**, with no early stopping, no dropped blocks and
  no retries. The quality run also ran once, with all four arms in one record.
- The ceiling harness is T2's, extended with a `--selection` flag; the anchor and stage
  baseline are defined exactly as they were there, so a T2 figure and a T3 figure mean the
  same thing.

## Verdict — `cutoff_40` clears every registered clause

| Selection | Grade-0 gate | Recall / coverage / support | Ceilings | Outcome |
| --- | --- | --- | --- | --- |
| `cutoff_25` | fails (28.58%) | intact | not measured | not pinned |
| `cutoff_40` | **passes (55.24%)** | **intact** | **4/4 cells** | **pinned** |
| `dominant_top_2x` | fails (43.81%) | intact | not measured | not pinned |

Under the registered decision rule, **`cutoff_40` is T3's outcome**: `stem`, head-only
candidate generation, deliver the leading run of pool members scoring at least 40% of the
top hit's BM25 magnitude.

## What T3 established, and what it did not

- **The gate is achievable on this corpus and the winner clears it with resources to
  spare.** 55.24% against a 50% target, with grade-2 and grade-1 delivery byte-identical to
  the baseline, and every ceiling passed under both statistics.
- **The winning constant is fitted, and its margin is 0.45% on one query.** That was
  declared before the run and is not softened by the pass. `dq13`'s answer sits at 0.401814
  of its top hit; at 0.41 the rule would lose it. No claim is made that 0.40 transfers to
  another corpus, another tokenizer or another query mix.
- **Neither unfitted rule reached the gate** — 28.58% and 43.81% — so nothing here shows
  that a rule chosen without looking at the hardest case can clear 50%.
- **The two rules differ in kind.** `dominant_top_2x` is all-or-nothing per query and cuts
  four queries harder than `cutoff_40` does while leaving five untouched; a fractional
  cutoff is graded. Their aggregate figures hide that, which is why the per-query table is
  above.
- **Nothing was measured about byte-level truncation.** No query on this corpus approaches
  the 8,192-byte delivery budget, so this stage measured item selection only.
- **A prefix is all these rules can cut.** Reordering the pool, or dropping an item from
  the middle of it, is a different mechanism and would need its own registration.
- **No v2 regression check has been run.** The charter carries a stage's chosen
  configuration into v2 unchanged, and that step inspects frozen evaluation data; it is not
  part of this run.
