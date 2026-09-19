# `dev-m1` — a memory-specific development set, frozen

**Development only.** k1–k4 have been run — in the A2 calibration (v2) and again in A2-R.
They are exposed. Nothing in this file is, or may later be relabelled as, held-out evidence.
A held-out evaluation needs tasks nobody has seen; this set exists to *develop a consultation
policy* before one is spent.

**Frozen before any model outcome.** Every label below is computed by
[`verify_dev_m1.py`](verify_dev_m1.py) from a probe against the task's own checkout. Nothing
here was tuned to a result, because no result exists.

| | |
| --- | --- |
| corpus | `corpus-dev-m1.json`, sha256 `2d2e6962a60f4633acc7044d0af097819669f3ef5bd1dcabd5da9cd4a8dd5df8` |
| measurement | `results-dev-m1.json`, sha256 `a72fde03ab9fa232d6dc26f632e25329c2198fbba2e630a1e5fb4a43277f24ca` |
| tasks | `prompts-calib-a2.json` `04d3051ceed1504e…`, `tasks-calib-a2.json` `00941f5dbc6e5ba4…` — **unchanged**; prompt digests stay as `LAUNCH-A2R.md` records them |
| fixtures | the A2-R sweep's own `base/` trees, at each task's registered `pre_fix` commit |
| counterexamples | `test_dev_m1.py`, 16 checks |

---

## 1. Why A1's corpus could not answer this

A1's held-out corpus is **deliberately unmatched** to k1–k4. That made it right for what the
calibration used it for — exercising retrieval overhead under realistic turn pressure — and
useless for asking whether a cheaper consultation policy still reaches information that
matters, because there was nothing that mattered to reach. This corpus is matched.

## 2. What a memory is, and where it came from

24 memories, three provenance classes, never merged.

| class | n | what it is |
| --- | ---: | --- |
| **captured** | 13 | the frozen `heldout-a1` corpus, carried **verbatim** — content, kind, tags and ids. Written by a prior session that had not read A1's held-out tasks. It was *not* written blind to k1–k4 either, because k1–k4 did not exist when it was captured; that is a weaker claim and is stated as the weaker one. |
| **derived** | 6 | written here. `m01`–`m04` are statements about code in a real checkout, with the file recorded. `m05` and `m06` are **constructed contradictions**: no revision of this repository is asserted, and each records where a reader can see it refuted. |
| **distractor** | 5 | written here, true of the repository, and bearing on none of the four tasks. They exist so that "useful information among unrelated memories" is a condition with a digest rather than an adjective. |

Every derived and distractor memory is flagged `synthetic_mechanism_test: true`.

**The answer key is withheld from both arms.** `probe`, `provenance`,
`synthetic_mechanism_test`, `derived_from` and `contradiction_discoverable_at` are the
evaluator's instrument. `seed_store.py` delivers `content`, `kind`, `tags` and nothing else,
and `render_notes.py` renders the same three — the existing mechanism, unchanged, and now
covered by a test.

## 3. Labels, measured not asserted

A **probe** is a regular-expression check against one file in the task's own pre-fix tree.
`true` / `false` / **`unknown`** — a missing file is unknown, never refuted: `h01` reads
`src/click/_utils.py`, which exists at k3's revision and at none of the others.

The one judgement in the file is **subject** — what a memory is *about* — and it is kept
separate from the measurement, which says whether the claim *holds*. Generic tooling and
testing procedure have no subject: counting them would make every task "useful" and empty the
category, the same rule `mix-declaration-a1.md` settled for A1.

| | definition |
| --- | --- |
| **useful** | ≥1 memory whose claim is TRUE here and whose subject is this task's mechanism |
| **unnecessary** | no memory's subject is this task's mechanism |
| **stale** | ≥1 memory whose claim is FALSE here and whose subject is this task's mechanism — following it leads somewhere wrong |
| **distracting** | a property of the corpus: the useful memories are a minority of what one retrieval returns |

Categories overlap and are not resolved into one label. All four tasks come out
**useful + stale**, which is the interesting case and the reason the conditions below exist.

### Measured truth (extract; full table in `results-dev-m1.txt`)

| memory | k1 | k2 | k3 | k4 | subject |
| --- | --- | --- | --- | --- | --- |
| h02 | false | **false** | true | false | k2, k3 |
| h09 | false | false | true | false | k3 |
| h01 | unknown | unknown | true | unknown | k2, k3 |
| h13 | true | **true** | true | true | k2 |
| m01 | **true** | true | true | true | k1 |
| m02 | true | true | true | **true** | k4 |
| m03 | true | true | **false** | true | k2, k3 |
| m05 | **false** | false | false | false | k1 |
| m06 | false | false | false | **false** | k4 |

**k2 and k3's stale cases were found, not built.** The fixtures sit at different Click
revisions: k3 carries the `UNSET` sentinel, `src/click/_utils.py` and the issue-3024
reconciliation; k1, k2 and k4 do not. So the captured corpus — written against the newer code
— is *contradicted by* k2's checkout (`h02`), and a memory describing the older form (`m03`)
is contradicted by k3's. Two real contradictions, in opposite directions, on the two tasks
whose subject is flag/default handling.

