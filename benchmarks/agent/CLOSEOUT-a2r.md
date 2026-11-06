# A2-R closeout

**A2-R is closed.** It establishes that the repaired toolchain is usable in these runs. It
establishes **nothing** about memory benefit, no turn ceiling, and no rate that generalises
beyond the twelve arm-runs it contains.

Everything below is derived from the retained records. No cell was rerun and no model was
invoked. Where the evidence does not settle a question, it says so.

> **Boundary exposure — read before any number below.** Two arm-runs, **k3/nexus** and
> **k4/nexus**, accessed bytecode from other sweeps through the macOS cache shadow (§6, R5).
> The effect on their behavior and outcomes is **unresolved**. A2-R's recorded outcomes remain
> **descriptive observations under that exposure**. Every result here retains both runs and
> their accounting; nothing is excluded. Disjoint task ids do **not** establish independence —
> all eight tasks across A1 and A2-R are defects in `src/click/core.py` and can share
> functions, implementation details and fixes.

---

## 1. The identities, kept apart

Three revisions matter and they are not the same revision.

| | | |
| --- | --- | --- |
| **execution HEAD** | `2f5f8fcceaa02ae44a4d6b4bb98c968c5aedc25b` | the checkout the sweep ran from, reported by the preflight immediately before launch and preserved outside the repository in `a2r-run/preflight-at-launch.txt` |
| **execution harness revision** | `225d8538a5c7fcf4d161448c76ee0ba18b53378e` | the measured identity — the last commit touching `benchmarks/agent`. Every one of the twelve records carries it. |
| **analysis revisions** | `0c692b7`, then this branch's `9345ad4`, `ad79977`, `91db898`, `d77aacf` and this commit | written **after** the sweep. Each is a repair to an *instrument*, never to a record. |

The published headline — `results-a2r.{json,txt}` — was produced at `0c692b7`, which is also
where the first two reporter repairs landed. Everything after it is this closeout.

| | |
| --- | --- |
| `product_revision` | `2cd531f9d7c274a065533e58ba3fde8582f8c269` |
| `config_version` / `config_digest` | `calib-v3` / `22eb0a3766cdde73` (`a2r-config-45.json`, `max_turns` 45, `wall_clock_s` 600) |
| `corpus_digest` / `schedule_digest` / `seed` | `9ae2a9f268dd894d` / `0a352b82ced14f10e8…` / `20260914` |
| prompt digests | k1 `5f4f1e210aa7f7ea`, k2 `0aa687b3be6b6a26`, k3 `b99a1fd5366f507f`, k4 `97ee8c80330b164a` |
| runner | `claude` 2.1.270, sha256 `a506b6d970a4cf44…`, `com.anthropic.claude-code` — see [`DIAGNOSTIC-turn-accounting.md`](DIAGNOSTIC-turn-accounting.md) §1 for the full argument vector and environment |

`diagnose_a2r_runs.py` asserts all twelve records against `LAUNCH-A2R.md`'s Identifiers table
on every run. Twelve match; none refused.

## 2. The headline, unchanged

12 of 12 arm-runs present and scored, 0 excluded, **9 functional passes**, **5 normal
completions**, **14 942 135 tokens** measured with no allowance, nothing unresolved,
`consumption_certain: true`, no overshoot against the 20 000 000-token soft threshold.

**Two of the twelve — k3/nexus and k4/nexus — ran under the boundary exposure of §6 R5.** They
are counted here, as they should be; the figures above are the sweep as it ran. What they are
not is twelve independently isolated arm-runs.

The closed v2 calibration's one unresolved arm-run (`c60/run-k1/attempt1/arms/baseline`,
560 844 tokens over its cap) **stays outstanding**. A2-R neither resolves it, covers it, nor
treats it as zero.

## 3. Pinned invocations: 127 attempted, 124 observed running, 0 not runnable, 3 unknown

That distinction is the point of the measurement and is **preserved**. The three unknowns are
not "probably ran": they are commands whose result the reporter's rule cannot attribute. Each
was read by hand for this closeout, and the reading is recorded **beside** the published
figure, not in place of it.

