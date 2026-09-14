# A2 budget calibration — registration (**configuration v2**)

> **v1 is void and is not pooled with v2.** Nine arm-runs executed on 2026-09-14 against an
> unrepaired sandbox in which here-documents failed, the documented interpreter invocation was
> absent, and `/tmp` was write-only. All three consume turns, which is the quantity this
> calibration measures, so those rows cannot be compared with repaired ones. They are retained
> under `v1-c30-unrepaired-sandbox/` with a `DO-NOT-POOL.md`, counted in the token budget and
> in no ceiling selection. See §8.

**Registered 2026-09-14, before any calibration arm-run.** This registers a *development*
measurement, not held-out evidence. Its only output is a turn ceiling for A2 and the evidence
that the ceiling was chosen by a rule rather than by preference.

It is not an A2 protocol. A2's held-out registration is a separate document and does not exist
yet.

## 1. What A1 left, and what this is for

A1 is closed with a negative result ([`CLOSEOUT-a1.md`](CLOSEOUT-a1.md)). 29 of its 36 arm-runs
ended at the 30-turn ceiling, and on the two tasks nobody solved **no arm modified a tracked
source file even once** across 18 arm-runs. A1 could therefore not distinguish a workload where
memory cannot help from a budget under which nothing can be shown.

This calibration asks one question: **is there an affordable ceiling at which these arms stop
being cut off mid-task?** It does not ask whether memory helps. A ceiling that merely buys more
exploration is not an improvement, so correctness, truncation, tokens and wall time are read
together and a ceiling is not selected on truncation alone.

## 2. The calibration workload

Four tasks, [`tasks-calib-a2.json`](tasks-calib-a2.json), disjoint from every A1 task
(`d1`–`d4`, `c1`–`c2`, `h1`–`h4`) and from the boundedness rejection `9caedb9206`.

**Drawn strictly before A1's capture boundary** (`ebcd548d50`, 2025-11-19), so every commit
after that boundary stays available for a future A2 capture/held-out split. A calibration set
drawn from recent history would buy a ceiling and spend the experiment.

Admitted under A1's own bar — at most 6 test functions' outcomes and at most 7 source hunks —
and chosen to **span** it rather than sit at one end:

| task | fix | pre-fix | subject | checks | hunks | failing fns | failing instances |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k1 | `c326df95e9` | `a8c0542760` | closing of callbacks on CLI exit | `test_context.py` | 1 | **2** | 3 |
| k2 | `884af5c20f` | `011b9f9d19` | flag value when `is_flag=True` and a type is given | `test_options.py` | 2 | 1 | 5 |
| k3 | `27aaed3fe5` | `2ed395b0b5` | defer `UNSET` normalization in default handling | `test_defaults.py` | 3 | 1 | 1 |
| k4 | `70c673d37e` | `273fb90106` | eagerness of the `help_option_names` help option | `test_commands.py` | 6 | 1 | 1 |

**Failing-function counts are measured by `build_fixture.py`, not read off the diff.** The
static proxy used during selection was wrong on every task: it counted test functions the diff
*touches*, which is not the number whose *outcome* the fix changes.

**The set therefore spans the hunk axis (1–6) and is nearly flat on the function axis (1–2).**
That is a limitation of this calibration, stated rather than dressed up. It is tolerable
because `tasks-click-a1.md` already established that failure count alone is a weak complexity
measure — `d3` was A1's calibrating case at 1 failing function across 7 hunks — and because a
turn ceiling is consumed by edits and test cycles, which track hunks. It would not be tolerable
for a claim about task difficulty, and none is made.

**Every task must clear all four admission checks before it runs**, and a task failing any one
is dropped and recorded, not repaired into the set:

1. the untouched tree **fails** the hidden checks (`no_model`);
2. the tree at the fix commit **passes** them (`fix_oracle`);
3. the fix is **unreachable** from the fixture (`oracle_reachability`);
4. the hidden checks are absent from the fixture the agent sees;
5. **the checks terminate.** `build_fixture.py` bounds every scorer invocation at 300 s. A
   scorer that never returns is a wedged harness, not a failing check.

**Two candidates were rejected by these checks, and are recorded rather than dropped:**

| candidate | rejected because |
| --- | --- |
| `262bdf0228` — raise on end of input in `CliRunner` | its checks include `tests/test_termui.py` and the no-model control **did not terminate**. Pager and TTY behaviour is one of the six classes A1 excluded; the survey that picked it screened commit *subjects* for those classes and not the test files the fix touches. The screen is now on test files. |
| `8c842a43e8` — pass `color` explicitly in error echoing | **vacuous**: its checks pass on the unpatched tree (24 passed, 0 failed), so every arm would score it correct without doing anything. |

