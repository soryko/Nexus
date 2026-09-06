# B2 — bounded evidence loading

**Status: measured and implemented 2026-09-06.** This is the slice [B2b](milestone-b2b.md)
deferred when it closed at `cdf51e7`. B2b's closing note named the work exactly: *bounded
evidence loading measured against a statistics-only control, with candidate selection
measured separately* — the comparison an automatic `ANALYZE` policy should be decided
against, and which B2b's own evidence did not justify.

Every claim below is marked **measured**, **assumed** or **decided**. The measurement of
record is [`tools/measure_evidence_loading.py`](../../tools/measure_evidence_loading.py) and
its as-run output is preserved verbatim at
[`benchmarks/dev/results-evidence-loading.txt`](../../benchmarks/dev/results-evidence-loading.txt).
The guard is [`tests/core/test_evidence_loading.py`](../../tests/core/test_evidence_loading.py).

## 1. What was wrong

**Measured, at `cdf51e7`.** `_hit_references` loads the reference evidence for one page of
hits. It built an OR-list — `(memory_id=? AND revision_id=?) OR …`, twenty of them for a
twenty-hit page — under a `namespace`/`actor` equality. With no statistics in
`sqlite_stat1`, SQLite served that by scanning the whole `(namespace, actor)` partition of
`revision_references` rather than by twenty point lookups on the primary key's autoindex.

The cost was therefore a function of the **scope**, not of the **page**. On a
20,000-memory scope it was 1,719,772 VDBE instructions to load evidence for twenty hits,
and — measured across all five filter shapes — that number does not move with the filter at
all. It is the same in the unfiltered control. Evidence loading was the dominant cost of a
browse search, and nothing about the query could reduce it.

`ANALYZE` changes the planner's mind, which is why B2b saw a large `ANALYZE` gain and why
amendment 5 located that gain in this stage. **Production never runs `ANALYZE`.**

## 2. The change

**Decided.** The page's pairs drive the lookup, one seek each:

```sql
WITH pairs(memory_id, revision_id) AS (VALUES (?,?), …)
SELECT r.… FROM pairs p CROSS JOIN revision_references r
  ON r.namespace=? AND r.actor=? AND r.memory_id=p.memory_id AND r.revision_id=p.revision_id
ORDER BY r.memory_id, r.revision_id, r.commit_oid, r.path
```

**`CROSS JOIN` is a plan constraint, not a semantic one.** In SQLite it is an inner join the
planner may not reorder, and that is the entire mechanism: it forces the twenty-row `pairs`
list to be the outer loop. Written as a plain `JOIN` the planner leads with
`revision_references` and scans the partition again — **measured** at 6,640,540 instructions,
worse than the form it replaces. The `CROSS` keyword is load-bearing and
`test_bounded_evidence_loading_is_bounded_in_both_statistics_states` fails without it.

**Decided.** The form shipped through `cdf51e7` is retained as `_unbounded_evidence_sql`,
selected by the class constant `BOUNDED_EVIDENCE`. Nothing in a request can reach it. It
exists because a control that is not executed is not a control, and it is kept honest by
`test_every_b2b_scenario_holds_under_the_unbounded_control`, which runs all nineteen B2b
acceptance scenarios against it. That turns the retained path from dead code into a
differential-testing oracle.

## 3. Results

**Measured**, 20,000 memories, 40,000 reference rows, 3 repositories, one scope, 20-hit
pages, five browse shapes. Evidence-loading stage only; full tables in the as-run file.

| Evidence loading | current | statistics only | bounded only | both |
|---|---:|---:|---:|---:|
| instructions | 1,719,772 | 1,593 | **1,353** | 1,353 |
| best replayed latency (ms) | 9.1 – 12.6 | 0.044 – 0.045 | **0.039 – 0.043** | 0.040 – 0.045 |

Instruction counts are identical across all five shapes and reproduce to the digit
(repeatability control: 1.000×). Four findings:

1. **Bounded loading removes the cost without statistics.** 1,719,772 → 1,353 instructions,
   a **1,271× reduction**, and ~9–12 ms → ~0.04 ms on the stage.
2. **It does not need `ANALYZE`, and `ANALYZE` adds nothing to it.** "Bounded only" and
   "both" are 1,353 instructions each — **identical**. The interaction is nil.
3. **Statistics alone get most of the way there** (1,593), but only by running `ANALYZE`.
   Bounded loading is 15% fewer instructions than statistics-only; **on latency the two are
   indistinguishable** — 0.045 vs 0.042 ms is 1.07×, well inside this instrument's measured
   1.64× spread across identical runs, and is **not** a result.
4. **Candidate selection is untouched by the change**, as intended: 361,054 → 360,860
   instructions for path-exact. Statistics barely move filtered selection (+0.5%) but cut
   the *unfiltered* browse by 36% (780,397 → 502,415) — the one selection-stage effect
   `ANALYZE` has here.

**Result identity is enforced, not assumed.** All four configurations return the same hits
in the same order with complete reference evidence, over a four-page pagination walk, for
every shape. The harness asserts it and fails otherwise.

## 4. The decision on `ANALYZE`

**Decided: no automatic `ANALYZE` policy, and B2b's deferred question is now answered
against it.**

The case for one rested entirely on the gain amendment 5 located in this stage. Bounded
loading captures that gain outright, needs no statistics to do it, and leaves `ANALYZE`
with nothing to add — finding 2 is the whole argument. An automatic policy would have to
decide when statistics are collected and refreshed, and carry the staleness that comes with
them, to buy a benefit that is now zero on this stage and, on the evidence here,
indistinguishable from noise on latency even before the change.

**Unchanged: migration `005` is still not written.** Nothing here bears on it. This slice
adds no index; it changes the shape of one statement.

## 5. What this evidence does not support

- **All five shapes are browse queries carrying no text.** The FTS branch of `search` is a
  different statement with a different plan, and nothing measured here describes it.
- **The end-to-end latency differences between the three non-baseline configurations are
  inside the noise floor** (1.64× across identical runs) and are not reported as results.
  Only the current-to-bounded end-to-end difference clears it.
- **Candidate selection is now the dominant cost** of a browse search on this corpus —
  roughly 260,000–780,000 instructions against evidence loading's 1,353. This slice does
  not address it, and the next performance question is that stage, not this one.
- The instrument measures a single scope on one machine. The instruction counts are
  deterministic; the latencies are not, and are reported best-of-15 with the spread stated.

## 6. Two instrument defects caught by controls, and what they cost

Recorded because both would have produced a confident wrong answer, and neither is visible
in a summary number.

**1 — Statistics contamination, a 485× error.** `ANALYZE` writes `sqlite_stat1` *into the
database file*, where it persists. The pilot measured a "baseline" arm on a database an
earlier arm had analyzed and read the current loader at 3,545 instructions instead of
1,719,772 — it would have reported the problem this slice fixes as already absent. Every arm
now runs on a fresh copy of a pristine corpus and asserts the statistics state it believes
it is in.

**2 — A stage classifier that merged the two stages it existed to separate.** Statements are
labelled by the table they name. Under a reference filter the *candidate selection*
statement also names `revision_references`, inside the correlated `EXISTS` the filter
compiles to — so a classifier testing for `revision_references` first labelled selection as
evidence, summed both into one column, and reported **zero selection work for every filtered
shape**. The order of the tests is now load-bearing and documented as such, and
`verify_stages` asserts the structure the labels assume: exactly one selection statement, at
most one evidence statement, and no statement carrying both tables.

The predecessor harness's defect was the same family — a hand-rebuilt query measured beside
the shipped one — which is why this harness captures the executed SQL from
`set_trace_callback` with parameters already expanded, and prints it. Nothing here is
reconstructed.
