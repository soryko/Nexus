# Automatic recall: paired evaluation specification

Status: proposed protocol. Parent: [delivery plan](../plans/2026-11-21-recall-quality.md).

## Boundaries

Automatic offers, automatic body reads, explicit search and exact get are four different events. Instrument them separately. Do not change explicit search, exact reads, history, deletion, or storage. Never infer user intent solely from record type; a test receipt can be the only evidence for a design decision. Compare against the installed DSH handler, not a similarly named checkout file. Absence of a receipt from instrumentation is not a zero.

## Corpus and cases

Use a fresh fixture store with fixed insertion order, stable fixture labels for runtime IDs, at least two decisions, successful and failed checks, a rollback rationale supported by a receipt, a superseded policy, distractors sharing project terms, one forgotten record and one out-of-scope lookalike. Include a case where a failing check is evidence, not a successful verification. Seed only artificial or public project facts. Reset session/seen state between cases; hold corpus generation constant during reads.

Freeze **20 distinct prompts**, four per row, and their acceptable evidence sets and answers before running. Use these examples as anchors, not tuning data. Smoke-test the harness on separate prompts.

| Class | Representative prompt | Expected behavior |
|---|---|---|
| No relevant memory | What else can we improve? | No body read for generic project-name overlap; include short and unrelated prompts. |
| Decisions | Why did we choose explicit capture? | Read controlling decision; include a narrowed policy with a surviving exception. |
| Receipts essential | What passed after the installer change? | Return the right check, scope and result; include debugging and failure queries. |
| Mixed evidence | Why was the rollout reversed after validation? | Retrieve both rationale and check when neither suffices alone. No blanket receipt penalty. |
| Different wording | Which launch needs permission before saving? | Retrieve the same authority despite synonyms or word-form changes; label unanswerable variants separately. |

## Paired arms and controls

- **A: observed baseline.** Replay the identified handler and its normal automatic-read policy. Record what actually ran; do not invent a model-query step for production.
- **B: quieter admission.** Same queries, same ranked candidates as A; only the automatic body-read decision may differ. Predeclare the deterministic rule on smoke data. Never delete, globally demote, or hide receipts from explicit search.
- **C versus D: cost experiment.** Hold the search implementation, admission, evidence cap, answer model and answer instructions fixed; vary only query generation (local terms vs one model call). If an arm duplicates A, reuse its identical trace. No retries or extra searches for one arm. Keep plain notes as a separately labeled handoff reference, not as the automatic-recall baseline.
- Freeze input corpus, caps for hits, reads, evidence bytes, query/answer tokens, timeouts, model/version and call ceiling in the manifest. Use equal caps per comparable stage, fresh answer sessions, and no answer-key exposure. Record endpoint errors as incomplete cases, never abstentions. Do not replace failures after seeing scores.

## Receipt and decision rules

For each case and arm save case ID, config hash, query method, hit IDs and ranks, offered IDs, body-read IDs, relevant IDs from the sealed key, expected/observed action and citation, latency and tokens per call. Aggregate tokens across all calls including failed attempts if known; mark missing usage unknown. Store only fixture data, not live prompts, memory bodies or credentials. Blind the answer scorer to arm; score action and citation separately by meaning, and formatting separately.

1. **Safety gate:** any forgotten or foreign record exposed (including ID or excerpt), changed explicit-search result on frozen inputs, or changed exact-read behavior disqualifies the candidate.
2. **Quiet gate (B vs A):** strictly fewer irrelevant body reads overall. For every case preserve previously retrieved required evidence (or an approved equivalent), previously correct answers and correct citations. Include raw counts of abstentions, required receipts lost, and reads per class. A system that reads nothing fails the positive cases.
3. **Cost gate (C vs D):** strictly fewer aggregate end-to-end tokens with the same per-case quality and safety protections. A cheaper query alone is not a win. Report latency separately.
4. Missing route proof, incomplete pairs, missing cost data for the cost gate, or a budget overrun means inconclusive for the affected comparison. On tie or failure retain the baseline. Passing 20 cases warrants replication on new prompts, not production enablement.
