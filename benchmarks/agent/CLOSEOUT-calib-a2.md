# A2 budget calibration — closeout

**Registered** in [`freeze-calib-a2.md`](freeze-calib-a2.md), configuration v2.
**Launched** from `3cd19d0302f802a39eb5c8f64a0cdf651779ad81` on 2026-09-15, at the frozen
harness identity `cbbaad64b4287fb90481fa79338dd6ac3cdac3be`.
**Computed** from the saved records by [`report_calibration.py`](report_calibration.py) and
[`reconcile_usage.py`](reconcile_usage.py). No model was invoked to write this, no cell was
re-run, and no record was edited.

---

## The result

> **No ceiling is selected.** Ceilings 30 and 45 are both complete and both truncate far above
> the registered 20% threshold — at 100% and 75%. Ceiling 60 is incomplete at 3 of 12 arm-runs
> and is not eligible. Under §5 that is **not a gap in the measurement; it is the measurement's
> answer**, and §5 says what follows from it: A2 does not proceed to a held-out registration,
> and the response is to revise the workload or the agent configuration — not to raise the grid
> until something passes.

The sweep stopped at `stopping_reason: "unresolved_accounting"`, driver exit 1
(`run_calibration.py:566`, exit at `:630`), with one arm-run whose consumption could not
be recovered. §4 makes an unresolved arm-run block continuation rather than shrink the total,
and that is why ceiling 60 stopped after its first row.

**Two things follow, and neither is "resolve the accounting and carry on":**

* Resolving it **would not restart the sweep.** By the end of that row the charge stood at
  36 560 844 against a 36 000 000 cap, so the pre-launch check `consumed + row_reserve ≤ cap`
  refuses the next row on the budget alone, independently of the unresolved arm.
* Resolving it **would not change the selection.** The two ceilings that decide the outcome are
  complete, and their truncation rates are nowhere near the threshold.

### Two gates, and the one that was previously conflated

§5 has two gates, and reading the first as the second is the error this closeout corrects:

| | question | ceilings 30 and 45 |
| --- | --- | --- |
| **eligibility** | are all 12 arm-runs present, scored, under one configuration? | **yes, both** |
| **qualification** | is pooled truncation at most 20%, with correctness not lowered? | **no, neither** |

Having two eligible ceilings satisfies §5's *"fewer than two eligible ceilings means no
selection"* clause. It makes the rule **applicable**. It does not make either ceiling pass it.
Earlier working notes recorded "§5 has what it needs" and that was true of eligibility and only
of eligibility; the sentence was carried forward as though it settled the selection, and it did
not. The rule is now applied by a program with a test for exactly this case rather than by
prose ([`test_report_calibration.py`](test_report_calibration.py),
`test_two_complete_ceilings_above_the_threshold_select_nothing`).

---

## 1. What this sweep executed

**27 arm-runs**, not 36. The grid registered in §3 is 36 (4 tasks × 3 arms × 3 ceilings); this
sweep completed 9 of the 12 planned rows and stopped inside the tenth.

| ceiling | rows | arm-runs | coverage |
| --- | --- | --- | --- |
| 30 | k1–k4 | 12 | complete |
| 45 | k1–k4 | 12 | complete |
| 60 | k1 only | 3 | **3 of 12** — k2, k3, k4 never ran, for all three arms |
| | | **27** | |

**`scored_arm_runs: 36` in `calibration-summary.json` is not this sweep's count.**
`grid_verdicts()` globs `*/run-*/attempt*/records.json` across the whole scratch — deliberately,
so a quarantining rename cannot drop rows from the ledger — so that field counts the 9 scored v1
arm-runs alongside this sweep's 27. Reading it as a sweep figure is what produced the earlier
claim of 36, and the composition is confirmed on the host: 9 + 27 = 36.

Two scopes, kept apart for the rest of this document:

* **accounting scope** — every invocation whose consumption belongs to this calibration,
  quarantined and void rows included. A quarantined row was still paid for.
