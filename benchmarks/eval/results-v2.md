# v2 measurement results

Executed under [protocol-v2.md](protocol-v2.md) and its amendments A1–A5, all registered before this run. One run. **No label was changed, and no retrieval change has been made since.**

Raw harness output is preserved verbatim in `results-v2-raw.txt`; the complete machine record — build commit, input hashes, load order, revision mapping, every figure at every cutoff — is in `results-v2.json`.

| | |
| --- | --- |
| Build | `4c81821`, working tree clean in `src/` |
| Corpus | `corpus-v2.json` · sha256 `c884e32c9fad2123…` |
| Labels | `judgments-v2.json` · sha256 `ccdbe144e21b5f23…` |
| Protocol | `protocol-v2.md` · sha256 `66e86c2098566a21…` |
| Assessor sheet | `judging-v2-assessor.md` · sha256 `318a6fd33ec1145f…` |

Query strings are quoted in full throughout. No abbreviations, no display labels.

## Headline — eleven clean queries, unweighted mean

| k | Current-head recall | Search-then-history recall | Task coverage |
|---|---|---|---|
| 5 / 10 / 20 | **0.848** (11/11 contributing) | 0.848 (11/11) | 10 / 11 |

**This number is not comparable to v1's 0.857.** The corpus, the query set and the labels all changed. Anyone placing them side by side is comparing three changes at once.

Two clean queries carry the entire shortfall, and neither is a ranking problem.

## The two candidate-generation failures

### q04 — "kerberos" — 0 results, every k

Replicated exactly from v1, now against labels produced by an assessor who never saw v1's. The grade-2 answer is the current OIDC decision, which contains the query term nowhere. The answer never enters the candidate set, so no reordering can recover it.

### q02 — "retry policy" — 0.333, and this one is new

Three grade-2 items: the card-charge policy, the CI retry job, and the search-index retry. Search returned **three results total** and found one of the three.

The cause is morphological, and it is checkable against the corpus text rather than inferred. The query tokens are `retry` and `policy`. The two missed items share **zero tokens** with the query: both say `retries`, and FTS5's default tokenizer does not stem, so `retry` and `retries` are unrelated tokens. Neither item was ever a candidate.

| Grade-2 item | Tokens shared with "retry policy" |
| --- | --- |
| `m01r2` — card charge policy | `retry`, `policy` — **found** |
| `m04r1` — CI retry job | *none* |
| `m10r1` — search index retries | *none* |

This is a *second* candidate-generation gap, and it is not the same as q04's:

| | q04 | q02 |
| --- | --- | --- |
| Gap | Semantic — the answer shares no vocabulary with the query | Morphological — the answer shares a word *stem* but not a token |
| Would embeddings fix it? | Plausibly, unmeasured | Plausibly, but a stemmer or an analyzer change would too, far more cheaply |

Neither is fixable by ranking. Recording them separately matters because they argue for different work: v1's write-up would have folded this into the same "semantic index" argument, and that would have been wrong.

## The three cases v2 was built for

Flagged for post-result authoring, reported per-query, never averaged into the headline.

### Case A — q15 — "what was the deploy schedule before it changed"

| k | Was `m17` returned at all? | Superseded answer covered? |
|---|---|---|
| 5 | **no** | missed |
| 10 | yes | **covered** |
| 20 | yes | **covered** |

**Search-then-history works end-to-end.** This is the first direct evidence of it in the project: the only grade-2 answer for this query is a superseded revision, search reaches the memory through its current head, and history supplies the answer.

The diagnostic required by A1 earned itself immediately. The k=5 miss is **not** a history failure — the memory was not in the candidate set at all at that cutoff, so history was never given a memory id to expand. Without splitting those two, the k=5 row would have read as "history expansion failed", which is false. Its current-head recall is **N/A**, not 0.0: it has zero eligible current heads by construction.

### Case B — q16 — "nightflow" — 0 results, every k

Exactly as designed, and the failure is total. The term exists only in a superseded revision; head-only search returns nothing; history is never reached because history needs a memory id that nothing supplied. **A history endpoint alone cannot solve this case** — now demonstrated rather than argued.

Its two denominators are reported separately and must stay that way: current-head 1, search-then-history 2.

### Case C — q17 — "how many times does search retry the index" — covered at every k

The one grade-2 item is found at every k, and the CI-retry distractor — which the assessor graded 0 and flagged as the sheet's sharpest term-versus-content divergence — is returned as noise rather than as the answer.

One thing this case does **not** show: that retry vocabulary worked. `m10r1` shares `search`, `index` and `the` with this query, and *not* `retry` — the same morphological gap that sinks q02 is present here too, and q17 succeeds around it on other discriminative tokens. Two queries needing the same item, one finding it and one not, on the same collection and the same build.

