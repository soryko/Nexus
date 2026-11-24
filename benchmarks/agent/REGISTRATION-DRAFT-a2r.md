# A2-R — assessing the interpreter repair — **DRAFT, NOT REGISTERED**

**Status: the design is frozen and awaiting approval. Nothing here authorises a paid run.** It
becomes a registration when the founder approves it and it is frozen with the launch record in
[`LAUNCH-A2R.md`](../../LAUNCH-A2R.md). Until then no cell is executed.

The design decisions in §7 are **closed** as of 2026-09-19 and are not reopened by this
document. What remains open is approval, and the three host-local preparation steps in §9.

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
| task set | `k1`–`k4`, `tasks-calib-a2.json`, **unchanged** |
| task bodies, tails, `consult`, `capture_instruction` | **byte-identical** to v2, verified by assembly diff |
| the requirement | *"Fix the behaviour in `src/`, and extend the existing test suite to cover it."* — unchanged |
| arms | `baseline`, `nexus`, `notes`, identical prompts, tool inventories and retrieval policy |
| retrieval policy | **unchanged.** No bounded-consultation rule. |
| completion instructions | **unchanged.** No stop rule, no discouraging reference hunting. |
| corpus | A1's frozen held-out corpus, digest `9ae2a9f268dd894d` |
| scorers | `a1-functional-2`, `a1-scorer-4` |
| model settings | unchanged |
| wall clock | **600 s, held** — §7 decision 2 |

**The task set is not pruned.** Retaining only the tasks that turn favourable would answer a
different question.

**A correction to an earlier draft of this document.** It described the task set as *"including
the two nobody solved"*. That is A1's task set, not this one, and it is **wrong for `k1`–`k4`**:
[`results-calib-a2-workflow.json`](results-calib-a2-workflow.json) records at least one passing
arm-run on **every one of the four tasks** — k1 7 of 9, k2 5 of 6, k3 2 of 6, k4 2 of 6. The
task set is retained because pruning it would change the question, not because any task is
unsolved.

## 3. What changed, and only this

Configuration **`calib-v3`**: the `environment` prompt block names `$A2_PYTHON`; the harness
sets `A2_PYTHON` in one place; the gate runs every documented command under both shell startups
and requires agreement; each run record carries `runtime_identity`. All four prompt digests
changed, so no v3 row can be pooled with a v2 row.

## 4. Design

**4 tasks × 3 arms × 1 attempt at ceiling 45 = 12 arm-runs.**

**Ceiling 45.** It is a **previously completed operating point** for this development
measurement — the calibration ran all 12 of its cells there — and that is the whole of the
justification. It is **not a selected ceiling and not a validated one**: `freeze-calib-a2.md` §5
selected none, and 45 failed its truncation gate at 75%.

**One attempt per cell**, justified by this diagnostic's limited scope and budget and by
nothing else. An earlier draft justified it by A1 having measured run-to-run variability at nil
in 11 of 12 cells; **that is A1's observed uniformity under A1's conditions and establishes
nothing about variability here.** It follows either way that no cell supports a per-task claim,
and none will be made.

## 5. What is reported, per (task, arm)

Five families, **reported separately and never collapsed into one score**.

### 1. Functional correctness
Hidden checks, `a1-functional-2`. Pass/fail per cell and the pooled count.

### 2. Requirement compliance — four independent observations
The tail requires *fixing the behaviour in `src/`* **and** *extending the existing test suite to
cover it*. **A changed file under `tests/` does not establish the second.** A comment, a
whitespace change, a rename and an unrelated edit all touch `tests/` and satisfy nothing. So
four observations are preserved independently, and **`unknown` is a real value** used wherever
the evidence is missing:

| | |
| --- | --- |
| *(a)* | a final source diff under `src/` |
| *(b)* | a **substantive** addition to, or extension of, the existing suite — a new `def test_*`, or added lines that are not blank and not comments inside a test the hunk header names |
| *(c)* | **execution of that test** — an invocation that **names its node id**, issued after the test file was last written, whose result shows that test ran |
| *(d)* | **its own result**, from that invocation — never a suite aggregate |