* **analysis scope** — only cells whose *recorded identity* matches
  [`LAUNCH-A2.md`](../../LAUNCH-A2.md). Identity, not directory name: `v1-…` is excluded because
  it records no identity block at all, and `v2-void-forwarder-down-…` because it records
  `harness_revision` `19b843e4…` against the frozen `cbbaad64…`. A directory renamed tomorrow is
  excluded by the same test.

---

## 2. Coverage and terminals

| ceiling | arm-runs | `completed` | `max_turns` | `timeout` | truncated | **rate** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 | 12/12 | 0 | 12 | 0 | 12 | **100%** |
| 45 | 12/12 | 3 | 9 | 0 | 9 | **75%** |
| 60 | 3/12 | 2 | 0 | 1 | 1 | 33% *(of 3 observed; not a ceiling rate)* |

`terminal_status.classify` counts **both `max_turns` and `timeout` as truncated**. A ceiling of
12 arm-runs admits at most **two** truncated runs under the 20% threshold. Ceiling 45 has nine.

**The terminal distribution does shift with the ceiling** — `completed` goes 0/12 at ceiling 30
to 3/12 at 45, and 2 of the 3 observed arm-runs at 60. That is the one directional signal this
sweep produced. It is not enough to select on: the 60 figure is over three arm-runs of one task,
and §5 offers no way to select on a trend.

---

## 3. Functional correctness

Hidden checks, scorer `a1-functional-2`, per (task, arm, ceiling) as §6 requires.
**§2 and §9 forbid any contrast between arms from this calibration** — the corpus is
deliberately unmatched to k1–k4 — so the arm columns are reported and never differenced.

| ceiling | task | baseline | nexus | notes | row |
| --- | --- | --- | --- | --- | --- |
| 30 | k1 | pass | pass | fail | 2/3 |
| 30 | k2 | fail | pass | pass | 2/3 |
| 30 | k3 | fail | fail | fail | 0/3 |
| 30 | k4 | pass | fail | fail | 1/3 |
| | | | | | **5/12** |
| 45 | k1 | pass | pass | pass | 3/3 |
| 45 | k2 | pass | pass | pass | 3/3 |
| 45 | k3 | pass | pass | fail | 2/3 |
| 45 | k4 | fail | fail | pass | 1/3 |
| | | | | | **9/12** |
| 60 | k1 | fail | pass | pass | 2/3 |

Correctness rises with the ceiling, 5/12 → 9/12. **It cannot rescue either ceiling**: §5's
correctness clause is a *proviso on a ceiling that has already passed the truncation gate*, and
neither did.

**Two distinctions the terminal record does not make for you**, both of which the earlier report
blurred:

* `completed` is a statement about how the run **ended**, not about whether the task was solved.
  This sweep produced 16 hidden-check passes, and **11 of them came from runs that ended
  `max_turns`** — a truncated run can still have landed the fix before it was cut off.
* `terminal.scored: true` means the arm-run is **admissible** — not `env_fail`, not `unknown`.
  The functional verdict is a different field, `scored.passed`, written by a different scorer.

---

## 4. Resources

The ledger below is the **accounting scope**, computed by `run_calibration.consumed` — the same
implementation the driver used, not a second one. It reconciles exactly with the summary the
sweep wrote.

| | tokens |
| --- | ---: |
| v1, quarantined and not pooled as evidence — but spent | 6 425 690 |
| ceiling 30, this sweep | 8 869 425 |
| ceiling 45, this sweep | 16 496 640 |
| ceiling 60, this sweep — **accounted subtotal of an incomplete row** | 3 369 089 |
| **`tokens_known`** | **35 160 844** |
| `tokens_allowance` — k4/baseline v1, a written assumption | 1 400 000 |
| **`tokens_budgeted` (the charge)** | **36 560 844** |
| cap | 36 000 000 |
| **`budget_overshoot_tokens`** | **560 844** |
| `overshoot_tokens` (of *consumption*) | **`null`** |
| `consumption_certain` | **false** |

**560 844 is by how much the CHARGE exceeds the cap. It is not a measured overshoot of
consumption**, and the distinction is the one §4 built two separate fields to preserve. Two
things keep consumption uncertain: the v1 allowance, which is a deliberately high assumption and
never was a measurement; and one arm-run of this sweep that is unresolved.

