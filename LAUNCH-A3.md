# A3 — the execution identity, for approval

**NOT APPROVED. This authorises nothing.** Every identifier below is computed and frozen,
and [`preflight-a3.py`](preflight-a3.py) asserts every one of them — but a green preflight
is not an authorisation. No budget is approved, no arm-run has been executed, and **paid
execution remains stopped** until the founder approves this record.

> ## Unauthorised execution already happened against this design
>
> On **2026-09-20** a development rehearsal ran two A3-shaped arm-runs against the **real**
> model endpoint and spent **2 669 285 tokens** — `k1/attempt1`, policies A and B — while
> paid execution was stopped. It reached a forwarder that had been listening on the
> production port since 2026-09-15, aimed at the real upstream. Full account:
> [`INCIDENT-a3-unauthorized-spend.md`](INCIDENT-a3-unauthorized-spend.md).
>
> **Those two arm-runs are not A3 results and are not used as any.** They fill a registered
> cell, so reusing them would launder unauthorised spend into the experiment; they are also
> a single cell selected by an accident. If A3 is approved it starts from nothing. They are
> **not netted against the 30 000 000 proposal** in either direction.
>
> The cause was a rehearsal that read "something is listening" as "my stub is listening".
> It is closed by a positive egress control: the stub echoes a per-run token and the
> rehearsal refuses to launch unless that token comes back through the forwarder. The
> sandbox is not implicated — a boundary governs what an arm can read and reach, not what it
> costs.

The design it would execute is
[`benchmarks/agent/REGISTRATION-DRAFT-a3-consult.md`](benchmarks/agent/REGISTRATION-DRAFT-a3-consult.md).

**What it would ask.** Does a bounded consultation policy cost materially less than the
current one *without* losing access to information the current one reaches?

**What it could not answer.** Whether memory helps at all — both policies have memory.
Whether Nexus beats static notes — there is no notes arm. Anything about a task set other
than k1–k4, which are **exposed development tasks** and stay development tasks.

---

## What is new in A3, and why it needs its own record

A2-R varied the **arm**. A3 varies the **policy**, and both policies run the *nexus* arm.
Three consequences, each of which is an identifier or a precondition below.

| | |
| --- | --- |
| **result identity** | `(task, attempt, policy)`. `(task, arm)` would name **eight** slots for **sixteen** arm-runs and silently overwrite one of each pair. The on-disk layout puts the policy above `arms/`, so two policies can never share a directory. |
| **launch unit** | the **A/B pair**. A B arm-run with no A beside it at the same task and attempt contributes to no dimension in §6 and can only spend, so the sweep starts and stops on pairs. |
| **new `config_version`** | `a3-v1`. B's prompt digests differ, so **no A3 row may be pooled with a v2, v3 or A2-R row.** |

## The checkout, which is not the harness revision

Two different identifiers, and a launch needs both. **`harness_revision` is the measured
identity** — the last commit touching `benchmarks/agent` — and it is what decides whether
two rows may be pooled, so it is frozen below as a literal. **HEAD is the execution
checkout** — reported by the preflight, never pinned.

This record and `preflight-a3.py` sit at the repository **root** precisely so that
committing them does not move the revision they name.

**Do not edit, commit, rebase or update dependencies in the execution checkout during the
sweep.**

## Identifiers

| | |
| --- | --- |
| `product_revision` | `2cd531f9d7c274a065533e58ba3fde8582f8c269` |
| `harness_revision` | `29566b629283f92b4491e27cc62a28458cdae754` |
| `config_version` | `a3-v1` |
| `config_digest` | `40f1f18bcfd25cb6` |
| `max_turns` | **45** — 45 *model responses*; the envelope's `num_turns` is two different counters and is not a bound |
| `wall_clock_s` | **600**, reported separately from turn exhaustion |
| corpus **content** digest | `004762cab5248613` — *which memories*: the `distracting` condition, all 24 |
| corpus **store** digest | `ae03ede5f19a3c74` — the seeded store's rows, which is what the arm runner gates on |
| store file sha256 | `22a91c249cf5cf3494e3e2cc8733e841138d4c1f66b23bb2c722afc1c8cce00e` |
| schedule digest | `de2821a294bddbc55a327b0d3b931ce9baf78239bbf0c8e777d1e0baff099c43` |
| schedule seed | `20260919` |