| cell | call | why the reporter left it unknown | what the output shows |
| --- | ---: | --- | --- |
| k2/nexus | 42 | mixed command — the pinned interpreter and another in one call, so one result cannot be attributed to one of them | the pinned segment ran: `ERROR tests/test_basic.py - pytest.PytestRemovedIn10Warning`, a pytest collection error, bracketed by the command's own `stashed` / `restored` markers |
| k3/nexus | 47 | mixed — `python3 --version` and `"$A2_PYTHON" --version` in one call | both ran and disagree, which is the whole point of the repair: `Python 3.9.6` and `Python 3.14.7` |
| k3/notes | 5 | `Exit code 2` with no pytest summary | the interpreter **started** and the program did not: `can't open file '/tmp/repro.py': [Errno 1] Operation not permitted`. `/tmp` is denied and the environment block tells every arm to write scratch inside the checkout. |

**Read narrowly — did the pinned interpreter execute — all three are yes.** The reporter's
127/124/0/3 is not corrected, because its rule is about attribution and its rule is right: a
result that cannot be assigned to one interpreter should not be.

## 4. The twelve patches, and what the added tests exercise

Eight patches add a test. For each, the reported behaviour it exercises, from the task prompt.

**k1** — `ctx.call_on_close` callbacks are not all run when an eager option calls `ctx.exit()`.
Reported: (R1) `--option-with-callback --force-exit` should run both cleanups; (R2) the
reverse order should run only the exiting option's; (R3) a raised logger level leaks because
its reset never runs.

| arm | added tests | exercises |
| --- | --- | --- |
| baseline | `test_call_on_close_runs_when_eager_option_exits`, `test_call_on_close_skipped_after_eager_exit`, `test_call_on_close_resets_state_after_eager_exit` | R1, R2, R3 — one test each |
| notes | `test_call_on_close_eager_exit`, `test_call_on_close_eager_exit_does_not_leak_state` | R1 **and** R2 in one test (both orders asserted); R3 |
| nexus | none | — |

**k2** — `is_flag=True` with an explicit `type=` gives the wrong value when the flag is passed.
Reported: (R1) `type=bool` → `False`, expected `True`; (R2) `type=bool, default=True` → `True`,
expected `False`; (R3) `type=click.BOOL` behaves as `type=bool`; (R4) `type=str` → empty,
expected `True`; (R5) omitting the flag is correct in every case.

| arm | added tests | exercises |
| --- | --- | --- |
| baseline | `test_flag_with_explicit_type_toggles_default` (3 params), `test_flag_with_explicit_non_bool_type_uses_flag_value` | R1, R2, R3, R4 — and R5 as the second half of each parametrised case |
| nexus | `test_flag_value_inferred_with_explicit_type` (3), `test_is_flag_with_explicit_type` (4) | R1–R4 through `Option.flag_value` directly **and** through the CLI, plus R5 |
| notes | `test_flag_with_explicit_type` (7), `test_flag_value_is_set_with_explicit_type` (4) | R1–R5, with R5 as its own parametrised cases; the second test also covers `type=int`, which the prompt does not name |

**k3** — when several flag options share one parameter name and more than one supplies a
default, the wrong default is chosen. Reported: (R1) default on the second option → `green`;
(R2) default on the first → `red`; (R3) passing a flag explicitly is correct.

| arm | added tests | exercises |
| --- | --- | --- |
| baseline | `test_flag_value_dual_options_default_selection` (6 params) | R1, R2 (two params) and R3 (four params, both orders × both flags) |
| notes | `test_dual_flag_options_default_order` (2 params) | R1, R2; R3 asserted inside the body for both flags |
| nexus | none — no source change either | — |

**k4** — the help option generated from `help_option_names` does not respect eagerness
because it is generated more than once. Reported: (R1) `--my-help` prints help and runs
neither callback; (R2) `-a` runs only `-a`'s; (R3) `-a -b` runs only `-b`'s.

| arm | added tests | exercises |
| --- | --- | --- |
| nexus | `test_help_option_is_generated_once`, `test_help_option_eager` | the *mechanism* (option identity is stable across `get_params`) and R1, R2, R3 — plus two cases the prompt does not name, `--my-help -b` and `-b --my-help` |
| baseline, notes | none — no source change either | — |

