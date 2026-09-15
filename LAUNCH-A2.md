# A2 calibration — the frozen execution identity

Deliberately **outside the measured tree**. `product_revision` is the last commit touching
`src/nexus_memory` and `harness_revision` the last touching `benchmarks/agent`, so a record
kept in either would move the identifiers it records every time it was written. Nothing here
is read by the harness; it is what a later reader compares an artifact against.

## The checkout

The execution checkout is **the HEAD that `preflight-a2.py` approved**, which it prints:

```
run from, and do not change, checkout <sha>
```

A full commit identifier, not a branch name — `docs/a2-calibration` moves. It is reported at
preflight rather than pinned in advance here, because a pinned one invalidates itself: every
commit to a root-level note moves HEAD while changing nothing a row records. What is *frozen*
is the measured identity below — the two revisions and the digests — which is what a row
actually records and what decides whether two rows may be pooled. Detaching at a commit does
not by itself make the tree match it.

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
| an **activated virtualenv** | **unchanged** | **empty** (not a file) | the PATH check only |

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
| `harness_revision` | `9e5e1e040094b7d44e5712d3028afa5af3f45776` |
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

`benchmarks/agent/PRELAUNCH-VERIFICATION.md` stamps **`d39f884e1b62332919db3ad7d2f62dd189b24dcf`**,
the revision it tested, and all five checks pass there. `harness_revision` is one commit ahead
of that because committing the report is itself a commit under `benchmarks/agent`. A report
cannot contain its own future hash, so what settles it is the intervening diff:

```bash
git diff --name-only d39f884 9e5e1e0 | grep -vE 'PRELAUNCH-VERIFICATION.md|verification-logs/'
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

## Launch from a shell with no virtualenv activated

`PATH` is on `a1_config.ENV_ALLOWLIST` by necessity — it is what resolves the runner, git and
the interpreter — so **whatever `python3` means in the shell that launches the sweep is what
`python3` means inside every arm's sandbox.**

This is not hypothetical. The 2026-09-14 launch attempt was made from a shell with the
project's `.venv` activated, which put `.venv/bin` first on `PATH`. That prefix is not among
the arm profile's readable subpaths, so the interpreter died at startup:

```
Fatal Python error: Failed to import encodings module
ModuleNotFoundError: No module named 'encodings'
Current thread 0x00000001f6306080 (most recent call first):
  <no Python frame>
```

The per-arm gate refused on `interpreter imports the intended checkout` and
`documented test command executes a test`, the driver stopped the sweep, and **nothing was
spent** — no `launched.json`, no trace, no model call, the ledger unmoved at 7 825 690.

The gate working is not a reason to rely on it here. Had `.venv` happened to be *readable*
inside the boundary, the arms would have run a different interpreter from the one A1 measured
and nothing would have said so. The intended interpreter on this host is
`/opt/homebrew/bin/python3`; `preflight-a2.py` checks for both an active `VIRTUAL_ENV` and the
resolution of `python3`, and refuses.

```bash
deactivate 2>/dev/null; python3 preflight-a2.py    # must print PREFLIGHT OK
```

## Start the forwarder first, and leave it up

`RUNBOOK-a1.md` §0: every arm reaches the model through `model_forwarder.py` and through
nothing else, because the sandbox denies all other egress.

```bash
python3 benchmarks/agent/model_forwarder.py &
```

**Nothing used to check this.** On 2026-09-14 it was not running. Every one of 36 arm-runs
returned `API Error: Connection refused`, terminated `env_fail`, and recorded an honest zero
usage block — and because `env_fail` is a per-arm classification rather than a runner failure,
every runner exited 0, nothing was unresolved, the token accounting was correct at zero, and
the driver wrote `"stopping_reason": "completed"` and exited 0 after two hours of measuring
nothing.

Two guards now exist. The arm environment gate checks the forwarder is reachable **from inside
the boundary** — the paired positive half of `egress denied`, a TCP connect only, since an HTTP
request there would be a model call. And the driver refuses to call a row complete when not one
of its arm-runs is scored, so an instrument that is down costs one row rather than twelve;
`scored_arm_runs` and `excluded_arm_runs` sit beside the token counts in every summary.

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
