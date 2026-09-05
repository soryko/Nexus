# Development charter — work that must not touch v2

This file governs every retrieval change developed in response to a v2 finding. It began as the abstention charter; v2 added three more targets, and they are all bound by the same rule.

**Nothing here is evaluation data, and nothing measured here may be reported as a v2 result.** v2 is now the regression baseline. Any improvement developed with knowledge of v2's failures cannot be re-measured against v2 and called a fresh evaluation — at best it is a regression check on a set whose failure modes were already known.

## The rule, stated once

Development happens against a **development corpus and development queries that are disjoint from v2**. Developing against v2's frozen corpus would be tuning on evaluation data even if v2's labels were never opened — the corpus itself carries the answers. Moving the queries to a development file while leaving the corpus shared would reproduce the same contamination one level down.

## T0 — Abstention (from v1, unchanged by v2)

This build has **no abstention behaviour at all**: disjunctive token matching means an unanswerable question returns a confidently ordered list of irrelevant memories. v2 reproduced it — q07 returned 9 and q14 returned 13, all grade-0, at k=20. The fix is a change to matching and rejection behaviour, which is exactly the kind of change that must never be developed against an evaluation set.

v2 also sharpened what "correct" looks like here. q11 returned exactly one result — the supporting item, nothing else — and that is the wanted behaviour, not abstention. A rule that demands an empty result set for every unanswerable query would score q11 as a failure.

## T1 — Morphology (from q02)

`retry` does not match `retries`; FTS5's default tokenizer does not stem. Two of q02's three grade-2 items were never candidates, and q17 finds the same item only by routing around the gap on other discriminative tokens.

