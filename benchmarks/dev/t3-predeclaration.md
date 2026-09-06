# T3 predeclaration — baseline, selections and decision rule, recorded before any variant was run

Development data. **Nothing here is a v2 result.**

| | |
| --- | --- |
| Build | `d913311` |
| Corpus | `corpus-dev2.json` · sha256 `73df79f8…40ff0736` — the same corpus T2 ran on, pinned |
| Baseline | **pinned `stem`, head-only candidate generation** — the configuration T2 left standing — with selection `all` |
| Interpreter | `.venv-sqlite/bin/python`, SQLite 3.53.4 |
| Budgets | pool 20 · history 5 memories × 20 revisions · delivered 5 items / 8 KiB incl. provenance · ≤3 predeclared variants · no network or model calls |
| Machine record | `results-dev-t3-baseline.json` (baseline control), `results-dev-t3.json` (the run) |

Candidate generation is **frozen**. A selection may reject; it may not add a candidate,
reach past the pool, or change what became one. It sees the pool's own BM25 scores and
nothing else — no query id, no grade, no answer id.

## The gate, in bytes

Baseline aggregate delivered grade-0 bytes: **18,185**. The registered contract requires a
reduction of at least 50%, so a variant passes only at **≤9,092 bytes**, and additionally:

- **no per-query loss of grade-2 delivered recall**;
- **no loss of task coverage** — registered here as: a query that received grade-2 bytes at
  baseline must still receive some;
- **grade-1-only support preserved** — registered here as: where a query has no grade-2
  answer at all, the grade-1 bytes it received at baseline must still be delivered. `dq14`
  (`d13`) and `dq18` (`d22`) are those queries.

A surviving revision id is not delivered evidence, and the harness scores discovery and
delivery apart, as T2 did.

## What is reachable at all — computed from the baseline before choosing any variant

For each query, the shortest delivered prefix that keeps every grade-2 item the baseline
delivered (and the grade-1-only support where that is all a query has):

| Query | Baseline items | Shortest prefix that loses nothing | Grade-0 bytes at that prefix |
| --- | ---: | ---: | ---: |
| `dq06` | 5 | 2 | 263 |
| `dq13` | 5 | **5** | **1,132** |
| every other query | 0–5 | 0–1 | 0 |

**Floor: 1,395 grade-0 bytes — a 92.3% reduction.** The gate is therefore reachable in
principle by prefix rejection; the question T3 asks is whether a label-free score rule can
find it. `dq13` alone accounts for 1,132 of the 1,395: its answer `d12` is the **lowest
scoring of its own five delivered items** (0.4018 of the top hit), so any rule that cuts
`dq13`'s volume at all loses that answer. `dq06`'s answer sits second at 0.571 of its top.

## The three selections — registered before any of them was run

| # | Selection | Rule |
| --- | --- | --- |
| 1 | `cutoff_25` | Deliver the leading run of hits scoring **≥25%** of the top hit's BM25 magnitude. |
| 2 | `cutoff_40` | The same rule at **≥40%**. |
| 3 | `dominant_top_2x` | If the top hit is **≥2× the second**, deliver it alone; otherwise deliver the pool as the baseline does. A judgment about whether the query had a clear winner, not about how good a hit is. |

**`cutoff_40` is fitted to this corpus, and that is declared here rather than discovered
later.** The baseline shows `dq13`'s answer at **0.401814** of its top score. 0.40 is
therefore the largest round constant that preserves the hardest known case, and it does so
by **0.0067 BM25 units — a 0.45% margin**. If it passes, the pass says the frontier is
reachable by a constant tuned to one query in a 24-query corpus. It says nothing about
whether the rule generalises, and the report will not claim otherwise. `cutoff_25` and
`dominant_top_2x` were chosen without reference to that margin: 0.25 clears it by a wide
band, and the dominance rule does not use an absolute fraction at all.

**Estimates, computed from the baseline scores before the run** — these are estimates, not
predictions of measurement error, and the run replaces them with measured values:

| Selection | Estimated grade-0 bytes | Estimated reduction | Estimated recall loss |
| --- | ---: | ---: | --- |
| `cutoff_25` | ≈13,800 | ≈24% — **short of the gate** | none |
| `cutoff_40` | ≈8,300 | ≈55% — **clears the gate** | none, by 0.45% on `dq13` |
| `dominant_top_2x` | ≈11,200 | ≈38% — **short of the gate** | none |

If those estimates hold, exactly one variant passes and it is the fitted one. That is the
result the run is expected to produce; it is registered here so that it cannot be presented
afterwards as a discovery.

## Decision rule — registered before the run

A selection is eligible only if **all four** hold: grade-0 bytes ≤9,092; no per-query
grade-2 delivered-recall loss; no task-coverage loss; grade-1-only support preserved.

Among selections that pass, the winner is: **lowest grade-0 bytes**, then **most grade-1
bytes preserved**, then the resource ceilings. The winner is then measured against a fresh
paired `exact` — retrieval worst-block p95 ≤50 ms and ≤1.5×, record/revise p95 ≤2×,
persistent retrieval structures ≤2× — on both fixture sizes, in a counterbalanced
schedule, as T2's ceilings were.

If no selection passes, **the baseline stands**: `stem`, head-only, deliver the pool in
rank order until a budget stops it.

## Known limits of this experiment

- Every constant here was chosen by looking at one 24-query development corpus, and one of
  them was chosen by looking at a single query in it.
- Selection acts on a prefix. A rule that reordered the pool, or dropped an item from the
  middle of it, is not represented in this set and would need its own registration.
- BM25 magnitudes are comparable within one query and not across queries. Every rule here
  is within-query by construction, which is why none of them uses an absolute score.
- The corpus never saturates the delivered-byte budget, so this stage measures item
  selection and not byte-level truncation.
