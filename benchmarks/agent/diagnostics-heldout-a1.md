# A1 held-out diagnostics — where the 36 arm-runs spent their effort

Computed by [`diagnose_heldout.py`](diagnose_heldout.py) from the 36 saved traces, the 12
`records.json` files and the 36 saved patches. **No model was invoked and nothing was
re-scored**: every functional verdict here is the one the frozen run already produced under
`a1-functional-2`.

**This is descriptive.** It says what the runs did. It can nominate hypotheses for A2; it
cannot establish why h2 or h3 failed, and nothing below is offered as a cause.

## What had to be measured rather than assumed

**Absence of `Edit`/`Write` does not prove absence of mutation.** Six of these 36 arm-runs
wrote files through Bash alone, with no direct-mutator call anywhere in the trace. Had the
diagnostic counted `Edit`/`Write` calls, it would have recorded those six as having written
nothing. Every Bash call is therefore parsed for a write target, and the target checked
against the task's own base tree at its `pre_fix` commit.

The parser was wrong first. Its initial redirect heuristic flagged 115 read-only commands as
unplaceable writes, because `2>&1` on a `pytest … | tail` matches a naive redirect pattern.
File-descriptor duplication and `/dev/` sinks are now stripped before anything else reads the
command, which leaves **11** unresolved writes — all of them real (`pip install --target
./_pt`, `uv venv .venv-test`), all environment provisioning, none a source change. They are
reported as `unknown`, not as zero.

**Two independent checks agree, and neither was used to build the other.** `first source
mutation is set` and `patch_touches_src` (recorded by the harness at run time) agree on all
36 rows; `patch_touches_src` and `passed` also agree on all 36. Every arm-run that touched
tracked source passed, and every arm-run that failed never touched tracked source at all.

## 1. On h1 and h3, no arm ever began implementing

| task | src mutations, all 9 arm-runs | scratch/repro files written | test runs |
| --- | --- | --- | --- |
| h1 | **0** | 22 | 11 |
| h3 | **0** | 5 | 1 |

Across 18 arm-runs on the two tasks nobody solved, **not one** modified a tracked source
file. They wrote 27 reproduction scripts instead. The uniform zeros in the headline table are
not eighteen failed patches; they are eighteen runs that never produced a patch.

This holds for `baseline` as much as for `nexus` and `notes`, so it describes the tasks and
the budget, not the memory arm. It is the sharpest available statement of what "the ceiling
dominates" actually looked like — and it is still a description, not a demonstration that a
larger ceiling would have produced a fix.

## 2. Retrieval is front-loaded, measured rather than inferred

Of 12 `nexus` arm-runs, **4** reached a source mutation. In those four:

| run | retrieval before 1st mutation | after | 1st mutation at call |
| --- | --- | --- | --- |
| h2/a3 | 8 | 0 | 36 of 40 |
| h4/a1 | 2 | 0 | 30 of 38 |
| h4/a2 | 6 | 0 | 17 of 35 |
| h4/a3 | 6 | 1 | 18 of 29 |

**22 retrieval calls before the first source mutation; 1 after.** The remaining 8 nexus
arm-runs never mutated source and spent 4–11 retrieval calls each (63 in total) without
reaching implementation.

The consultation policy the prompt asks for is visible in the data: the arm consults, then
works, and does not return to memory once it starts editing. That is the pattern a bounded
consultation policy would be designed against — which is a reason to test one in development,
not evidence that the calls caused any failure.

## 3. Nothing was fetched twice

**Zero repeat deliveries across all 12 nexus arm-runs.** Unique bodies fetched per run ranged
0–8; body fetches never exceeded unique bodies. Re-reading is not a cost driver in this run,
so bounded *batch delivery* has no repeat traffic to save here. If bounded delivery is worth
building, the argument for it has to come from somewhere other than this evidence.

## 4. Resource cost, over all attempts including failures

Means over all 12 arm-runs per arm — failures included, because comparing only successful runs
would flatter whichever arm fails more.

| arm | input tokens | output tokens | tool calls | `num_turns` | wall s |
| --- | --- | --- | --- | --- | --- |
| baseline | 20,987 | 11,388 | 36.3 | 31.3 | 94.0 |
| nexus | 30,088 | 13,688 | 38.8 | 30.9 | 109.8 |
| notes | 23,150 | 12,874 | 33.2 | 28.8 | 134.6 |

