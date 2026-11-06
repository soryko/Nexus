# A1 held-out result — 2026-09-12

Executed against the registration frozen in [`freeze-heldout-a1.md`](freeze-heldout-a1.md) and
its revision 2, the corpus frozen at digest `9ae2a9f268dd894d`, and the mix declared in
`mix-heldout-a1.json` (sha256 `21a44b688ed3f65f…`) before any arm ran. 36 arm-runs, 12 rows of
`schedule-heldout-a1.json` (digest `1a960a7465fa36a0`, seed 20260912). Scorers: `a1-scorer-4`
for compliance, `a1-functional-2` for the hidden checks.

## The headline, stated as §5.4 requires

**The nexus arm did not beat baseline on any task. It lost on one.** Memory, as measured here,
did not help. That is the finding, and it is reported as prominently as the reverse would have
been.

**The notes arm did not beat baseline on any task either.** It tied on all four. So this run
does not support "having the information helps" any more than it supports "Nexus's retrieval
helps".

**Both statements are heavily qualified by a ceiling that truncated 29 of 36 arm-runs**, and by
two tasks that no arm solved at all. See *What this cannot establish*.

## Per task, per arm (the primary reporting; §5 forbids one score)

Functional = attempts passing the hidden checks / contributing attempts. All 36 arm-runs were
scored; none was excluded.

| task | mix | arm | functional | compliance | delivered B | outdated | irrelevant | calls | wall s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **h1** | outdated | baseline | **0/3** | 0.00 | 0 | 0 | 0 | 32.7 | 89.6 |
| | | nexus | **0/3** | 0.00 | 5 265 | 2 | 4.7 | 38.7 | 112.3 |
| | | notes | **0/3** | 0.00 | 6 591 | 2 | 6 | 36.3 | 197.8 |
| **h2** | unnecessary | baseline | **3/3** | 0.47 | 0 | 0 | 0 | 42.3 | 126.4 |
| | | nexus | **1/3** | 0.28 | 3 851 | 0 | 9.7 | 39.7 | 114.0 |
| | | notes | **3/3** | 0.50 | 8 788 | 0 | 11 | 37.7 | 174.4 |
| **h3** | unnecessary | baseline | **0/3** | 0.00 | 0 | 0 | 0 | 36.7 | 70.5 |
| | | nexus | **0/3** | 0.00 | 4 839 | 0 | 9 | 43.0 | 89.6 |
| | | notes | **0/3** | 0.00 | 6 591 | 0 | 11 | 39.7 | 121.4 |
| **h4** | useful | baseline | **3/3** | 0.80 | 0 | 0 | 0 | 33.7 | 89.3 |
| | | nexus | **3/3** | 0.78 | 2 058 | 0 | 4.3 | 34.0 | 123.5 |
| | | notes | **3/3** | 0.78 | 6 591 | 0 | 11 | 19.3 | 44.7 |

Delivered bytes, outdated and irrelevant counts are means over the three attempts; "outdated"
and "irrelevant" count memories delivered in those buckets, per the declared mix.

## Paired contrasts and sign count

| task | nexus − baseline | notes − baseline | nexus − notes |
| --- | --- | --- | --- |
| h1 | 0.00 | 0.00 | 0.00 |
| h2 | **−0.67** | 0.00 | **−0.67** |
| h3 | 0.00 | 0.00 | 0.00 |
| h4 | 0.00 | 0.00 | 0.00 |

- `nexus − baseline`: baseline favoured on **1** task, tied on 3, nexus favoured on **0**.
- `notes − baseline`: tied on **4**.
- `nexus − notes`: notes favoured on **1**, tied on 3, nexus favoured on **0**.

**Percentile bootstrap over tasks, 10 000 resamples, seed 20260912** — computed by
[`bootstrap_heldout.py`](bootstrap_heldout.py) from the saved records:

| contrast | contributing tasks | point | 2.5% | 97.5% |
| --- | --- | --- | --- | --- |
| nexus − baseline | 4 | −0.167 | −0.500 | 0.000 |
| notes − baseline | 4 | 0.000 | 0.000 | 0.000 |
| nexus − notes | 4 | −0.167 | −0.500 | 0.000 |

**No inferential claim is made from this interval and no difference is called established on
it**, exactly as §5 requires. With one non-zero task contrast among four the resampling
distribution is **discrete, not degenerate** — resampling four values of which one is non-zero
admits exactly five possible means (0, −1/6, −1/3, −1/2, −2/3) and the interval has real width
(0.500). What it lacks is a population behind it: it describes the arithmetic of four numbers.
The primary reporting remains the per-task table and the sign count above.

**Reporting deviation, disclosed.** The first release of this document computed no interval,
on the reasoning that §5 forbade one. §5 forbade an *inferential claim from* the interval; it
required the interval itself — "a percentile bootstrap over tasks, 10 000 resamples, reported
with the contributing task count". Revision 2 §5 lists "the analysis and exclusion rules in
§5" among what stands unchanged, so r2 did not relax the requirement. The frozen registration
is not edited; the omission is corrected here and recorded as a deviation of the report, not
of the run.

