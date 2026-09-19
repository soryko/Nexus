# A2-R: the three failures and the four ceiling-truncated passes

**One bounded pass over saved records. No model invoked, nothing re-run, nothing edited.**
Produced by [`diagnose_a2r_runs.py`](diagnose_a2r_runs.py), which re-uses
`diagnose_workflow.describe` and asserts every record against `LAUNCH-A2R.md`'s own
Identifiers table (all twelve match; none refused).

Five limits are carried, four inherited and one added because this pass reads intermediate
states:

> Issue order is not execution order. An errored command may still have written. A patch is a
> **final** state, so when it first became correct is not inferred. A completion message is a
> statement the model made, not a verification. **And a prototype is not a deliverable**: a
> monkeypatch that prints the right answers shows the mechanism was found, not that the
> repository was changed.

The five completed passes appear only as **descriptive context**. They are not controls: one
attempt per cell, four tasks, no randomisation over anything that would make them one.

---

## 1. The allocation

| | calls | src edits | after last src edit | pytest | repro runs | reference hunts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **3 failures** | 147 | **0** | — | 4 | 17 | 13 |
| **4 passing, truncated** | 205 | 9 | 53 (**25%**) | 20 | 21 | 4 |
| 5 completed (context) | 222 | 5 | 83 (**37%**) | 35 | 22 | 5 |

Per run:

| cell | outcome | calls | last src edit | after | test edits | pytest | repro | ref hunts | wall s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| k3/nexus | fail | 50 | **none** | — | 0 | 0 | 3 | 1 | 197.6 |
| k4/baseline | fail | 50 | **none** | — | 0 | 3 | 7 | 2 | 337.5 |
| k4/notes | fail | 47 | **none** | — | 0 | 1 | 7 | **10** | 385.3 |
| k1/nexus | pass | 48 | **45 of 48** | 2 | **0** | 1 | 7 | 1 | 164.5 |
| k3/baseline | pass | 55 | 35 | 19 | 1 | 8 | 3 | 2 | 196.4 |
| k3/notes | pass | 51 | 32 | 18 | 1 | 6 | 6 | 0 | 159.6 |
| k4/nexus | pass | 51 | 36 | 14 | 2 | 5 | 5 | 1 | 314.4 |

**No failure wrote to `src/` even once.** That is A1's shape and v2's shape again, on a third
task set, under the repaired sandbox — and this time it is *not* a diagnosis failure in at
least one of the three.

---

## 2. The failures, one at a time

### k4/baseline — the mechanism was found, validated, and never applied

The strongest single finding in this pass.

| calls | what happened |
| --- | --- |
| 0–13 | repository investigation: `help_option_names`, `get_help_option`, `iter_params_for_processing`, the parser |
| **14** | a compound command containing `cat > /tmp/doesnotexist 2>/dev/null` **hung for 120 s and was moved to the background**. `/tmp` is denied; the redirect suppressed the error. |
| 15–16 | `pkill` to clear it; then `timeout: command not found`, exit 127 |
| 17 | reproduction succeeds — `--my-help` → `b`, `-a` → `b`, `-a -b` → `b`: the defect, exactly as the prompt describes it |
| 18–44 | more source reading, two host searches for another copy of click, a monkeypatched order trace |
| 45 | `exp_cache.py` — caching the generated help option fixes `--my-help`, not `-a` |
| **46** | `exp_both.py` — cache **plus** an ordering change. Output: `['--my-help'] → Usage: …`, `['-a'] → 'a'`, `['-a','-b'] → 'b'`. **All three behaviours the task names, correct.** |
| 47–49 | back to reading `src/click/core.py`. Ceiling. |

It had the fix, in a file called `exp_both.py`, four calls before the ceiling, and never
transcribed it into `src/click/core.py`. **Classification: implementation prototyped outside
the deliverable and never applied.** Not unresolved diagnosis; not reverted; not obstructed
at the moment of failure — though ~120 s and three calls were lost to obstruction earlier.