Both corpus digests are asserted because **either can move without the other**: a reseed of
the same memories changes neither, an edit to the corpus file changes the content digest,
and a store seeded from a different file changes the row digest.

### Prompt digests — two policies, one paragraph

| task | A (current) | B (bounded) |
| --- | --- | --- |
| k1 | `5f4f1e210aa7f7ea` | `2b00dc58add11488` |
| k2 | `0aa687b3be6b6a26` | `4ca030e6960fb03a` |
| k3 | `b99a1fd5366f507f` | `1edd570c435baa61` |
| k4 | `97ee8c80330b164a` | `34da2ea06f44f590` |

**The assembly diff is measured, not asserted.** `a3_prompts.assembly_diff()` checks that
each A prompt is **byte-identical** to the prompt `calib-v3` assembles and that each B
prompt is its A prompt plus the bound and differs in nothing else. It holds at a constant
**+215 bytes**. The four A digests are the same four `preflight-a2r.py` froze for `calib-v3`
before A3 existed — computed here from a different file by a different code path, which is
the independent check that an A arm-run is given exactly what an A2-R nexus arm-run was
given.

### The schedule — over policies, drawn once

8 pairs, seed `20260919`, **two** distinct orders (`A->B` and `B->A`), each policy first
four times. **Reported, not corrected**: a seeded draw is not a balanced design, and the
failure this replaces is a "randomised" order that was reproducibly constant.

## The execution path, rehearsed

[`run_a3.py`](benchmarks/agent/run_a3.py) is the adapter that would execute this. It imports
`run_arms_isolated` rather than forking it — §2 holds the runner fixed — and changes five
things: the prompt is assembled **per policy**; `OUT` carries `(task, attempt, policy)` so
two policies that are the same arm cannot share a directory; each policy's profile **denies
the other**; `check_boundary` is called with **`require=isolation.REQUIRE_A3`**; and the
launch is **ledgered** before the process starts.

[`rehearse_a3_production.py`](benchmarks/agent/rehearse_a3_production.py) drives that path
against the real fixture trees, sandbox profiles, environment gate, boundary controls and
compliance scorer, replacing **only** the model endpoint with the loopback stub. Measured on
this host:

| | |
| --- | --- |
| outbound prompt A | `5f4f1e210aa7f7ea` **observed** |
| outbound prompt B | `2b00dc58add11488` **observed** |
| artifacts | `a3-launch.json`, `launched.json`, `trace.jsonl`, `patch.diff`, `record.json`, `sandbox.sb`, `envcheck.json` — written by the **production** writer, for both policies, in **two distinct directories** |
| environment gate | 8 checks pass, runtime pinned |
| boundary | `required_unresolved: []`, `required_failed: []` on **prepared A3 arms** |
| cost | **108 tokens** |

A preflight that computes prompt digests establishes nothing about what reaches the model.
This is the observation that closes that gap: the digests were read from what the stub
actually received, and compared against digests computed from the registration.

## Design

| | |
| --- | --- |
| cells | 4 tasks × 2 policies = 8 |
| attempts | 2 per cell — **16 arm-runs, 8 pairs** |
| ordering | contemporaneous, task by task, from the frozen schedule |
| isolation | one sandbox profile, one store copy and one checkout per arm-run; the per-arm gate runs and refuses before spending |

**Two attempts is not a sample size.** No rate, no interval and no significance is claimed
or computed anywhere in this design.

## What stands behind the corpus

[`FREEZE-dev-m1.md`](benchmarks/agent/FREEZE-dev-m1.md), condition **`distracting`** — the
whole 24-memory corpus. Its standing is
**reviewed provenance with limited automated leakage checks**, recorded per memory in
[`PROVENANCE-dev-m1.md`](benchmarks/agent/PROVENANCE-dev-m1.md) for **all 24** — every
memory an arm can receive, not only the on-subject ones. It is **not** proof that no memory
conveys a fix, and A3 may not be launched on a stronger reading of it.

