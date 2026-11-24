# A2 calibration — what the arm-runs actually did

**Step 1 of [`CLOSEOUT-calib-a2.md`](CLOSEOUT-calib-a2.md) §9.** Descriptive, model-free,
computed from the saved records by [`diagnose_workflow.py`](diagnose_workflow.py). Nothing was
re-run, no record was edited, and no paid execution took place.

Machine-readable table: [`results-calib-a2-workflow.json`](results-calib-a2-workflow.json)
(27 rows, 31 fields). Rendered:
[`results-calib-a2-workflow.txt`](results-calib-a2-workflow.txt).

## The defensible conclusion

> **Successful patches often coexist with unfinished contribution work, while most failed runs
> produced no recorded source change. Interpreter friction occurs in both groups.**

Everything below is the evidence for that sentence and the limits on reading more into it.

## Interpretation limits, preserved

* **Issue order is not execution order.** `tool_calls` carries the order the model *issued*
  calls. "After the last identified source-edit event" means after it *in issue order*
  throughout.
* **An errored command may still have written files.** Measured here: one compound call wrote
  its repro script *and* failed on `pytest` in the same command. Exit status is reported beside
  a write, never as a veto on it.
* **A final passing patch does not establish when it became correct.** The records carry a
  final patch, not a history of one. **This question is not answered and nothing below infers
  it.** Where a run edited source several times, "the last identified source-edit event" is
  exactly that and is not a claim about which edit mattered.
* **The edit timeline is not a complete mutation timeline.** `git stash push -- src/...`
  changes source state with no redirect and no `sed -i`. **Twelve such operations occur in this
  sweep, eight of them after the arm-run's last authored edit**, in 5 of the 11
  passing-but-truncated runs — mostly as a negative control: revert the fix, re-run the tests,
  restore it. They are counted and reported separately (`gitm`), never folded into the edit
  timeline.
* **A completion message is not a verification.** Messages are quoted, never scored.

**One check on the extractor, because a detector that silently misses writes would invert every
conclusion:** of 27 arm-runs, every one whose final patch touches `src/` has at least one
identified source-write call, and every one with no such call has a patch that does not touch
`src/`. Both discrepancy lists are empty. Across 767 Bash commands there is **not one** redirect
or `sed -i` into `src/`, so authored edits went through the `Edit` tool. That validates
**run-level detection of authored edits** — not a complete account of when the tree changed, for
the mutation reason above.

---

## 1. What followed the last source edit in the 11 passing-but-truncated runs

**They kept working, and much of what they did next is work the task explicitly asked for.**
The registered tail is *"Fix the behaviour in `src/`, and **extend the existing test suite to
cover it.** Do not change behaviour unrelated to this bug. When you are done, reply DONE."* The
regression-test edits below are therefore **required work**, and verification belongs in the
task budget. What the numbers show is a gap between what the task asks and what the hidden
functional checks reward — not agent overshoot.