**The hang exposed a defect in the admission rule itself.** It read
`fix_oracle["passed"] is not False`, written so an authored task with no upstream fix (`d4`)
could return `None` for "not applicable". Adding a timeout makes a *hang* also return `None` —
so under the old rule every non-terminating candidate would have been **admitted**, silently.
Each clause is now answered explicitly, "not applicable" is identified by the task having no
fix commit rather than by the `None`, and an unreached verdict rejects. See `admissible()`.

**Regression-checked against A1**: under the new rule `h1`–`h4` all still admit, with figures
identical to the registration (h1 18 failed/637 passed, h2 2/90, h3 10/776, h4 3/10).

## 3. The budget grid, fixed before any run

**Ceilings: 30, 45, 60 turns.** 30 is A1's registered ceiling and is carried unchanged so the
grid contains the point A1 actually measured.

**The ceiling is the `--max-turns` flag value.** It is not `num_turns`, which `runner-a1.md`
measured reading 3 under a cap of 2, and it is not the tool-call count. The three are reported
in separate columns and never substituted.

**Ceilings are equal across arms at every grid point.** Retrieval consumes real turns; giving
the memory arm free retrieval turns would measure a different system from the one that ships.

Arms are A1's: `baseline`, `nexus`, `notes`, with identical prompts, tool inventories and
retrieval policy. **This calibration changes no *task* prompt and no retrieval policy** — the
task bodies, the tails and the consultation instruction are the A1 text, byte-for-byte. It does
add one shared `environment` block, appended identically to all three arms, describing how to
run the checkout; that is a change to the instrument, which is why the configuration is
versioned at v2 and why v1 rows are not pooled with v2 (§7, §8). A bounded consultation
policy is the *next* experiment and cannot be tested in the same run that moves the ceiling,
because the two would confound.

### The corpus the memory arms consult

`k1`–`k4` have no corpus of their own, and capturing one is not needed to answer a ceiling
question. **The memory arms consult A1's frozen held-out corpus** (digest `9ae2a9f268dd894d`),
unchanged, per-arm-run private as in A1, with the master digest checked either side.

**What that makes this measurement.** The corpus is *not matched* to `k1`–`k4`: it was captured
against `c1`/`c2`, which are option and flag-value fixes. Some incidental relevance to `k2` is
possible and nothing is arranged to prevent it. For the most part the memory arms will consult
an irrelevant corpus and spend turns doing so — which mirrors A1's `h2` and `h3`, whose declared
mix was *unnecessary*.

This is the right corpus for the question actually being asked. A2's arms will consult, so a
ceiling that ignores consultation cost would be a ceiling the memory arms truncate at. It is
the wrong corpus for any statement about retrieval quality, and none is made: **no contrast
between arms is reported from this calibration.** Arm identity exists here only so the ceiling
is chosen under the turn pressure all three arms actually exert.

**Design: 4 tasks × 3 arms × 3 ceilings × 1 attempt = 36 arm-runs.** One attempt per cell is
deliberate: A1 measured run-to-run variability at nil in 11 of 12 cells, and a ceiling choice
does not need a variance estimate. It follows that **no cell here supports a per-task claim**,
and none will be made.

## 4. Budget, in tokens

**The budget is 36 000 000 tokens** (input + cache-read + cache-creation + output), **not a
dollar figure.** `runner-a1.md` §3 found `"costBasis":"unknown"` on this model and concluded the
CLI's dollar field has no established provenance, so A1 quotes no cost and neither does this.
Token counts come from the provider and are valid accounting. The dollar field is still
recorded, under the name `usd_unprovenanced`, and is never used for enforcement.

Scale: A1's 36 arm-runs at ceiling 30 consumed 20 050 240 input+cache-read tokens. This grid is
36 arm-runs across three ceilings, where a higher ceiling consumes more per run because a
truncated run spends its whole budget. 36 000 000 is that scale with headroom.

**Accounting source.** Four sources, most durable first: each arm's own `record.json`, written
the moment that arm finishes; the row's `records.json`; the arm's `trace.jsonl`; and a bare
`launched.json`, which establishes that an arm-run started and nothing about what it spent. An
aborted or failed row still consumed, and counting only completed rows would understate the
total. **Each arm-run is resolved exactly once**, by the first source carrying a usable usage
block; the later sources are the same arm-run seen again, not another one. A file that will not
parse raises rather than counting as zero, and a usage block whose token fields are all null —
which is what the runner writes when the envelope carried no usage — is *unknown*, never zero.

**Pre-launch check, not post-hoc.** Before a row is *started*, the driver requires
`consumed + row_reserve ≤ cap`, where `row_reserve` is the largest row yet observed at that
ceiling (seeded at 2 500 000 until one has). 

