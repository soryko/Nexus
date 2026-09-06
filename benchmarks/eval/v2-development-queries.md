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

## T1-C — performance confirmation (registered 2026-09-06)

**A prospective measurement amendment, not a fourth tokenizer configuration.** It does not consume a slot from the three-variant configuration-search budget: no new configuration is proposed, and nothing about `stem` or `exact` changes. It re-measures two profiles already registered, under a corrected harness and a schedule designed to test order sensitivity directly.

### Why it exists

`stem` exceeded the registered retrieval gate at 1,000 memories in forward order:

| Order | `stem` p95 | `exact` p95 | Ratio | Against ≤1.5× |
| --- | ---: | ---: | ---: | --- |
| Forward | 25.9749 ms | 13.9946 ms | **1.8561×** | **fail** |
| Reversed | 16.0233 ms | 17.9944 ms | 0.8905× | pass |

This charter says that **exceeding a gate prevents promotion**. `stem` was nonetheless promoted on 2026-09-06 by selecting the 10,000-memory result and treating the 1,000-memory disagreement as run position. That **changes the rule rather than satisfying it**, and the promotion is withdrawn.

What the disagreement between orders licenses is an investigation into measurement instability. **It does not establish that run position explains the failure** — that is a hypothesis, and T1-C is the measurement that tests it.

### Harness defect fixed first

The two T1 harnesses enforced **different delivery budgets**, so they timed and gated different paths:

- The **quality** harness counted `len(content) + len(serialized provenance)` against the 8,192-byte budget.
- The **performance** harness counted **body bytes only**.

Different byte accounting yields a different number of delivered items, hence a different number of `get()` calls inside the timed region. The measurement therefore did not time the path the gate governs.

The fix is a **single label-free, budgeted retrieval function shared by both harnesses**, with provenance counted in both. Diagnostics (the over-budget probe that separates "never a candidate" from "cut off by the pool limit") and relevance scoring stay **outside** the timed section and outside the shared function. **This discrepancy does not establish the cause of the timing swing**; it is fixed because measuring the wrong path invalidates the measurement either way.

### The decision rule — registered before the run

1. **Profiles: `exact` and unchanged `stem` only.** No third profile. `dual` and `split` are already rejected on 10,000-memory figures that are stable across orders; neither needs another run to resolve this decision.
2. **Both fixture sizes retained** — 1,000 and 10,000 — with workload generation, seed, interpreter, durability settings and fixture construction unchanged from T1.
3. **Application and harness revisions are pinned** in the result record.
4. **Six paired blocks per fixture: three `exact→stem` and three `stem→exact`, interleaved** in the schedule rather than run as two consecutive runs. Each pair uses **identical queries**: 50 warm-up queries followed by 200 measured queries per profile, from equivalently prepared database copies.
5. **Retrieval only.** Writes are excluded from this experiment; the write gate is not under review here.
6. **Decision rule:** within each order, take **each profile's worst block p95**. `stem` passes only if it is **≤50 ms and ≤1.5× `exact` in both orders at both fixture sizes** — four ratios, all of which must hold. Individual query durations and per-block results are retained.
7. **The complete schedule runs once.** No early stopping, no dropping blocks, no additional retries until a pass appears. The first complete run is the result.

### Outcome binding

- **If `stem` clears the rule**, it is promoted on the new evidence, and T2's baseline is pinned `stem`.
- **If it does not**, `exact` is T2's baseline, and T3's fallback is amended to `exact` accordingly.

Either way, **the original failure and both original records are preserved**. T1-C adds evidence; it does not replace `results-dev-t1-perf.json` or `results-dev-t1-perf-reversed.json`, and it does not edit the numbers already reported.

### Outcome — run once on 2026-09-06, pinned revision `f82138a`

**`stem` cleared the rule in all four cells and is promoted.** Full report: [results-dev-t1c.md](../dev/results-dev-t1c.md).

| Fixture | Order | `exact` worst-block p95 | `stem` worst-block p95 | Ratio | Cell |
| --- | --- | ---: | ---: | ---: | --- |
| 1,000 | `exact→stem` | 7.4948 ms | 8.3850 ms | 1.1188x | pass |
| 1,000 | `stem→exact` | 7.5304 ms | 8.9346 ms | 1.1865x | pass |
| 10,000 | `exact→stem` | 18.0782 ms | 20.8957 ms | 1.1559x | pass |
| 10,000 | `stem→exact` | 19.0476 ms | 18.5715 ms | 0.9750x | pass |

