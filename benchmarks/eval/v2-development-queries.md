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

## The development corpus — not built yet

It is deliberately deferred until the v2 labels are frozen, so that authoring it cannot be influenced by what the v2 assessor decided. What it needs:

- Items on topics that do not appear in v2, so a development query cannot accidentally be answered by a v2 item.
- **Paired queries**: for each unanswerable development query, an answerable one of similar length and vocabulary. A rejection rule that is only ever tested on unanswerable queries will be tuned into refusing everything, and the pairing is what catches it.
- Deliberate near-misses: a question the corpus almost answers. Abstention that only fires on total vocabulary mismatch is not worth having.
- For T1: prose and non-prose items sharing a stem, so a stemmer that improves prose recall and damages identifier matching shows both effects in one run.
- For T2: superseded revisions whose vocabulary is genuinely gone from the current head, plus at least one where the old term still appears at the head — otherwise a rule that always prefers history looks correct.

## Registration, before any v2 result is inspected

Every configuration considered gets a row **before** it is run, and the budget is fixed in advance. Filling this table after seeing outcomes is how a search over configurations gets reported as a single principled choice.

Budgets below are **not yet fixed**. A row whose budget still reads *unset* has not been authorised to run.

| # | Target | Configuration | What it changes | Registered on | Budget (runs) | Development outcome |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | T1 | Prose stemming with exact-preserved code references | Tokenizer / analyzer; candidate generation | 2026-09-06 | *unset* | |
| 2 | T2 | Historical-term discovery with matched-revision and current-head provenance | Candidate generation over non-head revisions; result shape | 2026-09-06 | *unset* | |
| 3 | T3 | Ordering and weak-match rejection under a fixed expansion budget | Ranking and cutoff only; candidate generation unchanged | 2026-09-06 | *unset* | |
| 4 | T0 | *(abstention configuration — not yet proposed)* | | | *unset* | |

Rules for the table:

- A configuration that is tried and abandoned stays in the table with its outcome. Deleting rows turns a search into a story.
- The budget is the number of development runs allowed, agreed before the first one. Exceeding it is recorded as exceeded, not renegotiated silently.
- Exactly one configuration per target is carried forward. If two look equally good on development data, that tie is broken on development data or by argument — never by trying both against v2.
- **One target at a time.** Two rows changed in one run void the comparison for both.

## What a v2 figure after this work will and will not mean

It will mean: the configuration chosen on development data, applied unchanged, produces *this* result on v2.

It will not mean that the change generalises, and it is not a fresh evaluation of v2. Two unanswerable queries against 25 items is a smoke test; a rule that abstains correctly on both is not thereby shown to abstain correctly on anything else. The same holds for the other three targets: v2 tells you whether a known failure moved, not whether retrieval got better.
