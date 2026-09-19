# A2-R — the execution identity, for approval

**Nothing here has been executed.** This record is presented for the founder's approval. Every
identifier below is **stamped**, and [`preflight-a2r.py`](preflight-a2r.py) asserts every one
of them.

The design it executes is [`benchmarks/agent/REGISTRATION-DRAFT-a2r.md`](benchmarks/agent/REGISTRATION-DRAFT-a2r.md),
whose §7 decisions are closed. **A2-R is not the calibration.** The calibration is closed with
no ceiling selected ([`CLOSEOUT-calib-a2.md`](benchmarks/agent/CLOSEOUT-calib-a2.md)) and
nothing here reopens it.

Deliberately **outside the measured tree**, for the reason [`LAUNCH-A2.md`](LAUNCH-A2.md)
gives: `product_revision` is the last commit touching `src/nexus_memory` and `harness_revision`
the last touching `benchmarks/agent`, so a record kept in either would move the identifiers it
records every time it was written. Nothing here is read by the harness; it is what a later
reader compares an artifact against.

## The checkout, which is not the harness revision

Two different identifiers, and a launch needs both.

**`harness_revision` is the measured identity** — the last commit touching `benchmarks/agent`.
It is what a row records and what decides whether two rows may be pooled, so it is **frozen
below as a literal**. There is no self-hash obstacle: this record and `preflight-a2r.py` sit at
the repository **root**, and a root-level commit does not touch `benchmarks/agent`, so
committing them leaves the revision they name exactly where it is.

**HEAD is the execution checkout** — reported by the preflight, never pinned, because every
commit to a root-level note moves it while changing nothing a row records. Run from the HEAD
the preflight prints, and leave it alone.

The four ways a checkout can drift while `harness_revision` stays put — a committed change, an
uncommitted edit to a tracked file, an edited gitignored config, an activated virtualenv — are
tabulated in [`LAUNCH-A2.md`](LAUNCH-A2.md) and apply here unchanged. `preflight-a2r.py` checks
all four.

**Do not edit, commit, rebase or update dependencies in the execution checkout during the
sweep.**

## Identifiers

| | |
| --- | --- |
| `product_revision` | `2cd531f9d7c274a065533e58ba3fde8582f8c269` |
| `harness_revision` | `225d8538a5c7fcf4d161448c76ee0ba18b53378e` |
| `config_version` | `calib-v3` |
| `schedule_digest` | `0a352b82ced14f10e85d50d17989ac38e02d4d9582c032ed743a75554a6a5d97` |
| `corpus_digest` | `9ae2a9f268dd894d` — A1's frozen held-out corpus, unchanged |
| `seed` | `20260914` — the same frozen schedule, drawn once |

| ceiling | configuration | `config_digest` | `max_turns` |
| --- | --- | --- | --- |
| 45 | `benchmarks/agent/a2r-config-45.json` | `22eb0a3766cdde73` | 45 |

Created on this host from `calib-config-45.json` with **exactly one field changed** —
`config_version: calib-v2` → `calib-v3`. Everything else, `wall_clock_s: 600` and
`corpus_digest: 9ae2a9f268dd894d` included, is identical. The v2 file still hashes to
`396c0d2d48a22eed`.

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
| output directory | `/Users/soko/Cerebros/nexus-a1-fixtures/a2r-run` — **its own**, a sibling of the calibration's `calib-run`. `run_a2r.py` refuses a directory holding another sweep's summary, and refuses to nest inside one. |
| summary | `a2r-summary.json` — never `calibration-summary.json` |
| soft launch threshold | **20 000 000 tokens.** Not a maximum: the sweep can finish above it by up to one row, and a largest-observed-row reservation bounds nothing about the next row. Recorded as `cap_is_soft: true`. |
| selection rule | **none is applied.** `freeze-calib-a2.md` §5 belongs to the calibration. `assess_a2r.py` refuses to summarise more than one ceiling. |

**The closed calibration's unresolved consumption stays outstanding.**
`c60/run-k1/attempt1/arms/baseline` has no usable usage record; that sweep's total is a lower
bound and its charge exceeded its cap by 560 844 tokens. A2-R does not resolve it, does not
cover it with a new allowance, and does not treat it as zero.

## Preflight

