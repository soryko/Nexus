# A2 budget calibration — registration

**Registered 2026-09-14, before any calibration arm-run.** This registers a *development*
measurement, not held-out evidence. Its only output is a turn ceiling for A2 and the evidence
that the ceiling was chosen by a rule rather than by preference.

It is not an A2 protocol. A2's held-out registration is a separate document and does not exist
yet.

## 1. What A1 left, and what this is for

A1 is closed with a negative result ([`CLOSEOUT-a1.md`](CLOSEOUT-a1.md)). 29 of its 36 arm-runs
ended at the 30-turn ceiling, and on the two tasks nobody solved **no arm modified a tracked
source file even once** across 18 arm-runs. A1 could therefore not distinguish a workload where
memory cannot help from a budget under which nothing can be shown.

This calibration asks one question: **is there an affordable ceiling at which these arms stop
being cut off mid-task?** It does not ask whether memory helps. A ceiling that merely buys more
exploration is not an improvement, so correctness, truncation, tokens and wall time are read
together and a ceiling is not selected on truncation alone.

## 2. The calibration workload

Four tasks, [`tasks-calib-a2.json`](tasks-calib-a2.json), disjoint from every A1 task
(`d1`–`d4`, `c1`–`c2`, `h1`–`h4`) and from the boundedness rejection `9caedb9206`.

**Drawn strictly before A1's capture boundary** (`ebcd548d50`, 2025-11-19), so every commit
after that boundary stays available for a future A2 capture/held-out split. A calibration set
drawn from recent history would buy a ceiling and spend the experiment.

Admitted under A1's own bar — at most 6 test functions' outcomes and at most 7 source hunks —
and chosen to **span** it rather than sit at one end:

| task | fix | pre-fix | subject | checks | hunks | failing fns | failing instances |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k1 | `c326df95e9` | `a8c0542760` | closing of callbacks on CLI exit | `test_context.py` | 1 | **2** | 3 |
| k2 | `884af5c20f` | `011b9f9d19` | flag value when `is_flag=True` and a type is given | `test_options.py` | 2 | 1 | 5 |
| k3 | `27aaed3fe5` | `2ed395b0b5` | defer `UNSET` normalization in default handling | `test_defaults.py` | 3 | 1 | 1 |
| k4 | `70c673d37e` | `273fb90106` | eagerness of the `help_option_names` help option | `test_commands.py` | 6 | 1 | 1 |

**Failing-function counts are measured by `build_fixture.py`, not read off the diff.** The
static proxy used during selection was wrong on every task: it counted test functions the diff
*touches*, which is not the number whose *outcome* the fix changes.

**The set therefore spans the hunk axis (1–6) and is nearly flat on the function axis (1–2).**
That is a limitation of this calibration, stated rather than dressed up. It is tolerable
because `tasks-click-a1.md` already established that failure count alone is a weak complexity
measure — `d3` was A1's calibrating case at 1 failing function across 7 hunks — and because a
turn ceiling is consumed by edits and test cycles, which track hunks. It would not be tolerable
for a claim about task difficulty, and none is made.

**Every task must clear all four admission checks before it runs**, and a task failing any one
is dropped and recorded, not repaired into the set:

1. the untouched tree **fails** the hidden checks (`no_model`);
2. the tree at the fix commit **passes** them (`fix_oracle`);
3. the fix is **unreachable** from the fixture (`oracle_reachability`);
4. the hidden checks are absent from the fixture the agent sees;
5. **the checks terminate.** `build_fixture.py` bounds every scorer invocation at 300 s. A
   scorer that never returns is a wedged harness, not a failing check.

**Two candidates were rejected by these checks, and are recorded rather than dropped:**

| candidate | rejected because |
| --- | --- |
| `262bdf0228` — raise on end of input in `CliRunner` | its checks include `tests/test_termui.py` and the no-model control **did not terminate**. Pager and TTY behaviour is one of the six classes A1 excluded; the survey that picked it screened commit *subjects* for those classes and not the test files the fix touches. The screen is now on test files. |
| `8c842a43e8` — pass `color` explicitly in error echoing | **vacuous**: its checks pass on the unpatched tree (24 passed, 0 failed), so every arm would score it correct without doing anything. |