**The four patches with no added test.** k1/nexus changes `src/click/core.py` (wrapping
`parse_args` so `ctx.close()` runs when an eager callback raises) and leaves six reproduction
scripts behind. k3/nexus, k4/baseline and k4/notes contain **only** reproduction scripts: no
`src/` hunk at all.

## 5. Executed by the agent, versus verified afterwards

These are different claims and the closeout keeps them apart. **Offline verification cannot
establish an earlier execution event**, and an earlier execution event does not establish the
final state either — k4/nexus is both halves of that sentence.

### The six cases the reporter left `unknown`, settled by reading

The reporter's rule is that execution is established by an invocation that **names the added
test's node id** after the test file was last written. That is a sufficient rule, not a
necessary one, and the task never asked an agent to name a node. Reading the commands, their
selection options, their counts and what happened afterwards settles all six.

| cell | what the reporter saw | what the record shows |
| --- | --- | --- |
| k1/baseline | file-level runs only | `pytest tests/test_context.py` gives 23 before the test edits and **26 after**; `-k "call_on_close" -v` gives **3 passed, 23 deselected** (no base test in that file carries the name — 18 functions, none matching); the whole suite goes 603 → **606**; and the paired control, fix stashed, **names all three** in its FAILED lines: `3 failed, 23 deselected` |
| k2/baseline | file-level | `-k "explicit_type or explicit_non_bool"` gives **4 passed, 133 deselected**; the control names all four added instances and gives `4 failed, 133 deselected`; the whole suite goes 719 → **723** |
| k2/nexus | file-level | `pytest tests/test_options.py` 133 → **140** across the one test edit; the control gives `7 failed, 133 deselected`; a later `-k "explicit_type"` gives **7 passed, 133 deselected** |
| k2/notes | file-level | `-k "explicit_type or type_from_flag_value"` gives **12 passed, 132 deselected** (11 added + 1 existing); the control gives `8 failed, 3 passed` — the three that pass unfixed are exactly the flag-omitted cases the prompt says are already correct; the whole suite goes 719 → **730** |
| k3/notes | the only invocation naming the node reverted the fix first | that control is real and is not the measurement; but the whole suite goes **1299 → 1301** across one test edit, the added test is parametrised ×2, and the only FAILED line at either point is a pre-existing unrelated `test_commands.py` failure present before the edit |
| k4/nexus | file-level | **named both nodes explicitly** and got `2 passed` — the reporter missed it because the first attempt at the same selection died on a collection error and the successful one added `--override-ini "filterwarnings="`; the control, fix stashed, gives `2 failed` |

**All six: the added tests executed and passed.** Five of the six also demonstrated a
discriminating control of their own; k3/notes' control discriminates on one of its two
parametrised cases.

`results-a2r.{json,txt}` are **not** rewritten. The reporter's rule is a good rule and the
`unknown`s it produced are honest under it.

### What was mutated after the last verification

| cell | after its last verifying run |
| --- | --- |
| k1/baseline, k2/nexus | nothing — the last call is the verification |
| k2/baseline, k2/notes | one `"$A2_PYTHON" - <<EOF` each: a program on **stdin**, creating no file |
| k3/notes | `git diff`, and one scratch script that deletes itself in the same command (`; rm -f scratch_check.py`) — absent from the final patch, as the patch shows |
| **k4/nexus** | a `CHANGES.rst` entry, scratch cleanup, `git diff` — and at call 50, **an edit to `tests/test_basic.py`**, dropping an unused `runner` fixture argument from a test already verified. The ceiling fell immediately after. **The agent never executed the patch in the form it shipped.** |

### What the offline instruments verified, afterwards

Three different instruments, run after the model exited, none of which is execution evidence:

- **`a1-functional-2`** — the hidden checks, run by the harness at the end of each arm-run.
  9 of 12 pass.
- **`a1-scorer-4` → `a1-scorer-5`** — the requirement-compliance scorer, a separate offline
  pass over the saved patch and trace. Its E1 probe runs each arm's added tests against the
  task's pristine tree.