**`3 369 089` is not what the ceiling-60 row cost.** It is the subtotal of the two arms that
returned an envelope. The third arm's consumption is unresolved, so the row's total is unknown
and no figure disproves any earlier estimate of it. §7 below bounds it from below.

Wall clock, which matters for what comes next:

| ceiling | arm-runs over 88% of the 600 s wall clock |
| --- | --- |
| 30 | 0 of 12 — the longest was 46% |
| 45 | **3 of 12** — all three k4 arms, at 89%, 91% and 96% |
| 60 | **1 of 3** — baseline at 100%, killed |

---

## 5. The selection rule, applied

Verbatim from [`results-calib-a2-report.txt`](results-calib-a2-report.txt):

```
  ceiling 30: FAILS_TRUNCATION   truncation 100.0%   correctness 41.7%
           truncation 100.0% over 12 arm-runs exceeds the registered 20%
  ceiling 45: FAILS_TRUNCATION   truncation 75.0%   correctness 75.0%
           truncation 75.0% over 12 arm-runs exceeds the registered 20%
  ceiling 60: INELIGIBLE
           coverage 3/12 arm-runs, 3 scored; missing k2/baseline, k2/nexus, k2/notes,
           k3/baseline, k3/nexus, k3/notes, k4/baseline, k4/nexus, k4/notes

  ELIGIBLE: [30, 45]
  SELECTED: NO CEILING
```

The three ways a ceiling fails to win are different outcomes and are reported as such:
**ineligible** (coverage is incomplete — more measurement could change it), **fails_truncation**
(complete, measured, above the threshold — more measurement of *this configuration* cannot
change it), and **not_reached** (a lower ceiling already qualified). Only the first is a gap.

---

## 6. The original summary, preserved

`calibration-summary.json` is left exactly as the driver wrote it at
`2026-09-15T09:14:18+00:00`. Nothing in this closeout is written back into it, and the
reconciliation in §7 is kept separate from it on purpose. Its load-bearing fields:

```json
{"tokens_known": 35160844, "tokens_allowance": 1400000, "tokens_budgeted": 36560844,
 "arm_runs": 73, "scored_arm_runs": 36, "excluded_arm_runs": 36,
 "unresolved_arm_runs": ["…/c60/run-k1/attempt1/arms/baseline"],
 "stopping_reason": "unresolved_accounting", "budget_overshoot_tokens": 560844,
 "overshoot_tokens": null, "consumption_certain": false}
```

`stopping_reason` is **`unresolved_accounting`, not `budget`**, and stays that way. The charge
does also exceed the cap, but unresolved accounting takes precedence in the driver, and
rewriting it as a clean budget stop would describe a sweep that stopped for a reason it did not
stop for.

---

## 7. The unresolved arm-run, reconciled without resolving it

`c60/run-k1/attempt1/arms/baseline` was killed at the 600 s wall clock. `forced_verdict` is
`timeout`, `result` is `null`, and `usage` is four nulls — the envelope that carries terminal
usage never arrived. Unlike v1's k4, **the trace survived**: 16 610 lines, 81 assistant messages
carrying usage.

**The obvious sum of those 81 messages is 1 650 675 tokens, and it is wrong.** The 81 messages
carry only **35 distinct message IDs**; a repeated ID repeats the same usage block rather than
reporting further consumption. Summing every message counts most requests two to four times and
inflates the figure by 2.28×.

Counting **one usage per distinct message ID** gives:

| | tokens |
| --- | ---: |
| `input_tokens` | 26 025 |
| `cache_read_input_tokens` | 697 728 |
| **reconstructed subtotal** | **723 753** |
| `output_tokens` | **unobserved** |

**The extraction was checked against arm-runs whose answer is already known.** Run over all 72
traces in the scratch, deduplicating by message ID reproduces the terminal envelope *exactly* on
both fields the trace carries, for **34 of the 35 comparable arm-runs**. The 36 forwarder-down
traces compare zero against zero and are counted as **vacuous**, not as agreements — including
them would have reported 70 successes and validated nothing.

