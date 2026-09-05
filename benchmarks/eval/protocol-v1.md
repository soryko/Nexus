# v1 measurement protocol — registered before execution

Registered before the harness was run and before any score existed. Deviating from this after seeing results invalidates the run; the correct response to a surprising number is a new run under an amended protocol, recorded as such.

## Configuration

| Parameter | Value |
| --- | --- |
| Corpus | `corpus-v1.json`, frozen — 23 revisions across 20 memories |
| Judgments | `judgments-v1.json`, frozen — 20 grade-2, 5 grade-1, 297 grade-0 |
| Retrieval cutoffs | k ∈ {5, 10, 20}, reported at every k |
| Search request | `limit = 20`, literal mode, no tag or kind filters, no cursor |
| History-expansion budget | `history(memory_id, limit=20)` for each **retrieved** memory, top-k only |
| Scope | one namespace, `eval-v1` / `local` |
| Build | the committed B1 implementation, unmodified for this run |

## How the corpus is loaded

Each memory is recorded at its first revision, then revised forward through its chain, so superseded revisions exist in history exactly as they would in real use. No memory is forgotten. Load order is the corpus order; ranking must not depend on it, and the determinism check below verifies that.

## Oracle prohibition

**History is expanded only for memory IDs that search actually returned.** Injecting a known target's memory ID — or expanding history for the whole corpus — turns the measurement into an oracle-assisted test and is forbidden. If search does not surface a memory, that memory's history is unreachable, and the result must record it as unreachable.

## Reported figures, never collapsed

1. **Current-head retrieval** — search alone. A judged item is *eligible* here only if its revision is a current head. Recall and precision at each k over eligible grade-2 items.
2. **Search-then-history** — search at each k, then bounded history expansion of returned memories. An item counts as covered if its revision appears in the expanded set.
3. **Task coverage** — the fraction of queries for which the pipeline surfaces at least one grade-2 item at each k.
4. **Per-query results** — every query listed individually, alongside the three aggregates. A query is never dropped for being difficult.

Aggregates are computed over the seven clean relevance queries. The five flagged queries are reported per-query and excluded from headline aggregates. Both sets appear in the output.

## Abstention, reported separately

q07 and q14 have no relevant items, so **recall is undefined and must not be reported** for them. Report instead: how many results were returned at each k, and whether the system returned anything at all. Their disclosed out-of-band exposure is printed alongside the numbers, not in a footnote.

## Negative controls, same corpus and configuration

1. **Empty index, eligible data present.** Clear the index structures while leaving the authoritative memories intact, then run the full query set. Any non-empty result proves retrieval is bypassing the index. Expected: zero results everywhere.
2. **Judged-relevant memories removed.** Forget every memory holding a grade-2 item, then re-run. Any reappearance proves the search is not reading current state. Expected: zero grade-2 coverage.

Neither control demonstrates good relevance. They detect a retrieval path that cannot fail and one that cannot succeed. A run in which the controls are not executed is not a valid run.

## Determinism

The full query set runs twice against the same database. Identical orderings are required. A difference means ranking depends on something outside the corpus and the query, which would invalidate every figure above.

## What this run cannot establish

A 23-item corpus judged by one exposed assessor, on queries authored by the implementer, five of which carry disclosure flags. It is a **reproducible baseline with explicit limitations**, not evidence of retrieval quality, and it supports no comparison against any other system.