`m05` and `m06` are the constructed ones, for k1 and k4, and say so. Their contradictions are
discoverable at `Context.__init__` / `Context.close` and at
`Command.get_help_option_names` respectively — both in `src/click/core.py`, both one read
away.

### Leakage, checked rather than assumed

- **No memory contains an identifier that appears only on the added side of its task's own fix
  diff.** Checked per task against the real `pre_fix..fix` diff over `src/`.
- **No memory names a function defined in the hidden acceptance checks and not in the visible
  tree.** Checked per task. A structural guard in `test_dev_m1.py` additionally forbids any
  test-function name in any memory, with a control that the guard still catches one.

## 4. Conditions: one corpus, four subsets per task

A one-category-per-task design confounds category with bug: four tasks and four categories
means every category is also a different defect. Holding the task fixed and varying the store
removes that.

| condition | contents |
| --- | --- |
| `useful` | support + this task's TRUE on-subject memories |
| `unnecessary` | support only — **the same six memories for every task**, which is what makes it a control |
| `stale` | support + this task's FALSE on-subject memories |
| `distracting` | the whole corpus: every other task's memories and all five distractors |

| cell | n | digest | cell | n | digest |
| --- | ---: | --- | --- | ---: | --- |
| k1/useful | 7 | `cd0321037880d862` | k3/useful | 13 | `d3471162711b5710` |
| k1/stale | 7 | `0d1d6640c911a762` | k3/stale | 7 | `d1c5b637d4e0b3fd` |
| k2/useful | 10 | `97a2377854037cf2` | k4/useful | 7 | `5c5a212855c47cfb` |
| k2/stale | 7 | `7a03f97c0ecb9d6b` | k4/stale | 7 | `fe69d08e25aba9aa` |
| *any*/unnecessary | 6 | `933f66cd16b1d78d` | *any*/distracting | 24 | `004762cab5248613` |

## 5. Reachability — the useful memories do arrive, and so do the wrong ones

Measured through the arm's **own** retrieval path: a store seeded by `seed_store.py`, queried
through `MemoryService.search` with a frozen per-task query. Not asserted; run.

| task | `useful` condition | `distracting` condition |
| --- | --- | --- |
| k1 | `m01` at rank **1** of 2 hits | `m01` at rank **2**; the stale `m05` at rank **1** |
| k2 | `h13` 1, `h05` 2, `m03` 3, `h07` 5 of 6 | `h02` (**stale**) at rank **1**; `h13` 2, `h05` 7, `m03` 8, `h07` 12 |
| k3 | `h11` 1, `h08` 2, `h02` 3, `h01` 4, `h05` 5, `h09` 6, `h07` 9 of 10 | `h02` 1, `h05` 2, `h11` 3 …; the stale `m03` at rank **13** |
| k4 | `m02` at rank **1** of 2 | `m02` 1; the stale `m06` at rank **2** |

**This is the finding that makes the set worth having.** Under `distracting` — 24 memories,
one search — a *refuted* memory is the top hit for k1 and k2 and the second for k4. A policy
of "one search, at most three full-body fetches" would fetch the wrong memory first on three
of four tasks, and on k2 would spend all three fetches without reaching `h05`, `m03` or `h07`.
k3 is the opposite shape: seven useful memories in the top six ranks and the stale one at 13,
where bounding consultation costs nothing.

So the set can show **benefit** (k3, and the `useful` conditions generally) and **harm**
(k2's `distracting` condition) from the same policy, which is what it was built for.

## 6. Controls retained

Unchanged and still required for any run of this set:

- **the unpatched tree** — the no-model control the runner already runs per task, and the
  fix-oracle beside it;
- **the upstream copy** — `/opt/homebrew` is a system read root and a released Click is
  readable from it; A2-R measured 10 of 47 calls going there in one arm-run. Either deny it or
  register it as a known channel, deliberately;
- **the collection control** — `PYTEST_IGNORE`, now applied by both scorers;
- **the timeout control** — wall clock reported separately from turn exhaustion;
- **the boundary controls** — including `cache_shadow_unreadable`, added after two A2-R
  arm-runs read another sweep's artifacts through the macOS cache shadow.

## 7. What this set still cannot do

- **It is not held out.** Every task in it has been run three times or more.
- **One repository, one library, one model.** Nothing here generalises past `pallets/click`
  and the configured model.
- **Four tasks.** Any per-condition figure is 4 observations wide before attempts are added.
- **`m05` and `m06` are constructed.** k1's and k4's stale conditions test the *mechanism* —
  does the agent notice a memory the checkout refutes — and are not evidence that stale
  memories of that shape occur.
- **Rank is not delivery.** The reachability table says what a search returns, not what an
  agent reads. What it actually consults is what the experiment measures.

## 8. Rebuilding

```bash
python3 benchmarks/agent/build_dev_m1.py
<venv>/bin/python benchmarks/agent/verify_dev_m1.py \
    /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run \
    --out benchmarks/agent/results-dev-m1.json
```

The verifier needs the product on its path (it seeds a real store and queries it through
`MemoryService`), so it runs under `.venv-sqlite`. `--no-reach` skips that half and needs
only the fixtures.
