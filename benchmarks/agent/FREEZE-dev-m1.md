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
| measurement | `results-dev-m1.json`, sha256 `a72fde03ab9fa232d6dc26f632e25329c2198fbba2e630a1e5fb4a43277f24ca` — **as first published**, retained unchanged |
| measurement, corrected | `results-dev-m1-r2.json`, sha256 `10ec29e7999bfdb679f43b1f3626fbfcbf2d2feb8ba431ec0ada191829c65269`, beside it. Labels, ranks and condition digests are **identical**; only the leakage half changes, from "none" to `unresolved`. The verifier exits 3 on it. |
| tasks | `prompts-calib-a2.json` `04d3051ceed1504e…`, `tasks-calib-a2.json` `00941f5dbc6e5ba4…` — **unchanged**; prompt digests stay as `LAUNCH-A2R.md` records them |
| fixtures | the A2-R sweep's own `base/` trees, at each task's registered `pre_fix` commit |
| provenance review | [`PROVENANCE-dev-m1.md`](PROVENANCE-dev-m1.md) / `provenance-dev-m1.json`, sha256 `8a2eca820be5d8a6711a2b3bc249b54ccb646a652479c65e80c6433890284d59` — **all 24 memories**, frozen beside the corpus |
| counterexamples | `test_dev_m1.py`, 27 checks — 17 on the labels, 10 on the leakage scans themselves; `test_provenance.py`, 13 on the review |

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

**What the label `useful` does and does not assert.** It is a two-part structural test: the
memory's subject is this task's mechanism, and its probe holds at this checkout. That is
*on-topic and currently supported*. It is **not** a measurement of usefulness — nobody has
shown that reading the memory helps an agent finish the task — and it is certainly not
**necessity**: nothing here tests whether the task can be completed without it. The word is a
label on a set, not a finding, and A3 treats the memories it names as *predeclared
task-relevant facts* rather than required ones for exactly this reason.

**What a probe establishes is narrower than the prose it labels.** A probe is a regular
expression over one file, so it establishes a **structural observation** — these patterns are
present, or this one is absent — and not the truth of the whole paragraph the memory contains.
`m02` is the clearest case: its probe checks that `get_help_option`, `get_params` and
`iter_params_for_processing` are *defined*. Its prose additionally claims that
`get_help_option` **constructs** an option on each call, that `get_params` **appends** its
result, and that `iter_params_for_processing` orders parameters **by invocation order and
`is_eager`, comparing the parameter objects**. Those are allocation and ordering behaviours;
three `def` lines do not establish any of them. Reading `true` in the table as "every sentence
in this memory is correct" is a stronger claim than the instrument supports, and where it
matters — which is wherever a memory is treated as a delivered *fact* — the stronger claims
need code review or a behavioural probe, neither of which has been done.

**Version mismatch is not observed staleness.** `h02` and `m03` are refuted at k2's and k3's
checkouts because the fixtures sit at different Click revisions and the captured corpus was
written against the newer code. That is information captured from a later revision and
presented to an earlier checkout — a **version mismatch**, constructed by the choice of
fixture. It is a legitimate way to obtain a memory the checkout refutes, and it is *not*
evidence that memories go stale chronologically in ordinary use. Nothing in this set observed
a memory rotting over time; the set arranges for refutation and then measures it.

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

**k2 and k3's stale cases were not authored as contradictions — but they were still
arranged.** The refutation was found rather than written, and the arrangement is the fixture
choice. The fixtures sit at different Click revisions: k3 carries the `UNSET` sentinel, `src/click/_utils.py` and the issue-3024
reconciliation; k1, k2 and k4 do not. So the captured corpus — written against the newer code
— is *contradicted by* k2's checkout (`h02`), and a memory describing the older form (`m03`)
is contradicted by k3's. Two real contradictions, in opposite directions, on the two tasks
whose subject is flag/default handling.

`m05` and `m06` are the constructed ones, for k1 and k4, and say so. Their contradictions are
discoverable at `Context.__init__` / `Context.close` and at
`Command.get_help_option_names` respectively — both in `src/click/core.py`, both one read
away.

### Leakage — limited identifier scans, one of which cannot run here

These are **token comparisons**. They ask whether a memory names something the fix introduced.
They cannot establish that no memory conveys a fix **semantically**, in different words: a
memory that describes the correct behaviour in prose would pass every scan below. Provenance
review, not scanning, is what bears on that, and it is recorded in §2.