**One memory is flagged, and the flag is a condition of this launch.** `m02` states both
halves of k4's defect — that `get_help_option` CONSTRUCTS an option on each call, and that
`iter_params_for_processing` compares the parameter OBJECTS — in the same two clauses as
k4's own in-code fix rationale, minus the remedy, and was written by an author who had seen
k4. It stays in the corpus: it is diagnosis rather than repair, and removing it after the
fact would be tuning the corpus to a review. What it changes is what may be concluded —

> **No k4 result in this sweep, under either policy, may be read as evidence that retrieval
> located that mechanism unaided.**

**The scans cannot see it.** The fix-locality scan added for the review fires on seven other
memories — `close` on `m01` for k1, `flag_value`/`is_flag`/`default` across the k2 memories,
`UNSET` on `h02` for k3 — and reports **nothing for `m02`**, because every identifier in it
predates the fix. The scans flag memories the reading clears and clear the memory the
reading flags. That is why the conclusion rests on reading.

## The boundary, read narrowly

[`boundary-evidence-a3.json`](benchmarks/agent/boundary-evidence-a3.json) is green:
`all_hold: true`, `required_unresolved: []`, `required_failed: []`, both shadow controls
conclusive, all five runtime positive controls holding, the sentinel placed by a **sibling
arm's** profile.

**What that establishes** is that the *tested* cache-shadow path is denied, with the token
absent inside the sandbox and present outside; that the directory listing is denied as a
separate operation; and that the denials are not a dead profile.

**What it does not establish**, carried with the artifact rather than left in a document
beside it:

- **that every equivalent destination is denied.** One path was tested; sandbox policy is
  per operation and per path.
- **that the redirection mechanism was reproduced.** It was not — `redirect_reproduced:
  false`, `placement: direct`. That does **not** invalidate the deny test, which depends on
  a known file at a known path rather than on how it got there. What is unresolved is
  whether the write channel that produced the original exposure is still open. **It is
  carried here as unresolved, and it is a precondition of no criterion.**
- **anything about this sweep.** That board was captured on a prepared *A2-R* arm. **It must
  be re-run on the prepared A3 arms**, with `require=isolation.REQUIRE_A3`, and a required
  control that is unknown or false refuses on its own.

## Budget — proposed, not approved

**The launch unit is the A/B pair**, and the threshold and the reservation use that unit.

| | tokens | label |
| --- | ---: | --- |
| A2-R nexus arm-runs | k1 1 285 670 · k2 1 101 843 · k3 1 717 242 · k4 2 415 785 | **measured** |
| mean per nexus arm-run | 1 630 135 | **measured** (the mean of four measurements) |
| mean per **A/B pair** | 3 260 270 | **derived** |
| 8 pairs at that mean | **26 082 160** | **estimate** |
| 16 arm-runs at the largest observed row | 38 652 560 | **scenario estimate** |
| **proposed soft launch threshold** | **30 000 000**, `cap_is_soft: true` | **engineering proposal** |
| **per-pair reservation** | **4 831 570** — two arm-runs at the largest observed row. A *planning* figure: what the sweep sets aside before admitting a pair, **not a cap on what that pair may spend** | **derived** |

**38 652 560 is a scenario, not a ceiling.** Nothing caps an arm-run's tokens below the turn
ceiling, and four measurements do not bound a fifth. **30M is plausible planning headroom,
not an established bound.**

**Two of the four arm-runs the estimate rests on — k3 and k4 — ran under the boundary
exposure** of [`CLOSEOUT-a2r.md`](benchmarks/agent/CLOSEOUT-a2r.md) §6 R5, and k4's is the
largest of the four. Whether its size is related is **unresolved**. They are used anyway,
because excluding them would leave two arm-runs and a worse estimate, and the dependency is
recorded here rather than buried.

**The direction of error is unknown.** A bounded policy can cost *more* per arm-run if
stopping consultation early sends the agent into repository investigation it would otherwise
have skipped. That possibility is why the cost dimension measures provider total tokens as
well as delivered text tokens.

**Soft** means it decides where the sweep stops *starting* pairs. The threshold is checked
**before a pair is started**, and a pair already admitted is not interrupted. **An admitted
pair may carry consumption above 30 000 000, and its consumption is NOT bounded by the
4 831 570-token reservation estimate: no numeric maximum overshoot is established.**

