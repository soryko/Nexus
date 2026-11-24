# A3 — bounded consultation, one development comparison

**DRAFT. Nothing here authorises a paid run.** No budget is approved, no launch record
exists, and `preflight-a2r.py` — which guards the spent A2-R authorisation — refuses on this
branch by design. A launch needs a new record, a new preflight, and the founder's approval of
the identifiers and the budget below.

**What it asks.** Does a bounded consultation policy cost materially less than the current one
*without* losing access to information the current one reaches?

**What it cannot answer.** Whether memory helps at all — both arms have memory. Whether Nexus
beats static notes — the notes arm is not in this design. Anything about a task set other than
k1–k4, which are exposed development tasks and stay development tasks.

---

## 1. The two policies

Both arms are the **nexus** arm. They differ in one paragraph of the prompt and in nothing
else.

**A — current.** The registered `consult` block, byte-identical to `calib-v3`:

> Before your first source edit, consult any available prior-work memory: Nexus memory tools
> or `NOTES-FROM-EARLIER-WORK.md`. If neither is available, proceed using the repository.
> Treat prior notes as potentially outdated and verify relevant claims against current code.

**B — bounded.** The same, with the bound appended:

> Consult prior-work memory at most once: a single search, and then at most three full-body
> fetches from its results. If those results give you no relevant evidence, stop consulting
> and continue with the repository.

**One search and three fetches is a proposed engineering choice.** A2-R established nothing
about either number. It is registered as a guess so that it can be rejected as one.

## 2. Held fixed