- **Hidden-check names: measured, and clean.** No memory names a function defined in a task's
  hidden acceptance checks and not in the visible tree. Scanned against 3, 2, 1 and 2 real
  check names for k1–k4 respectively — a non-vacuous comparison. A structural guard in
  `test_dev_m1.py` additionally forbids any test-function name in any memory, with a control
  that the guard still catches one.
- **Fix-diff identifiers: `unresolved` on all four tasks.** This is a correction. The first
  version of this file reported "no memory contains an identifier that appears only on the
  added side of its task's own fix diff, checked per task against the real `pre_fix..fix`
  diff". **That check never ran.** No A2-R `fixtures.json` records a `clone` key, so
  `fix_added_tokens` took its "missing clone" branch, returned an empty token set, and an
  empty set matches nothing — which published as "none" for all four tasks. It was the absence
  of evidence, printed as evidence of absence.

  The verifier now refuses instead of returning empty (`Unresolved`, exit 3), takes the clone
  from `a1-config.json` when the fixture names none, and the scan runs. **It still cannot
  settle the question on this task set**, for a second and independent reason: none of the
  four fixes introduces a single identifier absent from its pre-fix tree. All four are small
  behavioural changes in `src/click/core.py` that reuse names already present, so there is no
  distinctive token to scan for. Reported as `unresolved`, never as `none`.

  Two intermediate versions of the scan were wrong in the other direction and are recorded so
  the correction is not mistaken for a tightening: taking "added side" literally matched
  `click`, `option`, `close` and `before` and flagged 5–20 of the 24 memories per task; keeping
  the prose of an added **docstring** flagged `precedence` and `declared`. A check that can
  never pass is as uninformative as one that can never fire. `test_dev_m1.py` now carries a
  positive control — a deliberately contaminated memory naming an identifier a synthetic fix
  introduces, which the scan must catch — so "clean" and "never looked" cannot be confused
  again.

  **What fix leakage rests on here is therefore provenance, not scanning**, and the
  provenance is now reviewed per memory rather than asserted per class:
  [`PROVENANCE-dev-m1.md`](PROVENANCE-dev-m1.md) records, for **all 24 memories** — every
  one an arm can receive under `distracting`, not only the on-subject ones — the source it
  came from, what its author had seen, what its probe supports as against what its prose
  asserts, which register it is written in (describes existing behaviour / diagnostic
  guidance / conveys the repair), and whether it is suitable here. The result is
  **reviewed provenance with limited automated leakage checks.** It is weaker than a
  measurement and is labelled as weaker.

  **One memory is flagged: `m02`.** It states both halves of k4's defect — that
  `get_help_option` CONSTRUCTS an option on each call, and that `iter_params_for_processing`
  compares the parameter OBJECTS — and k4's fix carries an in-code rationale reading *"avoid
  creating it multiple times. Not doing this will break the callback odering by
  iter_params_for_processing(), which relies on object comparison"*. The same two clauses,
  minus the remedy. It was written by an author who had seen k4. It is diagnosis rather than
  repair and stays in the corpus, but **a k4 result under either policy may not be read as
  evidence that retrieval located the mechanism unaided.**

  **And `m02` is exactly what the scans cannot see.** A third scan was added for this review
  — fix *locality*, over identifiers the memory presents as code that also appear on a
  changed line of the fix. Unlike the added-identifier scan it fires: `close` on `m01` for
  k1, `flag_value`/`is_flag`/`default` across the k2 memories, `UNSET` on `h02` for k3. It
  reports **nothing for `m02`**, on k4 or anywhere, because every identifier in it predates
  the fix and the words it shares with k4's changed lines are English prose from an added
  docstring. So on this task set the scans flag memories the reading clears and clear the
  memory the reading flags. That is the general claim about semantic leakage in its concrete
  form.

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

## 5. Reachability — what one search returns, and at what rank

Measured through the arm's **own** retrieval path: a store seeded by `seed_store.py`, queried
through `MemoryService.search` with a frozen per-task query. Not asserted; run.

