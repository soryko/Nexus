# A1 held-out registration — frozen 2026-09-12

This fills `protocol-a1` §15 slots 2, 4, 5 and 6 for the held-out evaluation. Everything here
is fixed **before any held-out task is run**. Nothing in it may be adjusted after a result is
seen; a change requires a new registration with a new date, and the superseded one stays.

One thing is deliberately **not** filled: the memory corpus. It cannot be, and §7 of this
document says why.

---

## 1. The task set, and the rule that produced it

### The boundedness admission bar

Read from failing **functions** and source **hunks** together, never the raw failure count:

> A candidate is admitted when its fix changes at most **6 test functions'** outcomes and at
> most **7 source hunks**.

The bar is calibrated against the development tasks that were actually completed under these
ceilings, not chosen to admit a desired set. `d3` is the calibrating case: 1 failing function
across **7 source hunks**, and all three arms completed it. A tighter hunk bar would have
excluded a task the harness has demonstrably run.

**The bar excludes none of the six remaining candidates, and that is the honest result rather
than a failure of the bar.** The filtering was already done upstream: six *classes* were
excluded before measurement (`tasks-click-a1.md` §3 — pager/TTY races, Windows error
reporting, shell completion, `readline`, `pdb`, and non-behavioural commits), and the fixture
check removed one more candidate after measurement. The nine in the manifest are the residue.
The bar is registered anyway, because a later survey needs a rule to apply rather than a
precedent to argue from.

### The disjoint split

Development and harness-validation tasks are `d1`–`d4` and are **spent**: the corpus paired
with them was authored by someone who had read the fixes (`capture-policy-a1` §5). They
appear here only to be excluded.

| set | task | fix | pre-fix | subject | fns | hunks |
| --- | --- | --- | --- | --- | --- | --- |
| dev | d1 | `8d7f03dac8` | `ef11be6e49` | empty `auto_envvar` → `None` | 1 | 1 |
| dev | d2 | `1b0e19f505` | `499bbeea64` | envvar hint when none configured | 1 | 1 |
| dev | d3 | `6de2121518` | `1339fd3323` | `UNSET` in a `default_map` | 1 | 7 |
| dev | d4 | *(authored)* | `1339fd3323` | register a pytest marker | — | — |
| **capture** | **c1** | `ebcd548d50` | `7f7bbe4569` | `is_flag=False` with `flag_value` | 1 | 2 |
| **capture** | **c2** | `546f2851f4` | `ae46cfd6bc` | callable `flag_value` as a default | 4 | 3 |
| **held-out** | **h1** | `0f71fe771c` | `c943271a26` | dual-option arbitration vs explicit defaults | 6 | 5 |
| **held-out** | **h2** | `762c97eef7` | `8929d39278` | double-bracketing of choices in the synopsis | 2 | 2 |
| **held-out** | **h3** | `b67832c216` | `8c1a0a7abb` | parsing when a parameter is named `help` | 5 | 4 |
| **held-out** | **h4** | `f58ca3e814` | `420c8fb44e` | `copy`/`deepcopy`/`pickle` of `Sentinel` | 2 | 1 |

Three disjoint sets. No task moves between them after this date, and none is added or dropped
once the run starts.

### Why the capture/held-out split falls where it does

It is chronological, and it has to be. `capture-policy-a1` §1 admits a memory only if it was
observable **at or before** the task's pre-fix commit, so every capture task must sit behind
every evaluation task in the history. Verified, not assumed — both capture fixes are
ancestors of all four held-out pre-fix commits (8 of 8 `git merge-base --is-ancestor` checks):

```
c1 ebcd548d50  2025-11-19  →  ancestor of c943271a26, 8929d39278, 8c1a0a7abb, 420c8fb44e
c2 546f2851f4  2026-03-01  →  ancestor of c943271a26, 8929d39278, 8c1a0a7abb, 420c8fb44e
h1 0f71fe771c  2026-05-15
h2 762c97eef7  2026-06-10
h3 b67832c216  2026-07-09
h4 f58ca3e814  2026-08-29
```

**Four evaluation tasks is thin, and it is the honest ceiling of this source.** Nine
candidates survived the class exclusions and the fixture check; four are spent on development
and two are needed for capture. Section 5 registers what may and may not be concluded from
four.

---

## 2. Capture provenance (§15 slot 3, procedure only)

The held-out corpus does not exist and **must not** exist yet. `capture-policy-a1` §3 fixes an
order that cannot be reordered:

1. A prior-session agent run works `c1`, then `c2`, seeing only the repository at or before
   each task's pre-fix commit. It does not see `h1`–`h4`, and is not told they exist.
2. It records memories under the capture policy — the convention, never the violation.
3. The corpus is frozen and its content digested (rows, not the file: the store is WAL and a
   file hash cannot tell a checkpoint from a write).
4. **Only then** are `h1`–`h4` revealed and the arms run.

A memory written after step 3 is seeded, not captured, and voids the corpus for that task.

Binding consequences of this document naming all six tasks in one place: the capture run is
given `c1` and `c2` and nothing else, by a harness that reads the capture set from this
registration; and no human who has read this document may author, edit or select a memory in
the held-out corpus. The development corpus was authored under declared exposure precisely
because that rule could not be met there, and the whole point of the held-out set is that it
can.

### The task mix cannot be fixed here, and is not

`protocol-a1` §7 requires the mix to include tasks where memory is **useful**, **unnecessary**
and **outdated**. That assignment is a property of the *pairing* of a task with the captured
corpus, not of a commit (`tasks-click-a1.md` §5), so it is fixed when the corpus is frozen at
step 3 — declared before any arm runs, and after the memories exist.

**If the captured corpus yields no outdated instance for any held-out task, that is reported
as an unmet mix requirement.** It is not repaired by editing a memory afterwards, by
re-selecting tasks, or by capturing again with the held-out tasks in view. Any of those would
convert the measurement into the thing it exists to detect.

---

## 3. Arms, trials and ceilings (§15 slots 4 and 6)

| | value | why this value |
| --- | --- | --- |
| arms | `baseline`, `nexus`, `notes` | arm 4 does **not** run |
| model | `deepseek-flash`, all three arms | `runner-a1.md` |
| trials, `n` | **3** attempts per task per arm | see below |
| arm-runs | **36** (4 tasks × 3 arms × 3 attempts) | plus 2 capture runs = 38 model runs |
| turn ceiling | `--max-turns 30` | in-run, verified to terminate a run |
| wall-clock ceiling | **600 s** per arm-run | max observed in development: 307 s |
| arm order | frozen in `schedule-heldout-a1.json` | digest `1a960a7465fa36a0`, seed 20260912 |

**`n = 3`.** Fixed now and not adjusted after results. Three is the smallest number that lets
a per-task summary be something other than a single draw, and 36 arm-runs is what the observed
per-run usage supports: median 13.8 k input / 6.4 k output / 232 k cache-read over the 24
development arm-runs, worst case 28.6 k / 19.3 k / 751 k.

**Arm 4 does not run.** Registering it would add a fourth arm's worth of spend to answer a
question this evaluation is not yet in a position to ask.

### Accounting fields, recorded per arm-run

`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`,
`wall_clock_s`, the **tool-call count derived from the trace**, and the terminal
classification. `total_cost_usd` is disqualified as a spend measure and `num_turns` is not
reported at all (`protocol-a1` §10); the configured `--max-turns` and the observed termination
reason are recorded instead.

### The seed, and what the draw actually gave

The seed is the freeze date, `20260912`, fixed before the draw rather than chosen from among
draws. The schedule is frozen in `schedule-heldout-a1.json` and read at execution; no order is
derived inside a run.

The realised counterbalance is **reported, not corrected**: 12 pairs, 4 distinct orders, and
`nexus` runs first 6 times against 3 each for `baseline` and `notes`. That is an imbalance. It
is left standing because re-drawing until the balance looks better is a choice made on the
draw, and the alternative — a balanced design — is not what `protocol-a1` §12 registered. **If
a first-position effect is suspected in the results, it is reported as a confound this
schedule cannot rule out**, not corrected after the fact.

---

## 4. Reported figures

Per arm, per task, never collapsed into one score (`protocol-a1` §8):

1. **Functional correctness** — the hidden acceptance checks.
2. **Task-requirement compliance** — `score_compliance.py` at `a1-scorer-4`, reported as
   `compliance` with its `unknown_count` and `not_applicable_count` beside it, the failed
   requirements named, and any `instrument_errors` listed separately from arm outcomes.
3. **Delivered context**, in three buckets — necessary, useful support, irrelevant — with only
   the third reported as waste.
4. **Usage** — the accounting fields above.
5. **Termination** — the terminal classification, with truncation reported as a fact rather
   than a verdict.
6. **Total storage** — bytes the nexus arm's store occupies, beside any benefit figure.

**Required-evidence completeness (§8 figure 2) is deferred and will not be reported.** See §6.

