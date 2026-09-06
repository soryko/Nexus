# v2 delivery regression — `stem`/`cutoff_40` carried unchanged onto the frozen set

**Not a fresh evaluation of v2.** `cutoff_40` was chosen on development data with v2's
failure modes already known. This check says whether a known failure moved and what the
chosen selection costs on frozen evaluation data. It carries **no gate**: the 50% grade-0
reduction is T3's target against T3's own pinned baseline, and restating it here would
turn the check into a second thing to tune toward.

| | |
| --- | --- |
| Pinned | `a362f7c` — application and harness committed **before** the run; `working_tree_clean_at_start: true`, and every executed file's sha256 is in the record |
| Interpreter | `.venv-sqlite/bin/python`, SQLite 3.53.4 |
| Configuration | `stem` · head-only candidate generation · selection `cutoff_40`, fraction fixed at **0.40** |
| Compared against | `stem`/`all` — same profile, same candidate generation, same budgets; selection is the only difference |
| Budgets | pool 20 · history 5 memories × 20 revisions · delivered 5 items / 8,192 bytes incl. provenance |
| Path | `budgeted_retrieval.retrieve`, the function the development quality and performance harnesses run |
| Machine record | [results-v2-delivery-regression.json](results-v2-delivery-regression.json) · diagnostic: [results-v2-delivery-cut-diagnostic.txt](results-v2-delivery-cut-diagnostic.txt) |

These are **delivered-evidence** figures. v2's registered discovery metrics at k=5/10/20
live in `results-v2.json` and `results-v2-regression-stem.json` and are untouched by this
run; the two must not be mixed or compared cell by cell.

## Result — the selection loses a grade-2 answer on v2

| | `stem`/`all` | `stem`/`cutoff_40` |
| --- | ---: | ---: |
| Grade-2 delivered bytes | 4,931 | **4,622** |
| Grade-1 delivered bytes | 1,822 | **900** |
| Grade-0 delivered bytes | 12,474 | **6,318** |
| Items delivered | 63 | 39 |

Grade-0 volume falls 49.35%. It does not come for free, which is the finding:

| Loss against `stem`/`all` | |
| --- | --- |
| Grade-2 delivered-recall loss | **`q02`** — 1.000 → 0.667 |
| Task-coverage loss | none |
| Grade-1-only support loss | none (`q11`'s `i18` and `q15`'s grade-1 bytes both survive) |
| Grade-1 supporting evidence lost elsewhere | `q02` (`i15`), `q03` (`i23`), `q08` (`i03`) — 922 bytes |

**`q02` is the query the whole `stem` promotion was for.** Its three grade-2 items are
`i12`, `i20`, `i25`. Frozen `exact` delivered one of them (head recall 0.333 at k=5);
`stem` recovered all three (1.000). `cutoff_40` now cuts `i12` back out:

```
q02  'retry policy'   grade-2 [i12, i20, i25]  grade-1 [i10, i15, i16]
   rank 1  i20  grade 2  score  3.3162  fraction 1.0000  KEPT
   rank 2  i25  grade 2  score  1.5364  fraction 0.4633  KEPT
   rank 3  i10  grade 1  score  1.5194  fraction 0.4582  KEPT
   rank 4  i15  grade 1  score  1.0406  fraction 0.3138  CUT
   rank 5  i12  grade 2  score  1.0251  fraction 0.3091  CUT
```

`i12` sits at **0.3091** of the top hit — inside `dq13`'s shape and below the constant
fitted to `dq13`'s 0.4018. The margin that made `cutoff_40` work on the development corpus
is 0.45%; the margin by which it fails here is 9 points of the same quantity. This is the
predicted failure mode arriving on the first set the rule was carried to, not a surprise:
T3's report registered that "a single answer sitting one place lower, or scoring a fraction
less, would move `dq13` below the cutoff and turn the pass into a recall loss." The same
sentence, applied to v2, is what happened — with `q02`'s `i12` in `dq13`'s place.

## By partition — flagged queries reported, never averaged into a headline

| Partition | Coverage `all` → `cutoff_40` | Delivered head recall | Grade-0 bytes |
| --- | --- | --- | ---: |
| clean (11) | 10/11 → 10/11 | 0.909 → **0.879** | 6,404 → 3,104 |
| flagged post-result authoring (3) | 1/3 → 1/3 | 0.500 → 0.500 (2/3 defined) | 2,533 → 1,534 |
| no direct answer (3) | 0/3 → 0/3 | N/A — no grade-2 denominator | 3,537 → 1,680 |

Coverage holds everywhere: no query that received grade-2 evidence stopped receiving some.
`q04` and `q16` deliver nothing in either arm, unchanged from the discovery record.

## What this says about abstention (T0) — still unsolved

`cutoff_40` is a prefix rule and always keeps rank 1, so it cannot reject a pool. On the
three no-direct-answer queries:

- `q07` (no relevant evidence) is **completely untouched**: 5 items, 1,361 grade-0 bytes in
  both arms. Its hits score close enough together that a fractional cutoff removes nothing.
- `q14` (no relevant evidence) collapses to a single item — still grade-0, still returned.
- `q11` (supporting evidence only) collapses to `i18` alone, which is the wanted behaviour
  and is the one place the rule looks like abstention without being it.

Cutting irrelevant volume is not rejecting an irrelevant pool. Nothing measured here moves
T0, and the query with genuinely no answer is the one the rule helped least.

## Controls — run under the assessed configuration

Both controls build independent fixtures under `stem`, run the same budgeted delivery
path, and are run once per arm.

| Control | `all` | `cutoff_40` |
| --- | --- | --- |
| Head postings cleared (21 memories, 21 head-projection rows retained) | 0 items delivered — **PASS** | 0 items delivered — **PASS** |
| 15 memories holding grade-2 items forgotten | 0 reached in delivery or expansion — **PASS** | 0 reached — **PASS** |

**Provenance correction.** Before this run, `control_empty_index` and
`control_forget_relevant` called `build_fixture` without a profile and therefore always
ran `exact`. The controls recorded in `results-v2-regression-stem.json` (build `2c7eaa49`)
exercised `exact`, not `stem`: they are not evidence about the configuration that record
reports. That file is kept unedited; this is the correction, and the code no longer allows
the mismatch.

## Limits of this check

- **One set, one configuration.** A recall loss here is a fact about `cutoff_40` on v2, not
  a measure of how often the rule fails.
- **Not a fresh evaluation.** v2's failures were known when 0.40 was chosen. The check says
  a known failure moved back; it cannot say retrieval got better or worse in general.
- **No gate, and no retuning.** 0.40 was kept fixed. Searching for a constant that keeps
  `q02` would be fitting to evaluation data — the one thing the charter forbids outright.
- **Delivery only.** No claim is made here about the k=5/10/20 discovery figures, which are
  unchanged and separately recorded.
- **Byte truncation still unmeasured.** As on the development corpus, no v2 query approaches
  the 8,192-byte budget; the item cap is what binds.
- **Labels are AI-assessed, AI-audited twice, human-authorised.** No human audit at label
  level. That provenance applies to every figure above.

## Consequence for the pinned configuration

T3 pinned `cutoff_40` on development data under a registered decision rule, and that
outcome stands as what T3 measured. What this check adds is that the first time the rule
was carried to a set it was not fitted on, it lost a grade-2 answer — on the query that
motivated the preceding stage. The configuration to carry forward is a decision for the
next registration, not something this run may make by picking a different constant.
