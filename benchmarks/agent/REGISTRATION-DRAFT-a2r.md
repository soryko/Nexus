# A2-R — assessing the interpreter repair — **DRAFT, NOT REGISTERED**

**Status: a draft for review. Nothing here authorises a paid run.** It becomes a registration
only when the founder approves it, the open decisions in §7 are closed, and it is frozen with a
launch record of its own. Until then no cell is executed.

Predecessors: the calibration is **closed with no ceiling selected**
([`CLOSEOUT-calib-a2.md`](CLOSEOUT-calib-a2.md)); the workflow diagnostic that motivated this is
[`DIAGNOSTIC-calib-a2-workflow.md`](DIAGNOSTIC-calib-a2-workflow.md); the repair being assessed
is [`REPAIR-interpreter-consistency.md`](REPAIR-interpreter-consistency.md).

---

## 1. The one question

> **Under the repaired toolchain (`calib-v3`), what do these four tasks look like on
> correctness, requirement compliance, termination, environment failures and resource use?**

It is a **development** measurement and produces no held-out evidence. It assesses **one**
change — interpreter consistency. It is not a ceiling selection, not a memory comparison, and
not A2's held-out experiment.

**The old runs are historical context and nothing more.** v2 ran under a different
configuration, a different prompt and a defect that has since been removed, and v2 is a single
attempt per cell. **No causal comparison between v2 and v3 is licensed, and none will be
reported.** Where a v2 figure appears it is labelled *historical* and carries no inference.

## 2. What is held fixed

| | |
| --- | --- |
| task set | `k1`–`k4`, `tasks-calib-a2.json`, **unchanged** — including the two nobody solved |
| task bodies, tails, `consult`, `capture_instruction` | **byte-identical** to v2, verified by assembly diff |
| the requirement | *"Fix the behaviour in `src/`, and extend the existing test suite to cover it."* — unchanged |
| arms | `baseline`, `nexus`, `notes`, identical prompts, tool inventories and retrieval policy |
| retrieval policy | **unchanged.** No bounded-consultation rule. |
| completion instructions | **unchanged.** No stop rule, no discouraging reference hunting. |
| corpus | A1's frozen held-out corpus, digest `9ae2a9f268dd894d` |
| scorers | `a1-functional-2`, `a1-scorer-4` |
| model settings | unchanged |
| wall clock | 600 s, unchanged — see §7, it is an open question and not a silent change |

**The task set is not pruned.** Retaining only the tasks that turn favourable would answer a
different question.

## 3. What changed, and only this

Configuration **`calib-v3`**: the `environment` prompt block names `$A2_PYTHON`; the harness
sets `A2_PYTHON` in one place; the gate runs every documented command under both shell startups
and requires agreement; each run record carries `runtime_identity`. All four prompt digests
changed, so no v3 row can be pooled with a v2 row.

## 4. Design

**4 tasks × 3 arms × 1 attempt at a single ceiling = 12 arm-runs.**

One ceiling, because this is not a ceiling experiment. **Ceiling 45** is proposed: it is the
higher of the two ceilings the calibration completed, so the instrument is the one A2 would
actually use, and it is the point where truncation had begun to move. *(Open — §7.)*

One attempt per cell, as registered in the calibration: A1 measured run-to-run variability at
nil in 11 of 12 cells. **It follows that no cell here supports a per-task claim, and none will
be made.**

## 5. What is reported, per (task, arm)

Five families, **reported separately and never collapsed into one score**:

1. **Functional correctness** — hidden checks, `a1-functional-2`. Pass/fail per cell and the
   pooled count.
2. **Requirement compliance** — did the run do what the task asked? Reported as three
   independent facts, because the scoring has been shown not to see all of them: *(a)* a source
   diff under `src/`; *(b)* **an addition to the existing test suite** — the tail requires it,
   and 14 such edits in v2 earned nothing; *(c)* `a1-scorer-4` compliance as registered.
3. **Termination** — `completed` / `max_turns` / `timeout` / `env_fail`, per cell, with
   `num_turns`, tool-call count and wall clock beside it. **Reported separately from
   correctness**: this sweep is the demonstration of why — 11 of v2's 16 passes ended at
   `max_turns`.