| task | `useful` condition | `distracting` condition |
| --- | --- | --- |
| k1 | `m01` at rank **1** of 2 hits | `m01` at rank **2**; the stale `m05` at rank **1** |
| k2 | `h13` 1, `h05` 2, `m03` 3, `h07` 5 of 6 | `h02` (**stale**) at rank **1**; `h13` 2, `h05` 7, `m03` 8, `h07` 12 |
| k3 | `h11` 1, `h08` 2, `h02` 3, `h01` 4, `h05` 5, `h09` 6, `h07` 9 of 10 | `h02` 1, `h05` 2, `h11` 3 …; the stale `m03` at rank **13** |
| k4 | `m02` at rank **1** of 2 | `m02` 1; the stale `m06` at rank **2** |

**What the table establishes.** Under `distracting` — 24 memories, one search — a *refuted*
memory is the top hit for k1 and k2 and the second for k4, and k3's stale memory sits at 13
while six of its seven useful memories occupy ranks 1–6.

**What it does not establish, corrected from the first version of this section.**

- **The k3 arithmetic was wrong.** It said "seven useful memories in the top six ranks", which
  is impossible. k3 has seven useful memories; six are at ranks 1–6 and the seventh, `h07`, is
  at **rank 15** under `distracting` (rank 9 under `useful`). A bounded policy reaching the top
  ranks reaches six of seven, not all seven.
- **Rank is not fetch order.** A bounded policy is not required to fetch results in rank order,
  and nothing in the bound says it must. "It would fetch the wrong memory first" assumes an
  ordering the policy does not specify; what the table shows is *what a search returns*, not
  what an agent picks from it.
- **The agent may not issue this query.** The per-task queries here are frozen so the ranks are
  reproducible. They are a reader's guess at what an agent would ask. An agent writing its own
  query gets its own ranking, and the whole table moves with it.
- **Ranking evidence cannot show that bounding "costs nothing".** Cost is measured in tokens
  and outcomes, not ranks; k3's favourable ranking is consistent with the bound costing
  nothing *and* with it costing something the ranking cannot see. The earlier claim that it
  "costs nothing" on k3 is withdrawn.
- **Ranking evidence cannot show harm either.** That a useful memory ranks 15th does not
  establish that an agent bounded to three fetches performs worse; it establishes that one
  route to that memory is long. The earlier claim of demonstrated **benefit** (k3) and
  **harm** (k2) is withdrawn in both directions.

What the set therefore offers is a store in which the policies *can* differ, with the
difference measured in advance and model-free. Whether they do differ, and in which direction,
is the experiment — not this table.

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
  agent reads, and not the order in which it reads it. What it actually consults is what the
  experiment measures.
- **`useful` is a structural label, not a measurement of usefulness, and never of necessity.**
  On-topic and currently supported is all it asserts.
- **A probe is narrower than the memory it labels.** It checks patterns in one file; a `true`
  does not certify every sentence of the prose. `m02` is the worked example in §3.
- **The refutations are arranged, not observed.** `m05` and `m06` are constructed; `h02` and
  `m03` arise from presenting a later revision's information to an earlier checkout, which is
  a version mismatch. Neither is evidence that memories go stale chronologically in use.
- **Fix leakage is unresolved by scanning.** No fix on this task set introduces an identifier
  absent from its pre-fix tree, so the token scan cannot detect anything here, and identifier
  scans could not establish semantic leakage in any case. What it rests on is a per-memory
  provenance review (§3), which is reading and not measurement.
- **`m02` supplies k4's diagnosis.** The review flags it: it states the mechanism of k4's
  defect in the same clauses as the fix's own rationale comment, stopping short of the
  remedy. Nothing measured on k4 shows that retrieval found that mechanism unaided.
- **Only one of the four conditions is in A3.** `distracting` is the one that run uses. The
  other three are frozen and reserved; nothing measured under one condition is evidence about
  the others.

## 8. Rebuilding

```bash
python3 benchmarks/agent/build_dev_m1.py
.venv/bin/python benchmarks/agent/review_provenance.py   # needs the clone; needs pyexpat
<venv>/bin/python benchmarks/agent/verify_dev_m1.py \
    /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run \
    --out benchmarks/agent/results-dev-m1-r2.json
```

**Exit status is the verdict**, not a report of whether the script crashed: `0` every scan ran
and found nothing, `1` a scan found leakage, `3` a scan could not gather its evidence. It exits
**3** here, for the reason in §3. `--clone <dir>` overrides the clone; without it the path comes
from the fixture, then from gitignored `a1-config.json`.

The verifier needs the product on its path (it seeds a real store and queries it through
`MemoryService`), so it runs under `.venv-sqlite`. `--no-reach` skips that half and needs
only the fixtures.
