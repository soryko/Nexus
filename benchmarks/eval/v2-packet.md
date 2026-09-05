# Retrieval benchmark v2 — judging packet

The deliverable that must be complete *before* anyone judges anything. It fixes the corpus and the query wording, states what the assessor may and may not see, and records the two separate freeze points so that neither can later be reported as one.

**No v2 query has been run against any build.** Judging comes first; measurement is registered separately afterwards.

## Manifest

| File | Role | Frozen |
| --- | --- | --- |
| `corpus-v2.json` | 21 memories / 25 revisions, 17 queries, exact wording, analysis metadata | Corpus and query wording: **yes**. Labels: do not exist yet. |
| `judging-v2.md` | The assessor-facing sheet. Generated, never hand-edited | Regenerate with `python make_judging_sheet.py 2` |
| `make_judging_sheet.py` | Sheet generator, version-parameterised | `python make_judging_sheet.py 1` still reproduces `judging-v1.md` byte for byte |
| `v2-scope.md` | The coverage scope, locked before v1 results | Amended below with a post-results finalisation section |
| `v2-development-queries.md` | Abstention development charter — never evaluation data | Charter only; the development corpus is not built yet |
| `judging-v2-assessor.md` | The assessor's first response, saved verbatim | Never edited. Source of truth for every label. |
| `parse_assessor_sheet.py` | Mechanical sheet → judgments conversion | Re-runnable and diffable, so the transcription is checked rather than trusted |
| `judgments-v2.json` | Assessor labels, adjudications, final labels | **Frozen** — 20 grade-2, 10 grade-1, 395 grade-0 |
| `protocol-v2.md` | Measurement protocol | **Registered**, after the label freeze and before any v2 query was run |

## Two freeze points, not one

| Freeze point | When | What it covers |
| --- | --- | --- |
| Coverage scope | 2026-09-05, before any v1 score existed | Which gaps v2 must close: the five intent rewrites, cases A/B/C, the two-dimension temporal recording |
| Concrete examples | After v1 run 2 results were read | The exact wording of q15/q16/q17 and the `m21` rename memory that case B requires |

Locking broad scope before the first measurement does **not** turn later concrete examples into data that predates it. What the earlier lock does buy is narrower and still worth something: the *choice of gaps* was not selected to flatter a number, because no number existed. The examples that fill those gaps were authored by someone who had seen 0.857 and knew where the system failed. Both facts belong in any report that cites a v2 figure.

## What is new in v2

### Case A — historical evidence access (`q15`, "what was the deploy schedule before it changed")

The query shares the token `deploy` with the **current** head of `m17` ("no fixed deploy window"), so search can reach the memory; the answer the question asks for lives in the superseded `m17r1`. No new corpus item was needed.

*Analysis requirement:* report whether `m17` appeared in the returned set **separately** from whether the superseded answer was covered. The query carries several common tokens, so a failure from common-word flooding would otherwise be indistinguishable from a failure of history expansion. Verified from corpus text, not by running search: `deploy` occurs in three current heads (`m07r1`, `m08r1`, `m17r2`); `schedule` occurs in no revision at all.

### Case B — discovery through obsolete terminology only (`q16`, "nightflow")

New memory `m21`, a pure rename: `nightflow` → `sweepcheck`, with the rest of the content unchanged. The old name appears in exactly one superseded revision and **in no current head** (verified over the corpus text: zero head-matching revisions for this query).

Head-only search cannot surface the memory at all, so history is never reached — history needs a memory id that nothing supplied. **A history endpoint alone cannot solve this case.** It is kept distinct from `q04` ("kerberos"), where the obsolete term and the answer belong to different technologies rather than to a rename; collapsing the two would hide that a rename is the easier half of the problem.

### Case C — narrow retry question with distractors (`q17`, "how many times does search retry the index")

Scoped to one retry mechanism with the others lexically adjacent: `m01` (card charge retries), `m04` (the CI job **for the search package**, which retries three times). Restores the disambiguation measurement that v1's `q02` lost when its intent made the distractors genuinely relevant. No new corpus item was needed.

