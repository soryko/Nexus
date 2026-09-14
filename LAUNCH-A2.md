# A2 calibration — the frozen execution identity

Deliberately **outside the measured tree**. `product_revision` is the last commit touching
`src/nexus_memory` and `harness_revision` the last touching `benchmarks/agent`, so a record
kept in either would move the identifiers it records every time it was written. Nothing here
is read by the harness; it is what a later reader compares an artifact against.

## The checkout

The execution checkout is **the commit that introduced this file**:

```bash
git log -1 --format=%H -- LAUNCH-A2.md
```

A full commit identifier, not a branch name: `docs/a2-calibration` moves. Detaching at that
commit does not by itself make the tree match it.

**Do not edit, commit, rebase or update dependencies in the execution checkout during the
sweep.** An earlier version of this note justified that by saying a local edit under
`benchmarks/agent` changes `harness_revision`. **That is wrong, and wrong in the dangerous
direction.** `identity.rev()` runs `git log -1 --format=%H -- <path>`, which reads commit
history and not the working tree. Three cases, and no single guard covers them:

| what changes | `harness_revision` | `git status --porcelain` | caught by |
| --- | --- | --- | --- |
| a commit under `benchmarks/agent` | **changes** | clean | the revision check |
| an **uncommitted edit** to a tracked file there | **unchanged** | ` M …` | the clean-tree check only |
| an edit to `calib-config-*.json` | **unchanged** | **empty** (gitignored) | the config digest only |

The middle row is the one that matters: rows written after such an edit record an identical
harness revision while having been produced by different code, so the identity check cannot
tell them apart and nothing downstream ever will. **The recorded revision is not a substitute
for leaving the checkout alone.** The bottom row is why `git status --porcelain` being empty is
not sufficient either — the host-local configurations are ignored files and are invisible to it.

`preflight-a2.py`, at the repository root, checks all three and the ledger together:

```bash
python3 preflight-a2.py && echo "safe to launch"
```

## Identifiers as frozen, 2026-09-14

| | |
| --- | --- |
| `product_revision` | `2cd531f9d7c274a065533e58ba3fde8582f8c269` |
| `harness_revision` | `19b843e437f2b1bf68080ae4e38f373f1c3d7715` |
| `config_version` | `calib-v2` |
| `schedule_digest` | `0a352b82ced14f10e85d50d17989ac38e02d4d9582c032ed743a75554a6a5d97` |
| `corpus_digest` (all three ceilings) | `9ae2a9f268dd894d` |

| ceiling | `config_digest` | `max_turns` |
| --- | --- | --- |
| 30 | `69df5d49a39142c4` | 30 |
| 45 | `396c0d2d48a22eed` | 45 |
| 60 | `b64cc9c137544b7a` | 60 |

| task | `prompt_digest` |
| --- | --- |
| k1 | `6bbe10c01d7e520d` |
| k2 | `d47e3eaa44a8d22c` |
| k3 | `905bf3bd43df8756` |
| k4 | `6fd3cb4335a4c1dd` |

The per-ceiling configurations are host-local (`benchmarks/agent/calib-config-*.json` is
gitignored) and carry absolute paths of this host. The digests above are what makes them
identifiable anyway: a configuration that hashes to one of these is the one that was frozen.

## Verification evidence

`benchmarks/agent/PRELAUNCH-VERIFICATION.md` stamps **`401deb69f61642216ab6a3d59985b8d51196b2df`**,
the revision it tested, and all five checks pass there. `harness_revision` is one commit ahead
of that because committing the report is itself a commit under `benchmarks/agent`. A report
cannot contain its own future hash, so what settles it is the intervening diff:

```bash
git diff --name-only 401deb6 19b843e | grep -vE 'PRELAUNCH-VERIFICATION.md|verification-logs/'
```

That is empty — **no executable code, configuration or prompt differs between the verified
revision and the frozen one.** Re-verifying to make the stamp match would only produce another
report whose stamp is one commit behind again.

## The ledger the sweep continues from

**Do not start in an empty scratch directory.** The budget is 36 000 000 tokens for the whole
calibration, and 7 825 690 of it is already spent or assumed:

| | tokens |
| --- | ---: |
| measured, v1 (quarantined, not pooled as evidence — but spent) | 6 425 690 |
| written allowance, k4/baseline v1 | 1 400 000 |
| **charged** | **7 825 690** |
| **remaining against the cap** | **28 174 310** |

Scratch: `/Users/soko/Cerebros/nexus-a1-fixtures/calib-run`. The v1 rows live there under
`v1-c30-unrepaired-sandbox/`, and the accounting globs any top-level directory precisely so a
quarantining rename cannot drop them. Running into a fresh directory would show a zero ledger
and silently grant a second full budget.

`consumption_certain` is **false** while the k4 allowance is carried; it is a deliberately high
assumption, not a measurement. See `benchmarks/agent/freeze-calib-a2.md` §4.

## The invocation

From the frozen repository root, after `preflight-a2.py` exits 0:

```bash
NEXUS_A1_ENV_GATE=1 python3 benchmarks/agent/run_calibration.py \
  /Users/soko/Cerebros/nexus-a1-fixtures/calib-run \
  --ceilings 30,45,60
```

`NEXUS_A1_ENV_GATE=1` is the default and is stated anyway, because the variable that turns the
per-arm gate off (`=0`) exists for re-executing A1's frozen runs, and a sweep that silently
inherited it from a shell would spend without the gate having run.

## At closeout, the selection rule as registered

§5 was written before any number exists and is applied exactly: only complete, compatible
ceilings are eligible; **fewer than two eligible ceilings means no selection**; no qualifying
ceiling means **no selection**, not a larger grid. This calibration selects a ceiling. It does
not produce a claim that memory, or any arm, is better — §2 and §9 forbid that, and the corpus
is deliberately unmatched to k1–k4.

## What is still true at freeze time

The sweep has not been relaunched. The grid is 30/45/60 with the registered workload, the
selection rule in §5 was written before any number exists, and nothing here authorises a
different workload, a larger budget or a retrieval change.
