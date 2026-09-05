# T1 predeclaration — baseline behaviour, recorded before any variant was run

Development data. **Nothing here is a v2 result.**

The paired baseline is the shipped `exact` profile. It is a control, not one of the three registered variants, so running it first does not spend configuration-search budget. This file fixes what the variants must recover and what they must not damage, and it was committed before `stem`, `dual` or `split` were run even once.

| | |
| --- | --- |
| Build | `ceb2a3e` |
| Corpus | `corpus-dev1.json` · sha256 recorded in `results-dev-t1.json` |
| Interpreter | `.venv-sqlite/bin/python` |
| SQLite | recorded in `results-dev-t1.json` |
| Budgets | pool 20 · history 5x20 · delivered 5 items / 8 KiB · diagnostic limit 100 |

The diagnostic query at limit 100 sits outside the budget and exists only to separate *never a candidate* from *a candidate the pool limit cut off*. Both baseline misses below are the former: they matched nothing at any limit.

## The predeclared missing candidates — what the variants must recover

| Query | Text | Answer | Baseline | Why it is missed |
| --- | --- | --- | --- | --- |
| dq02 | "thumbnail generation failure" | d02 | pool 2, candidate recall **0.0** | Query has `thumbnail`, `generation`, `failure`; the item has `Thumbnails`, `generate`, `fail`. Zero shared tokens. |
| dq06 | "publishing schedule" | d06 | pool **0**, candidate recall **0.0** | Query has `publishing`, `schedule`; the item has `published`, `publish`. Zero shared tokens, and nothing else in the corpus matches either. |

Two of seven morphology queries. The other five already reach their answer through a *different* token — `caption`, `licence`, `storage`, `ingest`, `render` — which is the same shape as v2's q17: a morphological gap that another discriminative term routes around. **A memory-level recall figure understates how often stemming would matter**, and that is a property of disjunctive matching, not of this corpus.

## What must not regress

| Check | Baseline | Requirement |
| --- | --- | --- |
| dq08 `transcode_worker.py` | pool 1, answer rank 1 | answer stays rank 1 |
| dq09 `AssetState.PUBLISHED` | pool 3, answer rank 1 | answer stays rank 1 |
| dq10 `/v3/assets/manifest` | pool 7, answer rank 1 | answer stays rank 1 |
| dq11 `caption_track_id` | pool 1, answer rank 1 | answer stays rank 1 |
| dq17 `mux2` (historical control) | pool 1, answer rank 1 | answer stays rank 1 |
| dq01, dq03, dq04, dq05, dq07 | candidate recall 1.0 | no per-query loss |
| dq13, dq15, dq19, dq20 | candidate recall 1.0 | no per-query loss |

Pool growth on an exact-reference query is recorded but is **not** by itself a failure: it is the precision cost of stemming, reported alongside the rank check rather than folded into it.

## Baseline figures the later targets will be measured against

Recorded now so that T0 and T3 cannot be scored against a baseline chosen after the fact.

| Figure | Baseline |
| --- | --- |
| Delivered bytes, grade 0 | 13,745 |
| Delivered bytes, grade 2 | 3,890 |
| Delivered bytes, grade 1 | 785 |
| dq16 "flowreel" | pool 0, superseded revision `d20r1` never reached — the T2 case, unchanged by anything in T1 |
| dq13 answer rank | 6, delivered-item cap binds — a T3 case |

## Known limits of this experiment

- The corpus and its labels were authored on the implementation side and were not blind-assessed. It is development data and is treated as such.
- Seven morphology queries is a smoke test. A variant that recovers both misses has been shown to recover *these two*, nothing more.
- Memory-level recall is the wrong instrument for measuring a tokenizer, for the reason given above. Rank movement and pool size are reported beside it.