`nexus` spent **1.43×**
baseline's input tokens and
**1.20×** its output
tokens, for a correctness result that was equal on three tasks and worse on one. That is the
efficiency statement A1 supports: the memory arm cost more and returned nothing measurable.

**`num_turns`, tool calls and the turn ceiling are three different numbers** and are never
substituted for one another here. `runner-a1.md` measured a ceiling of 2 producing
`num_turns: 3`; the ceiling for these runs was the flag value 30.

## 5. Operational friction

Tool errors: baseline 38, nexus 39, notes 24 across 12 arm-runs each. **One** permission
denial in 36 arm-runs (h4/a3/nexus). No arm was meaningfully obstructed, and the error counts
do not separate the arms.

## What this does not establish

- **Not why h2 failed.** The two failing nexus attempts spent 11 and 4 retrieval calls; the
  passing one spent 8. No dose-response is visible at n = 3 on one task.
- **Not that a larger ceiling would help.** §1 shows no arm began implementing h1 or h3. It
  does not show that more turns would have carried them into implementation.
- **Not whether stale advice was adopted.** Establishing that needs a rubric over the traces,
  which would be a new and exploratory analysis, not this one.

## How to rebuild every number above

Nothing here needs a model, a network, or the 64 MB of traces. The twelve `records.json`
files under [`run-heldout-a1/`](run-heldout-a1/) carry each arm-run's full `tool_calls` array
— id, name, input, result, `is_error` — plus the result envelope, and
`base-trees-heldout-a1.json` carries each task's tracked paths at its `pre_fix` commit:

```
python3 diagnose_heldout.py run-heldout-a1 --from-records --json diagnostics-heldout-a1.json
python3 bootstrap_heldout.py run-heldout-a1
```

Three checks keep that package honest, and each is run rather than asserted:

| check | what it proves | result |
| --- | --- | --- |
| `--check-sources` | the records path and the 64 MB trace path agree | 36/36 arm-runs identical |
| `--verify-base-trees` | the cached trees match `git ls-tree` on the clone | identical for all 4 tasks |
| `SHA256SUMS-external-traces` | the external traces and patches have not moved | 89 files |

The traces stay outside the repository at 64 MB and are checksummed, not committed; the
evidence needed to reproduce every published table is committed, at 2.9 MB.

## Full table, one row per arm-run

Machine-readable in [`diagnostics-heldout-a1.json`](diagnostics-heldout-a1.json).