### Five intent rewrites

`q02`, `q06`, `q08`, `q09`, `q11` keep their query text and lose the collection commentary from their intents. Their v1 labels are **not** carried over; a reworded intent requires re-judging. The v1 wording is preserved in `corpus-v2.json` under `v1_intent`.

**`q11` is more than commentary removal.** "Find anything about terminal firmware" became "Who is responsible for payment terminal firmware releases?", which narrows the need to responsibility and changes what evidence would satisfy it. The frozen wording stands and the assessor judges it as written; its labels carry **no** expectation of resembling v1's. The v2 scope table describes all five rewrites as commentary removal, and for this one that description was incomplete.

## What the assessor sees

| Shown | Withheld |
| --- | --- |
| Item content, kind, tags | v1 labels, rationales, uncertainties |
| Which item is an earlier version of which | Any system output, score or result position |
| Query text and information need | Expected answer counts |
| | Test categories and the two temporal dimensions |
| | Design notes explaining why a query or an item exists |
| | Which queries are new in v2 |

Queries are presented in a seeded non-authoring order. **Residual:** query ids remain sequential, so an assessor who reasons about numbering could infer that `q15`–`q17` were added last. Recorded rather than hidden.

## What a fresh assessor does and does not fix

**Does not:** restore blindness to an implementation already shaped by v1. The corpus, the query wording and the repair history were all authored with v1's results in view. No choice of assessor changes that.

**Does:** produce judgments made without sight of the earlier labels, the expected answers or any retrieval result. That is the whole of its contribution, and it is worth having — it is the one form of exposure that can still be removed, and it cannot be removed retroactively once the set has been judged. It also clears one specific v1 defect: the v1 judge was told out of band that two queries have no answer, and a fresh assessor is not, so v2's abstention labels are free of that leak.

The description is **AI-assessed, AI-audited, human-approved**. The assessor is an AI and supplies the initial labels; the assistant audits them; the project owner is the only human in the chain and gives final approval. "Human-reviewed" is not available for the assistant's audit, and v1's use of it was accurate only because a human did that review. Shared model bias between assessor and auditor is acknowledged and unquantified.

## Partition rules, declared before any label exists

Fixed now so that no query can be moved out of the headline for producing an unexpected result. Membership rules that depend on labels are separated from those that do not.

**Independent of labels — decided here:**

| Set | Criterion | Members |
| --- | --- | --- |
| Clean | Query text authored before any v1 measurement existed, v2 intent free of collection commentary, no out-of-band exposure to the v2 assessor | `q01`–`q14` |
| Flagged: post-result authoring | Wording authored after v1 run 2 was measured, by someone who knew where the system failed | `q15`, `q16`, `q17` |

v1's disclosure flags do not carry over. The exposure they record reached the *v1 judge*, and a fresh assessor never saw those intents or the out-of-band message about two unanswerable queries. `q11` stays clean: a changed information need is a change to record, not an exposure.

The new flag is a different kind of exposure from v1's and is named differently on purpose. `q15`–`q17` are reported per-query and prominently — they are the cases v2 exists to measure — but they are never averaged into the headline.

**Legitimately dependent on labels:**

The abstention set is whichever queries the frozen labels give no grade-2 item. That cannot be declared in advance, because "has no answer" is the property being measured. `q07` and `q14` are expected to land there; the assessor decides. This is the **only** partition allowed to depend on labels. Assessor uncertainty, an ambiguous item, or a label that surprises the implementer are never grounds for moving a query out of the headline.

## Dispatch procedure

Contamination control: the assessor gets the **sheet content only**, inline. It must not be given repository access or a path, because `judgments-v1.json` sits next to the sheet and would answer the whole exercise.

1. Copy the full text of `judging-v2.md` into the prompt below.
2. Run it in a session with **no history of this project** and no file access.
3. Save the returned sheet as `judging-v2-assessor.md`, verbatim, including anything the assessor got wrong or left blank.

