# Retrieval benchmark v2 — scope, locked before v1 measurement

Locked on 2026-09-05, **before any v1 relevance score has been produced or seen**. Every change below is justified by a coverage gap or a disclosure defect identified during v1's review, not by a result.

Anything added to v2 *after* v1's scores are read is a **development case**, recorded as such and never used as evaluation data. That boundary is the whole point of locking this now.

## Assessor status

Rewording v2's intents does not restore blindness: both the implementer and the judge now know the corpus and the v1 labels. Use a fresh assessor for re-judging if one is available. Otherwise record the continued exposure and treat v2 as a **revised diagnostic benchmark**, not a clean-room evaluation. It cannot carry a headline claim about retrieval quality on its own.

## Two temporal dimensions, recorded separately

v1 collapsed these into one `category` field and mislabelled q05 as a result.

| Dimension | Values |
| --- | --- |
| Information need | current state · past state · explanation of a change |
| Required evidence | current revision · historical revision · both |

q05 is *explanation of a change* + *current revision*: the current head carries both the old cap and the rationale. That is legitimate coverage, not a mistake.

## Locked changes

### Rewrites — remove collection commentary, preserve the need

| Query | New intent | Measures |
| --- | --- | --- |
| q02 | Find what retry behaviour exists across the system. | Broad recall across retry behaviour |
| q06 | How should callers reuse idempotency keys, and what can go wrong? | Contract and failure evidence |
| q08 | How should we deploy a schema change that requires a backfill? | Operational procedure and applicable constraints |
| q09 | What are the rules about writing sensitive values to logs? | Sensitive-value logging rules |
| q11 | Who is responsible for payment terminal firmware releases? | Firmware release responsibility |

q02 keeps its breadth; the point is to state the need without announcing that unrelated senses exist. Its v1 labels are **not** carried over — a reworded intent requires re-judging.

### Additions — the coverage v1 cannot provide

**A. Historical evidence access.** A question whose answer requires an old revision, where the query terms are *also present in the current head*.

Example shape: "what was the deploy schedule before it changed?" — shares deploy vocabulary with i12 (current), so search can find the memory, and history must then supply i23. Measures whether search-then-history closes the gap.

**B. Discovery through obsolete terminology only.** A question identified by a term that exists *only* in an old revision.

Example shape: a question using terminology that appears in no current head at all. Search cannot find the memory, so history is never reached, because history requires a memory ID that nothing supplied. **A history endpoint alone cannot solve this case** — it is the deeper gap, and it is the one B1's README claims most prominently.

These two must remain separate cases. Case A can succeed through shared vocabulary while case B still fails completely; collapsing them would hide that.

**C. Narrow retry question with distractors.** A retry question scoped to one mechanism, with the other retry mechanisms (CI flake retries, search index retries) present as distractors. Restores the disambiguation measurement that v1's q02 lost.

## Corpus consequences

Additions B and C may require new corpus items; A is satisfiable with existing items. Any new item is added to the corpus **before** re-judging, and the full v2 corpus is re-judged as a unit rather than patching labels onto v1's.

## What v2 still will not measure

- Semantic similarity of any kind. There are no embeddings.
- Whether a retrieved memory is *true*, or still applicable to the code as it stands.
- Anything about repository-verified commits or paths — that is B2.
- Performance at any realistic corpus size. 23 items measures behaviour, not scale.

---

## Finalisation, recorded after v1 results were read

Everything above this line was locked on 2026-09-05 before any v1 relevance score existed. Everything below was written after v1 run 2 was measured, and is marked as such.

| Locked change | How it was realised | New corpus item? |
| --- | --- | --- |
| Five intent rewrites | Query text unchanged, intents rewritten as tabled above; v1 wording preserved in `corpus-v2.json` under `v1_intent` | No |
| **A.** Historical evidence access | `q15` — "what was the deploy schedule before it changed" | No. `m17` already had the shape: `deploy` in the current head, the old cadence in `m17r1`. |
| **B.** Obsolete terminology only | `q16` — "nightflow" | **Yes**, `m21`: a pure rename (`nightflow` → `sweepcheck`) whose old name appears in no current head. |
| **C.** Narrow retry with distractors | `q17` — "how many times does search retry the index" | No. `m01` and `m04` are already lexically adjacent distractors. |

The concrete wording of these three queries, and the content of `m21`, were authored **after** 0.857 was known. That is a different freeze point from the scope lock above, and [v2-packet.md](v2-packet.md) states both. Locking the scope early means the *choice of gaps* was not selected to flatter a number; it does not mean the examples predate the measurement.

One thing this section deliberately does not do: revisit any query outside the five locked rewrites. `q03` and `q14` carry intents that presuppose a procedure exists, which is the same defect class as v1's `q06`/`q08` flags. Fixing `q14` after learning that abstention is the weak spot would be a post-result change to evaluation data, so it stays as written and is recorded as a residual instead.