Across those 11 arm-runs, **123 of 505 tool calls (24%) were issued after the last source
write** — a median of 10 per run, ranging from 2 to 26 (6%–47% of a run's calls).

| activity after the last identified source-edit event | total calls | runs with ≥ 1 |
| --- | ---: | ---: |
| `pytest` invocations | 44 | 10/11 |
| local repro scripts run | 16 | **11/11** |
| **edits to files under `tests/`** | 14 | **8/11** |
| files read again | 17 | 9/11 |
| inspections (`grep`/`cat`/`ls`/`git log`) | 21 | 6/11 |
| other (changelog, lint, cleanup, `git diff`) | 18 | 8/11 |
| **retrieval** | **0** | **0/11** |

**8 of 11 ran a suite of ≥ 500 tests after their last source edit** — the full project suite,
600 to 1 300 tests, sometimes more than once. Several went further than that. `30/k1/baseline`
ran a **negative control on its own fix**: `git stash push -- src/click/core.py`, re-ran the
targeted tests to confirm they fail without the change (`2 failed, 23 deselected`), then
`git stash pop`. `45/k2/nexus` issued **nine** `pytest` invocations after its last source
write, ending on a lint check.

The final messages say plainly what the turns were going to:

> "Tests fail before the fix and pass after. Cleaning up scratch files and running the full suite." — `30/k1/baseline`
> "All 603 tests pass. Now let me add regression tests." — `45/k1/baseline`
> "Let me clean up scratch files and add a changelog entry." — `45/k1/nexus`
> "Let me check formatting/lint of my changes against the project's tooling." — `45/k2/nexus`
> "All edge cases behave correctly. Let me review the final diff and clean up scratch files." — `45/k3/baseline`

**No retrieval occurs late.** Zero retrieval calls occur after the last identified source-edit
event in any of the 11. **That does not establish that retrieval contributed nothing to
truncation**: consultation spent earlier still consumes budget and leaves less for the work that
follows, and nothing here measures that. The narrow finding is that the *post-edit* phase is not
made of retrieval.

**One friction item recurs inside this phase:** 8 of the 11 hit `No module named pytest` on a
test attempt *after* their last source edit, because bare `python3` resolves to
`/Library/Developer/CommandLineTools/usr/bin/python3` while the interpreter carrying `pytest`
is `/opt/homebrew/bin/python3`. Each occurrence costs a turn or more to rediscover and route
around.

---

## 2. Did the failures reach implementation?

**Almost none of them did.**

| | failures |
| --- | ---: |
| **no source diff** — never wrote to `src/` at all | **10 of 11** |
| **failing source diff** — wrote to `src/`, checks still fail | **1 of 11** |
| unknown | 0 |

The one that reached implementation, `45/k3/notes`, is instructive in the opposite direction:
62 calls, four source edits, the last at call **61 of 62** — and **zero test attempts of any
kind in the entire run**. It implemented without ever verifying, and was cut off immediately
after its final edit.

The other ten end mid-investigation. Their last messages are all forward-looking:

> "Let me write a reproduction script." — `30/k1/notes`
> "Let me review the relevant existing tests before editing." — `30/k2/baseline`
> "Let me search the filesystem for any other click copies or caches to compare against upstream." — `30/k4/nexus`
> "Now I understand the mechanism. Let me look at the existing eager-processing test and the test fixtures." — `60/k1/baseline`

### Observable friction, and what it does not explain

Friction is real, and it is **everywhere** — which is exactly why it does not separate the
outcomes:

| signal observed in a tool result | failing runs (11) | passing runs (16) |
| --- | ---: | ---: |
| `No module named …` | 10 | **16** |
| `Traceback (most recent call last)` | 10 | 12 |
| nonzero exit code | 8 | 6 |
| `command not found` / `No such file` | 7 | 4 |
| `Operation not permitted` / `Permission denied` | 4 | 5 |

**Every passing run hit the missing-`pytest` problem.** Nothing in this table discriminates, and
neither does volume: failing runs used a median of 42 tool calls against 47 for passing ones,
and re-read files a median of 9 times against 8. **Obstruction coexists with both outcomes and
is not established as a cause of either.**

One behaviour does differ, in intensity rather than in kind. Ten of eleven failing runs searched
the host filesystem for another copy of `click` to compare the fixture against — `find / -name`,
the `uv` archive cache, `site-packages/click` — issuing **47 such commands in total** (median
2.5 per run, up to 13 in `45/k3/notes`). Thirteen of sixteen passing runs did it too, but
**17 commands in total** (median 1, up to 3). The failures dwell there; the passes glance and
move on. That is a description of how turns were spent, not a mechanism, and the overlap is
large enough that it separates the groups only by degree.

---

## 3. Does this support a solve–verify–stop workflow?

**Not yet, and not as the first intervention.**

**The post-edit phase is real and measured.** A quarter of the passing-truncated runs' tool
calls are issued after the last identified source-edit event: full-suite re-runs, regression
tests written into `tests/`, changelog entries, lint checks and scratch cleanup. But **the
registered tail requires extending the test suite**, so a rule that stopped earlier would be
cutting work the task asked for, and verification belongs in the task budget. What this section
can say is that the *scoring* does not see that requirement — not that the work should stop.

**Three things weigh against reading the 24% as a saving.** First, **it cannot be banked.** When the patch became correct is not answered here, so the calls-after figure does
not say what a stop rule would have reclaimed, and nothing in this diagnostic establishes that
those runs would have terminated `completed` instead of `max_turns`. Second, a stop rule needs a
verification signal to stop *on* — and one of the 16 passing runs, `30/k2/notes`, never invoked
`pytest` at all, verifying with ad-hoc repro scripts and passing the hidden checks that way. A
rule keyed to "the suite is green" would not have fired there. Third, **part of that phase is
required by the task**, as above.

**And it does not reach the failures.** **10 of 11 failing arm-runs never wrote a source file.**
You cannot stop after a solve that never happened, so a stop rule does not act on the state
those runs were in. **This is not the same as showing a workflow change cannot affect
correctness**: explicit stopping and verification instructions also change how a run *plans*
earlier on, and whether that would move a run from never-implemented to implemented is
**unknown** — nothing here measures it. What is measured is the state, not the counterfactual.
The state mirrors what A1 closed on: *on the two tasks nobody solved, no arm modified a tracked
source file even once across 18 arm-runs.* The same shape has now appeared in a second,
disjoint task set under a repaired sandbox.

**So the evidence points at two different interventions, and they are not interchangeable:**

* **Execution friction** — the **bare-`python3` interpreter resolution**. Observed in both
  outcome groups, mechanically understood, and fixable without touching the task, the model
  settings, the retrieval policy or the completion instructions.
* **Workflow shape** — an explicit solve–verify–stop rule. Its budget effect is visible here;
  its correctness effect is unknown, and it would trade against a requirement the task states.
* **Reference hunting** — the host-wide search for another copy of `click`. Present in both
  groups and differing only by degree, so the evidence for it is weaker than for either above.

**Chosen, 2026-09-19: interpreter consistency.** It is the one candidate whose mechanism is
established rather than inferred, it is friction the harness created and can therefore remove,
and it holds every registered variable fixed. **Whether it improves correctness remains an
experimental question** — this diagnostic does not predict that, and the repair is not evidence
for it. The repair and its gate are in
[`REPAIR-interpreter-consistency.md`](REPAIR-interpreter-consistency.md); the experiment that
would assess it is drafted in
[`REGISTRATION-DRAFT-a2r.md`](REGISTRATION-DRAFT-a2r.md). Solve–verify–stop and any retrieval
change are deliberately **not** bundled with it.

**Nothing here authorises a paid run.** This report is step 1; the choice of one intervention is
step 2 and belongs to the next decision, not to this document.

---

## Reproducing

```bash
python3 benchmarks/agent/diagnose_workflow.py /Users/soko/Cerebros/nexus-a1-fixtures/calib-run \
  --json benchmarks/agent/results-calib-a2-workflow.json
```

Read-only over the scratch. The scratch is host-local and outside the repository, as
`LAUNCH-A2.md` records.
