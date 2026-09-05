# T1 morphology — development results

Development data. **Nothing here is a v2 evaluation result.** The final section reports a regression check against v2, and says plainly what that check can and cannot mean.

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

### Retrieval p95 at 10,000 memories — the binding measurement

Every profile was measured in two orders, `exact→split` and `split→exact`, because the first profile measured absorbs cold-cache cost.

| Profile | p95 forward | p95 reversed | vs baseline | ≤ 50 ms | ≤ 1.5× |
| --- | ---: | ---: | --- | --- | --- |
| exact | 21.59 ms | 19.64 ms | baseline | — | — |
| **stem** | **22.42 ms** | **20.06 ms** | **1.04× / 1.02×** | **pass** | **pass** |
| dual | 191.64 ms | 169.86 ms | 8.88× / 8.65× | fail | fail |
| split | 171.17 ms | 180.35 ms | 7.93× / 9.18× | fail | fail |

At 1,000 memories `dual` and `split` measured 1.74× and 2.04× — over the gate but unremarkable, and at that size the ordering noise is larger than the effect (`stem` swung 25.97 → 16.02 ms between orders). The 10,000 fixture is where the design breaks and where the figures are stable across orders. **Measuring at one size would have missed it.**

The cause is mechanical, not a tuning matter. `EXPLAIN QUERY PLAN` on the two-index profiles shows a `USE TEMP B-TREE FOR GROUP BY` over both legs' complete match sets, ahead of the sort the baseline already does. Because matching is disjunctive, nearly everything matches: **2,538 of 3,000 documents** for one two-word query. The union materialises roughly twice the corpus per query, so its cost tracks corpus size rather than result size.

### Storage — persistent retrieval structures at 10,000 memories

Checkpointed database; every FTS shadow table and every real index counted by exact name, with the whole-database size reported beside it so a growing outbox cannot be mistaken for a retrieval structure.

| Profile | head_fts | stem index | prose index | head_index + head_tags | Total | vs baseline | ≤ 2× | Database file |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| exact | 1,536,000 | — | — | 4,157,440 | 5,693,440 | 1.00× | — | 55,062,528 |
| stem | 1,454,080 | — | — | 4,169,728 | 5,623,808 | 0.99× | pass | 55,152,640 |
| dual | 1,536,000 | 1,454,080 | — | 4,157,440 | 7,147,520 | 1.26× | pass | 56,659,968 |
| split | 1,536,000 | — | 7,229,440 | 4,161,536 | 12,926,976 | **2.27×** | **fail** | 62,410,752 |

`stem` adds no structure at all — it replaces a tokenizer. `dual`'s stemmed index is external-content and stores only postings. `split`'s prose index is standalone and stores its own filtered copy of every body, which is where its 2.27× comes from; that copy was deliberately not hidden behind a view so the gate could see it.

### Write latency — gate met, direction not established

| Worst observed ratio to baseline, either order, either fixture | ≤ 2× |
| --- | --- |
| record p95: 1.68× (dual, 1,000, reversed) | pass |
| revise p95: 1.82× (dual, 1,000, reversed) | pass |

In forward order the **baseline was slower than all three variants**, which cannot be a real effect: `dual` writes two indexes where `exact` writes one. Reversing the order flipped the pattern — `exact` record p95 went 3.245 → 1.741 ms at 1,000, and `split` went 1.514 → 1.809 — so the measured differences track *position in the run*, not profile. All figures sit between 1.514 and 3.640 ms across every profile, size and order.

**The write gate is met with margin in both orders. The direction of any profile difference in the write path is not established by this measurement, and is not claimed.**

## Outcome

| Profile | Quality gate | Retrieval | Writes | Storage | Verdict |
| --- | --- | --- | --- | --- | --- |
| stem | pass | pass | pass | pass | **promoted** |
| dual | pass | fail | pass | pass | not promoted |
| split | pass | fail | pass | fail | not promoted |

`stem` is the one configuration carried forward. Exceeding a gate does not delete a result: `dual` and `split` stay in the registration table with their numbers.

Promoting `stem` **accepts a measured cost**: identifier queries widen (dq09 3→5, dq10 7→11) and grade-0 delivered bytes rise 24%. `split` was the variant that avoided the first of those, and it is not viable as built — the two-index union is what makes it eight times slower, and the standalone prose index is what busts the storage gate. A profile with `split`'s selectivity and one index — selective stemming inside a single tokenizer rather than a second table — would be a **new registered configuration**, not a rerun of this one.

## v2 regression check — what it is and is not

`stem`, unchanged, run through the frozen v2 protocol. **This is not a fresh evaluation of v2.** The configuration was chosen on development data with v2's failure modes already known; the run says whether a known failure moved. The two figures below must never be presented side by side as an improvement measurement.

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
