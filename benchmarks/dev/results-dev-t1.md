# T1 morphology — development results

Development data. **Nothing here is a v2 evaluation result.** The final section reports a regression check against v2, and says plainly what that check can and cannot mean.

> **Status, amended 2026-09-06: `stem` is NOT promoted.**
>
> `stem` is **quality passed; performance qualification pending.** An earlier revision of this report recorded it as promoted. That acceptance rested on an incomplete summary and has been withdrawn.
>
> `stem` exceeded the registered retrieval gate at 1,000 memories in forward order — 25.9749 / 13.9946 = **1.8561×**, against a **≤1.5×** limit — and passed reversed at 0.8905×. The registered charter says exceeding a gate prevents promotion. **The disagreement between orders supports investigating measurement instability; it does not establish that run position explains the failure**, and selecting the 10,000-memory result after the fact would change the rule rather than satisfy it.
>
> A bounded performance confirmation is registered in [t1c-predeclaration.md](t1c-predeclaration.md) and reported in [results-dev-t1c.md](results-dev-t1c.md). Until it resolves, `exact` remains both the shipped default and the standing baseline. **Every number below stands as recorded**; nothing in the original measurement is withdrawn or replaced.

| | |
| --- | --- |
| Registered in | [v2-development-queries.md](../eval/v2-development-queries.md) — budgets, gates and the three variants, all recorded before the first run |
| Predeclaration | [t1-predeclaration.md](t1-predeclaration.md) — baseline behaviour, committed before any variant was run |
| Corpus | `corpus-dev1.json` — 24 memories, 27 revisions, 20 queries |
| Interpreter | `.venv-sqlite/bin/python`, SQLite **3.53.4** |
| Machine record | `results-dev-t1.json`, `results-dev-t1-perf.json`, `results-dev-t1-perf-reversed.json` |

The `uv`-resolved interpreter ships SQLite 3.50.4, below this project's 3.51.3 floor, and raises `UnsupportedRuntime` on every database test. That is an environment mismatch, reported here and never counted as a test result. Under `.venv-sqlite`: **75 tests pass** — the 60 that passed before this work, unchanged, plus 15 covering the profiles.

## Quality — all three variants clear the gate

The registered T1 gate: recover the predeclared missing candidates while preserving exact identifier and path checks, reporting any per-query recall loss.

| | exact (baseline) | stem | dual | split |
| --- | --- | --- | --- | --- |
| Morphology candidate recall (n=7) | 0.714 | **1.000** | **1.000** | **1.000** |
| dq02 "thumbnail generation failure" | missed | recovered, rank 1 | recovered, rank 1 | recovered, rank 1 |
| dq06 "publishing schedule" | missed, pool 0 | recovered, rank 2 | recovered, rank 2 | recovered, rank 2 |
| Exact-reference answers at rank 1 (n=4) | 4/4 | 4/4 | 4/4 | 4/4 |
| `mux2` historical control at rank 1 | yes | yes | yes | yes |
| Per-query recall losses | — | none | none | none |
| Delivered-evidence recall, morphology | 0.714 | 1.000 | 1.000 | 1.000 |

Candidate recall and delivered recall are reported separately throughout, and on this set they agree for every morphology query: nothing was found and then lost to a delivery cap.

### What the variants cost, which the gate does not capture

| | exact | stem | dual | split |
| --- | --- | --- | --- | --- |
| dq09 `AssetState.PUBLISHED` — pool size | 3 | 5 | 5 | **3** |
| dq10 `/v3/assets/manifest` — pool size | 7 | 11 | 11 | **7** |
| Delivered grade-0 bytes, all queries | 13,745 | 17,024 | 17,382 | 16,759 |

Only `split` keeps an identifier query from pulling in prose, which is exactly what routing code-shaped tokens to the exact index was built to do. Under `stem` and `dual` the stemmed index still sees identifiers, so `AssetState.PUBLISHED` starts matching text about published assets. **The answer still ranks first in every case**, which is why this is a recorded cost and not a gate failure — the predeclaration said so before the numbers existed.

All three raise delivered grade-0 bytes by 22–26%. A wider candidate pool spends more of the same delivery budget on irrelevant text. That cost belongs to T3 and is recorded here rather than netted out of the T1 result.

### One thing this experiment does not show

Five of seven morphology queries already reached their answer under the baseline, through a *different* token — `caption`, `licence`, `storage`, `ingest`, `render`. That is the same shape as v2's q17: a morphological gap that another discriminative term routes around. Under disjunctive matching, a memory-level recall figure **understates** how often stemming matters, because the item is usually still a candidate on some other word. Rank movement and pool size are reported beside recall for that reason.

## Performance and storage — the variants separate sharply where quality did not

Two fixtures, built once per size under `exact` and then copied per profile, so load order, content and durability are identical across a pair by construction. Three blocks of 200 measured queries after 50 warm-up queries; block-level figures are retained in the JSON. The reported figure is the **worst block's p95**, not a pooled one.