Two limits, both properties of the instrument rather than of the arithmetic:

1. **`output_tokens` is 0 in all 3 226 assistant messages across every trace**, while every
   envelope reports a real figure. The forwarder does not report output per message, so for an
   arm-run without an envelope output is *unobserved, not zero*. On this sweep's 26 comparable
   arm-runs output ran 12 006 – 48 020 tokens, 1.0–3.3% of the total.
2. **The method is a lower bound even where it matches.** One completed arm-run,
   `c45/k3/baseline`, is already 39 770 tokens short of its own envelope — one request's usage
   never reached a message. A run killed mid-request can lose at least that much again.

**So:** the row's consumption is at least `3 369 089 + 723 753 = 4 092 842`, plus an unobserved
output component and anything lost at the kill. That is a *reconstruction with a stated
remainder*, labelled as such, and it is deliberately **not** written into any record.

**No allowance has been written and the arm-run stays unresolved.** §4 permits resolving it with
a documented conservative allowance, but that is a spending-authority decision, not a reporting
one, and nothing in this closeout needs it: *"no ceiling selected; consumption remains partly
unresolved"* is a complete and honest statement of what happened.

**1 650 675 is not established as a conservative allowance, and this closeout withdraws the
suggestion that it might serve as one.** An earlier draft of this section argued it would be
defensible because it sits 2.28× above the reconstructed subtotal. **That reasoning does not
hold.** The subtotal it exceeds is *itself incomplete* in two known ways — it contains no output
tokens at all, and `c45/k3/baseline` demonstrates that a whole request's usage can go unreported
— so a multiple of an incomplete figure bounds neither missing quantity. Being larger than what
the trace supports is not the same as being larger than what the arm-run spent. Since no further
execution depends on resolving this arm, it stays unresolved.

---

## 8. Corrections to the earlier report

| earlier claim | corrected |
| --- | --- |
| "36 arm-runs scored … in this sweep" | **27** in this sweep; the summary field is scratch-wide and includes v1's 9 |
| two complete ceilings make the selection valid | two complete ceilings make the rule **applicable**; neither ceiling **qualifies** |
| the ceiling-60 row "cost 3 369 089" | that is the **accounted subtotal** of two arms; the row total is unknown, ≥ 4 092 842 |
| 1 650 675 tokens recovered from 81 messages | 81 messages, **35 responses**; the deduplicated figure is **723 753**, output unobserved |
| 560 844 tokens over budget | over budget **as a charge**; consumption overshoot is `null` and not computable |

**And two to this document's own first draft**, both caught at review on 2026-09-19 and both
corrected in place above:

| first draft | corrected |
| --- | --- |
| 1 650 675 would be defensible as a conservative allowance, at 2.28× the reconstruction | **withdrawn** (§7): a multiple of an *incomplete* subtotal bounds neither the missing output nor an unreported request |
| "k4's arm-runs are the ones that ran to the wall clock" | k4's three at ceiling 45 reached 89–96% and terminated on `max_turns`; the only arm-run **killed** at the wall clock is `k1/baseline` at ceiling 60 (§9) |

The estimate history is worth keeping as a lesson rather than a correction: the endgame
forecasts for the ceiling-60 row were wrong every time because each assumed cost scales with the
turn ceiling. That holds only while runs truncate — and it breaks exactly where the calibration
becomes interesting, because a run that `completed` stops spending.

---

## 9. What this closeout does not establish, and what comes next

It establishes nothing about whether memory helps, nothing about bounded consultation, no
per-task result, and nothing about a ceiling outside {30, 45, 60}. The grid is three points and
§5 selects within it or selects nothing. It selected nothing.

§5 names the response: **revise the workload or the agent configuration.** The registered
truncation rule stays exactly as frozen; everything below is about what to *investigate*, not
about loosening it.

### The finding that should drive the next decision

**11 of this sweep's 16 hidden-check passes ended at `max_turns`.** Functional success and
normal termination are substantially different quantities here, and that gap — not the ceiling
arithmetic — is what to understand before another experiment is designed. A run that solved the
task and was then cut off is not short of budget in the way a run that never solved it is, and a
pooled truncation rate cannot tell the two apart. **That difference is the thing to investigate;
the rule that reports it stays as registered.**

