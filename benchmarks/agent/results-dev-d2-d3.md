# A1 development runs — `d2` and `d3`, three arms each

> **Figures here are superseded where they concern delivered context, task-requirement compliance, or termination.** Six instrument defects were found in review of `cbdf1f3`; the repairs and the recomputed figures are in [`results-recomputed.md`](results-recomputed.md). This document is kept as the record of what was reported at the time.

**Harness validation only.** Prompts and corpus were both authored by someone who had read
the fixes ([`capture-policy-a1.md`](capture-policy-a1.md) §5). No benefit figure. Same
harness, model, ceilings and seed as
[`results-dev-a1-run2.md`](results-dev-a1-run2.md): `deepseek-flash`, `--max-turns 30`,
600 s, seed `20260911`, order baseline → nexus → notes, consultation policy in every arm.

Artifacts: [`run-dev-d2/`](run-dev-d2/), [`run-dev-d3/`](run-dev-d3/).

Controls, per task and per arm: fixture `isolated=True`, `checks_hidden=True`, no-model
control **fails** (1 failing test each); egress blocked; corpus visible at `13` before every
Nexus arm; store digest unchanged across all arms.

## 1. Outcomes

| task | arm | checks | terminal | tool calls | nexus calls | wall | patch |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **d2** | baseline | pass | completed | 31 | — | 179.3 s | 1,079 B |
| | nexus | pass | completed | 41 | 10 | 190.2 s | 1,134 B |
| | notes | pass | completed | 36 | — | 307.0 s | 1,538 B |
| **d3** | baseline | pass | **max_turns** | 38 | — | 81.7 s | 4,663 B |
| | nexus | pass | **max_turns** | 47 | 11 | 225.5 s | 2,804 B |
| | notes | pass | **max_turns** | 42 | — | 92.4 s | 4,686 B |

Nine arm-runs across `d1`–`d3` in which memory was actually delivered, and all nine **pass
the hidden functional checks**. That phrasing is load-bearing three ways.

**It is not "satisfied every task requirement."** §8.4's compliance figure separates the two,
and it separates them here: `d3`'s Nexus arm passes every check at **3/5** compliance, having
written no test at all. See §4a.

**It is not "nine runs."** Eighteen arm-runs were executed across seven attempts; nine of
them had memory delivered. The attempt manifest, derived from the records rather than
narrated, is [`MANIFEST.md`](MANIFEST.md).

**And contamination does not make success inevitable.** An authored prompt and an authored
corpus do not guarantee a pass; what they do is prevent these passes from supporting a clean
benefit claim. The runs could have failed and did not — that is simply uninformative here.

## 2. Delivered context

| task | arm | delivered | bytes | necessary | support | outdated | irrelevant | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1 | nexus | 8/13 | 1,828 | c01, c03 | 3 | **0** | 3 | **2/2** |
| d1 | notes | 13/13 | 2,776 | c01, c03 | 4 | **c13** | 6 | 2/2 |
| d2 | nexus | 6/13 | 1,342 | c04 | 2 | **0** | 3 | **1/1** |
| d2 | notes | 13/13 | 2,776 | c04 | 4 | **c13** | 7 | 1/1 |
| d3 | nexus | 8/13 | 1,828 | c05 | 4 | **0** | 3 | **1/1** |
| d3 | notes | 13/13 | 2,776 | c05 | 5 | **c13** | 6 | 1/1 |

Full necessary-fact coverage in all six memory-arm runs. Retrieval delivered 46–62% of the
corpus and never surfaced `c13`; the rendered file delivers all thirteen every time, `c13`
included. The classification runs, discriminates and has a non-zero denominator on every
task — which is what these runs were for. It remains **not** evidence that retrieval is
better: a file has no selection step, and the corpus is contaminated.

One figure here was wrong before it was reported: the coverage column initially read `2/2`
for `d2` and `d3` because the analyser's denominator was hard-coded to `d1`'s necessary set.
`d2` and `d3` have one necessary fact each. Fixed, and the table above is the recomputation.

## 4a. Task-requirement compliance, scored separately

Added after these runs and applied to the **saved patches and traces** — no model was
re-invoked. Deliverables come from the prompt's own words: fix in `src/`, extend the existing
test suite, change nothing unrelated, reply DONE, and consult memory before the first source
edit where memory exists.

| task | arm | functional | compliance | termination | original rule | amended rule | failed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| d1 | baseline | pass | 4/4 | completed | pass | pass | — |
| d1 | nexus | pass | 5/5 | completed | pass | pass | — |
| d1 | notes | pass | 5/5 | completed | pass | pass | — |
| d2 | baseline | pass | 4/4 | completed | pass | pass | — |
| d2 | nexus | pass | 5/5 | completed | pass | pass | — |
| d2 | notes | pass | 5/5 | completed | pass | pass | — |
| d3 | baseline | pass | 3/4 | max_turns | **fail (truncated)** | pass | R4 reply DONE |
| d3 | nexus | pass | **3/5** | max_turns | **fail (truncated)** | pass | R2 extend tests, R4 reply DONE |
| d3 | notes | pass | 4/5 | max_turns | **fail (truncated)** | pass | R4 reply DONE |

