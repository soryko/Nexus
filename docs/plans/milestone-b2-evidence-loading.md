# B2 — bounded evidence loading

**Status: measured and implemented 2026-09-06; corrected under review and re-measured
2026-09-07 (see [Amendments](#amendments)).** This is the slice [B2b](milestone-b2b.md)
deferred when it closed at `cdf51e7`. B2b's closing note named the work exactly: *bounded
evidence loading measured against a statistics-only control, with candidate selection
measured separately* — the comparison an automatic `ANALYZE` policy should be decided
against, and which B2b's own evidence did not justify.

Every claim below is marked **measured**, **assumed** or **decided**. The measurement of
record is [`tools/measure_evidence_loading.py`](../../tools/measure_evidence_loading.py).
Two as-run outputs are preserved verbatim: the run this slice shipped on, at
[`benchmarks/dev/results-evidence-loading.txt`](../../benchmarks/dev/results-evidence-loading.txt),
and the run after amendment 2 re-attributed the instrument, at
[`benchmarks/dev/results-evidence-loading-reattributed.txt`](../../benchmarks/dev/results-evidence-loading-reattributed.txt).
**Every count in §§1–5 is from the second.** §6 and the amendments quote the readings they
are *about*, superseded ones included, and say which. Both as-run files are preserved byte
for byte, including the first: a superseded measurement is evidence about the instrument,
and deleting it would leave the correction unfalsifiable.

**The unit is a progress callback, and it is called that here.** Earlier drafts of this
document and both as-run files call these numbers "instructions", "VDBE instructions",
"steps" or "VM steps"; that name was wrong from the beginning and amendment 2 did not
change what is counted, only what it is charged to. SQLite invokes the progress handler
during [`sqlite3_prepare`](https://www.sqlite.org/c3ref/progress_handler.html) as well as
inside `sqlite3_step`, so a callback count covers **preparation and execution**, not
executed opcodes. The two as-run files still carry the old column labels — they are raw
records and are not edited — and their numbers are unchanged by the relabelling.
The guard is [`tests/core/test_evidence_loading.py`](../../tests/core/test_evidence_loading.py).

## 1. What was wrong

**Measured, at `cdf51e7`.** `_hit_references` loads the reference evidence for one page of
hits. It built an OR-list — `(memory_id=? AND revision_id=?) OR …`, twenty of them for a
twenty-hit page — under a `namespace`/`actor` equality. With no statistics in
`sqlite_stat1`, SQLite served that by scanning the whole `(namespace, actor)` partition of
`revision_references` rather than by twenty point lookups on the primary key's autoindex.

The cost was therefore a function of the **scope**, not of the **page**. On a
20,000-memory scope it was 1,719,981 progress callbacks to load evidence for twenty hits, and —
measured across all five filter shapes — that number does not move with the filter at all.
It is the same in the unfiltered control. Evidence loading was the dominant cost of a
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
`revision_references` and scans the partition again — **measured** at 6,640,540 callbacks,
worse than the form it replaces. That figure alone was taken under the attribution amendment
2 replaced, which misplaced a few hundred callbacks at most; against a 6.6-million to
1.7-million comparison it is immaterial, and it is left as measured rather than restated
from a run that was not repeated. The `CROSS` keyword is load-bearing and
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

Counts are **progress callbacks**, and a stage's number is the cost of **one whole call** to
its statement, on a schema-loaded connection that has not seen that statement before:
prepare it, run it, consume every row. Preparation is inside the number because the counter
counts it and because production pays it — `search()` opens a connection per call. The
figures below are therefore not comparable to the ones this document carried before
amendment 2, which measured the gaps between statement boundaries and so charged each
statement's preparation to the statement ahead of it.

| Evidence loading | current | statistics only | bounded only | both |
|---|---:|---:|---:|---:|
| callbacks, four filtered shapes | 1,719,981 | 3,546 | **1,368** | 1,437 |
| callbacks, unfiltered | 1,719,981 | 3,545 | **1,367** | 1,436 |
| best replayed latency (ms) | 8.7 – 9.9 | 0.145 – 0.177 | **0.040 – 0.044** | 0.060 – 0.065 |

Callback counts reproduce to the digit (repeatability control: 1.000×). They are identical
across the four filtered shapes; the unfiltered shape is one callback cheaper in every arm
but `current`, reproducibly, and is reported as its own row rather than rounded into the
others. Four findings:

1. **Bounded loading removes the cost without statistics.** 1,719,981 → 1,368 callbacks, a
   **1,257× reduction**, and ~8.7–9.9 ms → ~0.04 ms on the stage.
2. **It does not need `ANALYZE`, and after it, `ANALYZE` is a small net cost.** "Bounded
   only" is 1,368 and "both" is 1,437: statistics **add 69 callbacks per call, 5.04%**, and
   the replayed stage latency moves from 0.043 to 0.063 ms. This document previously
   reported that increment as exactly **zero**, which was an artefact — the old boundary put
   the preparation `ANALYZE` adds into the statement ahead of the evidence load, where it
   was invisible. §4's decision does not change direction and its ground is firmer: after
   bounding, statistics are not merely useless on this stage, they cost a little.

   **Those 69 callbacks are additional counted work in this fixture, not a latency
   penalty.** The millisecond figures are reported for scale and nothing is concluded from
   them; see §5.

   **This is not an absence of interaction; it is a strong one.** Statistics are worth
   1,716,435 callbacks under the shipped loader and −69 under the bounded one. The
   benefit of statistics therefore depends entirely on which loader runs — the two
   optimisations **substitute** for one another, which is interaction in the only sense
   the word has here. An earlier draft of this document said "the interaction is nil"; that
   was wrong and it is withdrawn.
3. **Statistics alone do not get most of the way there** (3,546 against 1,368): the bounded
   loader is **2.59× cheaper**, not the 15% this document claimed before amendment 2.

   **That ratio is over the whole prepare-run-consume cycle of a first call, and it is not
   decomposed.** This instrument reports one figure per call; it does not separate first
   preparation from re-preparation from execution, so none of the 2,178-callback gap is
   attributed here to any one of the three. What *is* separately measured is a preparation
   surcharge a warm connection does not remove under statistics: **1,142** callbacks for the
   OR-list against the bounded form's **56**, on a statement that connection had already
   prepared once (209 against 15 without statistics, where warming does remove it). The
   mechanism SQLite documents for that is
   [`ENABLE_STAT4`](https://www.sqlite.org/c3ref/prepare.html) — with statistics present the
   planner uses parameter *values*, so a statement is re-prepared when its bindings change,
   and evidence loading's bindings are the page's own pairs. That is the documented
   explanation for the surcharge, not a measured account of the full ratio. The old
   instrument charged the surcharge to candidate selection, which is why statistics-only
   looked close to bounded and no longer does. On latency the two measured 0.148 vs
   0.043 ms, reported for scale.
4. **Candidate selection is untouched by the change**, and now measures so exactly: under
   both loaders path-exact is **360,999** callbacks, and in every shape the two loaders
   match to the digit — in both statistics states. This document previously reported
   361,054 → 360,860 and read the 194-callback difference as the change
   nearly-but-not-quite missing the stage; the
   selection SQL is byte-identical between those two arms, and the difference was the
   evidence statement's preparation charged to the statement before it. The harness now
   asserts this equality as a control rather than reporting the discrepancy as a result.

   `ANALYZE` does still move this stage, and the comparison that matters is the one **within
   the bounded arms**, since bounded loading is what production now runs: unfiltered
   selection goes 780,222 → 500,561 callbacks, a **35.84% reduction**. Filtered selection
   moves slightly the other way (+182 callbacks for path-exact). So statistics buy
   something real on this stage — which is why §4's decision is scoped to evidence loading
   and leaves candidate selection open.

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
with them, to buy a benefit that is not zero on this stage but negative: after bounding,
statistics cost 69 callbacks a call.

**Not decided: whether `ANALYZE` is worth collecting for candidate selection.** Finding 4
measures a real effect there and this slice does not act on it: after bounding, statistics
cut unfiltered candidate selection from 780,222 to 500,561 callbacks, 35.84%. That is
not a scheduling policy and it does not become one by being large — it says nothing about
collection cost, refresh cadence, staleness, or whether the unfiltered browse is a shape
worth optimising for. But it is not nothing, and it is not covered by the decision above.
The question is open and belongs to the candidate-selection slice, which is the next
measured bottleneck; the statistics-only control is retained for it.

> **Answered by [B3](milestone-b3-candidate-ordering.md), 2026-09-08.** Declined. That
> 35.84% was statistics changing which table drove a join that still sorted the whole
> scope; ordering the browse page on `head_index_recent` removes the sort and the gain with
> it — 780,222 → 704 callbacks unfiltered — and statistics then *add* 65 to 248 callbacks
> in every shape. `ANALYZE` is now declined for both stages of a browse search, on
> measurements of both.

**Unchanged: migration `005` is still not written.** Nothing here bears on it. This slice
adds no index; it changes the shape of one statement.

## 5. What this evidence does not support

- **All five shapes are browse queries carrying no text.** The FTS branch of `search` is a
  different statement with a different plan, and nothing measured here describes it.
- **The end-to-end latency differences between the three non-baseline configurations are
  smaller than what this instrument returns when nothing changes** (1.17× across identical
  runs), and nothing is concluded from them in either direction. The spread is a noise
  description, not a test: it neither establishes that those configurations differ nor
  that they are equivalent. Only the current-to-bounded end-to-end difference is large
  enough to be discussed at all, and it is corroborated by a deterministic callback count
  rather than resting on the timings.
- **No statement here is a claim about `ANALYZE` in the application generally.** §4 decides
  one stage. The statistics effect on candidate selection is measured, unexplained and
  unaddressed.
- **Candidate selection is now the dominant cost** of a browse search on this corpus —
  roughly 261,000–780,000 callbacks against evidence loading's 1,368. This slice does
  not address it, and the next performance question is that stage, not this one.
- The instrument measures a single scope on one machine. The callback counts are
  deterministic; the latencies are not, and are reported best-of-15 with the spread stated.
  **No latency claim is made in either direction.** Where a change adds callbacks, that is
  additional counted work in this fixture; it is not a general latency penalty, and this
  document does not treat it as one.
- **The callback count and the millisecond figure have different boundaries.** A stage's
  callback count is one whole call on a connection that has not seen the statement before;
  its replayed latency is a warm best-of-15 on a connection that has run it fifteen times.
  How much preparation survives into the warm figure is not established here — so the two
  are reported side by side and are not reconciled into one number.
- **No stage figure is decomposed.** Each is one prepare-run-consume cycle. Where two arms
  differ, this instrument does not say how much of the difference is first preparation, how
  much is re-preparation, and how much is execution.
- **An accounting check, not a stage result.** Across all twenty rows of the run of record,
  end-to-end callbacks minus the two stage figures is **15**, exactly — the same in every
  arm and every shape. The two instruments are not expected to agree to the digit; a
  residual that does not move with the arm is evidence that neither drifts against the other
  in a way a comparison between arms would pick up.

## 6. Three instrument defects, and what they cost

Recorded because each would have produced a confident wrong answer, and none of them is
visible in a summary number. The first two were caught by controls this harness already had.
**The third was not caught by anything, and is the reason a fifth control exists.**

**1 — Statistics contamination, a 485× error.** `ANALYZE` writes `sqlite_stat1` *into the
database file*, where it persists. The pilot measured a "baseline" arm on a database an
earlier arm had analyzed and read the current loader at 3,545 instead of 1,719,772 — it
would have reported the problem this slice fixes as already absent. (Both figures are as the
pilot measured them, under the attribution amendment 2 replaced and under the unit name it
also corrected; the 485× is a ratio between two readings of the same instrument and survives
both changes.) Every arm
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

**3 — A statement boundary that charged each statement's preparation to the statement
before it.** Steps were attributed by reading the progress-handler counter at each statement
boundary the trace reports: the count between statement *n* starting and statement *n+1*
starting was called statement *n*'s. But SQLite calls the progress handler while it
**prepares** a statement as well as while it steps one, and the trace fires when a statement
starts *running* — after its own preparation. So each span was one statement's execution plus
the next statement's preparation. It follows that a callback count is preparation *and*
execution, whatever it is attributed to: "instructions" and "VM steps" were never the right
name for it.

That is not a rounding error on this measurement, because the two arms differ in precisely
the statement whose preparation was misplaced. Preparing the OR-list evidence statement costs
209 callbacks against the bounded form's 15 — preparing an OR-list grows with its terms,
preparing the bounded form does not — and the 194-callback difference was reported as
**candidate selection being 194 callbacks cheaper under bounded loading**, on a statement
whose SQL is byte-identical between those arms. Under `ANALYZE` the same defect was worth
1,142 against 56, and it is what made statistics-only look 15% behind bounded loading rather
than 2.59× behind.

Warming the statement cache does not fix it, and why not is worth recording: this build has
`ENABLE_STAT4`, so once statistics exist the planner uses parameter *values* and SQLite
re-prepares on changed bindings. Measured: under statistics the surcharge persists on every
call however warm the cache is, so a warm-cache boundary would have been honest in the two
arms without statistics and wrong in the two with them. The fix is to charge preparation to
the statement that pays for it: each stage is now measured as one whole call — prepare, run,
consume — on a connection that has loaded the schema and never seen that statement,
re-executing the `(sql, parameters)` pair recorded at the call rather than the trace's
expanded text, which compiles to a different program. **The figure is not decomposed**: it
does not separate first preparation from re-preparation from execution, and no comparison
here claims otherwise.

**The control that would have caught it, and now does.** Where two arms ran byte-identical
SQL for a stage under the same statistics state, that stage must measure identically. Nothing
in the first version of this harness asserted that, so a difference that could only be an
artefact was available to be read as a finding — and was. The harness also asserts that the
statements it traced and the calls it recorded are the same list, which is how it found that
`search()` closes its snapshot through `commit()` rather than through `execute`.

The predecessor harness's defect was the same family as the first two — a hand-rebuilt query
measured beside the shipped one — which is why this harness captures the executed SQL from
`set_trace_callback` with parameters already expanded, and prints it. Nothing here is
reconstructed.

## Amendments

### 1 — 2026-09-07, three qualifications and two record corrections; the change stands

**Every count in this amendment predates amendment 2 and is superseded by it, and its unit
name — "instructions" — is the one amendment 2 corrected to "progress callbacks".** It is
left as written because it is the record of what was corrected when; the current figures,
under the current name, are in §3 and in amendment 2's table.

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

### 2 — 2026-09-07, the instrument was re-attributed and the slice re-measured; the change stands

Raised in review of amendment 1. The production loader was not in question and is not
changed. What was wrong is how the harness divided work between the two stages it exists to
separate, and the correction moves numbers in both of them.

**1 — The defect.** Work was attributed to a statement by reading the progress-handler
counter at each statement boundary the trace reports. SQLite calls that handler while it
*prepares* a statement as well as while it steps one, and the trace fires when a statement
starts running, after its own preparation — so every span was one statement's execution plus
the next statement's preparation. §6 records it in full. The reproduction is small and is the
whole argument: preparing the OR-list evidence statement takes 209 callbacks and preparing
the bounded form takes 15, and the harness reported candidate selection as exactly 194
callbacks cheaper under bounded loading — 361,054 against 360,860 — on a statement whose SQL
is byte-identical between those two arms.

**2 — What the fix is.** Each stage is now measured as one whole call to its statement:
prepare it, run it, consume every row, on a connection that has loaded the schema and has
never seen that statement. What is re-executed is the `(sql, parameters)` pair recorded at
the call, not the trace's expanded text, because expanded literals compile to a different
program from bound parameters. A fifth control asserts what the defect violated: byte-
identical SQL under the same statistics state must measure identically. Warming the statement
cache was tried first and rejected: the surcharge vanished in the two arms without statistics
and persisted in the two with them, so that boundary would have been honest in half the
comparison and wrong in the other half. The mechanism SQLite documents for the persistence is
[`ENABLE_STAT4`](https://www.sqlite.org/c3ref/prepare.html), which this build has.

**Correct attribution does not change the unit.** These counts were called "instructions",
"VDBE instructions" or "steps" throughout; they are **progress callbacks**, and SQLite
invokes the handler during
[`sqlite3_prepare`](https://www.sqlite.org/c3ref/progress_handler.html) as well as inside
`sqlite3_step`, so they cover preparation as well as execution and are not executed opcodes.
The harness and §§1–5 now say "callbacks". Both as-run files keep their original column
labels, because they are raw records and are not edited; the numbers in them are unaffected
by the renaming, and the run of record predates it.

**3 — What moved.** Re-measured in full; the as-run is
[`results-evidence-loading-reattributed.txt`](../../benchmarks/dev/results-evidence-loading-reattributed.txt)
and the superseded run is kept beside it.

| Evidence loading, filtered shapes | before | after |
|---|---:|---:|
| current | 1,719,772 | 1,719,981 |
| statistics only | 1,593 | 3,546 |
| bounded only | 1,353 | 1,368 |
| both | 1,353 | 1,437 |
| candidate selection, path-exact, no statistics | 361,054 / 360,860 | 360,999 / 360,999 |

**4 — Three conclusions changed; the decision did not.**

- **"After bounding, `ANALYZE` adds nothing" is withdrawn.** It adds 69 callbacks per call,
  5.04%, and 0.043 → 0.063 ms. The increment was reported as zero because the preparation
  `ANALYZE` adds was being charged to the statement ahead of the evidence load. §4's
  decision to decline an automatic `ANALYZE` policy for this stage is unchanged and rests on
  firmer ground: the benefit is not zero, it is slightly negative. **The 69 are counted work
  in this fixture, not a latency penalty** — the timings stay descriptive.
- **"Statistics alone get most of the way there, 15% behind" is withdrawn.** Statistics-only
  is 3,546 against bounded loading's 1,368 — **2.59× behind**, not 15%. **That ratio is over
  the whole prepare-run-consume cycle and is not decomposed:** the instrument does not
  separate first preparation, re-preparation and execution, so no share of the gap is
  attributed to any of them here. The separately measured piece is the preparation surcharge
  a warm connection does not remove under statistics — 1,142 callbacks against the bounded
  form's 56 — which the old boundary hid inside candidate selection.
- **"Candidate selection is untouched, 361,054 → 360,860" is corrected to an equality.**
  Both loaders measure 360,999, in every shape and in both statistics states. The
  statistics effect on that stage is unchanged in substance: unfiltered selection goes
  780,222 → 500,561, 35.84%, the same percentage this document already recorded.
- **Unchanged:** the 1,271× headline becomes 1,257×; result identity across all four
  configurations still holds over the four-page walk; migration `005` is still unwritten;
  the `CROSS` keyword is still load-bearing.

**An accounting check on the two instruments.** Across all twenty rows of the run of record,
end-to-end callbacks minus the two stage figures is **15**, exactly, in every arm and every
shape. The per-call cost and the in-place total are different boundaries and are not expected
to agree to the digit; a residual that does not move with the arm is what says neither drifts
against the other in a way a between-arm comparison would pick up.

**5 — Two stale statements in the harness, left behind by amendment 1.** The corrections
amendment 1 made to this document had not been made to the code that produced it: the module
docstring still called a latency ratio inside the repeatability spread "not a result", and
`verify_stages`'s docstring still described its guard as "no statement carrying both tables".
Both now say what amendments 1.3 and 1.5 say. A correction recorded only in prose is a
correction the next reader of the code will not get.

The suite is unaffected — the guard tests assert plan shape through `EXPLAIN QUERY PLAN` and
never read this counter: 272 passed, 2 skipped.