- **this closeout** — for k4/nexus specifically, the post-mutation test file run directly:
  the probe's exact argv errors on collection, and the same argv plus
  `-W ignore::pytest.PytestRemovedIn10Warning` gives **2 passed**. So the final patch's tests
  do pass. That is a fact established on 2026-09-19 by re-running them, **not** a fact about
  what happened during the arm-run.

## 6. Four post-run repairs to the instruments, and one to the boundary

Every one is a repair to an instrument or a profile. **No record was edited.** The originals
are retained and every figure below is re-derived.

### R1 and R2 — the reporter, at `0c692b7` (recorded in that commit)

1. **The write detector read `2>&1` as a redirect.** It asked only whether a command named the
   test file and contained any `>`, so `pytest tests/test_context.py -q 2>&1 | tail` counted as
   a *write* to that file. That pushed the "last test write" boundary past the last test run in
   every arm that redirected stderr and demoted **every** piece of execution evidence in the
   sweep to `unknown`. A write is now the file as the *target* of a redirect, `tee`, `sed -i`,
   `cp` or `mv`.
2. **An arm's own negative control was read as its test's result.** Three arm-runs ran
   `git stash push src/click/core.py && pytest <node>` to show the new test fails without the
   fix. That failure is the evidence, not a defect. Two arm-runs that passed the hidden checks
   were being reported as having a failing test. Controls are separated now, and a control
   alone settles neither the result nor execution.

### R3 and R4 — the compliance scorer, found writing this closeout

3. **The regression probe never applied `PYTEST_IGNORE`.** That flag is `a1-functional-2`'s
   registered demotion of one warning class, adopted because Click turns warnings into errors
   and an unrelated `parametrize` call in `tests/test_basic.py` raises
   `PytestRemovedIn10Warning` during **collection** under the pinned pytest 9.1.1. The
   compliance scorer never learned it, so k4/nexus — the one arm whose added test lives in that
   file — had all three probe trees come back `rc=4, collection error`, and E1 reported
   **`fail`**: *the test does not discriminate*. It says nothing of the kind; the measurement
   did not happen. Reproduced directly on the retained tree.
4. **P2 was read against a corpus no A2/A2-R arm was given.** `score_compliance.NOTES_FILE`
   was hard-wired to `notes-dev-a1.md` while `run_arms_isolated` seeds the arm from
   `CFG.bench_path("notes_file")` — `notes-heldout-a1.md` for these sweeps. The two renderings
   share no line. So all four notes arms, each of which read its notes file in **tool call 0**,
   scored *"no prior-work content was delivered at any point"*. `score()` takes the path as a
   required keyword now.

**Both R3 and R4 bias the same way — a measurement that did not happen reported as an arm that
failed — so no row was ever flattered by either.** Five of twelve rows move, all upward, and
no check flips the other way:

| cell | `a1-scorer-4` | `a1-scorer-5` | which check |
| --- | ---: | ---: | --- |
| k1/notes | 4/6 | **5/6** | P2 fail → pass |
| k2/notes | 4/6 | **5/6** | P2 |
| k3/notes | 4/6 | **5/6** | P2 |
| k4/notes | 0/6 | **1/6** | P2 |
| k4/nexus | 4/6 | **5/6** | E1 fail → pass |
| the other seven | — | unchanged | — |

Published as `results-a2r-compliance-r2.json` and `results-a2r-r2.{json,txt}`, **beside** the
originals. `compliance-a2r-r2.json` sits beside `compliance-a2r.json` in the sweep directory.
The repaired reporter over the **original** artifact still reproduces `results-a2r.txt` byte
for byte — the check that says this is a re-derivation and not a rewrite.

A fifth repair, to the reporter, has no numeric effect on A2-R: it named `a1-scorer-4` as a
literal in six places, so a repaired scorer's numbers would have published under the previous
scorer's name. It reads the version from the artifact now.

### R5 — the boundary, found reading k3/nexus's trace

