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

| fix | pre-fix parent | subject | acceptance tests | src touched | pre-fix failures | post-fix passed | pytest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `6de2121518` | `1339fd3323` | Treat `UNSET` in a `default_map` as absent | `test_defaults.py` | 1 file | **1** | 34 | 9.1.1 |
| `1b0e19f505` | `499bbeea64` | Don't include envvar in error hint when envvar not configured | `test_options.py` | 1 file, 2 lines | **1** | 144 | 9.1.1 |
| `8d7f03dac8` | `ef11be6e49` | Treat empty `auto_envvar` as `None` | `test_options.py` | 1 file, 5 lines | **1** | 110 | 9.1.1 |
| `ebcd548d50` | `7f7bbe4569` | Options setting both `is_flag=False` and `flag_value` | `test_options.py` | 1 file, 6+/3- | **2** | 537 | 9.1.1 |
| `762c97eef7` | `8929d39278` | Double-bracketing of choices in the synopsis | `test_basic.py` | 1 file | **2** | 90 | 8.4.2 |
| `f58ca3e814` | `420c8fb44e` | `copy`, `deepcopy` and `pickle` of `Sentinel` members | `test_utils/test_sentinel.py` | 1 file | **3** | 10 | 9.1.1 |
| `546f2851f4` | `ae46cfd6bc` | Callable `flag_value` instantiated when used as a default | `test_defaults.py`, `test_options.py` | 1 file | **7** | 584 | 9.1.1 |
| `b67832c216` | `8c1a0a7abb` | Parsing when a parameter is named `help` | `test_basic.py`, `test_info_dict.py`, `test_options.py` | 1 file, 44+/2- | **10** | 776 | 9.1.1 |
| `0f71fe771c` | `c943271a26` | Dual-option arbitration respecting explicit defaults | `test_options.py` | 1 file | **18** | 637 | 9.1.1 |

Pre-fix failure count is the **boundedness measure**. The set is ordered by it deliberately:
the rows above the middle are single-behaviour fixes, and the last two are broad enough that
a partial patch could pass some checks and fail others. Whether a task is admitted at 18
failures is a selection-rule decision (§5), not a fixture one — the fixture works either way.

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

## 5. Still to be fixed by the selection rules

- The boundedness admission bar (the `pre-fix failures` ceiling).
- The disjoint development / harness-validation / held-out split.
- The **useful / useful-support / unnecessary / outdated** assignment. This is **not a
  property of a commit** and cannot be assigned in this table: it is a property of the
  *pairing* of a task with the captured memory corpus, and is therefore fixed when the
  capture policy runs, not here. Assigning it from the commit subject would be assigning the
  answer.

## 6. Unverified — blocking registration

1. **Capture provenance.** No capture policy exists yet, so no memory corpus exists, so no
   task above has a memory pairing. `protocol-a1` §6 steps 1 and 2 are unexecuted.
2. **Budget enforcement.** See `protocol-a1` §4. Wall-clock enforcement is available to the
   harness; a hard in-run spend cap is not yet established.
3. **Public-history exposure.** Click is a widely-mirrored public repository and these fixes
   predate the model's training cutoff. Isolating the checkout prevents the agent from
   *retrieving* the fix; it does not establish that the model has not **memorised** it. This
   is a limitation of the whole source, not of any row, and it is recorded in every result
   rather than mitigated — see `protocol-a1` §13. It bears on all arms roughly equally, which
   is why it threatens the absolute numbers more than the between-arm comparison, but "roughly"
   is an assumption and is labelled as one.
