# v1 measurement results

Executed under [protocol-v1.md](protocol-v1.md) against the frozen benchmark. Two runs, because the first exposed a functional defect in search. **No label was changed, and the protocol was not amended.**

## Run 1 — B1 as committed at `b17b5e7`

| k | Current-head recall | Search-then-history recall | Task coverage |
|---|---|---|---|
| 5 / 10 / 20 | 0.143 | 0.143 | 1 / 7 |

Abstention: q07 and q14 returned **0** results at every k.

## What run 1 exposed

0.143 was implausible for queries like q13 "append only ledger" against an item reading "The ledger is append-only." Diagnosis showed the harness was correct and the implementation was not: `_match_expression` quoted the entire query as a single FTS phrase, making **literal mode a strict contiguous-phrase search**. Only q03 passed, because "payments integration suite" happens to appear verbatim.

"Literal" was specified to mean *FTS operators are inert*, not *the text must appear verbatim*. Each token is now quoted individually and combined disjunctively, leaving BM25 to rank; a caller wanting a phrase asks for one through advanced mode.

**This was a functional bug fix, not ranking tuning.** No parameter was adjusted against the labels, and no result was inspected to choose a value. The distinction is the reason run 2 is reportable at all.

## Run 2 — after the literal-mode fix

| k | Current-head recall | Search-then-history recall | Task coverage |
|---|---|---|---|
| 5 / 10 / 20 | 0.857 | 0.857 | 6 / 7 |

Flagged queries (never averaged into the headline): 5/5 covered at every k.

Results are identical at k=5, 10 and 20 — with 23 items and 20 memories, the cutoff never binds. These figures say nothing about ranking quality at any realistic corpus size.

### The one clean failure

**q04 ("kerberos") fails completely, at every k, and no ranking change can fix it.** Its grade-2 answer i03 is the current OIDC decision, which contains the word "kerberos" nowhere. Lexical retrieval cannot bridge a vocabulary gap between the query and the answer. This is the clearest evidence in the run for what a semantic index would buy, and the strongest argument for keeping difficult queries in the headline.

### Search-then-history added nothing to the headline

The two recall columns are identical for the clean set, because no clean query has a grade-2 item in a superseded revision — exactly the gap recorded in `known_gaps.no_superseded_only_answer`. The mechanism does work: q02 rises from 2 to 3 covered items once history is expanded, reaching the superseded i11. But q02 is a flagged query, so that improvement appears nowhere in the headline. **v1 cannot demonstrate the value of history browsing.** v2's two historical cases exist for this.

## The regression run 2 introduced

| Query | Run 1 results returned | Run 2 results returned (k=20) |
|---|---|---|
| q07 "database backup schedule" | 0 | 8 |
| q14 "canary rollback" | 0 | 12 |

Both queries have no relevant item in the corpus. **Run 2 has no abstention behaviour at all**: disjunctive token matching means common words match something, so an unanswerable question returns a confidently ordered list of irrelevant memories. An agent asking about backups gets eight wrong answers instead of none.

Run 1 scored *perfectly* on abstention — because search was broken in a way that made it return almost nothing. A metric that looks ideal for the wrong reason is exactly what the abstention measurement was built to catch, and it caught one.

**No fix is attempted here.** Abstention work is a ranking-behaviour change, and developing it against this frozen set would be tuning on evaluation data. It goes to development queries in v2.

## Negative controls

| Control | Result |
|---|---|
| Index emptied, 20 eligible memories still present | 0 results returned — **PASS** |
| 15 memories holding grade-2 items forgotten | 0 grade-2 results — **PASS** |

Determinism: two identical passes produced identical orderings in both runs.

Neither control demonstrates relevance. They detect a retrieval path that cannot fail and one that cannot succeed.

## What these numbers are not

0.857 is recall over **seven queries against 23 items**, judged by a single assessor who is the project owner and who was exposed to part of the corpus design, on queries written by the implementer. It is a reproducible baseline for detecting regressions in this system. It is **not** evidence of retrieval quality, and it supports no comparison against any other system.
