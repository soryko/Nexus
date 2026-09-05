# v2 measurement protocol — registered before execution

Registered after the v2 labels were frozen and **before any v2 query had been run against any build**. Deviating from this after seeing results invalidates the run; the correct response to a surprising number is a new run under an amended protocol, recorded as such.

## Configuration

| Parameter | Value |
| --- | --- |
| Corpus | `corpus-v2.json`, frozen — 25 revisions across 21 memories |
| Judgments | `judgments-v2.json`, frozen — 20 grade-2, 10 grade-1, 395 grade-0 |
| Retrieval cutoffs | k ∈ {5, 10, 20}, reported at every k |
| Search request | `limit = 20`, literal mode, no tag or kind filters, no cursor |
| Literal mode | FTS operators inert **and** tokens combined disjunctively. Both halves are policy; see `results-v1.md`. |
| History-expansion budget | `history(memory_id, limit=20)` for each **retrieved** memory, top-k only |
| Scope | one namespace, `eval-v2` / `local` |
| Build | the committed B1 implementation at `4c81821`, unmodified for this run. No source file has changed since that commit. |

The harness is a generalisation of `run_v1.py` and must be written to this document, not the reverse. It has not been written at the time of registration.

## How the corpus is loaded

Each memory is recorded at its first revision, then revised forward through its chain, so superseded revisions exist in history exactly as they would in real use. No memory is forgotten. Load order is the corpus order; ranking must not depend on it, and the determinism check below verifies that.

## Oracle prohibition

**History is expanded only for memory IDs that search actually returned.** Injecting a known target's memory ID — or expanding history for the whole corpus — turns the measurement into an oracle-assisted test and is forbidden. If search does not surface a memory, that memory's history is unreachable, and the result must record it as unreachable.

This matters more in v2 than it did in v1. `q16` is constructed so that head-only search cannot reach the memory at all; the temptation to "help" it is exactly what the prohibition exists to stop.

## Partition, fixed before the labels existed

| Set | Members | Basis |
| --- | --- | --- |
| Clean — headline | `q01` `q02` `q03` `q04` `q05` `q06` `q08` `q09` `q10` `q12` `q13` | Declared exposure criteria, independent of labels |
| Flagged — post-result authoring | `q15` `q16` `q17` | Wording authored after v1 run 2 was measured |
| No direct answer | `q07` `q11` `q14` | Derived from the frozen labels: no grade-2 item |

"Clean" names a declared exposure category, **not** an untouched test set. The corpus and the implementation remain contaminated by v1 regardless.

Aggregates are computed over the eleven clean queries. The three flagged queries are reported per-query, prominently — they are the cases v2 exists to measure — and never averaged into the headline.

## Reported figures, never collapsed

1. **Current-head retrieval** — search alone. A judged item is *eligible* here only if its revision is a current head. Recall and precision at each k over eligible grade-2 items.
2. **Search-then-history** — search at each k, then bounded history expansion of returned memories. An item counts as covered if its revision appears in the expanded set.
3. **Task coverage** — the fraction of queries for which the pipeline surfaces at least one grade-2 item at each k.
4. **Per-query results** — every query listed individually, alongside the three aggregates. A query is never dropped for being difficult.

### Two denominators that must not be silently merged

`q16` has two grade-2 items: `m21r2` (current head) and `m21r1` (superseded). Its current-head denominator is **1**; its search-then-history denominator is **2**. Report both.

### The q15 diagnostic

For `q15`, report whether `m17` appeared in the returned set **at all**, separately from whether the superseded answer was covered. The query carries several common tokens, so a failure caused by common-word flooding would otherwise be indistinguishable from a failure of history expansion. These are different defects with different fixes.

## Queries with no direct answer, reported separately

`q07`, `q11` and `q14` have no grade-2 item, so **recall is undefined and must not be reported** for them. They are not equivalent to each other:

| Query | Evidence in the corpus |
| --- | --- |
| `q07` | None of any grade |
| `q14` | None of any grade |
| `q11` | Supporting evidence (`i18`, `m15r1`) but no item naming an owner |

Report, at each k, how many results were returned and how they split into **grade-1** and **grade-0** buckets. Returning `i18` for `q11` is not an irrelevant-result error, and a rule demanding an empty result set for all three would penalise useful context. An empty result set is the right answer for `q07` and `q14`; for `q11` the right answer is the supporting item without a fabricated owner.

## Abstention is measured, not developed here

v1 run 2 established that this build has no abstention behaviour. Any change to matching or rejection is developed against a separate development corpus and development queries under `v2-development-queries.md`, with the configuration and budget registered before v2 results are inspected. Exactly one configuration is carried here, unchanged.