| Fixture | Memories | Revisions | Mean revisions/memory | Long-history slice | Content bytes p50 / p95 | Tags/memory |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| 1,000 | 1,000 | 2,979 | 2.979 | 20 memories × 25 revisions | 533 / 857 | 2.03 |
| 10,000 | 10,000 | 29,950 | 2.995 | 200 memories × 25 revisions | 534 / 862 | 2.00 |

The long-history slice sits at 25 revisions, above the 20-revision expansion cap, so the cap binds rather than being nominal.

### Retrieval p95 — both fixtures, both orders

Every profile was measured in two orders, `exact→split` and `split→exact`, because the first profile measured absorbs cold-cache cost. Both fixture sizes are reported; neither is dropped.

At 10,000 memories:

| Profile | p95 forward | p95 reversed | vs baseline | ≤ 50 ms | ≤ 1.5× |
| --- | ---: | ---: | --- | --- | --- |
| exact | 21.59 ms | 19.64 ms | baseline | — | — |
| **stem** | **22.42 ms** | **20.06 ms** | **1.04× / 1.02×** | **pass** | **pass** |
| dual | 191.64 ms | 169.86 ms | 8.88× / 8.65× | fail | fail |
| split | 171.17 ms | 180.35 ms | 7.93× / 9.18× | fail | fail |

At 1,000 memories:

| Profile | p95 forward | p95 reversed | vs baseline | ≤ 50 ms | ≤ 1.5× |
| --- | ---: | ---: | --- | --- | --- |
| exact | 13.99 ms | 17.99 ms | baseline | — | — |
| stem | 25.97 ms | 16.02 ms | **1.86×** / 0.89× | pass | **fail forward**, pass reversed |
| dual | 24.39 ms | 29.35 ms | 1.74× / 1.63× | pass | fail both |
| split | 28.56 ms | 17.73 ms | **2.04×** / 0.99× | pass | fail forward, pass reversed |

**Both rejected variants had already failed the 1.5× ratio gate at 1,000 memories** — `dual` in both orders, `split` in forward order. The 10,000 fixture did not reveal the failure. It revealed the **severity** of the scaling problem, and it is where the figures stop depending on order.

At 1,000 memories the order sensitivity is comparable to the effect being measured, so that size ranks the variants unreliably in **both** directions. Reversed, `split` measured 0.99× — a pass. Forward, the promoted `stem` measured 1.86× — a **gate failure**, against 0.89× reversed. Read on its own, the 1,000 fixture flags the right variants in one order and the wrong one in the other.

**`stem`'s 1,000-memory forward measurement exceeds the ratio gate and is recorded here as a failure, not netted out.** Its promotion rests on the 10,000 fixture, where every profile's two orders agree (`stem` 1.04× / 1.02×, `dual` 8.88× / 8.65×, `split` 7.93× / 9.18×), and on the judgment that a 1.86×/0.89× swing across orders at 1,000 measures run position rather than profile. That judgment is stated so it can be disagreed with; the number it sets aside is above.

### Why the two-index profiles are slow — a partial explanation

`EXPLAIN QUERY PLAN` on the two-index profiles shows a `USE TEMP B-TREE FOR GROUP BY` over both legs' match sets, ahead of the sort the baseline already does. Matching is disjunctive, so those match sets are large: **2,538 of 3,000 documents** for one two-word query. The temporary grouping structure is consistent with the slowdown and points at the union as its likely site.