## The one non-tied cell, and what it is not

h2, nexus 1/3 against baseline 3/3. **It is not a case of memory giving bad advice.** In both
failing attempts the nexus arm produced a **0-byte patch** — it changed nothing, and scored
exactly the unpatched tree (`2 failed, 88 passed`, identical to the no-model control). h2's
declared mix is *unnecessary*: no memory in the corpus concerns usage-line rendering, and the
arm was delivered **zero** outdated memories and ~9.7 irrelevant ones.

What the traces show is turn allocation. Across all 12 nexus arm-runs the arm spent 2–11 calls
on `status`/`search`/`get` **before any source edit**, which is what its prompt asks of it, and
in 8 of 12 it never reached an `Edit`/`Write` call at all.

That last count has since been checked against shell activity rather than trusted: an agent
can edit through `sed -i`, a heredoc or `patch` without a direct-mutator call, and six of the
36 arm-runs did write files through Bash alone. Every Bash write target was resolved against
the task's base tree. **All six wrote scratch or reproduction files; none reached tracked
source.** The count stands.

Two corrections to how that check was first reported. The detector originally missed `&>`
redirects, interpreter writes through `pathlib`, and the BSD `sed -i` idiom, and it skipped
every errored call on the assumption that a failed command writes nothing — which the h1
records falsify, since a script is written there and the *next* step fails. It also counted
every path in the base tree as "source": of 38 mutations first reported, **16 are under `src/`
and 22 are under `tests/`** — agents added their own tests beside the fix, which is not source
editing. Counterexamples for each defect are in
[`test_diagnose_counterexamples.py`](test_diagnose_counterexamples.py).

**Whether that turn cost caused the h2 failures is not established.** The two failing attempts
spent 11 and 4 memory calls; the passing one spent 8. There is no dose-response across n = 3 on
one task, and 11 of 12 nexus arm-runs were truncated at the ceiling regardless. The honest
statement is that the memory arm failed to produce a patch where the other two succeeded, that
it front-loads retrieval, and that this run cannot separate those two facts.

## Termination and exclusions (§5.3)

| arm | contributing | excluded | truncated at `max_turns` |
| --- | --- | --- | --- |
| baseline | 12/12 | 0 | 10 |
| nexus | 12/12 | 0 | 11 |
| notes | 12/12 | 0 | 8 |

Exclusions are balanced at zero: **no environment exclusion was recorded for any arm**, so
the §5.3 imbalance threat does not apply on the evidence recorded. That establishes the absence
of *recorded* exclusions and nothing more.

**Operational friction was in fact substantial, and an earlier version of this document
understated it.** "One permission denial" counts one envelope field; it does not count sandbox
denials, which appear in tool *output*. Re-reading the saved records for them:

| defect | scope | effect |
| --- | --- | --- |
| shell heredocs fail: `can't create temp file for here document: operation not permitted` | reproduced in **every** arm-run | agents fall back to `printf` chains, at a cost in turns |
| `import click` fails without `PYTHONPATH=src` | the checkout is src-layout and uninstalled; no arm was told the invocation | repeated failed reproductions, venv and `pip` attempts against a denied network |
| `/tmp` is writable but not readable | several arms wrote a repro script to `/tmp` and could not read it back | wasted turns |

The heredoc failure was traced to the sandbox profile: the shell writes its heredoc temp file
under `/private/tmp`, which the profile admits only as a bare directory entry. It is a property
of the harness, not of any arm, and it applied equally to all three — but "no arm was
meaningfully obstructed" was not supportable and is withdrawn. Truncation is not balanced —
the notes arm finished within the ceiling more often — and is reported as a fact, not a verdict.

## Attempt-level spread (never a test of the comparison)

```
h1   baseline=...   nexus=...   notes=...
h2   baseline=PPP   nexus=..P   notes=PPP
h3   baseline=...   nexus=...   notes=...
h4   baseline=PPP   nexus=PPP   notes=PPP
```

Run-to-run variability is nil except for h2/nexus. h1 and h3 are uniform zeros; h4 a uniform
three.

## Storage (§4 figure 6)

The frozen store occupies **172 032 bytes** on disk for **6 591 bytes** of memory content — a
**26.1×** multiple. Reported beside the benefit figures, which are zero, so no cost-per-benefit
ratio is computable.

## What this cannot establish

1. **Two of four tasks were solved by nobody.** h1 and h3 are 0/9 across all arms. That is
   informative about this configuration — it shows every arm failed these tasks under the
   registered conditions — but it does not **discriminate between arms**: each contributes a
   tie to every contrast. The discriminating comparison rests on h2 and h4.
   The diagnostics add what the zeros were made of: across all 18 arm-runs on h1 and h3, **no
   final source diff was recorded**, and no recorded command named a path under `src/` in a
   write context. These are not failed patches; they are runs that produced no source change.
   The stronger claim — that no arm ever touched source — is **not** established: 27 writes
   across those 18 arm-runs could not be placed by the parser and are counted `unknown`, and a
   file could in principle have been changed and restored within a run. See
   [`diagnostics-heldout-a1.md`](diagnostics-heldout-a1.md).