**Overshoot is possible and is stated rather than implied.** An arm-run cannot be interrupted
part-way, and tokens per turn are not bounded by `--max-turns`, so the total can pass the cap by
at most one row. `calibration-summary.json` records it twice, because they are two questions:
`budget_overshoot_tokens` is by how much the *charge* exceeds the cap and is always a number,
and `overshoot_tokens` is by how much *consumption* exceeded it and is `null` unless
`consumption_certain`. A row killed in flight leaves
no envelope at all, and the runner buffers its whole trace until the subprocess returns, so the
interruption window produces no usage record of any kind.

**That window is now instrumented and resolved rather than absorbed.** Each arm writes a
`launched.json` marker immediately before the model is invoked, so an arm that started and was
never accounted for is `unresolved` — and an unresolved arm-run **blocks the sweep** rather than
shrinking the total. Where consumption cannot be recovered, it is resolved by a written
`resolution.json` recording a conservative allowance, its basis, and the fact that it is not a
measurement. Allowances are carried in `tokens_allowance`, never in `tokens_known`; the budget
is enforced on the sum, so an assumption can never make the sweep look cheaper than it is.

**An allowance is the only thing that can unblock the sweep, so it is validated like an input.**
It must give a non-negative integer number of tokens, name the `task`, `attempt` and `arm` it
stands for — matching the directory it sits in — and state a `basis`. One that does not is
refused, charges nothing, and **leaves its arm-run outstanding**, which still blocks: a refused
allowance is somebody's statement that an arm-run happened there. An allowance is applied only
where no measurement was recovered; if one is recovered later, **the measurement supersedes the
allowance** rather than being added to it, and the superseded allowance is kept in
`allowance_superseded_arm_runs` for audit.

**An allowance clears the blocker; it does not establish a fact.** While one is applied,
`consumption_certain` is false and `overshoot_tokens` is `null`, however exact the charge is.

The one instance: **k4/baseline under v1**, stopped in flight on 2026-09-14. Recovery was
attempted and failed — the arm directory holds only its profile and checkout, and the forwarder
keeps no per-request accounting. Allowance **1 400 000 tokens**, a deliberately high
*assumption* and not a demonstrated bound: it sits above the largest *completed* v1 arm-run
(k2/nexus, 1 371 306), and the killed run had executed for well under a minute against a 600 s
ceiling. Neither of those bounds what a killed run spent — a truncated run is not limited by
what completed runs used, and tokens per turn are not a function of wall clock. Erring high is
deliberate: an allowance that flattered the budget would let the sweep spend more than it was
authorised to. The accounting treats it as the assumption it is — while it is carried,
`consumption_certain` is false and no overshoot is computed. **No terminal usage record was
fabricated.**

Runs execute in ceiling order 30 → 45 → 60 so that a cap hit costs the most expensive cell.

## 5. The selection rule, written before the numbers exist

> **Select the lowest ceiling in the grid whose truncation rate across all 12 calibration
> arm-runs at that ceiling is at most 20%, provided its correctness at that ceiling is no worse
> than at any lower ceiling in the grid.**

**20% is an engineering choice, not a statistical guarantee.** It is the point past which
truncation stops being an occasional event and starts being the modal outcome — A1 ran at 81%
and could not be read. Nothing about the number is derived.

The correctness clause matters: a larger ceiling that raises truncation-free runs while
*lowering* the number of tasks solved has bought exploration and not progress, and is refused.

The rule is applied **across arms**, on the pooled 12 arm-runs at each ceiling — not per arm.
Choosing a ceiling that suits one arm would build the comparison's answer into its budget.

**Unequal coverage.** The rule is applied only to ceilings whose **all 12 arm-runs at that
ceiling completed under one configuration**. A ceiling with missing cells — because the budget
stopped the sweep, because a row failed, or because its rows span two configurations — is
reported with its missing cells named and is **not eligible for selection**. Comparing a
complete ceiling against a partial one would let coverage, not the ceiling, decide. If that
leaves fewer than two eligible ceilings, the outcome is "no ceiling selected": a grid of one
point cannot show that a lower ceiling was insufficient.

**If no ceiling qualifies**, no ceiling is selected and A2 does not proceed to a held-out
registration. The response is to revise the workload or the agent configuration — a harder
question than this measurement, and one that must not be settled by raising the grid until
something passes.

## 6. What is reported, per (task, arm, ceiling)

Functional verdict; terminal reason; truncation; `num_turns`; tool calls; input, output and
cache tokens; cost; wall clock; first source mutation or none; retrieval calls before and after
it; tool errors and permission denials. Cost is reported **over all runs including failures**;
averaging an arm's cost over only its successes flatters whichever arm fails more.

