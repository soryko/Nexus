# v1 measurement results

Executed under [protocol-v1.md](protocol-v1.md) against the frozen benchmark. Two runs, because the first exposed a functional defect in search. **No label was changed, and the protocol was not amended.**

> Query strings below are quoted in full where they are the subject of a claim. Where a short label appears in a table, it is a **display label**, not the submitted string; the harness always submits the exact text in `corpus-v1.json`. The two labels used here are `q07` = "what is our database backup schedule" and `q14` = "how do we roll back a failed canary".

## Run 1 — B1 as committed at `b17b5e7`

| k | Current-head recall | Search-then-history recall | Task coverage |
|---|---|---|---|
| 5 / 10 / 20 | 0.143 | 0.143 | 1 / 7 |

Abstention: q07 and q14 returned **0** results at every k.

## What run 1 exposed

0.143 was implausible for queries like q13 "append only ledger" against an item reading "The ledger is append-only." Diagnosis showed the harness was correct and the implementation was not: `_match_expression` quoted the entire query as a single FTS phrase, making **literal mode a strict contiguous-phrase search**. Only q03 passed, because "payments integration suite" happens to appear verbatim.

### Two decisions, not one

The repair bundled two things that must be recorded separately, because the first does not imply the second:

1. **The phrase defect.** "Literal" was specified to mean *FTS operators are inert*, not *the text must appear verbatim*. Each token is now quoted individually. This is a correction against the written specification.
2. **The combination policy.** Individually quoted tokens still have to be combined, and nothing in "operators are inert" chooses between AND and OR. The tokens are combined **disjunctively**, leaving BM25 to rank. That is a deliberate recall-first policy choice, taken during a benchmark-informed repair. A conjunctive reading of the same literal text would have been equally consistent with the specification and would have produced different numbers everywhere below.

Disjunction is now stated explicitly in the README rather than left implicit in the fix.

### What run 2 therefore is

No numeric parameter was adjusted against the labels, and no result was inspected to choose a value. That is why run 2 is reportable. It is **not** a fresh evaluation: it is a benchmark-informed repair followed by a regression measurement on the same frozen set. Changing no numeric parameter does not remove that distinction — the defect was found *by* this benchmark, and the policy choice in (2) was made while its results were visible.

## Run 2 — after the literal-mode fix

| k | Current-head recall | Search-then-history recall | Task coverage |
|---|---|---|---|
| 5 / 10 / 20 | 0.857 | 0.857 | 6 / 7 |

Flagged queries (never averaged into the headline): 5/5 covered at every k.

Results are identical at k=5, 10 and 20. That establishes one thing only: **positions 6–20 recovered no additional judged-relevant item for the clean queries.** It does not establish that the cutoff never binds — it binds on result *volume* whenever more than five results come back, which is most of the corpus (see the abstention counts below, where k=5 truncates 8 results to 5 and 12 to 5). These figures say nothing about ranking quality at any realistic corpus size.

### The one clean failure

**q04 ("kerberos") fails completely, at every k, and no ranking change can fix it.** Its grade-2 answer i03 is the current OIDC decision, which contains the word "kerberos" nowhere.

This is a **candidate-generation gap**, precisely stated: the answer is never placed in the candidate set, so no reordering of that set can recover it. It is the strongest case in the run for keeping difficult queries in the headline.

It is *not* a demonstrated benefit from embeddings. At least two mechanisms could close it, and this run measures neither:

- **Semantic candidate generation.** An embedding index could place i03 in the candidate set for "kerberos". Plausible, unmeasured here, and it brings its own failure modes.
- **Historical lexical matching linked back to current heads.** "kerberos" *does* appear in the corpus — in the superseded i04. Indexing superseded revisions and resolving a match forward to its current head would find i03 by lexical means alone, with clear provenance ("matched an earlier revision of this memory"). No embeddings required.

Which one is worth building is an open question. v1 shows only that head-only lexical retrieval cannot answer it.

### What search-then-history contributed

The two recall columns are identical **for the clean set**, because no clean query has a grade-2 item in a superseded revision — exactly the gap recorded in `known_gaps.no_superseded_only_answer`.