The verdict holds under a stricter statistic too: computing the ratio **within each pair**, so blocks measured minutes apart are never compared, the worst of all twelve pairs is **1.3611x**. Both readings clear 1.5x; neither clears it by a wide margin, and per-pair ratios span 0.9001x to 1.3611x.

The paired reading is stricter by arithmetic: for positive paired block p95s `s_i` and `e_i`, `max(s_i)/max(e_i) <= max(s_i/e_i)`, since `s_i <= r*e_i` for every pair forces `max(s_i) <= r*max(e_i)`. Selecting each maximum independently can mask a bad pair. Both statistics are computed from the same 4,800 durations, so their agreement is a sensitivity check on one dataset, **not independent replication**.

**What the 1.8561x was made of.** T1's per-block figures show the registered statistic divided `stem`'s worst block by `exact`'s worst block when those were **different blocks running different query sets** — `stem` block 1 at 25.9749 (siblings 9.7370, 12.2273) over `exact` block 3 at 13.9946 (siblings 7.7502, 6.4850). Underneath sits a real paired anomaly: on block 1's queries `stem` measured 3.35x `exact`. T1-C's measured block is byte-for-byte T1's block 1, and across six fresh blocks `stem`'s worst there is 8.9346 ms. **The spike did not recur; its cause remains unresolved.** First-touch cost on the freshly rebuilt stem index is a candidate mechanism, but every T1-C block rebuilds that same index and none of the twelve spiked, so non-recurrence does not identify the cause. Neither cache causation nor an absence of production impact was established.

**Two limits on this outcome.** The registered statistic still selects its two maxima independently — it passed because no extreme block was drawn, not because the statistic was repaired, which is why the paired cross-check is reported beside it. And the spread it clears is wide enough that a rerun could plausibly produce a pair above 1.4x.

**Harness correction carried into T2 and T3.** The T1 harnesses rounded percentiles and ratios to 4 decimal places before comparing them to the gates. Gates now compare unrounded values; rounding is for display only. No T1 or T1-C cell is close enough to a gate for this to change a recorded outcome.

## T2 — predeclaration (registered 2026-09-06)

Full text, with the baseline row every variant is scored against: [t2-predeclaration.md](../dev/t2-predeclaration.md). Committed **before the first variant existed as code**.