**Two arm-runs read outside the intended boundary.** macOS redirects a sandboxed process's
writes under `/private/tmp` into `~/Library/Caches/com.apple.python/private/tmp/…`, and
`Library/Caches` is granted whole because the runner needs its own caches. The entry-only
grant on `/tmp/claude-<uid>` exists precisely to keep other sessions' working directories out
of an arm; the redirect put a shadow of that tree back on the allowed side under another name.

k3/nexus (calls 42–49) and k4/nexus (calls 22–31) enumerated A1's capture tasks and the whole
held-out execution tree — h1–h4, every attempt, every arm — grepped those repos for the defect
they were working on, and disassembled `core.cpython-39.pyc` from them.

**What was exposed, checked rather than assumed: 221 files, all `.pyc`.** No `.py` source, no
tests, no hidden checks, no patches. Every *source-text* grep the two arms ran came back empty
— there is no `tests/` directory in the shadow. k3/nexus made no source edit at all and
failed; k4/nexus's probes ended at call 31 and its first source edit is call 35.

**The earlier wording here said "nothing was extracted, on this evidence". That is withdrawn:
it was not established.** Bytecode carries implementation information, and disassembly can
expose it while a grep over source text returns nothing — k3/nexus *did* disassemble
`core.cpython-39.pyc`. A source-text grep is not a test of what a disassembler can recover.
The honest statement, and the one carried forward:

> Two arm-runs accessed bytecode from other sweeps. The effect on their behavior and outcomes
> is unresolved. A2-R's recorded outcomes remain descriptive observations under that exposure.

**Nor does task disjointness establish independence.** A2-R runs k1–k4 and the shadow held
A1's h1–h4, but different Click tasks can share functions, implementation details and fixes:
all eight are defects in `src/click/core.py`. Disjoint task ids are not disjoint subject
matter, and the earlier "disjoint from A1's, so this does not void it" understated what would
have to be shown.

**All results and accounting are kept. No arm-run is retroactively excluded**, and the two
affected runs are flagged wherever A2-R's numbers are read — excluding them would produce
cleaner figures by dropping the runs selected on the problem being reported. The
runtime-usability finding is unaffected and remains supported. What did not hold is the
intended isolation.

The path is denied as of this branch, the deny is the subpath rather than the cache root, and
`check_boundary` now runs **two** controls over it: the original *listing* of the shadow root,
which cannot pass on an empty tree, and a **sentinel read by exact path** with an
outside-sandbox positive control, because an unlistable directory does not by itself make a
known file inside it unreadable — and reading files by path is what these two arm-runs did. An
absent or inconclusive result is `None` rather than `True`, and A3 declares both controls
preconditions so that `None` refuses the launch instead of being skipped.

**A second, milder exposure is not new and is not closed.** `/opt/homebrew` is a system read
root, and k4/notes spent 10 of its 47 calls reading a released Click under
`/opt/homebrew/Cellar/aider/…/site-packages/click` and comparing behaviour against it;
k3/baseline found another in `~/.cache/uv/archive-v0`. `diagnose_workflow` already counts
these as reference hunts, so they are reported rather than discovered. A released library
containing the upstream fix is readable by every arm, and any held-out design has to decide
deliberately whether that is acceptable.

## 7. What A2-R does not establish

- **No memory benefit.** The corpus is unmatched to k1–k4 and no arm contrast is reported.
- **No ceiling.** `freeze-calib-a2.md` §5 belongs to the closed calibration, which selected
  none. `assess_a2r.py` refuses to summarise more than one ceiling.
- **No causal comparison with v2.** Prompt digests differ on all four tasks, so no v3 row
  pools with a v2 row.
- **No rate.** One attempt per cell, four tasks, twelve arm-runs.
- **Not clean isolation.** Two arm-runs read another sweep's bytecode (§6 R5). Whether that
  changed their behaviour or outcomes is unresolved and cannot be settled from the records:
  disassembly leaves no trace a source-text grep would find. The runtime-usability finding
  stands; a claim that these twelve runs were independent does not.
