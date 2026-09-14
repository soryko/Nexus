# A1 held-out diagnostics — where the 36 arm-runs spent their effort

Computed by [`diagnose_heldout.py`](diagnose_heldout.py) from the 12 `records.json` files and
the 36 saved patches. **No model was invoked and nothing was re-scored**: every functional
verdict is the one the frozen run already produced under `a1-functional-2`, and none of the
corrections below moves one.

**This is descriptive.** It says what the runs did. It can nominate hypotheses for A2; it
cannot establish why any task failed, and nothing here is offered as a cause.

## Corrections to the first release of this document

Review found four defects in the write detector and one overstated claim. All are fixed, every
figure below is recomputed, and each defect is pinned by a counterexample in
[`test_diagnose_counterexamples.py`](test_diagnose_counterexamples.py) (17/17 pass).

| defect | why it mattered | fix |
| --- | --- | --- |
| `&>` and `&>>` stripped as redirect noise | `printf x &> src/click/core.py` classified as **no write at all** | fd-duplication stripping narrowed to `N>&M`; `&>` is a target pattern |
| interpreter writes matched only `open(...,'w')` | `Path('src/…').write_text(...)` classified as no write | `write_text`/`write_bytes`/`writelines`/`shutil`/`os.replace` detected; unquotable targets become `unknown` |
| BSD `sed -i '' s/a/b/ f` | captured the **script** `s/a/b/` as the file | in-place editors take the last argument |
| errored calls skipped entirely | a command can write a file and *then* fail — the h1 records contain exactly that | errored calls no longer assumed inert; their write attempts count as `unknown` |
| every base-tree path counted as "source" | conflated test edits with implementation edits | `src/`, `tests/` and other tracked paths counted separately |

**The correction that changes a reported number most:** of 38 "source mutations" first
reported, **16 are under `src/` and 22 are under `tests/`**. Agents added their own tests beside
the fix; that is not source editing and should not have been counted as it.

Unresolvable writes rose from 11 to **49** once errored calls stopped being treated as inert.
That is the honest figure. This is not a shell parser and cannot become one: command
substitution, `eval`, variable targets and loops are unresolvable by construction and land in
`unknown`, never in "no write".

**Issue order is not execution order.** `first_src_mutation` and the before/after split are
positions in the order the model *issued* calls. With parallel tool use a call issued earlier
can complete later, so these describe the order of asking.

## 1. On h1 and h3, no source change was recorded

| task | `src/` mutations, all 9 arm-runs | `tests/` mutations | scratch files | unresolvable writes |
| --- | --- | --- | --- | --- |
| h1 | **0** | 0 | 22 | 22 |
| h3 | **0** | 0 | 5 | 5 |

Across 18 arm-runs on the two tasks nobody solved, **no final source diff was recorded and no
recorded command named a path under `src/` in a write context.** They wrote reproduction
scripts instead.

**The stronger claim — that no arm ever touched source — is not established.** 27 writes across
those runs are `unknown`, and a file could have been changed and restored within a run without
appearing in the final patch. What the evidence supports is the absence of a recorded source
change, uniformly across `baseline`, `nexus` and `notes`. Being uniform, it describes the tasks
and the budget rather than the memory arm.

## 2. Retrieval is front-loaded — in the four runs that got that far

Of 12 `nexus` arm-runs, **4** reached a recorded source mutation:

| run | retrieval before 1st mutation | after | 1st mutation at call |
| --- | --- | --- | --- |
| h2/a3 | 8 | 0 | 36 of 40 |
| h4/a1 | 2 | 0 | 30 of 38 |
| h4/a2 | 6 | 0 | 17 of 35 |
| h4/a3 | 6 | 1 | 18 of 29 |

**22 retrieval calls before the first source mutation and 1 after — across those four runs
only.** The other **8** reached no mutation and spent a further
**63** retrieval calls, so the 22/1 split is not a
property of the arm. Reported with its denominator for that reason.

## 3. No body was fetched twice — which does not settle batching

**Zero repeat deliveries across all 12 nexus arm-runs.** Unique bodies per run ranged 0–8 and
body fetches never exceeded unique bodies, so re-reading is not a cost driver here.

**This does not dispose of bounded batch delivery.** Batching would combine several *different*
body fetches into fewer agent–tool round trips, and these traces say nothing about that
mechanism either way. What the evidence removes is only the narrower argument for batching that
rests on re-reading.

## 4. Consumption, over all attempts including failures

| arm | `input_tokens` | `cache_read_input_tokens` | `output_tokens` | tool calls | `num_turns` | wall s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 251,839 | 5,224,704 | 136,651 | 36.3 | 31.3 | 94.0 |
| nexus | 361,051 | 8,202,624 | 164,255 | 38.8 | 30.9 | 109.8 |
| notes | 277,798 | 5,732,224 | 154,491 | 33.2 | 28.8 | 134.6 |

`nexus` consumed **1.43×** baseline's `input_tokens`,
**1.57×** its `cache_read_input_tokens`
(**1.56×** combined) and
**1.20×** its `output_tokens`. `cache_creation_input_tokens` was
zero for every arm.