**Corpus.** `corpus-dev2.json` = `corpus-dev1.json` **plus** two memories and four queries. Nothing in dev1 is edited, so dev1 stays frozen and `results-dev-t1.json` stays reproducible. The extension exists because three registered T2 requirements are untestable on dev1: resolution to an *eligible* head (dev1 has no forgotten memory), a revision outside the most recent twenty (dev1's deepest history is two), and the head-versus-history contest in prose rather than on an identifier. `dq21`/`dq24` restore the pairing rule for the new unanswerable case.

**Registered interpretation of the out-of-twenty clause.** A revision matched directly by a historical index is reachable, and delivering it consumes one of the five history-expansion slots. The twenty-revision limit bounds *enumerating* a memory's history; it does not put a directly matched revision out of reach. The opposite reading would make `dq22` unrecoverable by construction.

**The three variants**, all sharing one new structure — `revision_fts`, an FTS5 index over non-current revision bodies, tokenised as the head index is, with the authoritative eligibility join applied inside the query so a filtered row never occupies a fusion slot:

| # | Variant | Index scope | Fusion order |
| --- | --- | --- | --- |
| 1 | `history_headfirst` | every superseded revision | strict head priority, then history-only memories |
| 2 | `history_interleaved` | every superseded revision | one fused BM25 ordering across both channels, head priority only as a tie-break |
| 3 | `history_window3` | 3 most recent superseded revisions per memory | as variant 1 |

Variant 3 is **predicted, before the run, to miss `dq22`**; it is run to price the full-history index against a bounded one.

**Decision rule, registered before the run.** All six must hold: (1) `dq16` and `dq18` recovered as *delivered content* with matched-revision provenance resolved to the correct head; (2) `dq21` delivers nothing and `dq24` still answers at rank 1; (3) `dq17` and `dq23` still answer from the current head at rank 1; (4) no per-query recall loss on the dev1 queries and `dq08`–`dq11` still rank 1; (5) the resource ceilings against a fresh paired `exact`; (6) fusion input ≤40 raw hits with collapse, truncation and shortfall recorded. Among variants that pass, the winner is the one that also recovers `dq22`, then lowest added retrieval-structure bytes, then lowest retrieval p95 ratio. **If none passes, `stem` with head-only candidate generation stands as T2's outcome and T3's baseline is `stem`.**

## T2 and T3 — authorisation (2026-09-06)

**T2 and T3 are authorised and their baselines are now pinned.** `stem` was promoted on 2026-09-06 after clearing [T1-C](#t1-c--performance-confirmation-registered-2026-09-06) in all four cells — not on the T1 measurement, which it failed. T2's baseline is pinned `stem`.

The limits below apply to both stages; the differences between the stages are the fusion-input limit (T2 only), the baselines, and the success contracts.

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
| T2 | **Pinned `stem`**, with the existing selection policy unchanged. Pinned 2026-09-06 on the T1-C result. |
| T3 | The pinned T2 winner, or **`stem`** if no T2 variant passes. |

`exact` remains the shipped default and the anchor for every resource ratio; promoting `stem` as the development baseline does not change either.

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

### T1 — morphology (complete; `stem` qualified by T1-C)

Index profile is the only thing that varies. Every variant is run against the same development corpus, the same queries and the same budgets as the baseline.

| # | Profile | Configuration | What it changes | Registered on | Outcome |
| --- | --- | --- | --- | --- | --- |
| B | `exact` | Current build: one FTS5 index, default `unicode61`, no stemming | *(nothing — the paired baseline)* | 2026-09-06 | Baseline. Morphology candidate recall 0.714. |
| 1 | `stem` | One FTS5 index, `porter unicode61`, applied to the whole body | Tokenizer for every token, prose and code alike | 2026-09-06 | **Promoted on [T1-C](#t1-c--performance-confirmation-registered-2026-09-06), not on T1.** Recall 1.000; storage 0.99x. T1 retrieval: 1.02–1.04x at 10,000 but a **gate failure of 1.8561x at 1,000 forward** (0.8905x reversed) — promotion recorded 2026-09-06 and **withdrawn the same day**. T1-C then cleared it 4/4 (worst 1.1865x registered, 1.3611x paired). Costs identifier-query precision. |
| 2 | `dual` | Two indexes over the same bodies — exact and porter — matched disjunctively, ranked best-of | Adds a second index; exact matching preserved by construction | 2026-09-06 | Not promoted. Recall 1.000, storage 1.26x, but retrieval p95 **8.6–8.9x** baseline at 10,000 memories — and already over the 1.5x ratio gate at 1,000 in both orders (1.74x / 1.63x). |
| 3 | `split` | Exact index over everything, porter index over **prose tokens only**; code-shaped tokens are routed to the exact index on both the index and the query side | As `dual`, but stemming never sees identifiers, paths or symbols | 2026-09-06 | Not promoted. Recall 1.000 **and** the only variant preserving identifier-query precision, but retrieval p95 **7.9–9.2x** at 10,000 and storage **2.27x**. At 1,000 it measured 2.04x forward and 0.99x reversed. |

Predeclared before the run: `stem` is expected to recover morphological misses and to be the most likely of the three to damage exact reference queries; `split` is expected to be the most conservative and the most complex; `dual` sits between them and is the one whose ranking rule is least principled, since best-of across two indexes compares BM25 scores computed over different corpus statistics.

Measured, in [results-dev-t1.md](../dev/results-dev-t1.md): the prediction about quality held — `split` was the only variant that left identifier-query pools untouched — and the prediction about cost was wrong in shape. The two-index profiles were not merely more complex; both are an order of magnitude slower at 10,000 memories. `EXPLAIN QUERY PLAN` shows a temp B-tree over both legs' match sets, which is consistent with that and does not isolate its contribution — a high-level strategy description is not a runtime measurement. **Neither implementation is viable as built**; that is a rejection of these two configurations, not of two-index designs in general. The parsimonious variant is the one that survives, and it survives carrying the precision cost `split` was designed to avoid.

### T2 and T3 — authorised to run, 2026-09-06

Limits registered in both rows. The full contracts are in [T2 and T3 — authorisation](#t2-and-t3--authorisation-2026-09-06).

| # | Target | Configuration | What it changes | Baseline | Registered limits | Registered on | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | T2 | Historical-term discovery with matched-revision and current-head provenance | Candidate generation over non-head revisions; result shape | Pinned `stem`, existing selection policy unchanged | Pool **20 distinct eligible memories** · history **5 memories × 20 revisions** · delivered **5 items / 8,192 UTF-8 bytes** incl. provenance · **≤3 predeclared variants** per stage · **no network or model calls** · **fusion input ≤40 raw index hits** (20 current-head + 20 historical) | 2026-09-06 | *[predeclared](#t2--predeclaration-registered-2026-09-06) 2026-09-06 on `corpus-dev2.json`; baseline control measured; variants not yet run* |
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