**Four things that are not execution**, each of which the first version of this reporter
credited:

* **mentioning** pytest — `echo pytest` contains the word and runs nothing;
* **attempting** pytest — an invocation answering `No module named pytest` executed no test,
  and counting it credits the repair with the very event it exists to make possible;
* executing a **file** — `pytest tests/test_other.py` runs tests, none of them this one;
* an **aggregate** result — a suite's `1 passed` is not the added test's result.

A suite run, a file-level run, a run issued before the test file was last written, a
deselection and a collection failure all leave execution **`unknown`**, with the candidate
commands listed as evidence. Twelve patches is few enough that reading that evidence beats
extending the parser.

**Nothing is inferred from edit counts.** An edit count is a count of edits: v2 contained
arm-runs that edited `tests/` repeatedly and never ran one.

**Relevance stays `unreviewed`.** Whether an added test covers *this* bug is not decidable from
a diff. Twelve patches is a readable number, so the reporter emits the node ids and the
executing commands as evidence and a reviewer fills the field in.

`a1-scorer-4` is the **registered** compliance scorer, reported beside these under its own name
and never merged with them. Its ratio is over the **settled** checks only — `tally()` excludes
`unknown` from both numerator and denominator — so it is always displayed with the unknown
count beside it: `4/4 +2?` is four passes among four settled checks with two unsettled, not
four of six.

*(An earlier revision of this document claimed `tally()` counted unknowns in the numerator.
**That was wrong** and is withdrawn: the implementation settles on `PASS`/`FAIL` only. The
failure in `test_compliance_counterexamples.py` that prompted the claim is a missing host
fixture, not a scorer defect — see §9.)*

### 3. Termination
`completed` / `max_turns` / `timeout` / `env_fail`, per cell, with `num_turns`, tool-call count
and wall clock beside it. **Reported separately from correctness**: 11 of v2's 16 passes ended
at `max_turns`. **Timeouts are reported separately from turn exhaustion**, not pooled into one
truncation figure.

### 4. Environment failures — and the repair, checked non-vacuously
**The absence of `No module named pytest` does not establish the repair.** It is vacuous in a
run that never attempted pytest, and v2 contained exactly such a run: `30/k2/notes` passed the
hidden checks having never invoked pytest at all. Five facts are therefore reported
**separately**:

1. the **gate passed**, and the `runtime_identity` it recorded;
2. the agent **invoked the documented pinned command**;
3. that invocation **ran, or could not run**;
4. the agent **used another interpreter**;
5. **no relevant invocation was observed** — from which no claim about the repair is available
   in either direction, and which is not counted as clean.

A **failing test is not a broken interpreter**: the documented command ran and the tests were
red, which is the ordinary state of an unfinished run. A **missing module that is not pytest**
is the program's own import problem and is reported separately.

**A gate refusal stops spending whatever refused it**, and its cause is classified before
anything calls it an interpreter-repair failure: a refusal on the forwarder or on egress is not
evidence about the interpreter.

Also reported: `env_fail` counts, permission denials, and the tool-result signals the diagnostic
counted.

### 5. Total resource use
Tokens (input + cache-read + cache-creation + output) over **all** arm-runs including failures,
plus wall clock. Dollars are not used; `runner-a1.md` §3 found the CLI's cost field has no
provenance.

`report_calibration.py` and `diagnose_workflow.py` compute 1, 3 and 5 from saved records.
Families **2 and 4** are new and are implemented in [`assess_a2r.py`](assess_a2r.py), with
counterexamples in [`test_a2r.py`](test_a2r.py) — **written before the run, not after**.

### Reading the runner's own format