```text
You are acting as a relevance assessor for a document collection. Below is a judging
sheet: a set of items and a set of information needs. Fill it in.

Return the sheet with every "grade 2", "grade 1", "rationale" and "uncertainty" field
completed, in the same format, and nothing else. Judge item content against the stated
information need only. Some needs may have no answer in this collection; leaving a query
with no items listed is a valid and expected outcome, and you should not stretch to find
something. Where you are unsure, say so in the uncertainty field rather than resolving it
with a confident guess.

Do not read any file, search anything, or use any tool. Everything you need is below.

---
<paste the full contents of judging-v2.md here>
```

## Review and freeze

1. **Assessor labels land first**, unreviewed, saved verbatim.
2. **Audit review** by the project owner: every label read, disagreements recorded as *disagreements*, not silently corrected.
3. Where the reviewer overrides the assessor, `judgments-v2.json` records the assessor's label, the reviewer's label, and the reason. An override rate that turns out to be high is itself a finding about the packet's clarity.
4. **Freeze**, with the same rules v1 carries: a later correction is a new version with a documented amendment, never an edit in place.
5. Only then is `protocol-v2.md` registered — its clean/flagged partition depends on the labels — and only then is anything run.

## Residual defects of this packet

- `q03` and `q14` say "the procedure for...". This is **not** demonstrated collection leakage, and the earlier draft of this file was wrong to call it a defect of the same class as v1's `q06`/`q08` flags: a user can legitimately ask for a procedure the collection does not contain, and a definite article does not establish that one exists. The wording stands as frozen. What it might be is an unmeasured framing effect on the assessor — if the assessor's rationale shows it operating, that is recorded as uncertainty, not as a disclosure flag. (The independent reason not to touch `q14` still holds: softening an abstention query's intent after learning abstention is the weak spot would be a post-result change to evaluation data.)
- The corpus is still 25 revisions. v2 measures behaviour, not scale, and no v2 figure will say anything about ranking at a realistic corpus size.
- v2 changes the corpus, the query set and the labels at once. **No v2 figure may be compared to a v1 figure.** v1 remains the regression baseline for the build it measured.

## Dispatch record

| | |
| --- | --- |
| Date | 2026-09-06 |
| Sheet | `judging-v2.md`, dispatched unchanged |
| Context | Fresh agent session; no history of this project, this conversation, or this packet. Received the neutral prompt and the sheet text inline, nothing else. |
| Model | Opus 5 — same model family as the auditing session. Shared-model bias acknowledged, unquantified. |
| Repository access | None. The sheet was pasted inline precisely so no path could lead to `judgments-v1.json` next door. |
| Tool disabling | **By instruction, not by configuration.** The dispatch mechanism here has no tools-off switch, so compliance with "use no tool" is asserted rather than enforced. Recorded as a weaker control than intended. |
| Output | Saved verbatim as `judging-v2-assessor.md`, including anything wrong or blank. Adjudication happens against that file; it is never edited. |

## State after dispatch

The assessor returned in one pass, made **0 tool calls**, and answered all 17 queries with rationales and uncertainties. Labels are parsed into `judgments-v2.json` and audited. **Nothing is frozen and nothing has been measured.**

What the audit found, in one line each:

- **12 of 14 carried queries have identical grade-2 sets to v1**, from an assessor who saw none of v1's labels. The two that differ are both intent rewrites.
- **`q11` has no grade-2 item** and enters the abstention set by the label-dependent rule declared before the labels existed. The rewritten need asks *who is responsible*; the only item on the subject excludes an owner rather than naming one. This is the rule operating, not a query being moved for producing an unexpected result.
- **`q15`'s only grade-2 answer is a superseded revision.** That closes v1's `no_superseded_only_answer` gap — the first query in the project whose answer is unreachable without history.
- **v1's one piece of observed history evidence does not carry over.** It came from `q02` grading the superseded card-retry policy a 2; under v2's rewritten intent that item is grade-1. All history value now sits in `q15`, which is flagged for post-result authoring and therefore out of the headline. **v2's headline can no more estimate the benefit of history browsing than v1's could** — it can now measure it per-query, which v1 could not. Moving `q15` into the headline after seeing its labels is exactly what the partition rule forbids.
- **Four auditor disagreements, all grade-1**, recorded in `judgments-v2.json` and not applied. Every protocol aggregate is computed over grade-2 items only, so none of them changes a figure.

The partition, with the clean set now larger than v1's because the v1 disclosure flags do not transfer to a fresh assessor:

| Set | Members |
| --- | --- |
| Clean (headline) | `q01` `q02` `q03` `q04` `q05` `q06` `q08` `q09` `q10` `q12` `q13` |
| Flagged, post-result authoring | `q15` `q16` `q17` |
| Abstention (derived from labels) | `q07` `q11` `q14` |

Next: human approval of the untouched assessor output, then adjudication of the four grade-1 disagreements, then the label freeze, then `protocol-v2.md`, then measurement.

## Adjudication and freeze

Two AI audits, no human re-derivation. The assistant compared every label against v1 through the revision ids and raised four grade-1 disagreements. ChatGPT independently reproduced the 12-of-14 grade-2 agreement from the revision texts, verified the 425-pair totals, and approved conditional on seven adjudications. The project owner authorised the freeze and accepted them.

**v2 is AI-assessed, AI-audited twice, human-authorised.** "Human-reviewed" is not available for it and must not appear in any report — unlike v1, where a human did perform the review.

| Query / item | Change | Why |
| --- | --- | --- |
| q02 / i07 | 1 → 0 | Settlement arrival times are not evidence about retry behaviour |
| q09 / i14 | 1 → 0 | Log structure states no rule about sensitive values |
| q13 / i23 | 1 → 0 | Testing ledger ordering does not explain the append-only decision |
| q04 / i08 | 1 → 0 | A token-logging rule does not explain the auth mechanism |
| q01 / i16 | 1 → 0 | An obsolete attempt count cannot support an explicitly *current*-policy need. Its rationale is useful for q05, where it stays grade 1 |
| q10 / i22 | 1 → 0 | An obsolete schedule cannot support an explicitly *current*-cadence need. It directly answers q15, where it is grade 2 |
| **q16 / i02** | **1 → 2** | Both revisions state the requested function and deadline identically, and the need never asks for the current job name |

The last one is the substantive change, and it separates two things the assessor had merged. An outdated name warrants *historical* labelling; it does not demote an unchanged, correct answer to supporting evidence. **Relevance and revision currency are different properties.** The consequence is that `q16` now has a current-head denominator of 1 and a search-then-history denominator of 2 — reported separately, never merged.

The six 1 → 0 changes share one shape: supporting evidence must support *the need as worded*. Adjacency of topic is not support.

Two annotations recorded without changing a grade:

- **q08's rationale overstates causation.** It calls `i03` "the incident that motivates the rule"; the corpus establishes no such link. `i03` illustrates migration locking risk. Grade stays 1.
- **q06's grade-1 field is absent**, not blank. Normalised to empty under the sheet's own rule, and `parse_assessor_sheet.py` now reports the normalisation instead of letting an incomplete response look structurally complete.

Partition unchanged at 11 / 3 / 3. None of the seven adjudications moves a query between sets.

## One reporting rule that came out of the adjudication

The three no-direct-answer queries are not equivalent, and reporting them as one "returned wrong results" count would be wrong:

| Query | Evidence in the corpus | Correct behaviour |
| --- | --- | --- |
| `q07`, `q14` | None of any grade | Empty result set |
| `q11` | Supporting evidence (`i18`), no item naming an owner | Return `i18`; state that ownership is unspecified rather than inventing an owner |

Returned results for these queries are reported in grade-1 and grade-0 buckets separately. Demanding an empty set for all three would penalise useful context.