## Cutoffs bind in v2

v1 reported identical figures at k=5, 10 and 20. **v2 does not**: q15 is missed at k=5 and covered at k=10. The v1 statement that "the cutoff never binds" was already corrected to a narrower claim about judged-relevant items; this run retires it entirely for v2.

## Queries with no direct answer

Recall is undefined and is not reported. Returned results are split by grade, because these three are not equivalent.

| Query | k=5 | k=10 | k=20 | Reading |
|---|---|---|---|---|
| "what is our database backup schedule" | 5 returned, all grade-0 | 9, all grade-0 | 9, all grade-0 | No abstention |
| "how do we roll back a failed canary" | 5, all grade-0 | 10, all grade-0 | 13, all grade-0 | No abstention |
| "payment terminal firmware" | **1 returned, grade-1** | 1, grade-1 | 1, grade-1 | Correct behaviour |

**q11 is the one to look at.** It returns exactly one result, and that result is the supporting item — the firmware release-train note — with nothing else attached. Under the adjudicated rule this is not an error and not a near-miss: it is the behaviour we want, and a protocol that demanded an empty result set for all three would have scored it as a failure. It is not abstention; the system returned something. It is the right something.

For the other two, the v1 finding stands unchanged: **this build has no abstention behaviour.** Nine and thirteen confidently ordered irrelevant results for questions the collection cannot answer. No fix was attempted here; that work belongs to the development corpus under `v2-development-queries.md`.

## Precision

`P@k = grade-2 hits / k`, reported with the actual returned count beside it, because most queries return far fewer than k. At k=20 only one clean query — q05 — fills the cutoff with 20 results; q01 returns 15, q09 returns 12, and the remaining eight return five or fewer, several just one or two. The k in the denominator is mostly padding, so **P@20 figures here measure the cutoff, not the ranking.** The returned counts in `results-v2-raw.txt` are the honest view.

*Correction (post-publication, no rerun): this paragraph previously stated that no clean query returns more than 15 results at k=20. That was wrong — q05 returns 20. The figures themselves are unchanged; the erroneous sentence described them.*

### Context volume — derived after the run, not a registered figure

Summed over the eleven clean queries, by grade of the returned items. This is arithmetic over `results-v2.json`, computed after the results were fixed; it was not registered in the protocol and is not part of the headline.

| k | Returned items | Grade 2 | Grade 1 | Grade 0 |
|---|---:|---:|---:|---:|
| 5 | 35 | 13 | 4 | 18 |
| 10 | 50 | 13 | 4 | 33 |
| 20 | 67 | 13 | 4 | 50 |

Raising k from 5 to 20 adds **32 grade-0 results and no additional relevant evidence** in the clean set — consistent with the headline being flat across cutoffs, and the same fact stated in units of context spent rather than recall. It does not generalise past this set: the flagged q15 needs the larger budget (missed at k=5, covered at k≥10, and its one grade-1 item appears only at k≥10). Cutoff choice therefore trades context volume against exactly the history-reaching case v2 was built to expose, which makes evidence selection — ordering and rejection of weak matches — a development target in its own right rather than a tuning knob.

## Negative controls — independent fixtures, strengthened forms

| Control | Result |
|---|---|
| FTS postings cleared only; 21 memories and 21 head-projection rows verified still present | 0 results returned — **PASS** |
| 15 memories holding grade-2 items forgotten; assert no revision of any of them reappears in search *or* history, at any grade | 0 reappearances — **PASS** |

Each ran from its own freshly built database. Chaining them, as v1 did in part, would let an emptied index make the deletion control pass for the wrong reason. The empty-index control is also strictly stronger than v1's, which deleted the head projection too and so proved less than it appeared to.

Neither control demonstrates relevance. They detect a retrieval path that cannot fail and one that cannot succeed.

## Repeatability, not ordering invariance

Two passes against one unchanged database produced identical orderings. That establishes **repeatability** and nothing else.

It does **not** establish independence from ingestion order, and this system does not claim that: B1's mutation-sequence tiebreaker makes insertion history relevant to ranking by design. v1's protocol asserted an invariance its check never tested; that claim is withdrawn. The load order used here is recorded in `results-v2.json`, so a future run can tell whether it is comparing like with like. No permutation test was run.

## What this run does not establish

A 25-revision corpus, 17 queries, labels from one AI assessor audited by two AI systems with no human re-deriving them, on a corpus and queries authored on the implementation side — three of them worded after this build's failure modes were known.

It is a reproducible diagnostic benchmark with explicit limitations. It is **not** evidence of retrieval quality, it supports no comparison against any other system, and **no figure here may be compared to a v1 figure**.