Scorers: `a1-functional-2` for hidden checks, `a1-scorer-4` for compliance, both unchanged from
A1 so the ceiling is the only thing that moves.

**The product revision is recorded in the run record itself**, closing the gap A1 left, where it
had to be inferred from git history after the fact.

**Identity is computed once and compared in full.** `identity.py` builds the poolable fields —
configuration version, product and harness revisions, configuration digest, ceiling as applied,
registered corpus digest, schedule digest and **prompt digest** — and both the runner that
records them and the resume check that refuses on them call it. They used to be two
implementations over different inputs: the runner hashed the resolved configuration, the resume
check hashed the raw configuration file, so an unchanged configuration produced a row the
resume check refused. The prompt digest was required to be present and never compared against
anything, so a changed prompt at the same filename would have resumed silently. The digest is
over the **resolved** configuration, defaults included, so a default changed between rows is
inside it.

## 7. The environment gate, added in v2

Before any row is run, the driver builds a throwaway arm from that row's own fixture and probes
it inside `sandbox-exec` with the arm's own child environment
([`verify_arm_environment.py`](verify_arm_environment.py)). A failure **refuses the run**; it is
not a warning.

| check | protects against |
| --- | --- |
| `PYTHONPATH=src python3 -c "import click"` succeeds | A1's arms ran `python3 -c "import click"` against an uninstalled `src/` layout, which cannot work, then spent turns on venv and `pip` against a denied network |
| `PYTHONPATH=src python3 -m pytest tests/test_context.py -q` passes | a documented test command that does not execute a test |
| a here-document works | heredocs failed in all 36 A1 and all 9 v1 arm-runs |
| a scratch file round-trips inside the checkout | arms wrote repro scripts to `/tmp` and could not read them back |
| `python3` is already on `PATH` | turns spent hunting an interpreter |
| the network is still denied | the repair silently opening egress |
| `/private/tmp` is still unlistable | the heredoc repair opening the runner's scratch tree |

**The heredoc repair, and the first attempt at it that was wrong.** zsh writes a
here-document's body to a temp file and reads it back. Its location is `$TMPPREFIX` (default
`/tmp/zsh`) — **not** `$TMPDIR`, which was measured and does not control it. With `/private/tmp`
admitted only as a bare directory entry, the read was denied and every heredoc failed.

The first repair granted `(allow file-read* (regex #"^/private/tmp/zsh"))` to every arm and
argued it was safe because `/private/tmp` stayed unlistable and nothing pre-existing carried
that prefix. **That reasoning was wrong and the grant was a channel between arms**: a filename
prefix names a pattern, not a process, so arm A could write `/private/tmp/zshSENTINEL` and arm B
could read it *by name* without listing anything. Measured, and it worked.

The repair now gives each arm **its own `TMPPREFIX` inside its own directory**. The existing
boundary already grants an arm its own directory and denies every sibling's, so one arm's
heredoc bodies are unreadable to the others by the same rule that protects everything else it
writes. `isolation.heredoc_probe` asserts both halves — heredocs work, and a sentinel written
through one arm's profile is unreadable through another's. A directory listing does not test
that property, which is why the first version passed while the channel was open.

**The prompt gained an `environment` block**, appended identically to all three arms, stating
that the checkout is a `src/` layout, how to run it and its tests, that there is no network, and
that scratch files belong in the checkout. It names no task and no fix. `prompts-a1.json` has no
such key and every A1 prompt still reconstructs byte-for-byte — verified.

**This is a change to the instrument**, which is why the configuration is versioned and why v1
is not pooled with v2.

## 8. v1: what it was, and why it is not evidence

| | v1 | v2 |
| --- | --- | --- |
| arm-runs | 9 (ceiling 30, k1–k3) + 1 killed in flight | pending |
| here-documents | fail in every arm-run | work (gated) |
| interpreter invocation | not stated to the arm | stated identically to all arms |
| tokens charged | 6 425 690 measured + 1 400 000 allowance = 7 825 690 | — |

v1's rows are kept, not deleted: they are the evidence that the defect was real and pervasive,
and they carry real consumption that the budget must count. They are excluded from any ceiling
selection. **Pooling them with v2 would compare a ceiling against a different harness.**

## 9. What this cannot establish

Whether memory helps. Whether bounded consultation helps. Any per-task result. Anything about
`h1`–`h4`, which are exposed and not in this set. Whether a ceiling outside {30, 45, 60} would
be better — the grid is three points, and the rule selects within it or selects nothing.

A ceiling chosen here is an input to A2's registration. It is not a finding.