### k4/notes — spent its budget comparing against an upstream copy

**10 of 47 calls (21%) are reference hunts**, the most in the sweep. At call 27 it found a
released Click under `/opt/homebrew/Cellar/aider/0.86.2/libexec/lib/python3.12/site-packages/click`
— readable because `/opt/homebrew` is a system read root — and spent calls 28–42 reading that
version's `core.py` and `decorators.py` and running two scripts that `sys.path.insert` it to
compare behaviour. Zero source edits, one pytest invocation, no final message at all. Longest
wall clock in the sweep at 385.3 s of 600.

**Classification: unresolved diagnosis, with a fifth of the budget outside the repository.**
The comparison it was running is a legitimate way to find a fix; it did not finish it.

### k3/nexus — unresolved diagnosis, then off the edge of the map

Calls 0–7 retrieval (6 nexus calls), 8–14 source reading, 15–16 two reproduction scripts,
17–40 more reading and a third probe. It never proposed a change. From **call 41 of 50** it
left the checkout entirely: `find /Users/soko`, `find / -name "click-*.whl"`, the pip cache —
and then found and read A1's capture and held-out arm-runs through the macOS cache shadow,
disassembling `core.cpython-39.pyc` files for their marshal hashes. Nine calls, 18% of its
budget, and nothing came back: the shadow holds 221 `.pyc` files and no source, no tests and
no checks. **That path is denied as of this branch** and probed every run; see the boundary
commit and [`CLOSEOUT-a2r.md`](CLOSEOUT-a2r.md) §6.

**Classification: unresolved diagnosis.** Zero pytest invocations in the whole run.

### What none of the three is

- **Not implementation attempted then reverted.** No `src/` edit and no source-state mutation
  in any of the three: `git stash`/`checkout` counts are zero.
- **Not insufficient evidence.** All three traces are complete, carry an envelope, and have no
  recorded evidence gaps.
- **Not operational obstruction, except in k4/baseline and even there not at the end.**
  Obstruction signals appear across the sweep and do not separate the groups — `no_module`
  in 2 of 3 failures and 7 of 9 passes, nonzero exits 3 against 7, permission denials 0
  against 2. The one substantial obstruction event is k4/baseline's 120 s hang, and it cost
  time that run did not run out of: it ended on the turn ceiling with 44% of its wall clock
  unused.

---

## 3. The four passes that ended at the ceiling

### k1/nexus — the required test work never started

Functional pass; **no test added**; `a1-scorer-5` 2/6. One source edit at **call 45 of 48**,
two calls after it. Its last message: *"The fix produces the expected behavior. Now let me run
the full existing test suite to check for regressions."* It was cut off at the start of
verification, before the tail's second requirement — *extend the existing test suite to cover
it* — had been touched at all. Seven reproduction scripts survive in the patch, un-cleaned.

**This is the run that makes a hidden-check pass uninformative on its own.** It satisfies the
scorer that matters least to the task as written.

### k3/baseline — deliverable complete at call 45; ceiling at 55

Source at 34–35, test added at 42, the added test named and run at 43, a discriminating
control at 44 (revert the fix, watch it fail), the broad suite at 45. Everything the tail asks
for is done and verified by then. Calls **47–54 (8 calls, 15%)** are: inspecting
`.pytest_cache/v/cache/nodeids`, grepping for an existing test name, and hunting the host —
finding a Click in `~/.cache/uv/archive-v0` and grepping its `core.py` for the upstream fix.
Its last message is *"There's a Click 8.5.0 in the uv cache — a later release containing the
upstream fix. Let me inspect it."*

**Outstanding at the ceiling: nothing required.** The tail is satisfied; the remainder is
optional investigation of a fix it had already written.

### k3/notes — deliverable complete at call 44; ceiling at 51

