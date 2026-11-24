# A1 closeout — what is frozen, what it is, and how to rebuild it

A1 is closed. This file names every revision the result depends on, points at the evidence,
and records the two things about it that are weaker than they should be.

## The result

**The nexus arm did not beat baseline on any task; it lost on one.** Memory, as measured in
A1, did not help, and it consumed **1.43× baseline's `input_tokens` and 1.57× its
`cache_read_input_tokens`** (1.56× combined) to not help. **No dollar figure is quoted**:
`runner-a1.md` §3 found `"costBasis":"unknown"` on this model and concluded the CLI's cost
field has no established provenance, so A1 omits it. The full statement
and its limits are in [`results-heldout-a1.md`](results-heldout-a1.md); where the runs spent
their effort is in [`diagnostics-heldout-a1.md`](diagnostics-heldout-a1.md).

## Revisions the result depends on

| component | revision | how it is known |
| --- | --- | --- |
| product (`src/nexus_memory`) | `2cd531f` | **inferred**, not recorded — see *Gaps* |
| harness (runner, schedule) | `9ce8607` | last change to `run_arms_isolated.py` / `run_schedule.py` before the run |
| compliance scorer | `a1-scorer-4` | recorded in every `records.json` |
| functional scorer | `a1-functional-2` | recorded in every `records.json` and every scored block |
| registration | [`freeze-heldout-a1.md`](freeze-heldout-a1.md) + [revision 2](freeze-heldout-a1-r2.md) | both kept; r1 is superseded on one point only and is not edited |
| corpus | digest `9ae2a9f268dd894d` | [`corpus-heldout-a1.json`](corpus-heldout-a1.json), frozen before any arm ran |
| mix | sha256 `21a44b688ed3f65f…` | [`mix-heldout-a1.json`](mix-heldout-a1.json) |
| schedule | digest `1a960a7465fa36a0`, seed `20260912` | [`schedule-heldout-a1.json`](schedule-heldout-a1.json) |
| model | `deepseek-flash` | registration §5, `modelUsage` in every envelope |
| turn ceiling | flag value **30** | not `num_turns`, which reads 31 on a truncated run |

## Evidence, and where it lives

Committed, 2.9 MB, sufficient to rebuild every published table:

* [`run-heldout-a1/`](run-heldout-a1/) — the twelve `records.json` (each carrying its arms'
  full `tool_calls` array and result envelope) and all 36 `patch.diff`.
* [`base-trees-heldout-a1.json`](base-trees-heldout-a1.json) — each task's tracked paths at
  its `pre_fix` commit, so mutation classification needs no clone of `pallets/click`.
* [`diagnostics-heldout-a1.json`](diagnostics-heldout-a1.json) — one row per arm-run.

Not committed: the 36 `trace.jsonl`, 64 MB, held outside the repository and checksummed in
[`run-heldout-a1/SHA256SUMS-external-traces`](run-heldout-a1/SHA256SUMS-external-traces)
(89 files: records, traces, patches, logs and reports).

Capture attempts 1 and 2 and their failures are recorded in
[`capture-attempt-1.md`](capture-attempt-1.md) and [`capture-attempt-2.md`](capture-attempt-2.md);
the third produced the corpus.

## Rebuilding, with no model and no network

```
python3 diagnose_heldout.py run-heldout-a1 --from-records --json diagnostics-heldout-a1.json
python3 bootstrap_heldout.py run-heldout-a1
```

Three checks, each run rather than asserted:

| check | proves | result |
| --- | --- | --- |
| `diagnose_heldout.py … --check-sources` | records path == 64 MB trace path | 36/36 identical |
| `diagnose_heldout.py … --verify-base-trees` | cached trees == `git ls-tree` | identical, 4/4 tasks |
| `shasum -c SHA256SUMS-external-traces` | the external traces have not moved | 89 files |

Suite at closeout: **334 passed, 2 skipped**; mutation matrix **36 passed**.

## Gaps, recorded rather than smoothed over

**0. Operational friction was understated, and the diagnostics that described it were wrong in
four ways.** Both are corrected in place rather than in a footnote: see the friction table in
[`results-heldout-a1.md`](results-heldout-a1.md) and the detector corrections recorded there.
The functional verdicts under `a1-functional-2` are **unchanged** — none of this re-scores a
run, and the negative result stands exactly as registered.

**1. The product revision was not recorded by the harness.** `records.json` captures the
scorer versions, the config, the corpus and schedule digests and the store digests either
side of every arm-run — but not the git revision of the code under test. `2cd531f` is
inferred: it is the only change to `src/` in the branch, and it predates the first
`started_utc` by roughly two hours. That inference is almost certainly right and is still an
inference. **A2's registration must record the product revision in the run record itself.**

**2. The §5 bootstrap was omitted from the first release of the result.** §5 required a
percentile bootstrap over tasks and separately forbade an inferential claim *from* it; the
first release read the prohibition as covering the computation. Revision 2 §5 lists "the
analysis and exclusion rules in §5" among what stands unchanged, so r2 did not relax it. The
interval is now computed by [`bootstrap_heldout.py`](bootstrap_heldout.py) and reported with
its prohibition intact. The frozen registration was **not** edited; the correction is in the
report, and is disclosed there as a deviation of the report rather than of the run.

## What A1 does not license

The four held-out tasks are now **exposed**. They may be used for labelled diagnostics and
regression checks; another run on them is not fresh held-out evidence and must not be
reported as one. Any further experiment gets its own registration and its own output
directory — this one is closed.