- **Not "the requirements were satisfied".** Nine hidden-check passes; **eight** patches add a
  test; `P1_final_reply_opens_done` fails in all twelve — no arm-run opened its final reply
  with `DONE`, and **eight have no final reply at all**: all seven that hit the ceiling, plus
  k1/baseline, which completed. k1/nexus passes the hidden checks with
  no test added at all, which is why a hidden-check pass is not read here as a finished task.
  See [`DECISION-a2r-failures.md`](DECISION-a2r-failures.md).
- **`relevance` stays `unreviewed`** in the published artifacts. §4 above is the evidence-linked
  review the reporter asked for — which behaviour each added test exercises — written here
  rather than folded into a machine field, because "does this test cover the defect rather than
  merely fail in the predicted way" is the judgement the scorer explicitly declines to make.

## 8. Preservation

`SHA256SUMS` is **not** regenerated: it is A1's freeze snapshot, deliberately stale.

[`manifest_a2r.py`](manifest_a2r.py) writes A2-R's own, 154 entries:

- **121 `sweep:`** — every record, trace, patch, launch record, environment check, mcp config,
  sandbox profile, stderr, per-attempt log, the summary, both compliance artifacts, the
  preflight taken at launch and the sweep log.
- **12 `tree:`** — one digest per arm over its **tracked working tree plus its diff**, not
  `HEAD`: several arm-runs used `git stash` as a negative control, so the working tree is the
  state that matters. It refuses rather than digests if any stash entry survives. None does.
- **21 `repo:`** — the published analysis, the configuration, the prompts, the task sheet, the
  schedule, the notes corpus, the launch record and the preflight.

The 12 arm checkouts themselves are 169 MB and 12 664 files, mostly the fixture repeated.
They stay in place under `/Users/soko/Cerebros/nexus-a1-fixtures/a2r-run` and are covered by
the tree digests; every file a reader actually reads is checksummed by name.

### Rebuilding, and what rebuilds exactly

```bash
S=/Users/soko/Cerebros/nexus-a1-fixtures/a2r-run

# 1. nothing moved
python3 benchmarks/agent/manifest_a2r.py $S --check benchmarks/agent/SHA256SUMS-a2r

# 2. the PUBLISHED headline, from the ORIGINAL compliance artifact (byte-identical at 0c692b7
#    and at this revision)
python3 benchmarks/agent/assess_a2r.py $S

# 3. the re-derived compliance pass and its report, side by side with the originals
python3 benchmarks/agent/score_a2r_compliance.py $S --out <elsewhere> --ceilings 45
python3 benchmarks/agent/assess_a2r.py $S --compliance $S/compliance-a2r-r2.json

# 4. the failure / truncation analysis
python3 benchmarks/agent/diagnose_a2r_runs.py $S --json benchmarks/agent/results-a2r-runs.json

# 5. the turn-accounting experiment (drives the real CLI against a loopback stub; no model)
python3 benchmarks/agent/diagnose_turns.py --out <dir> --max-turns 45
```

Step 3's `--out` is **elsewhere on purpose**: `score_a2r_compliance` defaults to writing into
the sweep's own root, and `compliance-a2r.json` there is the published scoring pass.

`preflight-a2r.py` now **refuses**, and should: it asserts harness revision `225d8538…`, which
this branch's analysis commits have moved. The authorisation it guarded was for one sweep and
was spent when that sweep ended.

## 9. Where this leaves the next decision

The next milestone is whether Nexus provides useful memory at an acceptable cost, and A2-R
does not speak to it. Three things do follow from this closeout:

1. **A budget can now be registered in a quantity that exists.** `--max-turns N` buys N model
   responses; the envelope's `num_turns` is two different counters and is not a bound.
   [`DIAGNOSTIC-turn-accounting.md`](DIAGNOSTIC-turn-accounting.md).
2. **Correctness alone must not be the screen.** k1/nexus passes the hidden checks having
   written no test, and k4/nexus shipped a patch it never executed in final form.
3. **A held-out run cannot be launched from this host until the boundary work is verified
   clean**, and the two nexus arm-runs that walked into the shadow are why.

[`DECISION-a2r-failures.md`](DECISION-a2r-failures.md) names the next testable mechanism and
declines to recommend running it. That is a decision, not a measurement.