An earlier release quoted only the 1.43× input ratio. It is arithmetically right and materially
incomplete — cache-read volume is roughly twenty times the uncached input — so both fields are
reported and any combined figure is labelled as combined.

**No dollar figure is quoted.** `runner-a1.md` §3 found `"costBasis":"unknown"` on this model
and concluded the CLI's cost field has no established provenance. Token counts come from the
provider and remain valid accounting; money does not follow from them without verified rates.

**`num_turns`, tool calls and the turn ceiling are three different numbers** and are never
substituted here. `runner-a1.md` measured a ceiling of 2 producing `num_turns: 3`; the ceiling
for these runs was the flag value 30.

## 5. Operational friction was substantial

Tool errors: baseline 38, nexus 39, notes
24 across 12 arm-runs each; one permission denial in 36 arm-runs.

**Those counts understate the friction, and the first release drew the wrong conclusion from
them.** The envelope's `permission_denials` counts one thing; sandbox denials surface in tool
*output* instead. Re-reading the records for them, three defects appear in every arm:

- **Heredocs fail in every arm-run** — `can't create temp file for here document: operation not
  permitted`. Traced to the sandbox profile: the shell writes its heredoc temp under
  `/private/tmp`, which the profile admits only as a bare directory entry. Agents fall back to
  `printf` chains, at a cost in turns.
- **`import click` fails without `PYTHONPATH=src`.** The checkout is src-layout and uninstalled,
  no arm was told the invocation, and the network is denied — so `pip` and venv attempts could
  not succeed either. Verified directly: inside the arm sandbox,
  `PYTHONPATH=src python3 -m pytest tests/test_context.py -q` returns **23 passed**.
- **`/tmp` is writable but not readable**, so a repro script written there cannot be read back.

These are properties of the harness and applied equally to all three arms, so they do not
explain a *difference* between arms. They do mean **"no arm was meaningfully obstructed" was
unsupportable**, and it is withdrawn.

## What this does not establish

- **Not why h2 failed.** The two failing nexus attempts spent 11 and 4 retrieval calls; the
  passing one spent 8. No dose-response at n = 3 on one task.
- **Not that a larger ceiling would help.** §1 records no source change on h1 or h3; it does not
  show more turns would have produced one.
- **Not whether stale advice was adopted.** That needs a rubric over the traces — a new and
  exploratory analysis, not this one.
- **Not that friction caused the failures.** It was uniform across arms and is a confound to be
  removed before A2, not an explanation of A1.

## How to rebuild every number above

```
python3 diagnose_heldout.py run-heldout-a1 --from-records --json diagnostics-heldout-a1.json
python3 bootstrap_heldout.py run-heldout-a1
python3 test_diagnose_counterexamples.py
```

| check | what it proves | result |
| --- | --- | --- |
| `--check-sources` | the records path and the 64 MB trace path agree | 36/36 arm-runs identical |
| `--verify-base-trees` | the cached trees match `git ls-tree` | identical, 4/4 tasks |
| `test_diagnose_counterexamples.py` | each repaired defect stays repaired | 17/17 pass |
| `SHA256SUMS-external-traces` | the external traces have not moved | 89 files |

## Full table, one row per arm-run

Machine-readable in [`diagnostics-heldout-a1.json`](diagnostics-heldout-a1.json).