**`preflight-a2.py` cannot approve this run and is not used.** It asserts harness revision
`cbbaad64…`, the three `calib-config-*.json` digests, the v2 prompt digests, a historical
charge of 7 825 690 tokens, and **zero unresolved arm-runs in the calibration directory**. The
last cannot be satisfied and must not be: one calibration arm-run is unresolved on purpose and
no allowance was written for it. Gating A2-R on another sweep's accounting would either block
this sweep indefinitely or invite an allowance written to unblock it.

[`preflight-a2r.py`](preflight-a2r.py) validates what A2-R actually runs, and shows the
calibration's charge and its outstanding arm-run as a **note, not a gate**:

```bash
python3 preflight-a2r.py
```

It asserts both revisions, a clean tracked tree, the configuration's version, digest,
`max_turns`, `wall_clock_s`, corpus and prompt file, all four prompt digests, the schedule
digest, **the three v2 config digests and versions** (which A2-R never reads — which is why
they need a guard), no active virtualenv, `A2_PYTHON` set / executable / carrying pytest, the
forwarder, and that the output directory is A2-R's alone with nothing unresolved in it.

The pinned-interpreter check is an **early filter, not the authority**: outside the sandbox
`/usr/bin/python3` imports pytest 8.4.2 from `~/Library/Python/3.9/…`, a path the arm profile
does not grant — which is precisely why that interpreter answered `No module named pytest`
inside every v2 arm-run. The per-arm gate, inside the profile and under both shell startups,
is what settles it.

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
| execution is not credited to a non-event | `test_a2r.py` — `echo pytest`, a `No module named pytest` attempt, a run of another file, a whole-suite run, and a run issued before the last test edit all leave execution `unknown` |
| a mention is not an invocation | `test_a2r.py` — `test -n "$A2_PYTHON" && echo ready` is a mention; only the variable in command position is an invocation |
| every mutation is detected | 24 seeded mutations across `assess_a2r.py`, `run_a2r.py`, `run_calibration.py` and `score_compliance.py`, all killed |
| the preflight's own checks can fail | the pinned-interpreter check controlled against an interpreter without pytest, and against a pin that does not exist; the harness-revision check controlled by restamping it |
| the reporter reads the RUNNER's format | `test_a2r.py` — an integration test on `run_arms_isolated`'s real schema and directory layout (`scored.passed`, `result.num_turns`, the `patch.diff` sidecar), with a genuine functional failure as a negative control |
| replayed over records it did not invent | [`replay_v2.py`](benchmarks/agent/replay_v2.py) — the reporter over the **27 accepted v2 arm-runs** recovers **16 functional passes, 11 terminated at `max_turns`**, matching `report_calibration.py` and `diagnose_workflow.py` on the same records. Host-local (the scratch is outside the repository), so it is not in CI; it costs no tokens. v2 carries no `runtime_identity`, so all 27 come back with **no repair evidence**, which is correct. |

## Approval

- [ ] **The founder approves this record and the design it names.** Until this box is ticked,
      `run_a2r.py` is not to be invoked against a live forwarder.
- [x] `benchmarks/agent/a2r-config-45.json` at `calib-v3` created — **a new file, not an edit
      to any `calib-config-*.json`** — and its `config_digest` stamped above
- [x] `harness_revision` stamped above
- [x] `preflight-a2r.py` green on this host — every identifier asserted, forwarder listening
- [ ] the per-arm environment gate passing on a prepared arm — it runs at launch, inside the
      profile, and is the authority on the runtime; it refuses before spending

### The run, once approved

```bash
python3 preflight-a2r.py                       # immediately before launch; preserve its output
python3 benchmarks/agent/run_a2r.py /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run --ceiling 45
```

Then, and only then:

```bash
python3 benchmarks/agent/score_a2r_compliance.py /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run
python3 benchmarks/agent/assess_a2r.py /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run \
  --json benchmarks/agent/results-a2r.json
```

**No retry, no mid-sweep repair, no budget increase.** Every stop condition in
[`REGISTRATION-DRAFT-a2r.md`](benchmarks/agent/REGISTRATION-DRAFT-a2r.md) §6 ends the sweep and
the partial results are kept and reported as partial. `run_calibration.preserve_partial` moves
an interrupted attempt aside rather than letting a rerun overwrite it.

`score_a2r_compliance.py` was validated on a **copy** of the v2 ceiling-45 tree before any
spend: 12 of 12 arm-runs scored, and the reporter then read its artifact with every row
carrying a ratio and an unknown count instead of `not_scored`.