Three further observations bear on the decision, and none of them is "run ceiling 60 to
completion":

* **Truncation is not close to the threshold.** Going 100% → 75% across a 50% ceiling increase
  does not extrapolate to ≤ 20%. Nothing here suggests ceiling 60 would land under it, and §5
  forbids raising the grid until something passes in any case.
* **Wall time is an observed constraint, but its future effect is not established.** At ceiling
  45 k4's three arm-runs used 89–96% of the 600 s limit and still terminated on `max_turns`; the
  one arm-run actually killed at the wall clock is **`k1/baseline` at ceiling 60**. *(An earlier
  version of this section said k4 produced every wall-clock-bound run. That was wrong: k4's came
  close and terminated on turns.)* One kill is not a trend, and no claim is made here about what
  a larger ceiling would do. What can be said is that `timeout` counts as truncated, so
  **increasing turns alone may leave wall time binding** — a variable to hold fixed or measure
  deliberately in any further ceiling work, not a demonstrated effect.
* **k3 and k4 are where the budget goes.** k3 solved 0/3 at ceiling 30; k4 solved 1/3 at both 30
  and 45. §2 registered that this set spans the hunk axis (1–6) and is nearly flat on the
  failing-function axis — k4 is the 6-hunk task. That is a workload property to decide about,
  not a budget to buy past.

### The recommended sequence

Agreed at review on 2026-09-19 and recorded here so the next session starts from it rather than
from a fresh argument:

1. **One short, model-free diagnostic** over the traces already saved. For the 11
   passing-but-truncated arm-runs: the final recorded source edit, the test attempts after it,
   repeated investigations, and any completion message. For the failures: distinguish *no source
   patch*, *incorrect patch*, *operational obstruction* and *missing evidence*. These are
   descriptive categories. **Do not infer when a patch first became correct without checking that
   intermediate state** — a record carries a final patch, not a history of one.
2. **Choose one workflow change from that evidence.** If passing runs continue exploring after
   adequate verification, test an explicit solve–verify–stop workflow across all arms. If
   implementation itself consumes the budget, revise task scope or the agent configuration.
   **Keep the task set fixed** while evaluating the chosen change; retaining only the tasks that
   turn favourable would answer a different question.
3. **Register a small development experiment before spending**: one candidate workflow, its
   unchanged reference configuration, fixed resource limits, a maximum experiment budget, and a
   stopping decision. **Correctness and termination reported separately** — this sweep is the
   demonstration of why. Do not combine it with a retrieval-policy change: the immediate
   uncertainty is about the agent's overall execution, and two variables at once confound,
   exactly as §3 already said of ceilings and consultation.
4. **Return to memory-specific development only after that decision.** Bounded consultation,
   batching, abstention and evidence selection all remain candidates. None has been shown to
   address the dominant failure mode this calibration found.

**Nothing here authorises another paid run.** Step 1 spends nothing; steps 2–4 are decisions, and
the experiment in step 3 is registered before it is funded.

### Where this leaves the project

The durable memory foundation is implemented. A1 found no coding benefit
([`CLOSEOUT-a1.md`](CLOSEOUT-a1.md)). This calibration did not establish an acceptable operating
budget. **The next milestone is therefore to establish a workable agent workflow under an
affordable budget** — not to expand the retrieval architecture, which would add another variable
before that question is settled.

---

## Reproducing this document

```bash
python3 benchmarks/agent/report_calibration.py  /Users/soko/Cerebros/nexus-a1-fixtures/calib-run
python3 benchmarks/agent/reconcile_usage.py     /Users/soko/Cerebros/nexus-a1-fixtures/calib-run
python3 benchmarks/agent/test_report_calibration.py
```

Both programs are read-only over the scratch. The rendered output of the first two is committed
as [`results-calib-a2-report.txt`](results-calib-a2-report.txt) and
[`results-calib-a2-reconciliation.txt`](results-calib-a2-reconciliation.txt); the scratch itself
is host-local and outside the repository, as `LAUNCH-A2.md` records.
