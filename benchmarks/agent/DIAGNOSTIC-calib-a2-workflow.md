# A2 calibration — what the arm-runs actually did

**Step 1 of [`CLOSEOUT-calib-a2.md`](CLOSEOUT-calib-a2.md) §9.** Descriptive, model-free,
computed from the saved records by [`diagnose_workflow.py`](diagnose_workflow.py). Nothing was
re-run, no record was edited, and no paid execution took place.

Machine-readable table: [`results-calib-a2-workflow.json`](results-calib-a2-workflow.json)
(27 rows, 31 fields). Rendered:
[`results-calib-a2-workflow.txt`](results-calib-a2-workflow.txt).

## Interpretation limits, preserved

* **Issue order is not execution order.** `tool_calls` carries the order the model *issued*
  calls. "After the last source write" means after it *in issue order* throughout.
* **An errored command may still have written files.** Measured here: one compound call wrote
  its repro script *and* failed on `pytest` in the same command. Exit status is reported beside
  a write, never as a veto on it.
* **A final passing patch does not establish when it became correct.** The records carry a
  final patch, not a history of one. **This question is not answered and nothing below infers
  it.** Where a run edited source several times, "the last source write" is exactly that and is
  not a claim about which edit mattered.
* **A completion message is not a verification.** Messages are quoted, never scored.

**One check on the extractor, because a detector that silently misses writes would invert every
conclusion:** of 27 arm-runs, every one whose final patch touches `src/` has at least one
identified source-write call, and every one with no such call has a patch that does not touch
`src/`. Both discrepancy lists are empty. The `Edit` tool turned out to be effectively the only
source-write channel — across 767 Bash commands there is **not one** redirect or `sed -i` into
`src/`.

---

## 1. What followed the last source edit in the 11 passing-but-truncated runs

**They kept working, and what they did next was project-contribution work that the hidden
checks do not score.**

Across those 11 arm-runs, **123 of 505 tool calls (24%) were issued after the last source
write** — a median of 10 per run, ranging from 2 to 26 (6%–47% of a run's calls).

| activity after the last source write | total calls | runs with ≥ 1 |
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

**Retrieval is not where these turns went.** Zero retrieval calls occur after the last source
write in any of the 11. Whatever consultation cost exists, it is spent early and is not what
the truncation is made of.

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

**Partly — and the part it does not support is the more important one.**

**For the passing runs, yes, and the evidence is direct.** A quarter of their tool calls are
issued after the last source edit, and that work is full-suite re-runs, regression tests written
into `tests/`, changelog entries, lint checks and scratch cleanup. These are the habits of
contributing to a project, and the hidden checks reward none of them. A workflow that stops
after verification would return that quarter to the budget.

**Two things must be said against over-reading that.** First, **the 24% is not a saving that can
be banked.** When the patch became correct is not answered here, so the calls-after figure does
not say what a stop rule would have reclaimed, and nothing in this diagnostic establishes that
those runs would have terminated `completed` instead of `max_turns`. Second, a stop rule needs a
verification signal to stop *on* — and one of the 16 passing runs, `30/k2/notes`, never invoked
`pytest` at all, verifying with ad-hoc repro scripts and passing the hidden checks that way. A
rule keyed to "the suite is green" would not have fired there.

**For the failures, no — solve–verify–stop addresses nothing.** You cannot stop after a solve
that never happened. **10 of 11 failing arm-runs never wrote a source file**, and a stop rule
changes none of them. This mirrors what A1 closed on: *on the two tasks nobody solved, no arm
modified a tracked source file even once across 18 arm-runs.* The same shape has now appeared in
a second, disjoint task set under a repaired sandbox.

**So the evidence points at two different interventions, and they are not interchangeable:**

* **To recover budget** — an explicit solve–verify–stop workflow. Well-supported, bounded in
  effect, and it acts only on runs that already pass.
* **To raise correctness** — something that moves runs from *never implemented* to *implemented*.
  This is the dominant failure mode, it is untouched by a stop rule, and the diagnostic does not
  say what would fix it. The candidates visible in the evidence are the **time spent hunting the
  host for a reference implementation** and the **bare-`python3` interpreter resolution**, which
  is per-run friction the environment gate does not currently prevent because the gate tests a
  command the arms do not then use.

**A recommendation on which to test first.** These are separable, and the correctness one is
worth more: a cheaper run that still does not solve the task is not progress, and §5 already
refused a ceiling that buys exploration without raising tasks solved. But the correctness
intervention is not yet specified, and the budget one is. **The honest next step is to pick the
correctness intervention from the evidence above and register it, with the workflow change
either held out of that experiment or applied identically to every arm** — not to run both at
once, which would confound them exactly as §3 warned of ceilings and consultation.

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