Retrieval engine, corpus, corpus digest, model, model configuration, `--max-turns`,
`wall_clock_s`, task bodies, the tail (*"Fix the behaviour in `src/`, and extend the existing
test suite to cover it"*), `capture_instruction`, the `environment` block, the sandbox profile,
the scorers and the runner.

**Not combined with anything.** No stopping prompt, no implement-then-verify instruction, no
model change, no ranking change, no larger ceiling. [`DECISION-a2r-failures.md`](DECISION-a2r-failures.md)
names an ordering instruction as the next testable mechanism for a *different* problem; running
both at once would make neither readable.

**Verified by assembly diff before launch:** each task's assembled prompt must differ between A
and B by exactly the appended paragraph, and the four A-prompts must be byte-identical to
`calib-v3`'s. A new `config_version` is required because B's prompt digests differ, so **no A3
row may be pooled with a v2, v3 or A2-R row.**

## 3. The store

[`FREEZE-dev-m1.md`](FREEZE-dev-m1.md), condition **`distracting`** — the whole 24-memory
corpus, digest `004762cab5248613`.

That condition is chosen because it is the only one where the policies can differ: it is a
store holding this task's evidence, every other task's, five irrelevant memories and — for
three of the four tasks — a **refuted** memory that a single search ranks first or second.
`useful`, `unnecessary` and `stale` are frozen and available; they are **not** in this run and
adding them would change the design, not extend it.

Measured, before any model outcome. The labels, ranks and condition digests are **identical**
in the original measurement ([`results-dev-m1.json`](results-dev-m1.json)) and the corrected one
([`results-dev-m1-r2.json`](results-dev-m1-r2.json)); only the leakage half changed. Both are
retained. The ranks below reproduce in either:

| task | what one search returns | where the useful memories sit | where the refuted one sits |
| --- | ---: | --- | --- |
| k1 | 16 | `m01` at 2 | `m05` at **1** |
| k2 | 15 | `h13` 2, `h05` 7, `m03` 8, `h07` 12 | `h02` at **1** |
| k3 | 16 | `h02` 1, `h05` 2, `h11` 3, `h01` 4, `h08` 5, `h09` 6, `h07` 15 | `m03` at 13 |
| k4 | 11 | `m02` at 1 | `m06` at **2** |

Read as a *rank* table and nothing more. Three corrections to how an earlier draft read it,
carried from [`FREEZE-dev-m1.md`](FREEZE-dev-m1.md) §5:

- **k3's seventh useful memory is at rank 15**, not in the top six. Six of seven are at 1–6.
- **A bounded policy need not fetch in rank order.** The bound says "at most three fetches"; it
  says nothing about which three, so "it would fetch the wrong memory first" assumes an
  ordering that is not registered and not required.
- **The agent may issue a different query.** These queries are frozen so the ranks reproduce.
  They are the evaluator's guess at what an agent would ask; a different query gives a
  different ranking and the whole table moves.

So what §3 registers is that this store is one in which the two policies **can** differ, with
the difference characterised in advance and model-free. It does not register that they will,
nor in which direction, nor that bounding is free on k3 or harmful on k2 — ranking evidence
cannot establish any of those. **Per-task results are expected to vary and are not pooled.**

**This is one mixed-corpus development comparison.** Only `distracting` is in this run.
`useful`, `unnecessary` and `stale` are frozen and **reserved for later questions**; nothing
measured here is evidence about them, and any of them could also produce a difference between
the policies. The paid design is not expanded to cover them now.

## 4. Design

| | |
| --- | --- |
| cells | 4 tasks × 2 policies = 8 |
| attempts | 2 per cell — **16 arm-runs** |
| ceiling | `--max-turns 45`. Per [`DIAGNOSTIC-turn-accounting.md`](DIAGNOSTIC-turn-accounting.md) this buys **45 model responses**; the envelope's `num_turns` is two different counters and is not a bound. |
| wall clock | 600 s, reported separately from turn exhaustion |
| ordering | both policies run **contemporaneously**, task by task, with the order of A and B drawn per attempt from the frozen schedule seed and recorded before launch |
| isolation | one sandbox profile, one store copy and one checkout per arm-run, as A2-R; the per-arm environment gate runs and refuses before spending |
| attempts | two, so a per-cell disagreement is visible. **Two is not a sample size.** No rate, no interval, no significance is claimed or computed. |

## 5. Registered measures

Reported **separately**. Nothing is folded into a single score.

**Correctness and compliance**
1. `a1-functional-2`, per arm-run.
2. `a1-scorer-5`, per arm-run, with its unknown count beside it.
3. **Required test work**, as its own column: does the patch add a test node, and did the
   arm-run execute it? A2-R's k1/nexus passed the hidden checks having added no test — so
   correctness alone is not the screen, and is registered here as insufficient.

**Consultation**
4. Consultation calls: `status`, `search`, `get` and `history`, counted **separately and all
   four**. `status` is a consultation of the memory service and a bounded policy leaning on it
   is still consulting.
5. Delivered context, in the three units of §5a. They are not interchangeable and none is
   reported alone.
6. **Predeclared task-relevant fact delivery**: for each task, the facts declared before
   launch as **equivalence classes** over the `useful` memories named in
   `results-dev-m1-r2.json`. Delivered = that memory's own prose appears in a tool result
   **before the first source edit**, recognised by `score_compliance.notes_content_delivered`
   against this corpus. Reported per memory **and** per fact, never as a count — and a member
   that was not measured is `unresolved`, not "not delivered". `useful` is a structural label
   (on-subject and currently supported); it does not assert that a memory is useful, and
   nothing here calls one necessary.
7. **Refuted-memory delivery**: the same, for the task's `stale` memory.

**Cost**
8. Total tokens per arm-run, in the categories the envelope records, **failures included**.
9. Delivered text tokens as a share of that total — reported as a ratio of two named
   quantities, not as "consultation cost".

### 5a. Cost units, defined

"Consultation tokens" is not an operational definition, and the first draft used it as though
it were. Three quantities are reported **separately**, per arm-run:

| unit | what it is | what it is not |
| --- | --- | --- |
| **calls and delivered bytes** | the count of `status`/`search`/`get`/`history` calls, and the bytes of tool-result text they returned | — |
| **delivered text tokens** | that same tool-result text, tokenised under a **named counting method** recorded with every arm-run | **not** attributable provider spend: retrieved content reappears in later requests, and cache accounting differs |
| **provider total tokens** | the envelope's own token categories, as the provider reports them, failures included | **not** decomposable into "the part consultation caused" |

An arm-run that does not name its counting method **cannot be counted**, and a sweep mixing
two methods is `indeterminate` on cost rather than summed — two methods are two units, and
their sum has no denominator. Both are enforced in
[`a3_decision.py`](a3_decision.py) and tested.

**Prompt-bound violations.** A B arm-run that issues a second search, or a fourth fetch, has
violated the candidate policy. It is **counted and kept**: `bound_respected` is recorded per
arm-run and reported, and **no arm-run is excluded from any dimension for breaking the bound**.
Excluding them would select the sample on the outcome being measured, and an instruction the
model does not follow is itself a result about the instruction.

**Termination**
10. Terminal reason, assistant-message count and wall clock, per arm-run. Assistant messages
    are recorded because they are the quantity the ceiling acts on and no A2-R artifact carries
    them.

**Stale outcomes**
11. For each arm-run where a refuted memory was delivered: did the final patch or a
    reproduction follow it? Read from the trace, reported per arm-run, `unknown` where the
    evidence does not settle it.

## 6. Acceptance criteria — a deterministic decision table

**Engineering screening criteria. Not statistical proof; no test of significance is computed
over 16 arm-runs.** Implemented in [`a3_decision.py`](a3_decision.py) and exercised against
synthetic outcomes in [`test_a3_decision.py`](test_a3_decision.py) — 33 checks, each one a
scenario the previous draft would have decided wrongly, plus the controls that stop the repair
over-correcting into "always indeterminate".

Every dimension returns **`hold`**, **`fail`** or **`indeterminate`**.

| dimension | rule |
| --- | --- |
| **Correctness** | **No task** has fewer functional passes under B than under A. Per task — a loss on one is never offset by a gain on another. A pooled total is reported and **decides nothing**. |
| **Required work** | **No task** loses verified relevant regression-test compliance. Unresolved evidence stays unresolved: it is not compliance and it is not a loss. |
| **Information** | **Predeclared task-relevant facts** are preserved. A fact is an **equivalence class** of memories; any member delivering it satisfies it, so reaching a redundant memory carrying the same claim loses nothing. These facts are *task-relevant*, declared in advance — **not** necessary: nothing here tests whether the task can be completed without them. |
| **Stale advice** | **Exposure and adoption are measured separately.** Only an increase in incorrect **adoption** is a failure. Reading a refuted memory and rejecting it is what the consult block asks for and is **not harm**; exposure is reported and judges nothing. |
| **Cost** | The registered thresholds, applied to **every arm-run including failures** — no exclusions, not for a failed run, not for one that broke the bound, not for an outlier. |

**Unresolved observations are intervals, not losses.** A comparison with an unresolved arm-run
in it is `fail` only if B loses even when every unresolved run is counted in B's favour, and
`hold` only if B holds when they are counted against it. Anything between is `indeterminate`.
"B scored fewer" and "B lost" are different claims and the first draft conflated them.

### The decision

| outcome | when |
| --- | --- |
| **reject** | any **established** failure on any dimension |
| **indeterminate** | no established failure, but the evidence is not decisive on at least one dimension |
| **accept for further development** | every criterion satisfied |

One rule, stated once. The previous draft said a C1 failure meant "rejected" in one paragraph
and "indeterminate" in another; there is no such ambiguity here, and `decide()` returns exactly
one of the three.

**Guardrails are correctness, required work, information and stale adoption.** Cost is a
criterion but not a guardrail: failing it rejects the policy without implying the policy did
harm — the change simply does not pay.

**Accept means accept *for further development*.** It is not a decision to adopt the bound as
a default, and nothing here would support that: 16 arm-runs on four exposed development tasks
in one repository.

### The thresholds

**≥ 40% in delivered text tokens and ≥ 10% in provider total tokens**, both required.

These are **engineering preferences**, registered in advance so they cannot be moved after the
numbers are seen. **The claim that they exceed stochastic variation is dropped**: two attempts
per cell cannot establish it, and the previous draft's justification asserted it. **The
spread-based exception is removed entirely** — it made C1 void for a task depending on the
spread of the very numbers it was judging, which is an outcome-dependent exception and not a
registered criterion.

A policy saving 60% and losing a predeclared fact is rejected. "Retrieved fewer tokens" alone
never qualifies.

### Indeterminate is a real outcome

It is reported as one. It is **not** re-run with friendlier settings, and no criterion is
relaxed after the numbers are seen. The next decision is taken on it.

## 7. Stop conditions

Identical in force to A2-R's, and each ends the sweep:

- the soft launch threshold is reached — **no new row is started**, partial results are kept
  and reported as partial;
- any arm-run's consumption cannot be accounted for — the sweep stops and the row stays
  `unresolved`, never zero, never covered by an allowance written to unblock it;
- the per-arm environment gate refuses;
- the boundary check fails, **or a control A3 requires is unknown** — `required_unresolved` and
  `required_failed` must both be empty, which covers `cache_shadow_unreadable` and its two
  components;
- the forwarder is unreachable or refuses a request.

**No retries, no mid-sweep repairs, no new allowances, no budget increases.** The
authorisation covers one sweep and is spent when that sweep ends, however it ends.

**Incomplete accounting is not completion.** A sweep that reaches its last row with an
unresolved arm-run reports `stopping_reason: unresolved_accounting`, exits nonzero, and its
total is a lower bound — as the closed calibration's still is.

## 8. Budget — proposed, not approved

### The launch unit, defined first

The previous draft called one historical **arm-run** a "row", which left the threshold unit
ambiguous. It is fixed here and used identically in the reservation, the threshold and the
disclosed overshoot:

| | |
| --- | --- |
| **arm-run** | one task, one policy, one attempt — one sandboxed execution. **16 in this design.** |
| **A/B pair** | the two arm-runs for one task and one attempt, A and B. **8 in this design.** |
| **launch unit** | **the A/B pair.** The sweep starts and stops on pairs, not on single arm-runs. |

**A3 launches A/B pairs.** A pair is the smallest unit that answers anything — a B arm-run
with no A arm-run beside it at the same task and attempt contributes to no dimension in §6, so
starting one alone can only spend. The soft threshold is therefore checked **before starting a
pair**, and the per-unit reservation is a **pair's** reservation.

### The figures, each labelled

Estimated from A2-R's **measured** nexus arm-runs, the only nexus arm-runs at this ceiling and
configuration. **Two of those four — k3 and k4 — ran under the boundary exposure** of
[`CLOSEOUT-a2r.md`](CLOSEOUT-a2r.md) §6 R5, and k4's is the largest of the four; its size may
or may not be related, which is unresolved. They are used anyway, because excluding them would
leave two arm-runs and a worse estimate, and the dependency is recorded here rather than buried.

| | tokens | label |
| --- | ---: | --- |
| A2-R nexus arm-runs | k1 1 285 670 · k2 1 101 843 · k3 1 717 242 · k4 2 415 785 | **measured** |
| mean per nexus arm-run | 1 630 135 | **measured** (the mean of four measurements) |
| mean per **A/B pair** (2 arm-runs) | 3 260 270 | **derived** from the above |
| 16 arm-runs = 8 pairs, at that mean | **26 082 160** | **estimate** |
| 16 arm-runs at the largest observed row | 38 652 560 | **scenario estimate** — every arm-run as expensive as the most expensive one observed |
| **proposed soft launch threshold** | **30 000 000**, `cap_is_soft: true` | **engineering proposal** |
| **per-pair reservation** | **4 831 570** — two arm-runs at the largest observed nexus row. A *planning* figure, **not a cap on an admitted pair** | **derived** |

**"Estimated ceiling" is renamed to "scenario estimate".** 38 652 560 is what the sweep costs
*if* every arm-run matches the most expensive one observed. It is not a bound: nothing caps an
arm-run's tokens below the turn ceiling, and four measurements do not bound a fifth. The 30M
proposal is **plausible planning headroom, not an established bound.**

The direction of error is unknown and the mean may be wrong either way — **a bounded policy can
cost *more* per run** if stopping consultation early sends the agent into repository
investigation it would otherwise have skipped. That possibility is why §6's cost dimension
measures provider total tokens as well as delivered text tokens.

### How the threshold behaves

**Soft**, as in A2-R: it decides where the sweep stops *starting* **pairs**. The threshold is
checked **before a pair is started**, and a pair already admitted is not interrupted.

**An admitted pair may carry consumption above 30 000 000, and its consumption is NOT bounded
by the 4 831 570-token reservation estimate. No numeric maximum overshoot is established.**
The reservation is a planning figure derived from four historical measurements — what the
sweep sets aside before admitting a pair, not a cap on what that pair may then spend, and
nothing caps an arm-run's tokens below the turn ceiling. An earlier version of this section
said the overshoot was "at most 4 831 570 tokens", which read a planning estimate as a bound:
the same error this document had already disclaimed for the 38 652 560 scenario figure. The
closed calibration's outstanding arm-run stays outstanding and is not netted against this.

## 9. Before launch

- [ ] a launch record at the repository root, naming `product_revision`, `harness_revision`,
      `config_version`, `config_digest`, both policies' prompt digests, the corpus digest
      `004762cab5248613`, the schedule digest and the seed
- [ ] a preflight that asserts every one of them, plus a clean tracked tree, no active
      virtualenv, `A2_PYTHON` carrying pytest, and the forwarder listening — and that refuses
      on a stale value, demonstrated
- [ ] the assembly diff of §2, showing exactly one paragraph differs
- [ ] **the boundary controls green on a prepared arm**, with the shadow controls named as
      **required**, not merely present — `check_boundary(..., require=isolation.REQUIRE_A3)`.
      See §10 for why naming them is what makes them binding. Both shadow controls must be
      conclusive: the **sentinel read by exact path** and the **directory listing**, each
      paired against an outside-sandbox positive control; plus the **sibling-arm** case, where
      the sentinel is placed by a second arm's profile; plus all five runtime positive controls
      (`own_checkout_readable`, `interpreter_runs`, `git_runs`, `runner_starts`,
      `runner_scratch_usable`)
- [ ] `seed_store.py` run from `corpus-dev-m1.json` restricted to the `distracting` condition,
      and the seeded store's digest recorded
- [ ] the founder's approval of the identifiers, the design and the budget

**Boundary evidence, already captured:** [`boundary-evidence-a3.json`](boundary-evidence-a3.json),
run on a prepared A2-R arm with a sibling arm as the shadow writer. `all_hold: true`,
`required_unresolved: []`, `required_failed: []`; both shadow controls conclusive; all five
runtime positive controls hold; runner `2.1.270`. `redirect_reproduced: false` — the
`/private/tmp` redirect did not fire on this host today, so the sentinel was placed directly,
which is recorded rather than read as a fix. **This is evidence the gate works, not an
authorisation.** It must be re-run on the prepared arms at launch.

## 10. What would make this void

- **The boundary.** Two A2-R arm-runs read another sweep's bytecode through the macOS cache
  shadow. That path is denied now and probed every run. **If either shadow probe is absent or
  `None` at launch, the sweep does not start.**

  This needed a repair, not just a restatement. `check_boundary` excluded a `None` result from
  `all_hold` — correct for a control that may legitimately not apply on a host, and exactly
  wrong for one this document declares a precondition: an unexercised boundary passed the gate
  that called it mandatory. A3 therefore passes `require=isolation.REQUIRE_A3`, and a required
  control that is unknown or false refuses on its own, independently of `all_hold`.

  A second repair: **a listing denial does not stand in for a file read.** The original probe
  listed the shadow root. An unlistable directory does not by itself make a known file inside
  it unreadable — sandbox policy is per operation — and the two arm-runs read `.pyc` files *by
  path*. Both controls now run: the listing, retained separately, and a **sentinel read by
  exact path** with an outside-sandbox positive control. The sentinel is placed by a sandboxed
  writer, preferring a **sibling arm's** profile, because one arm's write becoming another
  arm's read is the shape of the exposure; whether the `/private/tmp` redirect still fires is
  recorded per run as `redirect_reproduced` and never assumed. If the sentinel cannot be
  placed, the result is `None` — a denied read of a path holding nothing proves nothing. The
  combined verdict is `None` if **either** control is inconclusive.

  Measured on a prepared arm on this host: both controls demonstrate the boundary, all five
  runtime positive controls hold, `required_unresolved` and `required_failed` are empty, and
  the sentinel was placed directly because `redirect_reproduced` came back **false** — the
  write channel did not fire today, which is recorded rather than read as a fix.

- **A leaked fix — and the scan that cannot settle it.** `verify_dev_m1.py`'s identifier scan
  must be re-run against the store actually seeded, not the corpus file. But its result on this
  task set is **`unresolved`, not "clean"**, and A3 may not be launched on a misreading of it:
  no fix on k1–k4 introduces an identifier absent from its pre-fix tree, so there is nothing
  for a token scan to find. The earlier "checked per task" claim was also false in a second
  way — no fixture records a clone, so the scan had never run at all. Both are corrected in
  [`FREEZE-dev-m1.md`](FREEZE-dev-m1.md) §3. What fix leakage rests on here is **provenance
  review**, which is weaker than a measurement and is labelled as weaker. Identifier scans
  could not establish semantic leakage in any case.

  That review is now done per memory and frozen:
  [`PROVENANCE-dev-m1.md`](PROVENANCE-dev-m1.md) covers **all 24 memories** — every one an
  arm can receive under `distracting`, not only the on-subject ones — recording each one's
  source, what its author had seen, what its probe supports against what its prose asserts,
  its register, and its suitability. The standing of the whole corpus is therefore
  **reviewed provenance with limited automated leakage checks**, and A3 may not be launched
  on a stronger reading of it.

  **It flags `m02`, and that flag carries into this design.** `m02` states both halves of
  k4's defect in the same two clauses as k4's own fix comment, minus the remedy, and was
  written by an author who had seen k4. It stays in the corpus — it is diagnosis, not
  repair, and removing it after the fact would be tuning the corpus to a review. But **no
  k4 result in this sweep, under either policy, may be read as evidence that retrieval
  located that mechanism unaided**, and the launch record carries the same sentence. A third
  scan added for the review — fix *locality* — reports nothing for `m02` while firing on
  seven other memories, which is the concrete demonstration that scanning does not
  substitute for reading here.
- **The upstream copy.** `/opt/homebrew` is a system read root; A2-R measured one arm-run
  spending 10 of 47 calls reading a released Click from it. It is a channel to a fixed version
  of the library under test. **This design does not close it** — closing it would change the
  runtime, which §2 holds fixed — and the count is therefore a registered measure in its own
  right, reported per arm-run beside consultation.

## 11. The other candidate, kept separate

[`DECISION-a2r-failures.md`](DECISION-a2r-failures.md) names an **implement-then-verify
ordering instruction** as a testable mechanism. It stays a **separate candidate**, not part of
this design: §2 holds everything but the consult paragraph fixed, and running both at once
would make neither readable.

**What the k4/baseline evidence supports.** At call 46 a scratch file, `exp_both.py`,
demonstrated the three behaviours the task names. It was never transcribed into
`src/click/core.py`, and the run reached the ceiling four calls later. That is a real
observation and it is the strongest single finding of that pass.

**What it does not support.** It does not show that more turns, better retrieval or a stopping
instruction could not have changed the outcome — none of those was varied, and a run that ends
at the ceiling is consistent both with "more turns would not have helped" and with "more turns
were exactly what was missing". The prototype was **not applied to the deliverable** and was
**never shown to pass the hidden checks in that form**; the checks were never run against it.
"It had the fix" means it had a program exhibiting three named behaviours, not a verified fix.

**And it is not a third task set.** A2-R reuses k1–k4, the same tasks v2 ran. The
never-wrote-to-`src/` shape has been seen on **two** task sets — A1's h1–h4 and k1–k4, the
latter twice — not three.

## 12. Done when

The bounded policy is **accepted**, **rejected** or **indeterminate** against §6, on the
evidence of one sweep. There is no second sweep at friendlier settings, and a criterion is not
relaxed after the numbers are seen.