Source at 29–32, test added at 41, whole suite at 44 (1301 passed, against 1299 before the
test edit, with the one pre-existing unrelated failure unchanged), a control at 43 naming the
node with the fix reverted. Calls 45–50 are `git diff`, a scratch behaviour check on
envvar/`default_map` interaction, and more diffs.

**Outstanding: nothing required; `CHANGES.rst` untouched, which the task does not ask for.**

### k4/nexus — deliverable complete at call 45, then mutated at call 50 and never re-run

Source at 35–36, tests added at 39, **both nodes named and run at 43 → `2 passed`**, a
discriminating control at 44 → `2 failed` with the fix stashed, whole suite at 45 → 594
passed. Then 46 a repro, **47 a `CHANGES.rst` entry** (optional contribution work), 48
cleanup, 49 `git diff` — and **50 an edit to `tests/test_basic.py`**, removing an unused
`runner` fixture argument from a test that had already been verified. The ceiling fell
immediately after. **The final patch was never executed by the agent in the form it shipped.**

It does still pass: confirmed offline, and separately from the agent's own evidence — see
[`CLOSEOUT-a2r.md`](CLOSEOUT-a2r.md) §4. That is verification after the fact, not an execution
event in the run.

### Allocation after the last source edit

25% of the four runs' calls, composed of 18 pytest invocations, 19 investigative commands, 7
reproduction runs, 4 test edits, 5 reads — and **zero retrieval**. The same shape v2 reported
at 24%, with the same zero. Of that 25%, the test edits and the first verification of each are
**required work**; the repeated whole-suite runs, the cache archaeology, the upstream hunting
and the changelog are not.

---

## 4. What this does and does not identify

**Two different problems, and they do not share a mechanism.**

The truncated passes are a **budget-shape** problem: the deliverable is finished with 15–25%
of the calls still to come, and the remainder goes to repeated verification and optional
investigation. A stop-when-done instruction acts here, and only here.

The failures are a **commitment** problem, and k4/baseline shows it is not always a knowledge
problem: one of the three had a validated fix in hand and never wrote it to `src/`. That is
not addressed by a stopping rule, by more turns, or by better retrieval.

**The next testable mechanism, from this evidence: an explicit implement-then-verify ordering
instruction — write the change into `src/` as soon as a reproduction confirms the mechanism,
before any further investigation — with the task set, ceiling, arms and retrieval policy held
fixed.** It is the smallest change that could move k4/baseline (prototype → deliverable) and
k1/nexus (fix at call 45 → fix earlier, leaving room for the required test), and it is
testable against a counterfactual the records already provide: both runs' own transcripts show
where the fix existed and where it did not.

**Three cautions, all from this pass.**

1. **It may not move the other two failures.** k3/nexus and k4/notes never reached a candidate
   mechanism; an instruction about when to write one down does nothing for them.
2. **It changes earlier planning too.** v2's closeout already narrowed this: an instruction
   about ordering is not confined to the phase it names, and its effect on failing runs is
   unknown in both directions.
3. **A hidden-check pass is not a finished task.** k1/nexus passed and added no test. Any
   experiment that reports correctness alone will score that run the same as k3/baseline,
   which did everything the tail asked.

**This memo does not recommend running it.** Task 5's registration is for the bounded
consultation policy, which was chosen before this pass; whether an ordering instruction
displaces it is a decision, and it is the founder's.

---

## 5. Reproducing this

```bash
python3 benchmarks/agent/diagnose_a2r_runs.py /Users/soko/Cerebros/nexus-a1-fixtures/a2r-run \
  --json benchmarks/agent/results-a2r-runs.json
```

Two detector repairs were needed first and are recorded in
[`CLOSEOUT-a2r.md`](CLOSEOUT-a2r.md) §5: under `calib-v3` the interpreter arrives by name, so
`diagnose_workflow`'s pytest and reproduction-script patterns — which required the literal
word `python` — saw neither. Re-running the repaired program over the **closed v2 calibration**
moves six per-run counts and **no published figure**; the deltas are listed there.