The mechanism does work, and there is observed evidence for it in the flagged subset: q02 rises from 2 to 3 covered items once history is expanded, reaching the superseded i11. The precise statement is that **the clean headline cannot estimate the benefit of history browsing** — not that v1 shows no benefit at all. v2's two historical cases exist to put that measurement in the headline.

## The regression run 2 introduced

| Query (display label) | Run 1 results | Run 2, k=5 | Run 2, k=10 | Run 2, k=20 |
|---|---|---|---|---|
| q07 "database backup schedule" | 0 | 5 | 8 | 8 |
| q14 "canary rollback" | 0 | 5 | 10 | 12 |

Both queries have no relevant item in the corpus. **Run 2 has no abstention behaviour at all**: disjunctive token matching means common words match something, so an unanswerable question returns a confidently ordered list of irrelevant memories. An agent asking about backups gets eight wrong answers instead of none.

Run 1 scored *perfectly* on abstention — because search was broken in a way that made it return almost nothing. A metric that looks ideal for the wrong reason is exactly what the abstention measurement was built to catch, and it caught one.

**No fix is attempted here.** Abstention work is a ranking-behaviour change, and developing it against this frozen set would be tuning on evaluation data. It goes to the v2 development queries, which are never evaluation data.

## Negative controls

| Control | Result |
|---|---|
| Index emptied, 20 eligible memories still present | 0 results returned — **PASS** |
| 15 memories holding grade-2 items forgotten | 0 grade-2 results — **PASS** |

Determinism: two identical passes produced identical orderings in both runs.

Neither control demonstrates relevance. They detect a retrieval path that cannot fail and one that cannot succeed.

## What these numbers are not

0.857 is recall over **seven queries against 23 items**, on queries and a corpus authored on the implementation side, five further queries held out under disclosure flags.

Judging was a **two-party process, not a single assessor**: the assistant supplied the initial labels, and the project owner — a human — reviewed every one of them, which is what `judgments-v1.json` means by *assistant-judged, human-reviewed*. Both parties were already exposed to the corpus and its design before judging began, so this is **not independent assessment** and must never be described as such. It is not, however, one person labelling alone.

It is a reproducible baseline for detecting regressions in this system. It is **not** evidence of retrieval quality, and it supports no comparison against any other system.

## Corrections to this report

Applied after review, listed so the earlier wording is not silently replaced.

| Claim as first written | Correction |
|---|---|
| The literal-mode repair was one decision, described as "a functional bug fix, not ranking tuning" | Two decisions. The phrase repair follows from the specification; the OR-versus-AND combination policy does not, and was chosen during a benchmark-informed repair. |
| Run 2 presented as a clean re-measurement | Run 2 is a benchmark-informed repair followed by regression measurement. Changing no numeric parameter does not make it a fresh evaluation. |
| q04 is "the clearest evidence in the run for what a semantic index would buy" | q04 demonstrates a candidate-generation gap. Embeddings are one unmeasured candidate remedy; historical lexical matching resolved forward to current heads is another. |
| "with 23 items and 20 memories, the cutoff never binds" | Identical recall at k=5/10/20 shows only that positions 6–20 added no judged-relevant item for the clean queries. Cutoffs do bind on volume: q07 returns 8 and q14 returns 12. |
| "v1 cannot demonstrate the value of history browsing" | Too strong. History contributed observed evidence in the flagged subset (q02, 2 → 3 covered). The clean headline cannot *estimate* the benefit. |
| "judged by a single assessor who is the project owner" | Inaccurate description of the process. Two-party: initial labels from the assistant, full review of every label by the project owner. Still non-independent; both were exposed. |
| A first pass at that correction alleged that `judgments-v1.json` asserted the reverse ordering | Withdrawn — there was no conflict. "Assistant-judged, human-reviewed" always meant what it says; only the report's compression of it was wrong. The withdrawal is recorded in the amendments rather than deleted. |
| q07/q14 quoted in abbreviated form | Marked as display labels; the exact submitted strings are stated at the top of this report and live in `corpus-v1.json`. |

`protocol-v1.md` carries the same "one exposed assessor" phrasing in its closing paragraph. It is **left unedited** because it is a document registered before execution; the correction is recorded here and in the `judgments-v1.json` amendments instead.