4. **Environment failures and friction** — `env_fail` counts, permission denials, and the
   tool-result signals the diagnostic counted (`No module named`, tracebacks, nonzero exits,
   denials). **The primary check on the repair is that `No module named pytest` is absent**,
   and `runtime_identity` is recorded for every arm-run.
5. **Total resource use** — tokens (input + cache-read + cache-creation + output) over **all**
   arm-runs including failures, plus wall clock. Dollars are not used; `runner-a1.md` §3 found
   the CLI's cost field has no provenance.

`report_calibration.py` and `diagnose_workflow.py` already compute 1, 3, 4 and 5 from saved
records. **2(b) needs a small addition** and must be written before the run, not after.

## 6. Budget, limits and the stopping decision

| | |
| --- | --- |
| **maximum experiment budget** | **20 000 000 tokens**, enforced as in the calibration: checked **before** a row starts against the largest row yet seen; unresolved consumption blocks rather than counting as zero |
| ceiling | one grid point (§4) |
| wall clock | 600 s per arm-run |
| attempts | 1 per cell; **no retries** — a retried cell is a second measurement of a changed thing |

**Where 20 000 000 comes from, and why it is soft.** v2's ceiling-45 row cost 16 496 640 tokens
for the same 12 cells. That is an **estimate from a different configuration**, and the estimate
history on this project is bad: the endgame forecasts for the v2 ceiling-60 row were wrong three
times, because each assumed cost scales with the ceiling, which holds only while runs truncate.
If the repair works, runs may stop wasting turns and cost **less**; they may also get further and
cost **more**. The figure is headroom over a historical measurement, not a prediction.

**Stop the sweep when any of these is true**, and report what was measured up to that point:

* the budget check refuses the next row;
* any arm-run records an unresolved consumption;
* the environment gate refuses an arm — **that is the repair failing, and it is a result**;
* a row produces no scored arm-run.

**The decision this sweep feeds.** It answers whether the repaired toolchain runs clean. It does
**not** decide the next intervention by itself:

* **`No module named pytest` absent and correctness materially unchanged** → friction was real
  but not load-bearing; the next candidate is workflow shape or task scope, chosen on evidence.
* **absent and correctness up** → the repair is worth carrying into any A2 registration. Still
  not a causal claim against v2, for §1's reasons; it would motivate a controlled comparison.
* **still present, or a new friction replaces it** → the repair is incomplete; return to §1 of
  the repair document before spending again.
* **runs still never reach implementation** → the dominant failure mode is untouched by
  toolchain work, and the next question is the workload or the agent configuration, as
  `freeze-calib-a2.md` §5 already said.

## 7. Open — the founder decides before this is registered

1. **The ceiling.** 45 is proposed (§4). 30 is cheaper and is the point A1 measured; 60 has no
   complete coverage at all.
2. **The wall clock.** Three v2 arm-runs at ceiling 45 used 89–96% of 600 s and one at ceiling
   60 hit it. **Increasing turns alone may leave wall time binding.** Holding it at 600 s keeps
   the instrument fixed; raising it changes a second variable. Recommendation: **hold at 600 s**
   and report the distribution, so the question is measured rather than assumed.
3. **The budget.** 20 000 000 (§6) is headroom over a historical figure, not a forecast.
4. **Whether to run all three arms.** All three keeps the instrument identical to A2's and
   exerts the turn pressure the arms actually exert. A baseline-only sweep is a third the cost
   and a different instrument. **No arm contrast will be reported either way.**

## 8. What this cannot establish

Whether memory helps. Whether bounded consultation helps. Any per-task result. Any causal
comparison with v2 or with A1. A turn ceiling — `freeze-calib-a2.md` §5 selected none, and
nothing here reopens that. A held-out claim of any kind.

## 9. Before it is registered

- [ ] founder closes §7
- [ ] `2(b)` — the test-suite-addition check — implemented and tested, **before** the run
- [ ] new host-local `calib-config-*.json` at `config_version: calib-v3`, created **alongside**
      the v2 files, never editing them
- [ ] a launch record of its own, outside the measured tree, freezing the identity
- [ ] `preflight-a2.py` green, forwarder up, gate passing on a prepared arm
