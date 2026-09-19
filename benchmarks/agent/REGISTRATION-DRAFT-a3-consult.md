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

Measured, before any model outcome ([`results-dev-m1.json`](results-dev-m1.json)):

| task | what one search returns | where the useful memories sit | where the refuted one sits |
| --- | ---: | --- | --- |
| k1 | 16 | `m01` at 2 | `m05` at **1** |
| k2 | 15 | `h13` 2, `h05` 7, `m03` 8, `h07` 12 | `h02` at **1** |
| k3 | 16 | `h02` 1, `h05` 2, `h11` 3, `h01` 4, `h08` 5, `h09` 6, `h07` 15 | `m03` at 13 |
| k4 | 11 | `m02` at 1 | `m06` at **2** |

So three of four tasks are a test of whether the bound spends its three fetches on the wrong
memory, and k3 is a test of whether the bound is free when ranking is already good. **That
asymmetry is registered here, in advance, as the reason a per-task result is expected to vary
and must not be pooled into one number.**

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
4. Consultation calls: `mcp__nexus__search`, `get` and `history`, counted separately.
5. Delivered context: bytes and tokens returned by those calls.
6. **Required-fact delivery**: for each task, the `useful` memories named in
   `results-dev-m1.json`. Delivered = that memory's own prose appears in a tool result
   **before the first source edit**, recognised by `score_compliance.notes_content_delivered`
   against this corpus. Reported per memory, not as a count.
7. **Refuted-memory delivery**: the same, for the task's `stale` memory.

**Cost**
8. Total tokens per arm-run, in the categories the envelope records, **failures included**.
9. Consultation tokens as a share of the total.

**Termination**
10. Terminal reason, assistant-message count and wall clock, per arm-run. Assistant messages
    are recorded because they are the quantity the ceiling acts on and no A2-R artifact carries
    them.

**Stale outcomes**
11. For each arm-run where a refuted memory was delivered: did the final patch or a
    reproduction follow it? Read from the trace, reported per arm-run, `unknown` where the
    evidence does not settle it.

## 6. Acceptance criteria, frozen

**These are engineering screening criteria. They are not statistical proof and no test of
significance will be computed over 16 arm-runs.**

The bounded policy is **accepted** only if all four hold:

| | criterion |
| --- | --- |
| **C1 cost** | consultation tokens fall by **≥ 40%** pooled across the 8 bounded arm-runs versus the 8 current ones, **and** total tokens fall by **≥ 10%** |
| **C2 correctness** | functional passes under B ≥ under A, counted over 8 and 8 |
| **C3 evidence** | **no** task where a `useful` memory delivered under A is not delivered under B in either attempt |
| **C4 harm** | arm-runs delivering a refuted memory do not increase under B, and no arm-run under B follows one into its patch where the corresponding A arm-run did not |

**Rejected** if C1 fails — the saving is not worth the change — or if C2, C3 or C4 fails, in
which case it is rejected *whatever* C1 says. **"Retrieved fewer tokens" alone does not
qualify.** A policy that saves 60% and loses one required fact is rejected.

**Indeterminate** is a real outcome and is reported as one: if C1's two halves disagree, or if
a criterion cannot be evaluated because an arm-run is unresolved. Indeterminate is **not**
re-run with friendlier settings; it is reported, and the next decision is taken on it.

The 40% / 10% pair is chosen so that a saving smaller than the observed spread between two
attempts of the same cell cannot pass. If the two attempts of any A cell differ by more than
40% in consultation tokens, **C1 is void for that task** and says so.

## 7. Stop conditions

Identical in force to A2-R's, and each ends the sweep:

- the soft launch threshold is reached — **no new row is started**, partial results are kept
  and reported as partial;
- any arm-run's consumption cannot be accounted for — the sweep stops and the row stays
  `unresolved`, never zero, never covered by an allowance written to unblock it;
- the per-arm environment gate refuses;
- the boundary check fails, **including `cache_shadow_unreadable`**;
- the forwarder is unreachable or refuses a request.

**No retries, no mid-sweep repairs, no new allowances, no budget increases.** The
authorisation covers one sweep and is spent when that sweep ends, however it ends.

**Incomplete accounting is not completion.** A sweep that reaches its last row with an
unresolved arm-run reports `stopping_reason: unresolved_accounting`, exits nonzero, and its
total is a lower bound — as the closed calibration's still is.

## 8. Budget — proposed, not approved

Estimated from A2-R's **measured** nexus arm-runs, which are the only nexus arm-runs at this
ceiling and configuration:

| | tokens |
| --- | ---: |
| A2-R nexus arm-runs, measured | k1 1 285 670 · k2 1 101 843 · k3 1 717 242 · k4 2 415 785 |
| mean per nexus arm-run (**measured**) | 1 630 135 |
| 16 arm-runs at that mean (**estimated**) | **26 082 160** |
| 16 at the largest observed row (**estimated ceiling**) | 38 652 560 |
| **proposed soft launch threshold** | **30 000 000**, `cap_is_soft: true` |
| per-row reservation | 2 415 785 — the largest observed nexus row |

Every figure above is labelled. The mean is measured; the totals are estimates and may be
wrong in either direction — **a bounded policy can cost *more* per run** if stopping
consultation early sends the agent into repository investigation it would otherwise have
skipped. That possibility is why C1 measures total tokens as well as consultation tokens.

As with A2-R the threshold is **soft**: it decides where the sweep stops *starting* rows, an
arm-run cannot be interrupted part-way, and the sweep may finish above it by up to one row.
The closed calibration's outstanding arm-run stays outstanding and is not netted against this.

## 9. Before launch

- [ ] a launch record at the repository root, naming `product_revision`, `harness_revision`,
      `config_version`, `config_digest`, both policies' prompt digests, the corpus digest
      `004762cab5248613`, the schedule digest and the seed
- [ ] a preflight that asserts every one of them, plus a clean tracked tree, no active
      virtualenv, `A2_PYTHON` carrying pytest, and the forwarder listening — and that refuses
      on a stale value, demonstrated
- [ ] the assembly diff of §2, showing exactly one paragraph differs
- [ ] the boundary controls green on a prepared arm, `cache_shadow_unreadable` included
- [ ] `seed_store.py` run from `corpus-dev-m1.json` restricted to the `distracting` condition,
      and the seeded store's digest recorded
- [ ] the founder's approval of the identifiers, the design and the budget

## 10. What would make this void

- **The boundary.** Two A2-R arm-runs read another sweep's artifacts through the macOS cache
  shadow. That path is denied now and probed every run. If the probe is absent or `None` at
  launch, the sweep does not start.
- **A leaked fix.** `verify_dev_m1.py` checks that no memory carries an identifier appearing
  only on the added side of its task's fix. It must be re-run against the store actually
  seeded, not against the corpus file.
- **The upstream copy.** `/opt/homebrew` is a system read root; A2-R measured one arm-run
  spending 10 of 47 calls reading a released Click from it. It is a channel to a fixed version
  of the library under test. **This design does not close it** — closing it would change the
  runtime, which §2 holds fixed — and the count is therefore a registered measure in its own
  right, reported per arm-run beside consultation.

## 11. Done when

The bounded policy is **accepted**, **rejected** or **indeterminate** against §6, on the
evidence of one sweep. There is no second sweep at friendlier settings, and a criterion is not
relaxed after the numbers are seen.