| task | att | arm | 1st src mut | src muts | new files | unk writes | retr | before | after | uniq bodies | repeats | turns | calls | in tok | out tok | errs | denials | tests | patch B | src? | pass | terminal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| h1 | 1 | baseline | unknown | 0 | 0 | 2 | 0 | unknown | unknown | 0 | 0 | 31 | 34 | 28443 | 10488 | 3 | 0 | 0 | 541 | no | no | max_turns |
| h1 | 1 | nexus | unknown | 0 | 1 | 2 | 11 | unknown | unknown | 8 | 0 | 31 | 44 | 41529 | 24854 | 2 | 0 | 1 | 0 | no | no | max_turns |
| h1 | 1 | notes | unknown | 0 | 3 | 4 | 0 | unknown | unknown | 0 | 0 | 31 | 40 | 31745 | 19804 | 3 | 0 | 0 | 844 | no | no | max_turns |
| h1 | 2 | baseline | unknown | 0 | 2 | 4 | 0 | unknown | unknown | 0 | 0 | 31 | 31 | 22427 | 13630 | 2 | 0 | 0 | 586 | no | no | max_turns |
| h1 | 2 | nexus | unknown | 0 | 6 | 2 | 7 | unknown | unknown | 4 | 0 | 31 | 35 | 28400 | 8912 | 5 | 0 | 3 | 1517 | no | no | max_turns |
| h1 | 2 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 32 | 33718 | 9490 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h1 | 3 | baseline | unknown | 0 | 2 | 4 | 0 | unknown | unknown | 0 | 0 | 31 | 33 | 33426 | 13361 | 3 | 0 | 2 | 0 | no | no | max_turns |
| h1 | 3 | nexus | unknown | 0 | 1 | 2 | 7 | unknown | unknown | 4 | 0 | 31 | 37 | 36054 | 15186 | 4 | 0 | 4 | 0 | no | no | max_turns |
| h1 | 3 | notes | unknown | 0 | 7 | 2 | 0 | unknown | unknown | 0 | 0 | 31 | 37 | 30241 | 22364 | 1 | 0 | 1 | 0 | no | no | max_turns |
| h2 | 1 | baseline | 34 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 41 | 21051 | 13470 | 4 | 0 | 2 | 3307 | yes | yes | max_turns |
| h2 | 1 | nexus | unknown | 0 | 0 | 0 | 11 | unknown | unknown | 5 | 0 | 31 | 38 | 27575 | 12380 | 0 | 0 | 2 | 0 | no | no | max_turns |
| h2 | 1 | notes | 28 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 33 | 25841 | 12370 | 2 | 0 | 2 | 3401 | yes | yes | max_turns |
| h2 | 2 | baseline | 31 | 1 | 1 | 3 | 0 | 0 | 0 | 0 | 0 | 31 | 41 | 17980 | 13754 | 5 | 0 | 6 | 1710 | yes | yes | max_turns |
| h2 | 2 | nexus | unknown | 0 | 0 | 0 | 4 | unknown | unknown | 0 | 0 | 31 | 41 | 23385 | 12883 | 3 | 0 | 2 | 0 | no | no | max_turns |
| h2 | 2 | notes | 44 | 1 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 31 | 45 | 18990 | 19118 | 2 | 0 | 0 | 1388 | yes | yes | max_turns |
| h2 | 3 | baseline | 44 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 | 45 | 16163 | 16817 | 3 | 0 | 0 | 741 | yes | yes | max_turns |
| h2 | 3 | nexus | 36 | 1 | 0 | 0 | 8 | 8 | 0 | 5 | 0 | 31 | 40 | 25171 | 15178 | 4 | 0 | 0 | 2523 | yes | yes | max_turns |
| h2 | 3 | notes | 27 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 36 | 35 | 19154 | 16102 | 2 | 0 | 3 | 2671 | yes | yes | completed |
| h3 | 1 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 38 | 25127 | 6675 | 1 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 1 | nexus | unknown | 0 | 1 | 1 | 10 | unknown | unknown | 8 | 0 | 31 | 49 | 32433 | 8023 | 6 | 0 | 0 | 85 | no | no | max_turns |
| h3 | 1 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 34 | 24711 | 10103 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 2 | baseline | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 37 | 25108 | 10730 | 3 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 2 | nexus | unknown | 0 | 4 | 1 | 7 | unknown | unknown | 3 | 0 | 31 | 39 | 40188 | 12788 | 3 | 0 | 0 | 1233 | no | no | max_turns |
| h3 | 2 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 43 | 31982 | 10927 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 3 | baseline | unknown | 0 | 0 | 1 | 0 | unknown | unknown | 0 | 0 | 31 | 35 | 25662 | 7957 | 2 | 0 | 0 | 0 | no | no | max_turns |
| h3 | 3 | nexus | unknown | 0 | 0 | 2 | 6 | unknown | unknown | 3 | 0 | 31 | 41 | 43783 | 18874 | 2 | 0 | 1 | 0 | no | no | max_turns |
| h3 | 3 | notes | unknown | 0 | 0 | 0 | 0 | unknown | unknown | 0 | 0 | 31 | 42 | 27628 | 15778 | 3 | 0 | 0 | 0 | no | no | max_turns |
| h4 | 1 | baseline | 8 | 1 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | 32 | 31 | 11072 | 9416 | 3 | 0 | 12 | 1962 | yes | yes | completed |
| h4 | 1 | nexus | 30 | 1 | 0 | 3 | 2 | 2 | 0 | 0 | 0 | 31 | 38 | 20338 | 15708 | 3 | 0 | 4 | 19193 | yes | yes | max_turns |
| h4 | 1 | notes | 12 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 20 | 10988 | 5925 | 2 | 0 | 3 | 1859 | yes | yes | completed |
| h4 | 2 | baseline | 14 | 1 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 34 | 33 | 10653 | 8153 | 5 | 0 | 11 | 1502 | yes | yes | completed |
| h4 | 2 | nexus | 17 | 1 | 1 | 5 | 6 | 6 | 0 | 1 | 0 | 31 | 35 | 19882 | 9117 | 4 | 0 | 11 | 2030 | yes | yes | max_turns |
| h4 | 2 | notes | 10 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 20 | 11908 | 8138 | 1 | 0 | 3 | 1914 | yes | yes | completed |
| h4 | 3 | baseline | 24 | 1 | 4 | 1 | 0 | 0 | 0 | 0 | 0 | 31 | 37 | 14727 | 12200 | 4 | 0 | 6 | 1867 | yes | yes | max_turns |
| h4 | 3 | nexus | 18 | 1 | 2 | 1 | 7 | 6 | 1 | 3 | 0 | 30 | 29 | 22313 | 10352 | 3 | 1 | 4 | 2028 | yes | yes | completed |
| h4 | 3 | notes | 9 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 19 | 18 | 10892 | 4372 | 2 | 0 | 3 | 1581 | yes | yes | completed |
