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

No confidence interval is computed. §5 registered that four tasks do not support an
inferential claim and that none would be made from one; a bootstrap here would invite exactly
the reading that registration forbids.

## The one non-tied cell, and what it is not

h2, nexus 1/3 against baseline 3/3. **It is not a case of memory giving bad advice.** In both
failing attempts the nexus arm produced a **0-byte patch** — it changed nothing, and scored
exactly the unpatched tree (`2 failed, 88 passed`, identical to the no-model control). h2's
declared mix is *unnecessary*: no memory in the corpus concerns usage-line rendering, and the
arm was delivered **zero** outdated memories and ~9.7 irrelevant ones.

What the traces show is turn allocation. Across all 12 nexus arm-runs the arm spent 2–11 calls
on `status`/`search`/`get` **before any source edit**, which is what its prompt asks of it, and
in 8 of 12 it never reached an `Edit`/`Write` call at all.

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

Exclusions are balanced at zero: **no arm is disadvantaged by environment failure**, so the
§5.3 imbalance threat does not apply. Truncation is not balanced — the notes arm finished
within the ceiling more often — and it is reported as a fact, not a verdict.

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

1. **Two of four tasks were solved by nobody.** h1 and h3 are 0/9 across all arms. They
   contribute a tie to every contrast and carry no information about memory. The effective
   comparison rests on h2 and h4.
2. **The ceiling dominates.** 29 of 36 arm-runs ended at `max_turns`. The registered 30-turn
   ceiling was calibrated on development tasks of 1–7 hunks; the held-out set runs to 5 hunks
   and 6 functions. It was **not** changed after seeing this, and must not be: the first result
   was in hand from the pilot row onwards. A future registration may set a different ceiling;
   this one may not.
3. **The outdated-memory hypothesis was not tested.** h1 is the task whose mix is *outdated*,
   and both memory arms were duly delivered both stale memories (`h02`, `h09`) as full bodies.
   But no arm solved h1 under any condition, so whether stale advice misleads an agent could
   not be observed. This is the question §7 most wanted an instance for, and the instance
   existed; the ceiling prevented its use.
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