Both scoring rules are printed. Under the **original** §10 rule every `d3` arm is a fail;
under the **amended** rule every one is a pass, and the truncation is reported as its own
column rather than folded into the outcome. Neither replaces the other.

Compliance is the dimension that moves. `d3`'s Nexus arm is the case that motivated it:
functionally correct, **3/5** on deliverables. The R4 failures across `d3` are truncation
showing up as a missing final reply — which is the point of keeping termination reason and
compliance visible side by side rather than merging them.

One correction inside this figure: the first version of the R2 check required a *new* test
function, which marked every `d2` arm non-compliant for appending assertions to an existing
test — exactly what the upstream fix does. R2 now counts added assertions and added
parametrisation too, and the table above is the recomputation.

## 3. The outdated category has a memory but no task

`c13` — *"Test configuration lives in setup.cfg"* — was delivered to the plain-notes arm on
all three tasks and to the Nexus arm on none. **No arm acted on it.** No patch touched
`setup.cfg`, `pyproject.toml` or `tox.ini`; every patch confined itself to `src/click/` and a
test file.

That is not a demonstration that either arm resisted an outdated memory. **None of `d1`–`d3`
gives an outdated memory any opportunity to mislead**, because none of them requires touching
test configuration at all. `protocol-a1` §7 requires tasks *where the memory is wrong now and
the correct behaviour is to notice and not follow it*. The corpus has such a memory; the task
set has no such task. `setup.cfg` does not even exist in the `d2` and `d3` fixtures.

This is a gap in §15 slot 2, and it cannot be closed by adding memories. It needs a task
whose obvious route runs through the thing the memory is wrong about.

**Closed by `d4`** — see [`results-dev-d4.md`](results-dev-d4.md). Its `stale-advice` control
executes `c13`'s instruction and fails, and retrieval delivered `c13` to the Nexus arm for the
first time. Both memory arms received the stale advice and neither followed it.

## 4. `d3` capped all three arms, and that is what corrected §10

Every `d3` arm terminated at the `--max-turns` ceiling, and every one produced a complete
patch that passed all 34 hidden checks. Under §10 as originally registered — `max_turns`
scored as **fail**, on the premise that a cap leaves a partial patch — the entire task would
have recorded 0/3 for arms that all solved it.

§10 is amended: `max_turns` is a **truncation fact, not a verdict**. A capped run is scored on
the patch it produced, and truncation is reported beside it as a per-arm truncation rate.
Nothing is excluded for hitting the cap. The bias the original row worried about is real and
now lives where it is visible.

**`num_turns` is disqualified for every purpose.** Three independent contradictions: a cap of
2 reporting 3; `d1`'s baseline capping at 31 while sibling arms *completed* at 33 and 34 under
a ceiling of 30; and `d2`'s Nexus arm reporting **`num_turns: 1` for a run with 41 tool calls
and a 1,134-byte passing patch**. Turn figures come from the trace's tool-call count instead.

## 5. Observations recorded, not interpreted

- **Wall clock is wildly dispersed and does not track tool calls.** `d3` notes: 42 calls in
  92.4 s. `d3` nexus: 47 calls in 225.5 s. `d2` notes: 36 calls in 307.0 s. Single
  observations, no repetition, no attribution attempted.
- **`d3`'s Nexus arm wrote no test**, though the prompt asked for one — its patch touches only
  `src/click/core.py`. It still passed, because the hidden checks are the upstream tests. An
  instruction-following miss that correctness scoring cannot see; `protocol-a1` §8's
  required-evidence figure is the place for it, and that figure is not yet implemented.
- **Patch sizes diverge sharply on `d3`**: nexus 2,804 B against baseline 4,663 B and notes
  4,686 B, all passing. Unexplained.
- The plain-notes arm added a `CHANGES.rst` entry on all three tasks; no other arm did on any.

## 6. What `d1`–`d3` now establish

**Established:** the harness runs three arms on three tasks end to end; fixtures isolate;
model-free controls fail as required; egress is blocked and verified per arm; the corpus is
visible and verified per arm; memory is retrieved and delivered; delivered context classifies
into three buckets with a live denominator; the frozen corpus survives every run unmutated;
traces, patches and scoring are preserved for all eighteen arm-runs, superseded ones
included.

**Not established, and not touched by these runs:** whether memory helps; spontaneous
adoption; any task's difficulty; resistance to an outdated memory; and the discovery
asymmetry, which the consultation policy sidesteps by design.