The reservation is a *planning* figure derived from four historical measurements. It is what
the sweep sets aside before admitting a pair; it is not a cap on what that pair may then
spend, and nothing in the design caps an arm-run's tokens below the turn ceiling. An earlier
version of this record said the overshoot was "at most 4 831 570 tokens", which read a
planning estimate as a bound — the same error it had already disclaimed for the 38 652 560
scenario figure two paragraphs earlier. **Any approval must be given on the accurate
disclosure above.**

The closed calibration's outstanding arm-run stays outstanding and is **not netted against
this**.

## Stop conditions

Each ends the sweep, and each is registered:

- the soft threshold is reached — **no new pair is started**, partial results are kept and
  reported **as partial**;
- **any arm-run's consumption cannot be accounted for** — the sweep stops, the row stays
  `unresolved`, never zero, never covered by an allowance written to unblock it. Missing
  *outcome* evidence does not stop a launch; unaccounted *consumption* does, and the two are
  separate gates in the decision engine;
- the per-arm environment gate refuses;
- the boundary check fails, **or a control A3 requires is unknown**;
- the forwarder is unreachable or refuses a request.

**No retries, no mid-sweep repairs, no new allowances, no budget increases.** An
authorisation covers one sweep and is spent when that sweep ends, however it ends.

## How it would be decided

[`a3_decision.py`](benchmarks/agent/a3_decision.py), deterministically, with **validity
established before any acceptance criterion**:

| outcome | when |
| --- | --- |
| **invalid** | a duplicated or unplanned identity — the records are not this experiment, and **no criterion is applied to them** |
| **reject** | any **established** failure on any dimension |
| **indeterminate** | coverage partial (acceptance unavailable), or the evidence is not decisive |
| **accept for further development** | every criterion satisfied, on complete coverage |

Counts with unresolved observations are compared as **possible-count bounds** — the smallest
and largest count each policy could have had. **They are not confidence intervals**: nothing
is estimated from a distribution, and two attempts per cell could not support one.

**Accept means accept *for further development*.** It is not a decision to adopt the bound
as a default, and nothing in 16 arm-runs on four exposed development tasks in one repository
would support that.

## Verified before this record was written

- [x] the assembly diff — exactly one paragraph differs, +215 bytes, A byte-identical to `calib-v3`
- [x] the store seeded from `corpus-dev-m1.json` at the `distracting` condition, digests recorded
- [x] `notes-dev-m1.md` rendered from **this** corpus, not the held-out one
- [x] the schedule frozen over policies before anything executes
- [x] the provenance review frozen, covering all 24, matching the corpus digest
- [x] the decision engine exercised end to end through saved files — schedule → stubbed
      execution → real compliance scoring → normalisation → decision, reaching accept,
      reject and indeterminate
- [x] **the preflight demonstrated to refuse a stale value**, not merely to pass
- [x] the **production adapter** exercised against the loopback stub, with the outbound
      prompt digests observed and the production writer producing every artifact
- [x] **the boundary controls run on prepared A3 arms** with `require=isolation.REQUIRE_A3`,
      both policies, `required_unresolved` and `required_failed` empty
- [x] the predeclared facts frozen in [`facts-a3.json`](benchmarks/agent/facts-a3.json),
      every member drawn from that task's `useful` set
- [x] the launch ledger: a marker before the process, a launch without terminal accounting
      unresolved whether or not a trace exists, and fatal stops separate from pair budget

## Outstanding before any launch

- [ ] the boundary board and the per-arm gate re-run **at launch, on the arms that will
      run** — the green board above is evidence the gate works on prepared A3 arms, and a
      board captured earlier is not a property of a later sweep
- [ ] `preflight-a3.py` re-run, its output and execution HEAD preserved
- [ ] **the forwarder confirmed to be the intended one before any arm-run.** The 2026-09-20
      incident spent 2.67M tokens against a stale forwarder on the configured port; a
      listener answering is not evidence of which endpoint is behind it
- [ ] **the founder's approval of the identifiers, the design and the 30 000 000 threshold**

**Nothing above is an approval.** This record exists to be approved or refused.