`run_arms_isolated.py` writes `record["scored"]["passed"]`, `record["result"]["num_turns"]`,
and the patch as a **sidecar file** at `arms/<arm>/patch.diff`. It does **not** write a
requirement-compliance field: `a1-scorer-4` is a separate offline pass. An earlier revision of
this reporter read `record["functional"]`, `record["num_turns"]` and `record["patch"]`, none of
which any runner produces — so against real rows it would have reported **every genuine
functional pass as no pass at all** and left every patch observation unknown.

Every test agreed with it, because every fixture was invented by the same hand as the reader.
Two things now prevent that:

* **one adapter at the boundary** (`normalise`), so nothing above it knows two shapes, with an
  integration test built from the runner's real schema *and* directory layout, including a
  genuine functional **failure** as a negative control;
* **[`replay_v2.py`](replay_v2.py)** — the reporter run over the **27 accepted v2 arm-runs**,
  which must recover **16 functional passes, 11 of them terminated at `max_turns`**, the
  figures `report_calibration.py` and `diagnose_workflow.py` computed independently from the
  same records. It costs nothing and is the one check that cannot agree by construction.

A **missing** patch and an **empty** patch are different: an empty `patch.diff` measures that
the arm-run changed nothing (`source_diff: no`); an absent one measures nothing (`unknown`). A
record claiming `patch_bytes` with no sidecar is reported as inconsistent rather than resolved
in favour of either. A missing or **timed-out** functional scorer is `unknown`, never `fail`.

Compliance reads `not_scored` until `a1-scorer-4` has actually been run over the sweep. That is
not `unknown`: a scorer that never ran has not failed to decide anything, and printing a ratio
or an unknown count for it would imply a measurement that does not exist.

## 6. Accounting, and the stopping decision

**A2-R is accounted separately from the closed calibration.** Its own launch identity
(`LAUNCH-A2R.md`), its own output directory, its own budget scope, its own summary
(`a2r-summary.json`). This is not tidiness: `consumed()` walks a whole scratch tree, so two
sweeps sharing one directory share one budget silently. [`run_a2r.py`](run_a2r.py) **refuses** a
directory that holds another sweep's summary, and refuses to nest inside one.

| | |
| --- | --- |
| **soft launch threshold** | **20 000 000 tokens** — a **separately proposed allocation**, not remaining allowance from the closed calibration and **not a guaranteed maximum** |
| ceiling | 45, one grid point |
| wall clock | 600 s per arm-run |
| attempts | 1 per cell; **no retries** — a retried cell is a second measurement of a changed thing |

**Why it is soft, stated rather than implied.** The check is the calibration's: a row is not
*started* without room for one the size of the largest yet seen. So is its weakness. **An
arm-run cannot be interrupted part-way, so the sweep can finish above 20 000 000 by up to one
row**, and **a largest-observed-row reservation bounds nothing about the next row's
consumption**. The summary records the figure as `cap_is_soft: true` so nothing downstream reads
it as a maximum that was enforced.

**Where 20 000 000 comes from.** v2's ceiling-45 row cost 16 496 640 tokens for the same 12
cells — *historical*, from a different configuration. The estimate history on this project is
bad: the endgame forecasts for the v2 ceiling-60 row were wrong three times, each assuming cost
scales with the ceiling, which holds only while runs truncate. The figure is headroom over a
historical measurement, not a prediction.

**The closed calibration's unresolved consumption stays outstanding and visible.** One arm-run
(`c60/run-k1/attempt1/arms/baseline`) has no usable usage record; the closed sweep's total is a
lower bound and its charge exceeded its cap by 560 844 tokens. **A2-R does not resolve it, does
not cover it with a new allowance, and does not treat it as zero.** A2-R's report prints it
under its own heading so that A2-R's clean ledger is never read as the project's.

**Stop the sweep when any of these is true**, and report what was measured up to that point:

* the pre-launch check refuses the next row at the threshold;
* any arm-run records an unresolved consumption — the next row is **not** started;
* the environment gate refuses an arm — **that is a result**, and its cause is classified (§5.4)
  before it is called an interpreter failure;
