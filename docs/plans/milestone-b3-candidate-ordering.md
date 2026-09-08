# B3 — browse candidate ordering

**Status: measured and implemented 2026-09-08.** This is the slice
[B2](milestone-b2-evidence-loading.md) deferred when it closed at `c82939a`. B2 ended with
candidate selection as the dominant cost of a browse search — 261,000 to 780,000 progress
callbacks against evidence loading's 1,368 — and left one question open beside it: whether
`ANALYZE` is worth collecting for that stage, where it cut unfiltered selection by 35.84%.
Both are settled here.

Every claim below is marked **measured**, **assumed** or **decided**. The measurement of
record is [`tools/measure_candidate_selection.py`](../../tools/measure_candidate_selection.py)
and its as-run output is preserved verbatim at
[`benchmarks/dev/results-candidate-ordering.txt`](../../benchmarks/dev/results-candidate-ordering.txt).
**Every count in this document is from that file.**

**The unit is a progress callback.** SQLite invokes the handler during
[`sqlite3_prepare`](https://www.sqlite.org/c3ref/progress_handler.html) as well as inside
`sqlite3_step`, so a count covers preparation *and* execution and is not a count of executed
opcodes. The counting boundary is B2's corrected one: one whole call to a statement —
prepare it, run it, consume every row.

The guard is [`tests/core/test_candidate_ordering.py`](../../tests/core/test_candidate_ordering.py),
against the ordering retained in
[`tests/core/ordering_control.py`](../../tests/core/ordering_control.py).

## 1. What was wrong

**Measured, at `c82939a`.** The browse branch of `search` built its page as

```sql
SELECT * FROM (SELECT …, -h.durable_seq AS rank, … FROM head_index h …) ORDER BY rank, seq LIMIT ?
```

`head_index_recent` is `(namespace, actor, durable_seq DESC)` and carries exactly that
order. `rank` is `-h.durable_seq` — an expression, behind a wrapper — so the planner could
not use the index for the ordering and sorted every eligible row in the scope through a
temporary b-tree to return one page of twenty. `EXPLAIN QUERY PLAN` said
`USE TEMP B-TREE FOR ORDER BY` in all four arms of the experiment, in both statistics
states.

The cost was therefore a function of the **scope**, not of the **page** — the same shape of
defect B2 removed from evidence loading, in the stage B2 named as the next one to look at.
On a 20,000-memory scope an unfiltered first page cost **780,222 callbacks**, and 527,284
under the repository filter.

**`ANALYZE` changed the plan without removing the sort.** With statistics the unfiltered
arm drove the join from `head_index` instead of from `memories` — 780,222 → 500,561, the
35.84% B2 measured and did not act on — and still sorted the whole scope. **Production
never runs `ANALYZE`.**

## 2. The change

**Decided.** The browse page is ordered on the indexed column, unwrapped:

```sql
SELECT …, -h.durable_seq AS rank, … FROM head_index h … ORDER BY h.durable_seq DESC, h.seq LIMIT ?
```

Two details are load-bearing, in the way `CROSS` is load-bearing in B2's evidence loader.

**`h.seq` must be ASC.** Within one `durable_seq` the index carries rowid ascending, so
`h.seq DESC` is a different order from the one the index holds and reintroduces the sort:
measured at 760,222 callbacks against the ascending form's 704. `rank` is still selected,
unchanged, because that is what a cursor carries.

**The cursor predicate is two halves doing different jobs:**

```sql
AND h.durable_seq <= ? AND (h.durable_seq < ? OR h.seq > ?)
```

`durable_seq <= ?` is the half the index can use — it becomes a range constraint, and the
seek starts at the cursor. The disjunction resolves the tie that bound admits, on rows
already arrived at. Written as the disjunction alone — `(durable_seq < ? OR (durable_seq =
? AND seq > ?))`, the direct translation of the wrapper's predicate and the obvious way to
write it — the statement returns exactly the same rows and is **not** a range constraint at
all: the walk arrives at every row ahead of the page and discards it. Measured at page one
the two are indistinguishable; ten thousand rows deep they are 70,821 callbacks against 828.

The lexical branch is untouched. Its `rank` is a `bm25` score computed per row, which no
index carries, and the multi-leg form is already a `GROUP BY` over a union — the sort there
is not avoidable by ordering differently. The two forms are now two methods,
`_browse_statement` and `_ranked_statement`, which is also the seam the retained control is
installed over.

## 3. What it costs, measured

**Measured**, four configurations over five shapes, each on a fresh copy of a pristine
20,000-memory corpus, each asserting its own statistics state. Callback counts repeated
exactly across separate copies. The latency spread across identical runs is **1.42×**
(measured; the widest of three runs, which gave 1.06×, 1.42× and 1.05×) — see the
correction note below.

> **Correction (B3 review).** This section first reported that spread as 1.03×. The
> harness computed it by taking the maximum *directional* ratio across shapes and only then
> reciprocating that single winner, so a shape that ran faster on the second copy was
> discarded rather than counted: pairs of 0.50 and 1.03 reported 1.03×, when the widest
> pair was 2×. The calculation now reciprocates within each pair before taking the maximum.
> **The archived 1.03× could not be recomputed.** That run retained only the aggregate — the
> per-shape timings the corrected formula needs were never printed — so the figure above is
> a *new measurement on this machine*, not a re-derivation of the original run, and the two
> are not strictly comparable. The as-run output is now archived at
> `docs/measurements/b3-candidate-ordering-as-run.txt` so that the next such correction has
> something to recompute from.
>
> **The callback comparisons are unaffected.** They are counts, not timings; they repeated
> exactly across separate copies, and the reductions this section reports are three orders
> of magnitude — far outside any spread this instrument shows. What the correction changes
> is that the instrument's latency noise is wider than was stated, which *strengthens* the
> refusal to make a latency claim below rather than weakening any conclusion drawn here.

| candidate selection, page 1 | current | statistics only | ordered | both |
|---|---:|---:|---:|---:|
| unfiltered | 780,222 | 500,561 | **704** | 769 |
| repository_bound (33.3%) | 527,284 | 527,461 | **1,932** | 2,109 |
| path_exact (0.1%) | 360,999 | 361,181 | 360,698 | 360,880 |
| path_prefix (0.1%) | 481,053 | 481,301 | 480,752 | 481,000 |
| commit_exact (0.1%) | 261,161 | 261,364 | 260,860 | 261,063 |

| candidate selection, deepest page the shape allows | rows paged past | current | ordered |
|---|---:|---:|---:|
| unfiltered | 10,000 | 370,517 | **828** |
| repository_bound | 6,660 | 180,978 | **897** |
| the three sparse shapes | 0 | — | — |

**Density decides whether the change reaches a shape, and depth decides how much.** A
filter dense enough that a page fills before the walk runs out — the repository filter, one
memory in three — is 273× cheaper on its first page and 202× cheaper 6,660 rows in. A
filter that selects twenty memories from twenty thousand must exhaust its candidates
whatever the ordering, and moves by 301 callbacks, 0.08%: that is the removed sort of a
twenty-row result and nothing else. **The three sparse shapes have no deep page**: their
result sets end inside one page, so every "deep" figure for them is their first page, and
this slice measures nothing about a deep page under a sparse filter.

End-to-end, with every instrument removed and best-of-15 reported: the unfiltered page went
12.135 ms → 0.530 ms and the repository-bound page 16.927 ms → 0.541 ms. **The timings are
descriptive.** They are corroboration for a deterministic callback count, not the evidence
the decision rests on, and the spread above is a description of this instrument's noise
rather than a threshold.

## 4. `ANALYZE` is not worth collecting for candidate selection either

**Decided.** B2 §4 left this open because statistics cut unfiltered selection by 35.84% and
that was too large to dismiss without measuring. Measured against the reordered form, the
whole of that gain is subsumed — 500,561 was still a full sort, and 704 is not — and what
statistics add on top of the reordering is **negative in every shape**:

| statistics, after the reordering | ordered | both | added |
|---|---:|---:|---:|
| unfiltered | 704 | 769 | +65 |
| repository_bound | 1,932 | 2,109 | +177 |
| path_exact | 360,698 | 360,880 | +182 |
| path_prefix | 480,752 | 481,000 | +248 |
| commit_exact | 260,860 | 261,063 | +203 |

**These are counted work in this fixture, not a latency penalty.** The decision does not
rest on their size: it rests on there being no benefit left to weigh against the collection
cost, refresh cadence and staleness an automatic policy would have to carry. With B2's
decision for evidence loading, `ANALYZE` is now declined for both stages of a browse
search, on measurements of both.

**The decline is scoped to the strategy measured, not to the database.** Both stages were
measured as production runs them today: five browse shapes carrying no text, against the
correlated-`EXISTS` candidate statement. It says nothing about whether statistics are worth
collecting for the lexical branch, which §7 records as unmeasured here. Nor does it settle
the reference-driven shape of §6 — where the table shows the opposite sign, statistics
taking the path-indexed probe from 161,374 callbacks to 1,614. If that shape is ever
adopted, `ANALYZE` is an open question again and has to be measured against it.

**Unchanged: migration `005` is still not written.** This slice adds no index and changes
no schema; it changes the shape of one statement.

## 5. The confirmation protocol

**Decided**, and registered before the change was measured, informed by the pilot that
found it. The retained wrapper ordering is the oracle: it is not wrong, it returns exactly
the rows the shipped form returns, which is what makes it usable as one. Thirty-three
tests, in two obligations that pull in opposite directions on purpose, plus one added in
review that belongs to neither — the cursor's error contract.

**Interchangeable** — identical hits, identical order, identical cursor behaviour:

- **First pages and exhausted result sets**, over five shapes × two statistics states. Each
  walk is asserted to reach a page that offers no cursor, so the end of a result set is
  covered rather than assumed, and each is asserted to return hits and more than one page —
  two empty walks compare equal while asserting nothing.
- **Sparse and dense filters.** Three shapes at 0.1% of the corpus and one at 33.3%,
  because a sparse filter cannot exercise a page that fills before the candidates run out.
- **Deep pages**, by walking, and **completeness independently of the oracle**: the whole
  scope is walked and compared to what is in `head_index`, so an ordering that loses or
  repeats a row fails even if both forms lose it.
- **Ties on `durable_seq`.** The schema does not make it unique; a written corpus cannot
  produce a tie, because the value comes from the outbox's autoincrement. The fixture
  constructs one anyway — four rows to a value, walked at limit 3 so a page boundary falls
  *inside* a tie group — with a negative control asserting the fixture could tell an
  ascending tiebreak from a descending one.
- **Eligibility before the limit.** The ten most recent memories are tombstoned and a page
  of twenty is asserted to fill with twenty eligible hits. This is the failure an
  early-terminating walk invites and a sort of everything does not.
- **Cursor compatibility across the change.** Both forms mint the same cursor payload, and
  a cursor minted under either is asserted to page correctly under the other — the case of
  a caller holding a cursor across the deploy.
- **Malformed cursor fields are a cursor-domain error.** Added in review, with the defect it
  covers. Ordering on `h.durable_seq` means the cursor's `rank` is negated *in Python* to
  recover the sequence — the one place in this slice where a decoded payload reaches
  arithmetic instead of a bind parameter. The wrapper form bound `rank` and never negated
  it, so a non-numeric value merely compared false; under the new form `-"not-a-number"`
  raises `TypeError`, which is not a `NexusError` and is not in `search`'s except clause,
  and a caller who edited their own cursor was handed `internal_error: operation failed`.
  `(rank, seq)` is now established as numbers before any arithmetic sees it, and a bad one
  is answered as `cursor_expired`, in the same terms as an expired cursor. Ten malformed
  payloads — non-numeric, null, absent, wrong-typed, and `true`, which subclasses `int` and
  would otherwise negate to `-1` and page silently from the wrong position — are asserted at
  the repository, and the two that reproduced the original report are asserted end to end
  over real MCP stdio, where the code a caller reads is actually produced. Each is built by
  re-encoding a *minted* cursor so the fingerprint still matches and the tampered field is
  the only reason the call can fail, with a negative control asserting an untampered
  re-encode still pages.

**Not equivalent in cost** — asserted structurally through `EXPLAIN QUERY PLAN`, never by
timing, and each with a negative control without which it would assert nothing:

- The browse page carries no `ORDER BY` line in either statistics state, and the retained
  wrapper is asserted to carry one.
- A cursor page's `head_index_recent` line carries a `durable_seq<` range constraint in both
  statistics states, and the disjunction-only predicate is asserted not to. **Page two is
  one page from the top and cannot distinguish a seek from a filter**, which is why the
  constraint is asked of the planner rather than inferred from a walk.

Statements under test are captured from a real `search()` at the call, never reconstructed
beside it. `tests/core/test_reference_filter_plans.py` reconstructs a browse statement for a
different question and has been moved onto the shipped ordering for the same reason.

Suite: **308 passed, 0 skipped** (`NEXUS_MUTATION_MATRIX=1`; 306 passed, 2 skipped
without it, the two skips being the opt-in mutation matrices CI runs separately).

## 6. Recorded as motivation, not acted on: reference-driven candidate generation

**Measured**, and deliberately outside the comparison above: one page of one shape, no
pagination, no identity check, no walk. Driving the statement from `revision_references` —
materialising the matching `(memory_id, revision_id)` pairs once and seeking `head_index`
per pair — instead of probing a correlated `EXISTS` per head row:

| path_exact, page 1 | callbacks | how `revision_references` is reached |
|---|---:|---|
| no statistics, no index | 161,368 | scan of the `(namespace, actor)` partition |
| no statistics, path index | 161,374 | the index is present and not used |
| statistics, no index | 161,508 | scan of the `(namespace, actor)` partition |
| statistics, path index | 1,614 | `revision_references_path`, `path=?` |

Against the shipped form's 360,999 this is **55.3% less work with no schema change at all**,
and 224× less with both an index and statistics — a combination in which neither half is
sufficient: the index alone is ignored by the planner, exactly the failure mode B2's
evidence loader fixed with a plan constraint rather than with statistics.

**This does not reopen B2b's index decision.** That decision was taken against the existing
query shape, on total-work measurements that the attribution defect did not invalidate.
This is a *different* shape, and what it argues for is a separate experiment: whether
candidate generation should be driven from the reference side at all, and only then whether
an index and a plan constraint are the way to make it robust without statistics. The order
matters — a shape first, an index afterwards, each measured against the baseline the other
establishes.

## 7. What this evidence does not support

- **The sparse shapes are essentially untouched**, and the correlated `EXISTS` probed once
  per head row is exactly the cost §6 is about. Nothing here improves them.
- **No claim about deep pages under a sparse filter.** Those result sets end inside one
  page on this corpus, so the depth axis is measured only for the unfiltered and
  repository-bound shapes.
- **The lexical branch of `search` is a different statement with a different plan**, and
  nothing here describes it. Five browse shapes carrying no text is the whole fixture.
- **No latency claim in either direction.** The spread across identical runs reached 1.42×
  (three runs: 1.06×, 1.42×, 1.05×), and the millisecond figures are corroboration for the
  callback counts, not a substitute. This spread is wide enough that the millisecond
  figures corroborate only differences of the order the callback counts report, and nothing
  finer; a millisecond ratio inside 1.42× says nothing in either direction.
  Where a change adds callbacks — statistics, in §4 — that is counted work in this fixture
  and not a general latency penalty.
- **The callback count and the millisecond figure have different boundaries**: a stage's
  count is one whole call on a connection that has not seen the statement, and its replayed
  latency is a warm best-of-15. The two are not expected to agree to the digit.
- **No claim that the cursor range makes work independent of depth inside a tie.**
  `durable_seq <= ?` is a range constraint, so the seek skips the newer `durable_seq`
  values ahead of the page; that is what the 70,821-against-828 figure measures, on a
  corpus where `durable_seq` is effectively unique. The constraint admits an equal-sequence
  group whole, and inside one the remaining `h.seq > ?` is not a range constraint. The tie
  fixture ties eight rows across two values — enough to establish that pagination is
  correct across a tie boundary, and far too small to measure what a large tie group costs.
  The performance claim belongs to the measured fixtures; the tie tests are correctness.
- **The §6 probe is not a result about a shipped path.** It has none of the controls the
  four arms have, and nothing in §§1–5 rests on it.
- **One machine, one scope, one corpus shape.** The callback counts are deterministic; the
  latencies are not.