## Negative controls, same corpus and configuration

1. **Empty index, eligible data present.** Clear the index structures while leaving the authoritative memories intact, then run the full query set. Any non-empty result proves retrieval is bypassing the index. Expected: zero results everywhere.
2. **Judged-relevant memories removed.** Forget every memory holding a grade-2 item, then re-run. Any reappearance proves the search is not reading current state. Expected: zero grade-2 coverage.

Neither control demonstrates good relevance. They detect a retrieval path that cannot fail and one that cannot succeed. A run in which the controls are not executed is not a valid run.

## Determinism

The full query set runs twice against the same database. Identical orderings are required. A difference means ranking depends on something outside the corpus and the query, which would invalidate every figure above.

## What this run cannot establish

A 25-revision corpus, 17 queries, labels produced by one AI assessor and audited by two AI systems with no human re-deriving them, on queries and a corpus authored on the implementation side — three of them worded after the implementation's failure modes were known.

It is a **reproducible diagnostic benchmark with explicit limitations**. It is not evidence of retrieval quality, it supports no comparison against any other system, and **no v2 figure may be compared to a v1 figure**: the corpus, the query set and the labels all changed at once. v1 remains the regression baseline for the build it measured.

---

# Amendments, registered before execution

Recorded after the protocol was first registered and **still before any v2 query was run against any build**. No label changes.

## A1 — Zero and split denominators, stated per query

A recall figure with a zero denominator is **N/A**, never 0.0. Reporting it as zero would let an unanswerable-by-construction case drag an average down as if the system had failed a question it was never asked.

| Query | Current-head denominator | Search-then-history denominator |
| --- | --- | --- |
| `q15` | **0 — N/A.** Its only grade-2 item is a superseded revision, so it has no eligible current head | 1 |
| `q16` | 1 | 2 |
| `q07` `q11` `q14` | 0 — N/A, no grade-2 item of any kind | 0 — N/A |
| all others | = number of grade-2 items, all of which are current heads | same |

The `q15` diagnostic stands unchanged and is reported alongside: **was `m17` retrieved at all?**, separately from whether the superseded answer was covered.

## A2 — Aggregation formulas, pinned

- **Headline recall** — the unweighted mean over the eleven clean queries, each query counting once regardless of how many grade-2 items it has. Queries whose denominator is N/A are excluded from the mean and the contributing count is reported (`n/11`), so exclusions can never be silent.
- **Precision** — `P@k = grade-2 hits / k`, with the **actual returned count** printed beside it. At k=20 against 25 revisions the denominator is mostly padding, and a precision figure without the returned count invites reading it as if the system had returned twenty things.
- **Search-then-history coverage** — the union of the search results and the revisions reached by expanding their history, **deduplicated by revision id**. Two distinct revisions of the same memory remain two entries; that is the whole point of the historical cases.
- **Task coverage** — the declared at-least-one-grade-2 hit rate, unchanged. It says a query surfaced *something* directly relevant. It does **not** establish that every part of a compound question was answered, and must never be described as if it did.

## A3 — Controls run from independent fixtures

Each destructive control loads its **own** freshly built database from the same corpus. Chaining them lets an emptied index make the subsequent deletion control pass for the wrong reason — nothing can reappear if nothing can be found.

- **Empty-index control:** clear **only the FTS postings**. The authoritative memories and the head projection stay in place, and the run asserts they are still there. v1 deleted the head projection too, which weakened the control: it proved less than it appeared to.
- **Deletion control:** after forgetting every memory that holds a grade-2 item, assert that **no revision belonging to any forgotten memory reappears anywhere** — in the search results or in history expansion — regardless of that revision's grade for the query at hand. The v1 form only checked for grade-2 reappearance.

## A4 — The determinism claim, narrowed

Two passes against one unchanged database establish **repeatability**, and nothing more. They do not establish independence from ingestion order: B1's mutation-sequence tiebreaker makes insertion history relevant to ranking **by design**, so ordering invariance is not a property this system claims.

The v1 protocol's phrasing — "ranking must not depend on load order, and the determinism check below verifies that" — asserted an invariance the check never tested. It is withdrawn. The load order is **recorded with the results** instead, so a future run can tell whether it is comparing like with like. No permutation test is required for this run.

## A5 — Run record

Recorded with the results, in the results artifact itself:

- the build commit, and whether the working tree was dirty in `src/`
- SHA-256 of every input file: corpus, judgments, this protocol, and the verbatim assessor sheet
- the exact submitted query string for every query, never an abbreviation
- the load order, and the mapping from live `(memory_id, revision_id)` pairs to normalised corpus revision ids and sheet item ids