* a row produces no scored arm-run.

### What this sweep's outcomes mean

The design does not support a causal reading, so none is written in advance:

> **Successful documented commands demonstrate that the repaired runtime is usable in the
> observed runs. Functional outcomes describe performance under this configuration. Differences
> from historical v2 outcomes do not establish the repair's effect on correctness.**

Two consequences of that sentence, stated because an earlier draft of this document broke both:

* **Carrying the repair forward does not depend on correctness going up.** A working documented
  command is worth keeping because it works, not because a number moved.
* **If runs still fail to reach implementation, the finding is that the failure mode
  PERSISTS.** It is *not* that toolchain work left it unaffected — this design cannot separate
  those, and 10 of 11 v2 failures never wrote a source file at all.

## 7. Decisions — closed 2026-09-19

| decision | closed as | why |
| --- | --- | --- |
| **turn ceiling** | **45** | a previously completed operating point for this development measurement; **not** a selected or validated ceiling |
| **wall limit** | **600 s** | preserve the existing limit; timeouts reported separately |
| **arms** | **all three** | check the repaired workflow under each arm's existing demands; **no memory-effect comparison** |
| **attempts** | **one per task/arm, 12 maximum** | a bounded diagnostic, with no claim of stable success rates |
| **budget** | **20 000 000 additional tokens, explicitly soft** | a separate proposed allocation, not remaining allowance from the closed calibration, and not a guaranteed maximum |

**These are design decisions. They do not launch or authorise paid execution.**

The design is frozen here. Retrieval policy, completion instructions, task scope and model
settings stay as §2 records them.

## 8. What this cannot establish

Whether memory helps. Whether bounded consultation helps. Any per-task result. Any causal
comparison with v2 or with A1. The repair's effect on correctness. A turn ceiling —
`freeze-calib-a2.md` §5 selected none, and nothing here reopens that. A held-out claim of any
kind.

## 9. Before it is registered

- [x] the design decisions in §7 — **closed**
- [x] `5.2` — the four requirement observations — implemented and tested, **before** the run
- [x] `5.4` — the non-vacuous repair check — implemented and tested, **before** the run
- [x] separate accounting verified under a stub runner: normal completion, threshold refusal,
      unresolved-consumption refusal, and refusal to share the calibration's directory
- [x] the reporter reads the **runner's** record format, with an integration test on its real
      schema and layout, and `replay_v2.py` recovering 16 passes / 11 passing-truncated from
      the 27 accepted v2 arm-runs
- [ ] **founder approves** [`LAUNCH-A2R.md`](../../LAUNCH-A2R.md)
- [ ] new host-local `a2r-config-45.json` at `config_version: calib-v3` — a **new file** under
      a name of its own, never an edit to a `calib-config-*.json`, whose digests
      `preflight-a2.py` checks against the closed sweep's launch record; its `config_digest`
      stamped into [`LAUNCH-A2R.md`](../../LAUNCH-A2R.md)
- [ ] `preflight-a2r.py` green, forwarder up, gate passing on a prepared arm

**A note on `test_compliance_counterexamples.py`.** It fails on this host, and the cause is
**not** a defect in `score_compliance`. Its assertion hardcodes a denominator of five — *"six
checks, five settled"* — and the run produces **two** unknowns rather than one. The second is
`E1_regression_discriminates`, whose recorded `instrument_error` reads
`FileNotFoundError … /scratchpad/a4run/base/d1`: the pristine fixture path in
`run-dev-a1-attempt4/fixtures.json` points into a previous session's scratchpad, which no
longer exists. That is why the suite is not in CI. The instrument-error machinery reported it
exactly as designed; the assertion is left as it is, because loosening it would hide a missing
fixture. A host-independent test of `tally()` with several unknowns is in
[`test_a2r.py`](test_a2r.py).