| task | att | arm | 1st src mut | src muts | new files | unk writes | retr | before | after | uniq bodies | repeats | turns | calls | in tok | out tok | errs | denials | tests | patch B | src? | pass | terminal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| h1 | 1 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 34 | 28443 | 10488 | 3 | 0 | 0 | 541 | no | no | max_turns |
| h1 | 1 | nexus | unknown | 0 | 1 | 0 | 11 | unknown | unknown | 8 | 0 | 31 | 44 | 41529 | 24854 | 2 | 0 | 1 | 0 | no | no | max_turns |
| h1 | 1 | notes | unknown | 0 | 3 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 40 | 31745 | 19804 | 3 | 0 | 0 | 844 | no | no | max_turns |
| h1 | 2 | baseline | unknown | 0 | 2 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 31 | 22427 | 13630 | 2 | 0 | 0 | 586 | no | no | max_turns |
| h1 | 2 | nexus | unknown | 0 | 6 | 0 | 7 | unknown | unknown | 4 | 0 | 31 | 35 | 28400 | 8912 | 5 | 0 | 3 | 1517 | no | no | max_turns |
| h1 | 2 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 32 | 33718 | 9490 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h1 | 3 | baseline | unknown | 0 | 2 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 33 | 33426 | 13361 | 3 | 0 | 2 | 0 | no | no | max_turns |
| h1 | 3 | nexus | unknown | 0 | 1 | 0 | 7 | unknown | unknown | 4 | 0 | 31 | 37 | 36054 | 15186 | 4 | 0 | 4 | 0 | no | no | max_turns |
| h1 | 3 | notes | unknown | 0 | 7 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 37 | 30241 | 22364 | 1 | 0 | 1 | 0 | no | no | max_turns |
| h2 | 1 | baseline | 34 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 41 | 21051 | 13470 | 4 | 0 | 2 | 3307 | yes | yes | max_turns |
| h2 | 1 | nexus | unknown | 0 | 0 | 0 | 11 | unknown | unknown | 5 | 0 | 31 | 38 | 27575 | 12380 | 0 | 0 | 2 | 0 | no | no | max_turns |
| h2 | 1 | notes | 28 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 33 | 25841 | 12370 | 2 | 0 | 2 | 3401 | yes | yes | max_turns |
| h2 | 2 | baseline | 31 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 41 | 17980 | 13754 | 5 | 0 | 6 | 1710 | yes | yes | max_turns |
| h2 | 2 | nexus | unknown | 0 | 0 | 0 | 4 | unknown | unknown | 0 | 0 | 31 | 41 | 23385 | 12883 | 3 | 0 | 2 | 0 | no | no | max_turns |
| h2 | 2 | notes | 44 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 45 | 18990 | 19118 | 2 | 0 | 0 | 1388 | yes | yes | max_turns |
| h2 | 3 | baseline | 44 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 45 | 16163 | 16817 | 3 | 0 | 0 | 741 | yes | yes | max_turns |
| h2 | 3 | nexus | 36 | 2 | 0 | 0 | 8 | 8 | 0 | 5 | 0 | 31 | 40 | 25171 | 15178 | 4 | 0 | 0 | 2523 | yes | yes | max_turns |
| h2 | 3 | notes | 27 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 36 | 35 | 19154 | 16102 | 2 | 0 | 3 | 2671 | yes | yes | completed |
| h3 | 1 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 38 | 25127 | 6675 | 1 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 1 | nexus | unknown | 0 | 1 | 0 | 10 | unknown | unknown | 8 | 0 | 31 | 49 | 32433 | 8023 | 6 | 0 | 0 | 85 | no | no | max_turns |
| h3 | 1 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 34 | 24711 | 10103 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 2 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 37 | 25108 | 10730 | 3 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 2 | nexus | unknown | 0 | 4 | 0 | 7 | unknown | unknown | 3 | 0 | 31 | 39 | 40188 | 12788 | 3 | 0 | 0 | 1233 | no | no | max_turns |
| h3 | 2 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 43 | 31982 | 10927 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 3 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 35 | 25662 | 7957 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 3 | nexus | unknown | 0 | 0 | 0 | 6 | unknown | unknown | 3 | 0 | 31 | 41 | 43783 | 18874 | 2 | 0 | 1 | 0 | no | no | max_turns |
| h3 | 3 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 42 | 27628 | 15778 | 3 | 0 | 0 | 0 | no | no | max_turns |
| h4 | 1 | baseline | 8 | 3 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 32 | 31 | 11072 | 9416 | 3 | 0 | 12 | 1962 | yes | yes | completed |
| h4 | 1 | nexus | 30 | 3 | 0 | 3 | 2 | 2 | 0 | 0 | 0 | 31 | 38 | 20338 | 15708 | 3 | 0 | 4 | 19193 | yes | yes | max_turns |
| h4 | 1 | notes | 12 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 20 | 10988 | 5925 | 2 | 0 | 3 | 1859 | yes | yes | completed |
| h4 | 2 | baseline | 14 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 34 | 33 | 10653 | 8153 | 5 | 0 | 11 | 1502 | yes | yes | completed |
| h4 | 2 | nexus | 17 | 3 | 1 | 4 | 6 | 6 | 0 | 1 | 0 | 31 | 35 | 19882 | 9117 | 4 | 0 | 11 | 2030 | yes | yes | max_turns |
| h4 | 2 | notes | 10 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 20 | 11908 | 8138 | 1 | 0 | 3 | 1914 | yes | yes | completed |
| h4 | 3 | baseline | 24 | 4 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 37 | 14727 | 12200 | 4 | 0 | 6 | 1867 | yes | yes | max_turns |
| h4 | 3 | nexus | 18 | 3 | 2 | 0 | 7 | 6 | 1 | 3 | 0 | 30 | 29 | 22313 | 10352 | 3 | 1 | 4 | 2028 | yes | yes | completed |
| h4 | 3 | notes | 9 | 3 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 19 | 18 | 10892 | 4372 | 2 | 0 | 3 | 1581 | yes | yes | completed |
