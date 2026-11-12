# A1 task manifest — `pallets/click` (**draft**, not registered)

Source A of [`protocol-a1.md`](protocol-a1.md). **No scored run may be executed against this
manifest.** It records which candidate tasks have a working fixture, which do not and why,
and the three things still unverified in §6.

Survey clone at `pallets/click` `6aabf09`. Candidates were drawn from commits touching both
`src/click/*.py` and `tests/*.py` whose subject names a fix, then filtered by §3 and put
through the fixture check in §2.

## 1. Why this repository

Click is pure-stdlib with no runtime dependencies, and its tests need only `pytest`. Its
`CliRunner` exposes output, exit code and exception for a command invocation, so a task
outcome is a value to compare rather than a judgement to make. The task surface — argument
parsing, defaults, environment variables, command behaviour — is the surface A1 wants.

**One repository supports conclusions about this workload and no more.** It establishes
nothing about repositories in general, and that limit is carried into every reported figure.

## 2. The fixture check, and what it measured

Each task is a **pinned pre-fix commit** — the fix commit's first parent — plus the test
files that commit changed, taken as hidden acceptance checks. Per `protocol-a1` §5, tasks are
not forced onto one snapshot; each carries its own pair.

```
tests   := git diff --name-only <parent> <fix> | grep '^tests/.*\.py$'
pre     := checkout <parent>; checkout <fix> -- $tests; pytest $tests   # must FAIL
post    := checkout <fix>;                              pytest $tests   # must PASS
```

Deriving `tests` from the parent diff is not incidental. Reading them from
`git show --name-only` returns **nothing for a merge commit**, which silently produced a
"0 failures pre-fix" row for `ebcd548` — a vacuous task by measurement error rather than by
construction. Two candidates are merges and only the diff form handles them.

Every row below was run. `pre-fix failures` is the measured count at the parent with the
post-fix tests applied; a row could not be listed without it.

| fix | pre-fix parent | subject | acceptance tests | failing instances | failing **functions** | src hunks | post-fix passed | pytest |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `1b0e19f505` | `499bbeea64` | Don't include envvar in error hint when envvar not configured | `test_options.py` | 1 | **1** | 1 | 144 | 9.1.1 |
| `8d7f03dac8` | `ef11be6e49` | Treat empty `auto_envvar` as `None` | `test_options.py` | 1 | **1** | 1 | 110 | 9.1.1 |
| `ebcd548d50` | `7f7bbe4569` | Options setting both `is_flag=False` and `flag_value` | `test_options.py` | 2 | **1** | 2 | 537 | 9.1.1 |
| `6de2121518` | `1339fd3323` | Treat `UNSET` in a `default_map` as absent | `test_defaults.py` | 1 | **1** | **7** | 34 | 9.1.1 |
| `762c97eef7` | `8929d39278` | Double-bracketing of choices in the synopsis | `test_basic.py` | 2 | **2** | 2 | 90 | 8.4.2 |
| `f58ca3e814` | `420c8fb44e` | `copy`, `deepcopy` and `pickle` of `Sentinel` members | `test_utils/test_sentinel.py` | 3 | **2** | 1 | 10 | 9.1.1 |
| `546f2851f4` | `ae46cfd6bc` | Callable `flag_value` instantiated when used as a default | `test_defaults.py`, `test_options.py` | 7 | **4** | 3 | 584 | 9.1.1 |
| `b67832c216` | `8c1a0a7abb` | Parsing when a parameter is named `help` | `test_basic.py`, `test_info_dict.py`, `test_options.py` | 10 | **5** | 4 | 776 | 9.1.1 |
| `0f71fe771c` | `c943271a26` | Dual-option arbitration respecting explicit defaults | `test_options.py` | 18 | **6** | 5 | 637 | 9.1.1 |

### Failure count alone is a weak complexity measure, and this table shows why

The draft ordered the set by raw failure count and called it "the boundedness measure". That
was wrong, and the correction is visible in the numbers above rather than argued from
principle.

**Parametrisation multiplies one defect into many failures.** Collapsing parametrised ids to
their test function changes the picture at both ends: `ebcd548` falls from 2 failures to **1**
function, `f58ca3e` from 3 to 2, and `0f71fe7` from 18 to **6** — a threefold inflation in the
row the draft called the most complex.

**And failure count can run opposite to the change's dispersion.** `6de2121` has the *smallest*
failure count in the set, one function, yet the *largest* source change by hunk count — **7
separate hunks**, more than the 5 of the 18-failure row. A task can be one observable symptom
and a widely dispersed fix. Ordering by symptoms would have put it first and called it the
simplest task in the set.

No single column is the measure. The admission rule in §5 reads at least failing **functions**
and source **hunks** together, and neither is a proxy for "how hard is this for an agent" —
which is not established by any static property of a diff and is not claimed here.

## 3. Excluded, by class

Excluded before the fixture check, per `protocol-a1` §5's bar on external services and
platform-specific infrastructure:

| Class | Why |
| --- | --- |
| Pager and TTY races | Timing-dependent; some are marked flaky in Click's own suite |
| Windows-specific error reporting | Platform infrastructure |
| `fish` / shell completion | Requires an external shell |
| `readline` line-wrapping | Terminal behaviour |
| `pdb` interaction | Debugger infrastructure |
| Changelog-, docs- and skip-only commits | No behavioural change to verify |

Rejected **after** measurement, for boundedness rather than class:

- `9caedb9206` — "Fix reconciliation of envvar with `default`, `flag_value` and `type`".
  Fixture works; **64 pre-fix failures across 4 test files, 161 changed source lines**. That
  is a reconciliation of a subsystem, not a bug fix, and a task where partial credit would
  dominate the score. Recorded rather than dropped, so the exclusion is auditable.

## 4. Two runner facts the fixture depends on

Both were found by the check failing, not by reading documentation.

1. **`pytest` must be pinned per task, not globally.** Under `pytest` 9.1.1, `762c97e` and
   `9caedb9` fail at *collection* with `PytestRemovedIn10Warning: Passing a non-Collection
   iterable to parametrize is deprecated` — Click's older test files, not Click's code. Both
   produce clean fail→pass under `pytest` 8.4.2. A single pinned runner across all tasks
   would have silently excluded the older half of the window, and the exclusion would have
   looked like a Click defect. The pinned version is part of each task's fixture.
2. **`pytest-randomly` is in Click's own test group and must be disabled.** Every run above
   used `-p no:randomly`. Left on, test order varies per run and adds noise to a measurement
   that already cannot be deterministic.

Interpreter for every run above: **CPython 3.14.7**. Commits older than the current window
may need an interpreter contemporary with them; that is a per-task fixture property too, and
is unmeasured outside the rows listed.

## 5. Fixed by the selection rules — see `freeze-heldout-a1.md` §1

The first two are **now fixed**; the third still cannot be fixed here, for the reason it
always gave.

- ~~The boundedness admission bar.~~ Registered: at most 6 failing functions and at most 7
  source hunks, calibrated against `d3` — 1 function across 7 hunks — which all three arms
  completed. It excludes none of the six remaining candidates, which is the residue of the
  class exclusions in §3 rather than a slack bar.
- ~~The disjoint split.~~ Registered: development `d1`–`d4`, capture `c1`–`c2`, held-out
  `h1`–`h4`, chronologically ordered so every capture fix is an ancestor of every held-out
  pre-fix commit (verified, 8 of 8).

The original wording, for the record:

- The boundedness admission bar. It reads failing **functions** and source **hunks**
  together; raw failure count is not the measure, for the two reasons the table above
  demonstrates.
- The disjoint development / harness-validation / held-out split.
- The **useful / useful-support / unnecessary / outdated** assignment. This is **not a
  property of a commit** and cannot be assigned in this table: it is a property of the
  *pairing* of a task with the captured memory corpus, and is therefore fixed when the
  capture policy runs, not here. Assigning it from the commit subject would be assigning the
  answer.

## 6. Unverified — blocking registration

1. **Capture provenance.** The policy now exists (`capture-policy-a1.md`) and the held-out
   capture procedure is registered (`freeze-heldout-a1.md` §2), but **no held-out memory
   corpus exists yet and none may**: it is produced by a prior-session run on `c1`/`c2` that
   never sees `h1`–`h4`. `protocol-a1` §6 steps 1 and 2 remain unexecuted for the held-out
   set, and the task-to-memory pairing — useful / support / unnecessary / outdated — is
   assigned when that corpus is frozen, never here.
2. **Budget enforcement.** See `protocol-a1` §4 and `freeze-heldout-a1.md` §3. Wall-clock
   enforcement (600 s) and an in-run turn ceiling (30) are registered and verified. A hard
   in-run *spend* cap is still not established, and none is claimed: spend is reported after
   the fact, in tokens.
3. **Public-history exposure.** Click is a widely-mirrored public repository, so prior
   exposure is **possible**. That these fixes predate the model's training cutoff is *not*
   established — no training window is published for the model in use — and that claim is
   withdrawn. Isolating the checkout prevents the agent from *retrieving* the fix; it does
   not establish that the model has not **memorised** it. This
   is a limitation of the whole source, not of any row, and it is recorded in every result
   rather than mitigated — see `protocol-a1` §13.

   **How it affects the arms is unknown, and the draft's "bears on all arms roughly equally"
   is withdrawn.** Exposure may help every arm, or it may interact with an arm — a memory that
   names the right subsystem could make recall of a memorised fix more likely in arm 2 than in
   arm 1, which would show up as a Nexus benefit that is nothing of the kind. Equal effect is
   not the conservative assumption; it is the convenient one. Nothing here measures which
   holds, so no result may lean on either, and a between-arm difference on a memorised task
   carries this caveat explicitly.