2. **Truncation is pervasive, and its effect is not established.** 29 of 36 arm-runs ended at
   `max_turns`. The registered 30-turn ceiling was calibrated on development tasks of 1–7
   hunks; the held-out set runs to 5 hunks and 6 functions. What can be said is that
   truncation was extensive and co-occurs with the zeros; what cannot be said is that a larger
   ceiling would have produced a fix — that is a claim about runs nobody executed, and the
   diagnostics show h1 and h3 exhausted their budgets without any arm entering implementation,
   which is consistent with a budget that is too small and equally consistent with tasks these
   arms do not solve. Calibrating a ceiling is A2 development work, on a separate task set.
   The ceiling was **not** changed after seeing this, and must not be: the first result was in
   hand from the pilot row onwards. A future registration may set a different ceiling; this
   one may not.
3. **The outdated-memory hypothesis was not tested.** h1 is the task whose mix is *outdated*,
   and both memory arms were duly delivered both stale memories (`h02`, `h09`) as full bodies.
   **Exposure did occur**: both stale bodies reached both memory arms. What A1 could not do
   is distinguish harm **through the correctness endpoint** — no arm solved h1 under any
   condition, and extensive truncation limits interpretation further, so task success cannot
   separate an arm that was misled from an arm that was not. Whether the stale advice was
   adopted or rejected may still be legible in the saved traces, but reading it out needs a
   rubric this registration does not contain, and any such reading would be an exploratory
   analysis reported as one. The instance existed and remains available; A1's endpoint could
   not use it.
4. **No memory is *necessary* for any task** (declared at freeze, reported by the validator).
   §7b's discoverable/absent labels therefore have nothing to apply to, so this corpus measures
   neither retrieval efficiency nor retrieval necessity on a required fact.
5. **Four tasks, one model, one repository.** Nothing here generalises beyond
   `pallets/click` under `deepseek-flash`.
6. **Required-evidence completeness (§8 figure 2) remains deferred**, as §6 registered.

## What it does establish

- The instrument works end to end under adversarial conditions: deny-by-default boundary,
  per-arm-run private corpus, digest gate, reachability gate, and the fix-oracle control, on
  all 36 arm-runs, with the master store digest unchanged (`9ae2a9f268dd894d`) afterwards.
- The corpus was captured blind, by a session that never saw the held-out tasks, and the mix
  contains a genuine outdated instance arising from the chronological split rather than from
  anyone's design.
- **A memory arm that retrieves is not thereby a better arm.** On the evidence here it
  retrieved fluently — 11 of 13 memories delivered on h1, correctly including both stale ones —
  and converted none of it into a better outcome on any task.
- **It consumed more to do so.** Over all 12 arm-runs each, including failures:

  | recorded field | baseline | nexus | nexus / baseline |
  | --- | ---: | ---: | ---: |
  | `input_tokens` | 251 839 | 361 051 | **1.43×** |
  | `cache_read_input_tokens` | 5 224 704 | 8 202 624 | **1.57×** |
  | `cache_creation_input_tokens` | 0 | 0 | — |
  | input + cache-read | 5 476 543 | 8 563 675 | **1.56×** |
  | `output_tokens` | 136 651 | 164 255 | **1.20×** |

  An earlier version of this document reported only the **1.43×**. That figure is arithmetically
  right and materially incomplete: cache-read volume is roughly twenty times the uncached input,
  and omitting it understates what the memory arm consumed. Both fields are reported here and
  any combined figure is labelled as combined.

  **None of these is a dollar ratio.** `runner-a1.md` §3 found `"costBasis":"unknown"` on this
  model and concluded the dollar figure has no established provenance, so A1 quotes no cost.
  Token counts come from the provider and remain valid accounting; converting them to money
  would need verified rates this project does not have.

  Reported across all attempts rather than over successes only, because averaging an arm's
  consumption over just its wins flatters whichever arm fails more often.
- **Retrieval is front-loaded, and no body was fetched twice.** Of 12 `nexus` arm-runs, **four**
  reached a recorded source mutation; in those four, 22 retrieval calls fell before the first
  mutation and 1 after it. The **other eight reached no mutation and spent a further 63
  retrieval calls**, so the 22/1 split describes four runs and not the arm. No memory body was
  fetched twice in any arm-run.

  **Zero repeats does not dispose of batching.** Batching would combine several *different*
  body fetches into fewer agent–tool round trips, and this run says nothing about that
  mechanism either way. What the evidence rules out is only the narrower case for batching that
  rests on re-reading. Both figures describe the observed workflow and explain no failure.
