# The four registered items, closed

These were registered open in `results-review-3.md` as prerequisites for held-out
registration. Each is implemented here and each is checked by executing it, not by reading
it: `verify_preparation.py`, five sections, no model invoked and nothing paid for.

All fifteen arm-runs recompute with **identical ratios and identical failure lists**, now
carrying every check's verdict.

## 1. One schedule, drawn once, saved before execution

`run_arms_isolated` built its order with `random.Random(SEED).shuffle(order)` — a fresh
generator, from the same constant, inside each run. The seed was recorded, which made the
result look reproducible. It was: reproducibly the same order everywhere. All eight saved
development runs record `baseline → nexus → notes`.

`schedule.py` draws one generator across every `(task, attempt)` pair, in a fixed enumeration
order, and writes the whole schedule to disk before the first arm runs. Execution reads it;
`order_for` raises rather than inventing an order for a pair that is not in the file, and
`load` refuses a schedule whose rows no longer match the digest recorded when it was frozen.

`counterbalance` reports how the draw actually came out — pairs, distinct orders, how often
each arm ran first, and whether every arm appears once per pair. It reports; it does not
correct. A seeded draw is not a balanced design and the record must not imply otherwise.

Verified: same inputs reproduce the schedule; a different seed changes it; one row per pair;
orders vary between pairs; the old per-run reseeding is re-run alongside and yields exactly
one order; an unscheduled pair is refused; an edited schedule is rejected.

## 2. Instrument errors are not arm outcomes

`_pytest` caught only `ET.ParseError`. Under this repository's own `.venv-sqlite` python,
whose `pyexpat` is broken, the resulting `ImportError` escaped the scorer and aborted the
whole record — and widening the `except` would have been worse: zero test cases, verdict
`absent`, `E1 = False`. An instrument that could not run would have been published as an arm
that wrote no regression test.

A failure of the measurement now produces `instrument_error` with its stage and detail, sets
that check to `unknown`, and leaves every other check in the record reportable. Three stages
are distinguished — `pytest_invoke`, `junit_read`, `pytest_exit` — plus a catch-all around
the probe so an unexpected fault anywhere in it lands in the same place.

Verified by injecting three failures and confirming each time that `E1` is `unknown` and not
`fail`, that the diagnostics survive, that the error is listed in `instrument_errors`, that
`P1` and `P2` still settle, and that the unknown is outside the compliance ratio.

**One thing this pass got wrong first, and the check caught it.** The initial rule read
pytest's exit code 4 as a usage error and therefore an instrument failure. Exit 4 is also how
pytest reports `file or directory not found` — which is the *legitimate* baseline verdict for
a test that does not exist on the fixture yet. Every absent baseline became an
`instrument_error`, and the counterexample suite failed on cases that had passed for two
reviews. The rule is now: exit 3 always, exit 4 only when the output does not say the test
was absent.

## 3. Every verdict retained, with its reason

A check used to be `True`, `False` or `None`. That cannot say *why*, and it cannot say *the
instrument broke*; both were being flattened into `False`, which is a statement about the arm.

Each check is now `{verdict, reason}` — `pass`, `fail`, `unknown`, `not_applicable` — with
`instrument_error` attached when one occurred, and the record carries `scorer_version`
(`a1-scorer-4`) because a ratio is not comparable across scorer versions. `recompute.py`
keeps the whole `checks` object rather than dropping it, so `recomputed.json` no longer
carries `checks: null`.

The counts sit beside the ratio, because the ratio hides its own denominator: `4/5` with one
unknown and `4/5` with one not-applicable are different measurements and neither is `4/6`.

```
run-dev-a1-attempt4 / baseline
  S1_touched_src                   pass            the patch changes a file under src/click/
  S2_paths_within_scope            pass            every changed path is within (src/click/, tests/, CHANGES.rst)
  S3_added_test_file_change        pass            the patch changes a file under tests/
  E1_regression_discriminates      pass            absent-or-passing on the fixture, fails as the task
                                                   predicts with the test hunks alone, passes on the patch
  P1_final_reply_opens_done        fail            the reply's first word is 'Confirmed'
  P2_content_delivered_before_edit not_applicable  the baseline arm is offered no prior-work memory
  compliance 4/5   unknown 0   not_applicable 1   instrument_errors 0
```

Verified by round-tripping a record containing all four states through JSON and confirming
the verdicts, reasons, diagnostics, scorer version and counts all survive, and that the
counts still agree with the checks after reloading.

## 4. Configuration is data, and the child environment is minimal

`PYTEST_PY` and `SOURCE_CLONE` were constants naming one session's scratchpad by its UUID.
The harness therefore worked on exactly one host on exactly one day — and worse, a deny for a
path that has moved looks, in the saved artifact, exactly like a deny that works.

`a1_config.py` loads `a1-config.json` (per host, gitignored; `a1-config.example.json` is the
committed template), validates it before anything runs, and returns **every** problem rather
than raising at the first point of use. The resolved configuration is recorded in each run's
artifact, so a reader can see what a figure was produced under without the harness.

`invoke()` used to hand each arm `dict(os.environ)` — the operator's whole shell: other
providers' keys, editor and shell settings, `CLAUDE_*` variables that change the runner's
behaviour, and whatever else was exported that day. None of it held fixed across arms, so
none of it belongs in a measurement. `child_env` builds from a declared allowlist of nine
names, each with its reason, plus the two the harness sets itself. On this host that is **8
names given to an arm against 60 on the host**. An allowlisted name that is unset is absent,
not invented. `env_record` writes the names that crossed and never a value — one of them is a
key.

The dead `NET` block is gone rather than kept as decoration. Its comment claimed it kept the
arms identical in the respects that are not the boundary; nothing merged it into any
environment, so it described behaviour the code did not have.

Verified: the committed configuration passes; a missing path, a file where a directory is
required, a non-executable interpreter and a clone without `.git` are each named; four bad
values are reported in one pass; `require()` refuses to proceed. Then — because cutting 60
names to 8 is the change least likely to be caught by reading it — **the runner is actually
started under the minimal environment**, against a loopback stub upstream, and reaches
`/anthropic/v1/messages` through the forwarder. No paid call.

## What was run

| | result |
| --- | --- |
| `verify_preparation.py` | 5 sections, all checks hold |
| `test_compliance_counterexamples.py` | all counterexamples rejected, compliant shapes accepted |
| `recompute.py` | 15 arm-runs; ratios and failure lists identical to the committed baseline; 0 unknowns, 0 instrument errors |
| `validate_boundary.py` | all controls hold on all three arms, sidecars live |
| Nexus suite | 333 passed, 2 skipped |

**One failure I cannot account for.** On one execution of the Nexus suite during this work,
the summary line read `1 failed, 332 passed, 2 skipped`. I did not capture the test name —
the output was piped through `tail -1` — and the suite has since run clean **14 consecutive
times**, including immediately after a boundary-validation run, which was my hypothesis for
an interaction. So it is neither diagnosed nor dismissed: it is one unexplained failure in
fifteen executions, recorded here because a summary count that says `333 passed` fifteen
times would not be the whole truth.

## Still deferred, deliberately

**Required-evidence completeness** (`protocol-a1` §7b) is *not* implemented and is deferred
from A1. Implementing it means a rubric that declares, per task, the facts an answer must
rest on, plus an instrument that reads a patch and a trace and decides whether each was
obtained — neither exists, and inventing either after seeing results is the failure mode this
whole protocol is built to avoid. The first evaluation reports functional correctness,
requirement compliance, delivered context, usage and termination, and says plainly that
evidence completeness is unmeasured.
