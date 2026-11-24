# A1 held-out registration, revision 2 — 2026-09-12

Supersedes exactly one thing in [`freeze-heldout-a1.md`](freeze-heldout-a1.md): the
definition of the **functional-correctness instrument** (§4 figure 1). Everything else in that
document — the task set, the split, the arms, `n = 3`, the ceilings, the schedule and its
digest, the analysis rules, the exclusion rules and the pre-registered negative result —
**stands unchanged**. The superseded revision stays in the tree, as its own rule requires.

**No arm-run had executed when this was written.** Not one held-out task had been run, the
capture run had not been started, and the corpus did not exist. Nothing here was chosen with a
result in view, because there were no results.

## 1. What was wrong

`h2`'s hidden acceptance checks **could not pass on any tree, including the tree at its own
fix commit**.

`tests/test_basic.py` at `762c97eef7` passes a `chain` iterable to `pytest.mark.parametrize`.
Under the pinned pytest 9.1.1 that raises `PytestRemovedIn10Warning`, and Click's own
`filterwarnings = ["error"]` turns it into an error during **collection** — so no assertion in
the module is ever evaluated. The failure has nothing to do with the task: it is an
incompatibility between the pinned test runner and a call elsewhere in the same file.

Measured, checks staged from each fix and run against the tree at that fix:

| task | as registered (r1) | verdict |
| --- | --- | --- |
| h1 | 637 passed | passes |
| **h2** | **1 error** | **cannot pass** |
| h3 | 776 passed | passes |
| h4 | 10 passed | passes |
| c1 | 537 passed | passes |
| c2 | 584 passed | passes |

The existing controls could not see this, and that is the more important half. §11.1 requires
the checks to **fail** on an unpatched tree; h2 satisfied it, for the wrong reason — the checks
failed because they cannot succeed. §11.4 asks whether the fix is reachable from the checkout,
which it is not. Neither asks whether the checks can pass **with** the fix, so a task that
nothing could solve was indistinguishable from a task that had not been solved yet. Left
standing, h2 would have scored every arm zero across nine arm-runs and read, in every reported
figure, as a task no arm could do.

## 2. What changed

Two things, both in `build_fixture.py`.

**A third control, `fix-oracle`.** The hidden checks are run against the tree at the fix
commit and must pass. `main` exits nonzero when they do not, and prints the collection error
inline. This is what found the defect and it now runs for every task on every build.

**The functional check runner is versioned, and takes one flag.**

    FUNCTIONAL_SCORER_VERSION = "a1-functional-2"
    PYTEST_IGNORE = ("-W", "ignore::pytest.PytestRemovedIn10Warning")

`a1-functional-1` is the unflagged runner every development figure was produced under.
`a1-functional-2` adds that one warning class, demoted from error to ignored. The version is
recorded in every `no_model`, `fix_oracle` and `scored` block, and in each run's `records.json`
beside `score_compliance`'s own version — they are two different instruments and a reader needs
both numbers.

**It applies to every task, not to h2.** A flag applied only where it is needed is an
instrument that varies with the thing it measures.

## 3. Why this is a repair and not a thumb on the scale

The change was measured on all ten tasks before adoption, not argued from the class name:

| | a1-functional-1 | a1-functional-2 |
| --- | --- | --- |
| d1 | 110 passed | 110 passed |
| d2 | 144 passed | 144 passed |
| d3 | 34 passed | 34 passed |
| h1 | 637 passed | 637 passed |
| **h2** | **1 error** | **90 passed** |
| h3 | 776 passed | 776 passed |
| h4 | 10 passed | 10 passed |
| c1 | 537 passed | 537 passed |
| c2 | 584 passed | 584 passed |

Bit-identical on eight of nine. Three further checks, each run rather than reasoned:

- **h2 is still non-vacuous.** Its §11.1 no-model control goes from `1 error` to
  `2 failed, 88 passed` — it still fails without a patch, which is what the control is for. A
  repair that made the task pass unpatched would have been worse than the defect.
- **d4 is untouched.** d4's whole subject is an unregistered pytest marker, whose mechanism is
  `PytestUnknownMarkWarning` — a different class. Checked directly against a minimal
  reproduction with `filterwarnings = ["error"]`: the marker still errors at collection with
  the flag set, exactly as without it.
- **No development figure moves.** d1–d3 are identical above; d4's is unaffected by the
  previous point. Re-scoring the saved development runs under `a1-functional-2` would return
  what they already report.

## 4. What this does not fix

The flag hides one deprecation from the **scorer's** pytest invocation. It says nothing about
the agent's own test runs inside its checkout, which are the agent's business and are not
scored by it. An arm that trips the same deprecation while working sees the same error the
project's configuration produces, as it should.

It also does not make h2 a better task than it was. h2 remains a two-function, two-hunk
synopsis-rendering fix, admitted by the boundedness bar in r1 §1 and unchanged by any of this.

## 5. What stands from revision 1

Task set and split; the chronological capture/held-out ordering and its 8 ancestry checks;
arms `baseline`/`nexus`/`notes` with arm 4 not running; `deepseek-flash`; `n = 3`; 36 arm-runs
plus 2 capture runs; `--max-turns 30`; 600 s wall clock; the schedule frozen at digest
`1a960a7465fa36a0` with seed `20260912`, and its reported counterbalance imbalance left
uncorrected; every reported figure in §4; the analysis and exclusion rules in §5; the
deferrals in §6; and the limitations in §7.

Registered 2026-09-12, after revision 1 and before any capture or arm-run.