**It does not isolate the grouping structure's contribution.** `EXPLAIN QUERY PLAN` reports SQLite's high-level execution strategy, not runtime cost ([SQLite: EXPLAIN QUERY PLAN](https://www.sqlite.org/eqp.html)). Nothing measured here separates the temp B-tree from the second leg's own matching and scoring work, and the query-plan inspection was an ad-hoc probe whose output is **not retained in the machine record** — it is reported as a probe, on the same footing as the porter mechanism probe in the charter.

What the measurement supports is a rejection of **these two implementations at these sizes**. It does not establish that two-index designs inherently require this cost. A union built to avoid the grouping step, or matched conjunctively, would be a new registered configuration with its own measurement — not a foregone conclusion either way.

### Storage — persistent retrieval structures at 10,000 memories

Checkpointed database; every FTS shadow table and every real index counted by exact name, with the whole-database size reported beside it so a growing outbox cannot be mistaken for a retrieval structure.

| Profile | head_fts | stem index | prose index | head_index + head_tags | Total | vs baseline | ≤ 2× | Database file |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| exact | 1,536,000 | — | — | 4,157,440 | 5,693,440 | 1.00× | — | 55,062,528 |
| stem | 1,454,080 | — | — | 4,169,728 | 5,623,808 | 0.99× | pass | 55,152,640 |
| dual | 1,536,000 | 1,454,080 | — | 4,157,440 | 7,147,520 | 1.26× | pass | 56,659,968 |
| split | 1,536,000 | — | 7,229,440 | 4,161,536 | 12,926,976 | **2.27×** | **fail** | 62,410,752 |

`stem` adds no structure at all — it replaces a tokenizer. `dual`'s stemmed index is external-content and stores only postings. `split`'s prose index is standalone and stores its own filtered copy of every body, which is where its 2.27× comes from; that copy was deliberately not hidden behind a view so the gate could see it.

### Write latency — observed ratios passed in both orders

| Worst observed ratio to baseline, either order, either fixture | ≤ 2× |
| --- | --- |
| record p95: 1.68× (dual, 1,000, reversed) | pass |
| revise p95: 1.82× (dual, 1,000, reversed) | pass |

**Observed write ratios passed in both orders.** That is the claim, and it is the whole of it. A worst observed ratio of 1.82× against a 2× gate **does not establish a reliable margin**: it is the extreme of a small set of paired measurements whose run-to-run spread is of the same order as the gap it leaves.

The two orders disagree about direction. In forward order the baseline measured slower than all three variants; reversing it moved `exact` record p95 from 3.245 to 1.741 ms at 1,000 while `split` went 1.514 → 1.809. **This demonstrates that the write measurement is order-sensitive. It does not show that ordering explains every difference**, and it is not grounds for calling any individual figure impossible — more index operations do not guarantee a higher observed wall-clock p95, so `dual` measuring faster than `exact` is not by itself incoherent.

All figures sit between 1.514 and 3.640 ms across every profile, size and order. **The direction of any profile difference in the write path is not established by this measurement, and is not claimed.**

## Outcome

| Profile | Quality gate | Retrieval @10k | Retrieval @1k | Writes | Storage | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| stem | pass | pass (1.04× / 1.02×) | **fail forward (1.8561×)**, pass reversed (0.8905×) | pass | pass | **not promoted — quality passed, performance qualification pending** |
| dual | pass | fail (8.88× / 8.65×) | fail both (1.74× / 1.63×) | pass | pass | not promoted |
| split | pass | fail (7.93× / 9.18×) | fail forward (2.04×), pass reversed | pass | fail | not promoted |

No configuration is promoted by this experiment. `stem` is the only candidate still open, on quality: it recovered every predeclared morphological miss with no per-query recall loss. **Its performance qualification is pending** — it exceeded the registered ratio gate at 1,000 memories in forward order, and the charter's own rule is that exceeding a gate prevents promotion.

An earlier revision of this report promoted `stem` on the reasoning that the 1,000-memory ratios measure run position rather than profile. That reasoning is retained above as the hypothesis it is. **It is not evidence, and it was not a sufficient basis for promotion.** Resolving it requires a measurement designed to test order sensitivity directly, which is what [T1-C](t1c-predeclaration.md) does.

Exceeding a gate does not delete a result: `dual` and `split` stay in the registration table with their numbers, and `stem`'s 1,000-memory forward failure stays in the table above rather than being summarised away.

Were `stem` to be promoted on the strength of a later confirmation, it would **accept a measured cost**: identifier queries widen (dq09 3→5, dq10 7→11) and grade-0 delivered bytes rise 24%. `split` was the variant that avoided the first of those, and it is not viable as built: its two-index union is where the eight-fold slowdown appears, and its standalone prose index is what busts the storage gate. A profile with `split`'s selectivity and one index — selective stemming inside a single tokenizer rather than a second table — would be a **new registered configuration**, not a rerun of this one.

## v2 regression check — what it is and is not

`stem`, unchanged, run through the frozen v2 protocol. **This is not a fresh evaluation of v2.** The configuration was chosen on development data with v2's failure modes already known; the run says whether a known failure moved.

**This run predates the withdrawal of `stem`'s promotion and is retained exactly as recorded.** It is a regression check on a candidate profile, not on a promoted one. Nothing here changes because the promotion was withdrawn — and nothing here counts toward reinstating it, since a v2 regression check says nothing about the retrieval gate that `stem` failed. The two figures below must never be presented side by side as an improvement measurement.

| | exact (frozen) | stem (regression) |
| --- | --- | --- |
| Headline, clean set, k = 5 / 10 / 20 | 0.848 | 0.909 |
| Task coverage | 10 / 11 | 10 / 11 |
| q02 "retry policy" | 0.333 | **1.000** |
| q04 "kerberos" | 0.000 | 0.000 |
| q16 "nightflow" | 0.000 | 0.000 |
| q15 history recall at k=5 | 0.0 | **1.0** |
| Both negative controls | PASS | PASS |

Reading it honestly, item by item:

- **q02 is the defect T1 targeted, and it closed.** All three grade-2 items are now found; the returned count goes 3 → 5.
- **q04 and q16 are unchanged, as predicted.** They are semantic and historical-vocabulary gaps. Stemming was never going to reach them, and T2 is the round that addresses them.
- **q15 was not a T1 target.** Its history recall at k=5 improved because stemming moved `m17` into the top five — an incidental ranking effect. It is recorded, not claimed as a designed result.
- **Context volume moved the wrong way.** Across the clean set, grade-2 returned items rise 13 → 15 and grade-0 rise 18 → 21 at k=5, and 50 → 54 at k=20. Recovering candidates costs irrelevant context, on v2 exactly as on the development corpus.
- Both negative controls still pass from independently built databases.

`exact` remains the default and the shipped behaviour. On this build, `--profile=exact` reproduces every measured field of the frozen v2 record — per-query figures, headline, controls, repeatability, load order, item mapping — with only the per-run UUIDs differing.