---

## 5. Analysis, fixed before any scored run (§15 slot 5)

**The unit is the task.** Three attempts on one task are three measurements of that task and
are never counted as three tasks.

**Collapsing rule, per (task, arm), applied first:**
- binary figures (functional correctness): **pass-rate over the 3 attempts**;
- ratio and continuous figures (compliance, delivered bytes, tokens, wall clock, tool calls):
  **arithmetic mean over the scored attempts**;
- an attempt excluded under §5.3 does not contribute, and the contributing attempt count is
  reported with every summary.

**Paired comparison over tasks.** Three contrasts, each paired per task, each reported
separately and none merged: `nexus − baseline` (does persistent memory help), `notes −
baseline` (does having the information help), `nexus − notes` (does Nexus's retrieval add
anything beyond notes). A result answering the first and not the third is evidence for the
habit, not the tool, and is reported that way.

**Uncertainty.** A percentile bootstrap over tasks, 10 000 resamples, reported with the
contributing task count. **With four tasks this interval does not support an inferential
claim and none will be made from it.** The primary reporting is the per-task table — four
rows, three arms, every figure — and a sign count of which arm each task favours. A difference
will not be called established on the interval.

**Attempt-level spread** is reported separately, as a property of the agent's run-to-run
variability, never as a test of the comparison.

### 5.3 Exclusion rules

| terminal | treatment |
| --- | --- |
| `completed` | scored |
| `max_turns` | **scored**, with truncation recorded as a fact |
| `timeout` | scored, with the wall-clock overrun recorded |
| `env_fail`, `unknown` | **excluded**, and counted |

Exclusion counts are reported **per arm**. If they are not balanced across arms, the imbalance
is reported as a threat to the comparison rather than absorbed into it — an arm that fails the
environment more often is not an arm that performed worse.

**Non-binary verdicts and their bias direction**, derived from the scoring rule rather than
the verdict's name (`protocol-a1` §10): a `skipped` acceptance check counts as **not passed**;
an `unknown` compliance check leaves the ratio's denominator, so it can only move a ratio
toward its settled checks and is reported with its count; an `instrument_error` is never an
arm outcome and its check is `unknown`.

### 5.4 What is pre-registered as a negative result

If the nexus arm does not beat baseline, that is the finding and it is reported as
prominently as the reverse would be. If an outdated memory makes a memory arm score worse
than baseline on some task, that is a real outcome this protocol exists to be able to report,
not a defect to be corrected.

---

## 6. Deferred, explicitly

**Required-evidence completeness** (`protocol-a1` §8 figure 2, §7b) is **deferred from A1**
and will not be reported. Implementing it needs two things that do not exist: a per-task
rubric declaring the facts an answer must rest on, and an instrument that reads a patch and a
trace and decides whether each was obtained. Writing either after seeing results is the
failure this protocol was built to prevent, and writing both properly is a larger piece of
work than the evaluation it would serve.

The five figures that remain — correctness, requirement compliance, delivered context, usage
and termination — are a useful first evaluation, and the result will say plainly that evidence
completeness is unmeasured.

**The harder stale-memory variant** is not part of this milestone. The corpus will contain
whatever outdated instances the capture run naturally produces; a deliberately adversarial
stale-advice design is registered as future work, before any broader robustness claim.

---

## 7. Limitations carried into this run

1. **Public-history exposure is unresolved.** Click is widely mirrored and no training window
   is published for `deepseek-flash`. Isolating the checkout prevents *retrieval* of the fix;
   it establishes nothing about *memorisation*. How it interacts with the arms is unknown, and
   the draft's "bears on all arms roughly equally" stays withdrawn.
2. **Four evaluation tasks.** Stated in §1 and constraining §5.
3. **The easy case only.** Every task is a bounded, single-defect fix in one small library.
   Nothing here speaks to large or unfamiliar codebases.
4. **The arm-order draw is unbalanced**, 6/3/3 on first position (§3).
5. **The corpus does not exist yet**, so no mix claim is made in this document (§2).

---

## Registration

| artifact | digest |
| --- | --- |
| `schedule-heldout-a1.json` | `1a960a7465fa36a0` (schedule rows) |
| this document | recorded in `SHA256SUMS` at the freezing commit |

Frozen 2026-09-12, before any held-out task has been run and before any held-out memory has
been captured. Slots 2, 4, 5 and 6 of `protocol-a1` §15 are filled by this document; slot 3 is
filled when the capture run completes step 3 of §2.