**The hang exposed a defect in the admission rule itself.** It read
`fix_oracle["passed"] is not False`, written so an authored task with no upstream fix (`d4`)
could return `None` for "not applicable". Adding a timeout makes a *hang* also return `None` —
so under the old rule every non-terminating candidate would have been **admitted**, silently.
Each clause is now answered explicitly, "not applicable" is identified by the task having no
fix commit rather than by the `None`, and an unreached verdict rejects. See `admissible()`.

**Regression-checked against A1**: under the new rule `h1`–`h4` all still admit, with figures
identical to the registration (h1 18 failed/637 passed, h2 2/90, h3 10/776, h4 3/10).

## 3. The budget grid, fixed before any run

**Ceilings: 30, 45, 60 turns.** 30 is A1's registered ceiling and is carried unchanged so the
grid contains the point A1 actually measured.

**The ceiling is the `--max-turns` flag value.** It is not `num_turns`, which `runner-a1.md`
measured reading 3 under a cap of 2, and it is not the tool-call count. The three are reported
in separate columns and never substituted.

**Ceilings are equal across arms at every grid point.** Retrieval consumes real turns; giving
the memory arm free retrieval turns would measure a different system from the one that ships.

Arms are A1's: `baseline`, `nexus`, `notes`, with identical prompts, tool inventories and
retrieval policy. **This calibration changes no prompt and no policy** — a bounded consultation
policy is the *next* experiment and cannot be tested in the same run that moves the ceiling,
because the two would confound.

**Design: 4 tasks × 3 arms × 3 ceilings × 1 attempt = 36 arm-runs.** One attempt per cell is
deliberate: A1 measured run-to-run variability at nil in 11 of 12 cells, and a ceiling choice
does not need a variance estimate. It follows that **no cell here supports a per-task claim**,
and none will be made.

## 4. Spend cap

**Maximum calibration spend: US$60.** A1 cost $25.78 for 36 arm-runs ($0.716 mean), and higher
ceilings cost more per run because a truncated run spends its whole budget.

**Stopping rule.** Cost is accumulated from each run's envelope after every arm-run. At $60 the
calibration **stops where it is**. Partial results are kept, reported as partial, and the
ceilings that did not run are named. A partial grid does not license a ceiling choice by
extrapolation: if the rule in §5 cannot be applied to the grid that actually ran, the outcome
is "no ceiling selected", not a guess.

Runs execute in ceiling order 30 → 45 → 60 so that a cap hit costs the most expensive cell.

## 5. The selection rule, written before the numbers exist

> **Select the lowest ceiling in the grid whose truncation rate across all 12 calibration
> arm-runs at that ceiling is at most 20%, provided its correctness at that ceiling is no worse
> than at any lower ceiling in the grid.**

**20% is an engineering choice, not a statistical guarantee.** It is the point past which
truncation stops being an occasional event and starts being the modal outcome — A1 ran at 81%
and could not be read. Nothing about the number is derived.

The correctness clause matters: a larger ceiling that raises truncation-free runs while
*lowering* the number of tasks solved has bought exploration and not progress, and is refused.

The rule is applied **across arms**, on the pooled 12 arm-runs at each ceiling — not per arm.
Choosing a ceiling that suits one arm would build the comparison's answer into its budget.

**If no ceiling qualifies**, no ceiling is selected and A2 does not proceed to a held-out
registration. The response is to revise the workload or the agent configuration — a harder
question than this measurement, and one that must not be settled by raising the grid until
something passes.

## 6. What is reported, per (task, arm, ceiling)

Functional verdict; terminal reason; truncation; `num_turns`; tool calls; input, output and
cache tokens; cost; wall clock; first source mutation or none; retrieval calls before and after
it; tool errors and permission denials. Cost is reported **over all runs including failures**;
averaging an arm's cost over only its successes flatters whichever arm fails more.

Scorers: `a1-functional-2` for hidden checks, `a1-scorer-4` for compliance, both unchanged from
A1 so the ceiling is the only thing that moves.

**The product revision is recorded in the run record itself**, closing the gap A1 left, where it
had to be inferred from git history after the fact.

## 7. What this cannot establish

Whether memory helps. Whether bounded consultation helps. Any per-task result. Anything about
`h1`–`h4`, which are exposed and not in this set. Whether a ceiling outside {30, 45, 60} would
be better — the grid is three points, and the rule selects within it or selects nothing.

A ceiling chosen here is an input to A2's registration. It is not a finding.
