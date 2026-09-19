# A2-R — the execution identity, for approval

**Nothing here has been executed.** This record is presented for the founder's approval. It
becomes the frozen identity of the A2-R development sweep when it is approved and the two
stamped-at-freeze rows below are filled in by `preflight-a2.py`.

The design it executes is [`benchmarks/agent/REGISTRATION-DRAFT-a2r.md`](benchmarks/agent/REGISTRATION-DRAFT-a2r.md),
whose §7 decisions are closed. **A2-R is not the calibration.** The calibration is closed with
no ceiling selected ([`CLOSEOUT-calib-a2.md`](benchmarks/agent/CLOSEOUT-calib-a2.md)) and
nothing here reopens it.

Deliberately **outside the measured tree**, for the reason [`LAUNCH-A2.md`](LAUNCH-A2.md)
gives: `product_revision` is the last commit touching `src/nexus_memory` and `harness_revision`
the last touching `benchmarks/agent`, so a record kept in either would move the identifiers it
records every time it was written. Nothing here is read by the harness; it is what a later
reader compares an artifact against.

## The checkout

The execution checkout is **the HEAD that `preflight-a2.py` approved**, which it prints. The
three ways a checkout can drift while `harness_revision` stays put — a committed change, an
uncommitted edit to a tracked file, an edited gitignored config, an activated virtualenv — are
tabulated in [`LAUNCH-A2.md`](LAUNCH-A2.md) and apply here unchanged.

**Do not edit, commit, rebase or update dependencies in the execution checkout during the
sweep.**

## Identifiers

| | |
| --- | --- |
| `product_revision` | `2cd531f9d7c274a065533e58ba3fde8582f8c269` |
| `harness_revision` | **stamped at freeze** — `preflight-a2.py` prints it; it is the commit that freezes this record, which cannot contain its own hash |
| `config_version` | `calib-v3` |
| `schedule_digest` | `0a352b82ced14f10e85d50d17989ac38e02d4d9582c032ed743a75554a6a5d97` |
| `corpus_digest` | `9ae2a9f268dd894d` — A1's frozen held-out corpus, unchanged |
| `seed` | `20260914` — the same frozen schedule, drawn once |

| ceiling | configuration | `config_digest` | `max_turns` |
| --- | --- | --- | --- |
| 45 | `benchmarks/agent/a2r-config-45.json` | **stamped at freeze** — the file does not exist yet | 45 |

**A2-R reads `a2r-config-<n>.json`, never `calib-config-<n>.json`.** The three calibration
files are the closed sweep's frozen identity and `preflight-a2.py` checks their digests against
`LAUNCH-A2.md`; writing a `calib-v3` configuration over `calib-config-45.json` would destroy
the provenance of a published sweep — and it is the natural mistake, because that is the name
the calibration driver's own locator produces. `run_a2r.py` carries its own locator so the
driver cannot fall back to it. The new file is gitignored like the others: it carries this
host's absolute paths.

**One ceiling.** `run_a2r.py` refuses the calibration's `--ceilings` grid flag.

### Prompt digests — computed, and fixed

Every one differs from its v2 value, so **no v3 row can be pooled with a v2 row**. The v2
column is the frozen calibration's, from [`LAUNCH-A2.md`](LAUNCH-A2.md).

| task | v2 (`calib-v2`) | **v3 (`calib-v3`)** |
| --- | --- | --- |
| k1 | `6bbe10c01d7e520d` | **`5f4f1e210aa7f7ea`** |
| k2 | `d47e3eaa44a8d22c` | **`0aa687b3be6b6a26`** |
| k3 | `905bf3bd43df8756` | **`b99a1fd5366f507f`** |
| k4 | `6fd3cb4335a4c1dd` | **`97ee8c80330b164a`** |

The only difference in the prompt is the `environment` block, which now names `$A2_PYTHON`.
Task bodies, tails, `consult` and `capture_instruction` are byte-identical to v2.

### The v2 configurations are untouched

| file | `config_version` | `config_digest` |
| --- | --- | --- |
| `calib-config-30.json` | `calib-v2` | `69df5d49a39142c4` |
| `calib-config-45.json` | `calib-v2` | `396c0d2d48a22eed` |
| `calib-config-60.json` | `calib-v2` | `b64cc9c137544b7a` |

Verified on this host at the revision that publishes this record. They still hash to the
digests `LAUNCH-A2.md` froze. The v3 configuration is created **alongside** them under a
different name; none of the three is edited, and the closed calibration's provenance stays
intact.

## Scope

| | |
| --- | --- |
| output directory | **its own**, outside any calibration scratch. `run_a2r.py` refuses a directory holding another sweep's summary, and refuses to nest inside one. |
| summary | `a2r-summary.json` — never `calibration-summary.json` |
| soft launch threshold | **20 000 000 tokens.** Not a maximum: the sweep can finish above it by up to one row, and a largest-observed-row reservation bounds nothing about the next row. Recorded as `cap_is_soft: true`. |
| selection rule | **none is applied.** `freeze-calib-a2.md` §5 belongs to the calibration. `assess_a2r.py` refuses to summarise more than one ceiling. |

**The closed calibration's unresolved consumption stays outstanding.**
`c60/run-k1/attempt1/arms/baseline` has no usable usage record; that sweep's total is a lower
bound and its charge exceeded its cap by 560 844 tokens. A2-R does not resolve it, does not
cover it with a new allowance, and does not treat it as zero.

## Verification evidence

Model-free, on this revision:

| what | evidence |
| --- | --- |
| the defect is reproduced, not assumed | [`REPAIR-interpreter-consistency.md`](benchmarks/agent/REPAIR-interpreter-consistency.md) §1 — both shell startups, inside a real arm profile |
| the repair is gated | §3 — every documented command under both startups, agreement required on a tagged marker line |
| the gate's own controls | §4 — the v2 command, an unset pin, a pytest-less interpreter, two disagreeing startups, and two silent ones are each refused |
| the repair check is non-vacuous | `test_a2r.py` — an absent error with no attempt is `no_relevant_invocation`, not a pass |
| a comment is not a test | `test_a2r.py` — a `tests/` diff whose only additions are comments adds no test node |
| separate accounting | `test_a2r.py` — completion, threshold refusal, unresolved-consumption refusal (asserting the **next row is never launched**), and refusal to share a calibration directory |
| every mutation is detected | 14 seeded mutations across `assess_a2r.py`, `run_a2r.py` and `run_calibration.py`, all killed |

## Approval

- [ ] **The founder approves this record and the design it names.** Until this box is ticked,
      `run_a2r.py` is not to be invoked against a live forwarder.
- [ ] `benchmarks/agent/a2r-config-45.json` at `calib-v3` created — **a new file, not an edit
      to any `calib-config-*.json`** — and its `config_digest` stamped above
- [ ] `harness_revision` stamped above from `preflight-a2.py`
- [ ] `preflight-a2.py` green, forwarder up, gate passing on a prepared arm
