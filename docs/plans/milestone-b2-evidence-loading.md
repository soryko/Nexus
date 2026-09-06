# B2 — bounded evidence loading

**Status: measured and implemented 2026-09-06; corrected under review 2026-09-07 (see
[Amendments](#amendments)).** This is the slice [B2b](milestone-b2b.md)
deferred when it closed at `cdf51e7`. B2b's closing note named the work exactly: *bounded
evidence loading measured against a statistics-only control, with candidate selection
measured separately* — the comparison an automatic `ANALYZE` policy should be decided
against, and which B2b's own evidence did not justify.

Every claim below is marked **measured**, **assumed** or **decided**. The measurement of
record is [`tools/measure_evidence_loading.py`](../../tools/measure_evidence_loading.py) and
its as-run output is preserved verbatim at
[`benchmarks/dev/results-evidence-loading.txt`](../../benchmarks/dev/results-evidence-loading.txt).
The guard is [`tests/core/test_evidence_loading.py`](../../tests/core/test_evidence_loading.py).

## 1. What was wrong

**Measured, at `cdf51e7`.** `_hit_references` loads the reference evidence for one page of
hits. It built an OR-list — `(memory_id=? AND revision_id=?) OR …`, twenty of them for a
twenty-hit page — under a `namespace`/`actor` equality. With no statistics in
`sqlite_stat1`, SQLite served that by scanning the whole `(namespace, actor)` partition of
`revision_references` rather than by twenty point lookups on the primary key's autoindex.

The cost was therefore a function of the **scope**, not of the **page**. On a
20,000-memory scope it was 1,719,772 VDBE instructions to load evidence for twenty hits,
and — measured across all five filter shapes — that number does not move with the filter at
all. It is the same in the unfiltered control. Evidence loading was the dominant cost of a
browse search, and nothing about the query could reduce it.

`ANALYZE` changes the planner's mind, which is why B2b saw a large `ANALYZE` gain and why
amendment 5 located that gain in this stage. **Production never runs `ANALYZE`.**

## 2. The change

**Decided.** The page's pairs drive the lookup, one seek each:

```sql
WITH pairs(memory_id, revision_id) AS (VALUES (?,?), …)
SELECT r.… FROM pairs p CROSS JOIN revision_references r
  ON r.namespace=? AND r.actor=? AND r.memory_id=p.memory_id AND r.revision_id=p.revision_id
ORDER BY r.memory_id, r.revision_id, r.commit_oid, r.path
```

**`CROSS JOIN` is a plan constraint, not a semantic one.** In SQLite it is an inner join the
planner may not reorder, and that is the entire mechanism: it forces the twenty-row `pairs`
list to be the outer loop. The mechanism is not inferred from the measurement — it is
SQLite's documented guarantee that
[`CROSS JOIN` suppresses reordering and holds the tables in the order written](https://www.sqlite.org/optoverview.html#manual_control_of_query_plans_using_cross_join);
the measurement is what shows the plan the planner picks without it. Written as a plain `JOIN` the planner leads with
`revision_references` and scans the partition again — **measured** at 6,640,540 instructions,
worse than the form it replaces. The `CROSS` keyword is load-bearing and
`test_bounded_evidence_loading_is_bounded_in_both_statistics_states` fails without it.

**Decided.** The form shipped through `cdf51e7` is retained as a control, and retained
**outside production**: it lives in
[`tests/core/evidence_control.py`](../../tests/core/evidence_control.py) and is installed
over `SQLiteRepository._evidence_sql` for the duration of a test or a measurement arm.
`SQLiteRepository` has one evidence loader and no switch. It exists because a control that
is not executed is not a control, and it is kept honest by
`test_every_b2b_scenario_holds_under_the_unbounded_control`, which runs all nineteen B2b
acceptance scenarios through it. That turns the retained form from dead code into a
differential-testing oracle. `tools/measure_evidence_loading.py` imports the same module
rather than restating the statement, so the arm labelled *current* is the form that
shipped and not a copy of it that drifted.

## 3. Results

**Measured**, 20,000 memories, 40,000 reference rows, 3 repositories, one scope, 20-hit
pages, five browse shapes. Evidence-loading stage only; full tables in the as-run file.

| Evidence loading | current | statistics only | bounded only | both |
|---|---:|---:|---:|---:|
| instructions, four filtered shapes | 1,719,772 | 1,593 | **1,353** | 1,353 |
| instructions, unfiltered | 1,719,772 | 1,592 | **1,352** | 1,352 |
| best replayed latency (ms) | 9.1 – 12.6 | 0.044 – 0.045 | **0.039 – 0.043** | 0.040 – 0.045 |

Instruction counts reproduce to the digit (repeatability control: 1.000×). They are
identical across the four filtered shapes; the unfiltered shape is one instruction cheaper
in each post-change arm, reproducibly, and is reported as its own row rather than rounded
into the others. Four findings:

1. **Bounded loading removes the cost without statistics.** 1,719,772 → 1,353 instructions,
   a **1,271× reduction**, and ~9–12 ms → ~0.04 ms on the stage.
2. **It does not need `ANALYZE`, and after it, `ANALYZE` adds nothing to this stage.**
   "Bounded only" and "both" are identical to the instruction — 1,353 filtered, 1,352
   unfiltered. The *additional* benefit of statistics on evidence loading, once loading is
   bounded, is **zero**.

   **This is not an absence of interaction; it is a strong one.** Statistics are worth
   1,718,179 instructions under the shipped loader and 0 under the bounded one. The
   benefit of statistics therefore depends entirely on which loader runs — the two
   optimisations **substitute** for one another, which is interaction in the only sense
   the word has here. What is nil is the *increment*, and only that. An earlier draft of
   this document said "the interaction is nil"; that was wrong and it is withdrawn.
3. **Statistics alone get most of the way there** (1,593), but only by running `ANALYZE`.
   Bounded loading is 15% fewer instructions than statistics-only. On latency the two
   measured 0.045 vs 0.042 ms, a ratio of 1.07×.

   **That timing difference is reported descriptively and nothing is concluded from it.**
   The 1.64× figure is what this instrument returns when nothing changes; it is a
   description of its noise, **not a significance threshold**. A ratio inside it is not
   evidence that the two differ, and it is equally not evidence that they are equivalent —
   comparing a ratio against a spread establishes neither superiority nor equivalence. The
   comparison here is carried by the instruction counts, which are deterministic and
   reproduce to the digit; the millisecond figures are recorded for scale.
4. **Candidate selection is untouched by the change**, as intended: 361,054 → 360,860
   instructions for path-exact. But `ANALYZE` still moves it, and the comparison that
   matters is the one **within the bounded arms**, since bounded loading is what production
   now runs: unfiltered selection goes 780,203 → 500,546 instructions, a **35.84%
   reduction**. (The same effect measured in the unbounded arms is 780,397 → 502,415, or
   35.62%; the earlier draft quoted that pair and rounded it to 36%.) Filtered selection
   barely moves either way. So statistics buy something real on this stage *after* bounding
   — which is why §4's decision is scoped to evidence loading and leaves candidate
   selection open.

**Result identity is enforced, not assumed.** All four configurations return the same hits
in the same order with complete reference evidence, over a four-page pagination walk, for
every shape. The harness asserts it and fails otherwise.

## 4. The decision on `ANALYZE`

**Decided: no automatic `ANALYZE` policy *for evidence loading*. B2b's deferred question
is answered on that stage, and only on it.**

The case for one rested entirely on the gain amendment 5 located in this stage. Bounded
loading captures that gain outright, needs no statistics to do it, and leaves `ANALYZE`
with nothing to add here — finding 2 is the whole argument. An automatic policy would have
to decide when statistics are collected and refreshed, and carry the staleness that comes
with them, to buy a benefit that is now zero on this stage.

**Not decided: whether `ANALYZE` is worth collecting for candidate selection.** Finding 4
measures a real effect there and this slice does not act on it: after bounding, statistics
cut unfiltered candidate selection from 780,203 to 500,546 instructions, 35.84%. That is
not a scheduling policy and it does not become one by being large — it says nothing about
collection cost, refresh cadence, staleness, or whether the unfiltered browse is a shape
worth optimising for. But it is not nothing, and it is not covered by the decision above.
The question is open and belongs to the candidate-selection slice, which is the next
measured bottleneck; the statistics-only control is retained for it.

**Unchanged: migration `005` is still not written.** Nothing here bears on it. This slice
adds no index; it changes the shape of one statement.

## 5. What this evidence does not support

- **All five shapes are browse queries carrying no text.** The FTS branch of `search` is a
  different statement with a different plan, and nothing measured here describes it.
- **The end-to-end latency differences between the three non-baseline configurations are
  smaller than what this instrument returns when nothing changes** (1.64× across identical
  runs), and nothing is concluded from them in either direction. The spread is a noise
  description, not a test: it neither establishes that those configurations differ nor
  that they are equivalent. Only the current-to-bounded end-to-end difference is large
  enough to be discussed at all, and it is corroborated by a deterministic instruction
  count rather than resting on the timings.
- **No statement here is a claim about `ANALYZE` in the application generally.** §4 decides
  one stage. The statistics effect on candidate selection is measured, unexplained and
  unaddressed.
- **Candidate selection is now the dominant cost** of a browse search on this corpus —
  roughly 260,000–780,000 instructions against evidence loading's 1,353. This slice does
  not address it, and the next performance question is that stage, not this one.
- The instrument measures a single scope on one machine. The instruction counts are
  deterministic; the latencies are not, and are reported best-of-15 with the spread stated.

## 6. Two instrument defects caught by controls, and what they cost

Recorded because both would have produced a confident wrong answer, and neither is visible
in a summary number.

**1 — Statistics contamination, a 485× error.** `ANALYZE` writes `sqlite_stat1` *into the
database file*, where it persists. The pilot measured a "baseline" arm on a database an
earlier arm had analyzed and read the current loader at 3,545 instructions instead of
1,719,772 — it would have reported the problem this slice fixes as already absent. Every arm
now runs on a fresh copy of a pristine corpus and asserts the statistics state it believes
it is in.

**2 — A stage classifier that merged the two stages it existed to separate.** Statements are
labelled by the table they name. Under a reference filter the *candidate selection*
statement also names `revision_references`, inside the correlated `EXISTS` the filter
compiles to — so a classifier testing for `revision_references` first labelled selection as
evidence, summed both into one column, and reported **zero selection work for every filtered
shape**. The order of the tests is now load-bearing and documented as such, and
`verify_stages` asserts the structure the labels assume: exactly one selection statement, at
most one evidence statement, and no *evidence* statement that also reads `head_index`.

**That last assertion is one-directional, and has to be.** An earlier draft of this
document described it as "no statement carrying both tables", which contradicts the very
defect the paragraph above records: under a reference filter the selection statement
legitimately names both tables — `head_index`, and `revision_references` inside the
correlated `EXISTS`. A check for "no statement carrying both tables" would fail on correct
code. **Stage labels must be exclusive; table names cannot be.** What the classifier
actually needs is that no statement labelled *evidence* also reads `head_index`, which is
what the code asserts and what the prose now says. The code was right; the description of
it was wrong.

The predecessor harness's defect was the same family — a hand-rebuilt query measured beside
the shipped one — which is why this harness captures the executed SQL from
`set_trace_callback` with parameters already expanded, and prints it. Nothing here is
reconstructed.

## Amendments

### 1 — 2026-09-07, three qualifications and two record corrections; the change stands

Raised in review of the implementation commit. The measurements are unchanged and no
decision is reversed; four claims *about* those measurements were overstated and one
described the harness incorrectly. Corrected in place above and recorded here, because a
number that was wrong should not be able to leave the record quietly.

**1 — The `ANALYZE` rejection was stated too broadly (§4).** It read as a decision about
`ANALYZE` in the application. The evidence supports it for evidence loading only. The same
run shows statistics cutting unfiltered candidate selection, after bounding, from 780,203
to 500,546 instructions — 35.84%. That does not establish a worthwhile scheduling policy;
it does keep the question open for candidate selection, and §4 now says so and stops there.

**2 — "The interaction is nil" was wrong (§3, finding 2), and is withdrawn.** What is zero
is the *additional* benefit of statistics after bounding. The benefit of statistics is
1,718,179 instructions under one loader and 0 under the other, so it depends entirely on
which loader runs. The two optimisations substitute for one another, which is interaction,
not its absence. The measurement was right; the word was wrong.

**3 — The 1.64× spread is not a significance threshold (§3 finding 3, §5).** It is what
this instrument returns when nothing changes. The earlier text used it as a test — a ratio
inside it was called "not a result", which reads as a null finding. A spread supports
neither conclusion: not that two arms differ, and not that they are equivalent. The small
timing differences are now reported descriptively, and the comparisons rest on the
deterministic instruction counts.

**4 — The unfiltered bounded arm is 1,352 instructions, not 1,353.** The table quoted the
four filtered shapes' figure for all five. The unfiltered shape measures 1,352 in both
bounded arms and 1,592 under statistics-only, reproducibly. It now has its own row.

**5 — §6 misdescribed its own guard.** It said `verify_stages` asserts "no statement
carrying both tables". That contradicts the defect described in the same paragraph:
filtered candidate selection names `revision_references` inside a correlated `EXISTS`, so
such a check would fail on correct code. The assertion is one-directional — no *evidence*
statement may also read `head_index` — which is what the code has always done. Stage labels
must be exclusive; table names cannot be.

**Also in this revision, decided rather than corrected:** the retained control moved out of
`SQLiteRepository` into [`tests/core/evidence_control.py`](../../tests/core/evidence_control.py)
and the `BOUNDED_EVIDENCE` switch was removed from production. §2 records the new
arrangement. All nineteen B2b acceptance scenarios still run against the control and the
differential comparisons are unchanged: 272 passed, 2 skipped; 36 mutation tests passed.