**Mechanism probe, run before registration and outside the harness.** In an isolated SQLite fixture, `unicode61` matched only the card-policy example, while `porter unicode61` matched all three retry examples ([FTS5 porter tokenizer](https://www.sqlite.org/fts5.html#porter_tokenizer)). That verifies the matching mechanism and nothing else: it is not a precision measurement, not a performance measurement, and not a benchmark result. It is recorded here as a probe rather than as a table row because it ran before registration — backdating it into the table would misrepresent when it happened.

Porter is English-only and destructive on non-prose. The open requirement is exact handling of identifiers, paths and symbols: a stemmer that mangles `retry_policy.yaml` or a symbol name buys prose recall at the cost of the code references the system exists to hold.

## T2 — Historical vocabulary (from q04, q16)

Obsolete terms produce no candidates at all. q16's `nightflow` exists only in a superseded revision; head-only search returns nothing, so history is never given a memory id to expand. q04 is the semantic form of the same shape — the current head shares no vocabulary with the query.

The direction is lexical matching against historical revisions, surfaced with explicit provenance: which revision matched, and which current head it belongs to. Provenance is not decoration here — a match on superseded text presented as a current answer is worse than no match at all.

## T3 — Evidence selection (from q15 and the context-volume derivation)

q15 is a ranking and cutoff problem, not a candidate-generation one: `m17` ranks between 6 and 10 under the registered protocol, so it enters the lexical pool and misses the first five. Separately, over v2's clean set, raising k from 5 to 20 adds 32 grade-0 results and no additional relevant evidence.

Those two facts point the same way: the work is ordering and rejection under a fixed expansion budget, measured on **both** axes — answers missed *and* irrelevant volume returned. Optimising either alone is trivially gameable.

## The development corpus

Authoring was deliberately deferred until the v2 labels were frozen, so that it could not be influenced by what the v2 assessor decided. `corpus-dev1.json` was then built to this specification and used for T1 — 24 memories, 27 revisions, 20 queries. It already carries the T2 material: `dq16` (`flowreel`) and `dq18` are the historical-vocabulary cases, and `dq17` (`mux2`) is the control where the superseded term still appears at the current head.

What the specification required, unchanged:

- Items on topics that do not appear in v2, so a development query cannot accidentally be answered by a v2 item.
- **Paired queries**: for each unanswerable development query, an answerable one of similar length and vocabulary. A rejection rule that is only ever tested on unanswerable queries will be tuned into refusing everything, and the pairing is what catches it.
- Deliberate near-misses: a question the corpus almost answers. Abstention that only fires on total vocabulary mismatch is not worth having.
- For T1: prose and non-prose items sharing a stem, so a stemmer that improves prose recall and damages identifier matching shows both effects in one run.
- For T2: superseded revisions whose vocabulary is genuinely gone from the current head, plus at least one where the old term still appears at the head — otherwise a rule that always prefers history looks correct.

Whether these three cases are sufficient coverage for T2 is a judgment to make when T2 is predeclared, not now. If the corpus needs extending for T2, the extension is authored and committed **before** the first T2 variant runs, on the same rule that governed the original: authoring may not be informed by variant outcomes.

## Budgets — registered 2026-09-06, before the first development run

Engineering choices for this round, not measured optima. They apply to **development comparisons only**; the frozen v2 protocol is unchanged, and the same limits are applied to baseline and variant in every pair.

| Budget | First-round limit |
| --- | --- |
| Candidate pool | **20 distinct eligible memories**, counted after resolving historical matches to current heads and deduplicating memory ids. Matched-revision provenance is preserved through the resolution. |
| History expansion | **5 candidate memories x 20 revisions.** Which memories are expanded must be chosen from retrieved evidence; selection may never consult known answer ids. |
| Delivered context | **5 evidence items and 8 KiB**, whichever binds first. UTF-8 text and provenance both count. |
| Configuration search | Baseline plus **at most 3 predeclared variants per experiment.** Unsuccessful runs are retained. |
| Retrieval dependencies | **No network calls and no model calls** anywhere in this round's retrieval path. |

The candidate pool deliberately exceeds the delivered-context limit. The point is for selection to find useful evidence inside that pool, not for the agent to consume the pool.

Accounting rules that follow from the budgets:

- **Misses are attributed to the limit that caused them**, separately: pool overflow, history cap, delivered-item cap, delivered-byte cap. A single count of misses cannot distinguish a retrieval failure from a budget doing its job.
- **Candidate recall and delivered-evidence recall are reported separately, always.** Collapsed into one number, selection can hide a retrieval gain or disguise a retrieval loss.
- A returned **revision id counts as discovery, not as delivered answer evidence** when its content was omitted. Any later fetch of that content counts against the delivered-context budget.

## Provisional promotion gates — registered 2026-09-06

Provisional, and scoped to the workloads below. They are not universal scaling guarantees. **Exceeding a gate means the variant is not promoted; it does not mean the result is discarded** — the run stays in the table with its numbers.

| Measurement | Gate |
| --- | --- |
| Warm retrieval p95, including bounded history expansion and selection | **<=50 ms and <=1.5x paired baseline** |
| Record / revise p95 | **<=2x paired baseline** |
| Persistent retrieval structures | **<=2x baseline**, counting every added index, projection and mapping |

For T1, "paired baseline" was `exact`. **From T2 onward the ratios are anchored to a fresh, paired measurement of the pinned `exact` baseline, not to the incoming stage baseline** — see [Resource ceilings](#resource-ceilings--anchored-to-exact-not-to-the-previous-winner). Otherwise successive promotions would multiply the allowed cost.

Measurement conditions, fixed in advance:

- Fixtures of **1,000 and 10,000 live memories**, averaging three revisions each, with text-size, tag and history-length distributions recorded alongside the results.
- A **long-history slice** so the 20-revision expansion limit actually binds rather than being nominal.
- Same machine, same interpreter, same load order, same durability settings for both halves of every pair.
- **Three blocks of 200 measured queries** after warm-up. Block-level results are retained, not just the pooled p95.
- Disk accounting compares **checkpointed** databases and reports total database size separately, so a growing outbox cannot conceal an expensive retrieval structure.

## Quality gates — registered 2026-09-06

Narrow on purpose.

| Target | Gate |
| --- | --- |
| T1 morphology, T2 historical vocabulary | Recover the **predeclared missing candidates** while preserving exact identifier and path checks. Per-query recall losses are reported, not netted out. |
| T0 abstention, T3 selection | **At least 50% fewer grade-0 context bytes**, with no reduction in grade-2 recall or task coverage on the development set. Useful grade-1-only evidence is preserved. |

## T2 and T3 — authorisation (2026-09-06)

`stem` is accepted as the **development baseline** and T1 is closed. T2 and T3 are authorised to run under the limits below. The limits apply to both stages; the differences between the stages are the fusion-input limit (T2 only), the baselines, and the success contracts.

### Budgets — identical for T2 and T3

| Budget | T2 and T3 limit |
| --- | --- |
| Candidate pool | **20 distinct eligible memories**, counted after resolving historical matches to current heads and deduplicating memory ids |
| History expansion | **At most 5 candidate memories × 20 revisions** |
| Delivered evidence | **At most 5 items and 8,192 UTF-8 bytes**, provenance included in the byte count |
| Configuration search | Baseline plus **at most 3 predeclared variants per stage** |
| Retrieval dependencies | **No network calls and no model calls** in the retrieval path |

The first-round accounting rules carry over unchanged: misses are attributed to the specific limit that caused them, candidate recall and delivered recall are always reported separately, and a returned revision id is discovery rather than delivered answer evidence.

### T2 only — fusion input limit

A T2 variant may take **at most 40 raw index hits in total**. For a two-channel variant that is **20 current-head hits and 20 historical hits**.

- **Scope and forgotten-status filtering happens before those limits**, so a row that would be filtered out never occupies one of the 40 slots.
- **Deduplication to the 20-memory candidate pool happens after.**
- Every run **records duplicate collapse** — how many of the 40 raw hits resolved to the same memory — **and truncation**, meaning whether either channel reached its own limit with more hits available.
- A channel that returns fewer than its limit is **not silently refilled** from the other channel to reach 40. The shortfall is recorded as a shortfall.

**This bounds fusion input. It does not bound SQLite's internal scan work** — the engine may examine any number of rows to produce those hits, and nothing in this limit constrains that. Any claim about engine-side cost has to come from the performance measurement, not from this budget.

### Baselines — pinned, one per stage

| Stage | Baseline |
| --- | --- |
| T2 | Pinned `stem`, with the existing selection policy unchanged |
| T3 | The pinned T2 winner, or **`stem` if no T2 variant passes** |

T3 **freezes candidate generation.** Selection may choose which five candidate histories to expand; it may not change what becomes a candidate. A T3 variant that alters candidate generation is a different experiment and voids the comparison.

### Resource ceilings — anchored to `exact`, not to the previous winner

Every ratio below is measured against a **fresh, paired measurement of the pinned `exact` baseline**, taken in the same run as the variant. Anchoring to `exact` rather than to the incoming baseline is deliberate: successive promotions must not be able to multiply the allowed cost, so T3 is not entitled to 1.5× of a T2 winner that already spent 1.5× of `exact`.

| Measurement | Ceiling |
| --- | --- |
| Warm retrieval p95, including expansion and selection | **≤50 ms and ≤1.5× `exact`** |
| Record / revise p95 | **≤2× `exact`** |
| Persistent retrieval structures | **≤2× `exact`**, counting every added index, projection and mapping |

Measurement conditions, carried over from T1 and tightened by what T1 showed:

- **Both fixture sizes are retained** — 1,000 and 10,000 live memories. Reporting one size is not a result. T1's 1,000-memory figures already registered gate failures that the 10,000 figures then showed the severity of.
- **The worst block's p95** is the reported statistic, not a pooled one. Block-level figures are retained.
- **Baseline and variant order is counterbalanced within the measurement schedule**, not run forward once and reversed once afterwards. T1's 1,000-memory ratios swung far enough across orders to flip a verdict in both directions, including for the promoted profile.
- **Failures are recorded, not discarded.** A variant that exceeds a ceiling keeps its row and its numbers, and a gate failure inside a promoted profile's own measurements is reported rather than summarised away.

### T2 success contract

All four, reported per query:

1. **Recover the predeclared historical-vocabulary cases** — terms that exist only in superseded revisions.
2. **Resolve each to the correct eligible current head.** A match that cannot be resolved to a live, in-scope head is not a recovery.
3. **Preserve matched-revision provenance** through that resolution: which revision matched, and which current head it belongs to.
4. **Preserve the control where the old term still appears in the current head.** Historical matching must not automatically prefer obsolete evidence — a rule that always prefers history scores correctly on the target cases and is wrong.

When the **historical revision itself supplies the answer**, retrieving its content **counts against the same history-expansion budget** — including when that revision lies outside the most recent twenty. Being the answer does not earn it a separate allowance.

**Every per-query candidate-recall and delivered-recall loss is reported**, not netted against gains elsewhere.

### Controls — extended to every candidate-generating index

The current empty-index control clears `head_fts` only. Once historical revisions generate candidates, **clearing the head index alone no longer tests the complete retrieval path**: a variant could return results entirely from a historical index and the control would still pass, proving nothing.

From T2 onward the control must clear **every index that can generate a candidate** — head, historical, and any further index a variant introduces — and still return zero results, with authoritative rows and the head projection retained. Adding a candidate-generating index without adding it to the control is a defect in the control, not a passing run.

### T3 success contract

Measured against **its own pinned baseline**, on the **same query set**:

- **Reduce aggregate delivered grade-0 bytes by at least 50%.**
- **No per-query loss of grade-2 recall, and no loss of task coverage.**
- **Preserve useful grade-1-only evidence** — evidence that is the only support a query has.

**Omitted answer content cannot count as delivered evidence merely because its revision id survived.** A surviving revision id is discovery. If the content was not delivered, the query did not receive that evidence, and any later fetch of it spends the delivered-context budget.

The 50% figure is an **engineering target, not a predicted outcome.** A variant that cuts grade-0 bytes by 30% with no recall loss is a recorded result that did not clear the gate; it stays in the table with its numbers.

### v2 stays frozen as the regression check

The v2 protocol is unchanged by either stage. Each stage's chosen configuration is carried into v2 **unchanged**, and the result says whether a known failure moved — not that retrieval improved, and not as a fresh evaluation.

## Order of work

Morphology, then historical matching, then selection and rejection. Each mechanism's effect is established independently before any combination is tried. Configurations are chosen on the development corpus and queries; the chosen configuration is then carried **unchanged** into v2 as a regression check — a check that a known failure moved, not a fresh evaluation.

## Runtime, recorded with every measurement

All development tests and measurements run under `.venv-sqlite/bin/python`, with its executable path and linked SQLite version recorded in the result file. The `uv`-resolved interpreter ships SQLite 3.50.4, below this project's 3.51.3 floor, and raises `UnsupportedRuntime` on every database test. **That failure is an environment mismatch and is reported separately from the 60 passing tests** — it is never counted as a test result.

## Registration, before any v2 result is inspected

Every configuration considered gets a row **before** it is run, and the budget is fixed in advance. Filling this table after seeing outcomes is how a search over configurations gets reported as a single principled choice.

The configuration-search budget is **baseline plus at most three predeclared variants per experiment**. A row with no configuration named has not been authorised to run.

### T1 — morphology (complete)

Index profile is the only thing that varies. Every variant is run against the same development corpus, the same queries and the same budgets as the baseline.

| # | Profile | Configuration | What it changes | Registered on | Outcome |
| --- | --- | --- | --- | --- | --- |
| B | `exact` | Current build: one FTS5 index, default `unicode61`, no stemming | *(nothing — the paired baseline)* | 2026-09-06 | Baseline. Morphology candidate recall 0.714. |
| 1 | `stem` | One FTS5 index, `porter unicode61`, applied to the whole body | Tokenizer for every token, prose and code alike | 2026-09-06 | **Promoted.** Recall 1.000; retrieval 1.02–1.04x at 10,000, storage 0.99x. **Recorded failure: 1.86x at 1,000 in forward order** (0.89x reversed), judged to be run position rather than profile. Costs identifier-query precision. |
| 2 | `dual` | Two indexes over the same bodies — exact and porter — matched disjunctively, ranked best-of | Adds a second index; exact matching preserved by construction | 2026-09-06 | Not promoted. Recall 1.000, storage 1.26x, but retrieval p95 **8.6–8.9x** baseline at 10,000 memories — and already over the 1.5x ratio gate at 1,000 in both orders (1.74x / 1.63x). |
| 3 | `split` | Exact index over everything, porter index over **prose tokens only**; code-shaped tokens are routed to the exact index on both the index and the query side | As `dual`, but stemming never sees identifiers, paths or symbols | 2026-09-06 | Not promoted. Recall 1.000 **and** the only variant preserving identifier-query precision, but retrieval p95 **7.9–9.2x** at 10,000 and storage **2.27x**. At 1,000 it measured 2.04x forward and 0.99x reversed. |

Predeclared before the run: `stem` is expected to recover morphological misses and to be the most likely of the three to damage exact reference queries; `split` is expected to be the most conservative and the most complex; `dual` sits between them and is the one whose ranking rule is least principled, since best-of across two indexes compares BM25 scores computed over different corpus statistics.

Measured, in [results-dev-t1.md](../dev/results-dev-t1.md): the prediction about quality held — `split` was the only variant that left identifier-query pools untouched — and the prediction about cost was wrong in shape. The two-index profiles were not merely more complex; both are an order of magnitude slower at 10,000 memories. `EXPLAIN QUERY PLAN` shows a temp B-tree over both legs' match sets, which is consistent with that and does not isolate its contribution — a high-level strategy description is not a runtime measurement. **Neither implementation is viable as built**; that is a rejection of these two configurations, not of two-index designs in general. The parsimonious variant is the one that survives, and it survives carrying the precision cost `split` was designed to avoid.

### T2 and T3 — authorised to run, 2026-09-06

Limits registered in both rows. The full contracts are in [T2 and T3 — authorisation](#t2-and-t3--authorisation-2026-09-06).

| # | Target | Configuration | What it changes | Baseline | Registered limits | Registered on | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | T2 | Historical-term discovery with matched-revision and current-head provenance | Candidate generation over non-head revisions; result shape | Pinned `stem`, existing selection policy unchanged | Pool **20 distinct eligible memories** · history **5 memories × 20 revisions** · delivered **5 items / 8,192 UTF-8 bytes** incl. provenance · **≤3 predeclared variants** per stage · **no network or model calls** · **fusion input ≤40 raw index hits** (20 current-head + 20 historical) | 2026-09-06 | *authorised, not yet run* |
| 5 | T3 | Ordering and weak-match rejection under a fixed expansion budget | Ranking and cutoff only; candidate generation **frozen** | Pinned T2 winner, or `stem` if no T2 variant passes | Pool **20 distinct eligible memories** · history **5 memories × 20 revisions**, selection choosing which five · delivered **5 items / 8,192 UTF-8 bytes** incl. provenance · **≤3 predeclared variants** per stage · **no network or model calls** | 2026-09-06 | *authorised, not yet run* |

### T0 — not authorised to run

| # | Target | Configuration | What it changes | Registered on | Outcome |
| --- | --- | --- | --- | --- | --- |
| 6 | T0 | *(abstention configuration — not yet proposed)* | | | *not run* |

Rules for the table:

- A configuration that is tried and abandoned stays in the table with its outcome. Deleting rows turns a search into a story.
- The budget is the number of configurations allowed, agreed before the first run. Exceeding it is recorded as exceeded, not renegotiated silently.
- Exactly one configuration per target is carried forward. If two look equally good on development data, that tie is broken on development data or by argument — never by trying both against v2.
- **One target at a time.** Two rows changed in one run void the comparison for both.

## What a v2 figure after this work will and will not mean

It will mean: the configuration chosen on development data, applied unchanged, produces *this* result on v2.

It will not mean that the change generalises, and it is not a fresh evaluation of v2. Two unanswerable queries against 25 items is a smoke test; a rule that abstains correctly on both is not thereby shown to abstain correctly on anything else. The same holds for the other three targets: v2 tells you whether a known failure moved, not whether retrieval got better.
