# T2 — historical vocabulary: three variants over superseded revisions

Development data. **Nothing here is a v2 evaluation result.**

| | |
| --- | --- |
| Registered in | [v2-development-queries.md](../eval/v2-development-queries.md#t2--predeclaration-registered-2026-09-06) and [t2-predeclaration.md](t2-predeclaration.md) — corpus, variants and decision rule fixed before any variant ran |
| Corpus | `corpus-dev2.json` · sha256 `73df79f8…40ff0736` · dev1 unchanged inside it |
| Quality record | `results-dev-t2.json` — every query, every delivered item, every fusion count |
| Performance record | `results-dev-t2-perf-history_cued.json` — every block and every query duration |
| Baseline | pinned `stem`, head-only candidate generation (policy `baseline`) |
| Resource anchor | a fresh `exact`, measured in the same runs — never the stage baseline |

## Quality — the registered clauses, per variant

One run, four policies, on the extended corpus. Pinned revision `c86ec66`, SQLite 3.53.4.

| Clause | `history_headfirst` | `history_paired` | `history_cued` |
| --- | --- | --- | --- |
| 1 · `dq16` and `dq18` delivered as content, with provenance | **fails** — `dq18` missed | passes | **passes** |
| 2 · `dq21` delivers nothing, `dq24` still rank 1 | passes | passes | **passes** |
| 3 · `dq17` and `dq23` answer from the head, no obsolete revision delivered | passes | **fails** — obsolete delivered on both | **passes** |
| 4 · no per-query recall loss, `dq08`–`dq11` still rank 1 | passes | **fails** — `dq13` delivered recall 1.0 → 0.0 | **passes** |
| 6 · fusion ≤40 raw hits, collapse and truncation recorded | passes | passes | **passes** |

Every one of those outcomes was predicted in the predeclaration, including the two
failures and the reasons for them.

| Policy | revision **discovery** recall | revision **delivered** recall | obsolete revisions delivered | grade-0 bytes |
| --- | ---: | ---: | ---: | ---: |
| `baseline` | 0.3333 | **0.0000** | 0 | 18,185 |
| `history_headfirst` | 1.0000 | 0.6667 | 0 | 18,721 |
| `history_paired` | 1.0000 | **1.0000** | **9** | 18,495 |
| `history_cued` | 1.0000 | **1.0000** | 0 | 18,426 |

The baseline row is the T2 problem in one line: of three answer revisions, one came back
as an **id** and none came back as **content**. T1 recorded that id and called the case
reached; it was not.

### What separates the three

**`history_headfirst` cannot reach `dq18`, and no amount of ordering would help.** `d22`'s
current head — "Caption turnaround is 6 hours from ingest" — matches *what was the caption
turnaround before it changed* about as well as the superseded 48-hour revision does. The
memory therefore enters the pool through the head channel, and under head priority a
head-matched memory delivers its head. The historical channel found `d22r1` (discovery
recall 1.0) and the delivery rule discarded it. This is the same failure the baseline has,
one layer further in.

**`history_paired` reaches `dq18` by delivering both, and pays for it three times.** It
delivered nine obsolete revisions across the query set, including the 48-hour turnaround
for `dq23`, which asks what turnaround is **now**, and the superseded "`mux2` for every
asset" for `dq17`, whose head already says `mux2` is archive-only. Both are the controls
the charter demanded, and both fired. It also spent a delivered-item slot on the extra
item in `dq13` and pushed that query's rank-5 answer out of delivery entirely — the
precise displacement the predeclaration warned about.

**`history_cued` asks whether the query is about the past.** A registered cue list, read
from the query text alone, decides whether a memory matched on both channels may deliver
its superseded revision beside its head. `dq18` carries *was* and *before*; `dq23` carries
*now*; `dq17` and `dq13` carry neither. That one bit is the whole difference between the
two failing variants and the passing one.

| Query | `history_cued` delivered | Provenance |
| --- | --- | --- |
| `dq16` "flowreel" | `d20r1` | matched revision `d20r1` → head `d20r2` |
| `dq18` turnaround *before it changed* | `d22r2` **and** `d22r1` | head, plus matched revision `d22r1` → head `d22r2` |
| `dq22` "beaconcast" | `d26r1` | matched revision twenty-one steps back → head `d26r22` |
| `dq21` "pipehold" | *(nothing)* | the only carrier is forgotten |
| `dq23` turnaround *now* | `d22r2` only | present-tense cue suppresses the pair |

Fusion input peaked at exactly 40 raw hits (20 + 20) on four queries, with duplicate
collapse recorded — 20 collapsed hits on `dq18`, where every historical hit belonged to a
memory the head channel had already found. No history claim was denied a slot on this
corpus, so the `history_budget` attribution is implemented and never fired: untested.

Pool growth is recorded and is not a failure: `dq01` 19→20, `dq03` and `dq07` +1, `dq14`
+1, and `dq16`/`dq22` 0→1. No rank moved, and no query lost candidate or delivered recall.

## Performance and storage — `history_cued` against a fresh paired `exact`

Machine record: `results-dev-t2-perf-history_cued.json`. Only `history_cued` was
performance-measured: the other two variants had already failed a quality clause, and the
charter's ceilings decide a promotion, not a post-mortem. That choice is recorded here
rather than left implicit.

Three arms, six orders per fixture size, every arm in every position twice: `anchor`
(`exact` / no history / `baseline`), `stage_baseline` (`stem` / no history / `baseline`)
and `variant` (`stem` / full history / `history_cued`). The gates are computed variant
against anchor, as registered; the third arm is not a configuration and spends no variant
slot — it exists so the cost of stemming and the cost of the historical channel can be
told apart. Fixtures: 1,000 memories / 2,979 revisions and 10,000 / 29,950.

| Fixture | Order | Retrieval p95 (anchor → variant) | Ratio | ≤50 ms | ≤1.5× | Storage ratio | ≤2× | Cell |
| --- | --- | ---: | ---: | --- | --- | ---: | --- | --- |
| 1,000 | `anchor→variant` | 9.3922 → 13.9803 ms | 1.4885× | pass | pass | 2.5676× | **fail** | **fail** |
| 1,000 | `variant→anchor` | 7.8815 → 12.5949 ms | 1.5980× | pass | **fail** | 2.5676× | **fail** | **fail** |
| 10,000 | `anchor→variant` | 37.2549 → 53.6327 ms | 1.4396× | **fail** | pass | 2.6999× | **fail** | **fail** |
| 10,000 | `variant→anchor` | 19.5763 → 51.2873 ms | 2.6199× | **fail** | **fail** | 2.6999× | **fail** | **fail** |

**0 of 4 cells pass.** Writes clear their registered ceiling everywhere — the worst
`record` ratio is 1.1728× and the worst `revise` ratio 1.3754×, both against ≤2×.

### What is robust here, and what is not

**Storage fails, and that finding does not depend on the machine at all.** It is a count
of pages, identical in every block:

| Fixture | Structure | `exact` | `stem` (stage baseline) | `stem` + history |
| --- | --- | ---: | ---: | ---: |
| 10,000 | `head_fts` | 1,495,040 | 1,396,736 | 1,396,736 |
| 10,000 | `head_index` + `head_tags` | 4,059,136 | 4,059,136 | 4,059,136 |
| 10,000 | `revision_fts` | — | — | 2,744,320 |
| 10,000 | `revision_index` | — | — | **6,795,264** |
| 10,000 | **total** | **5,554,176** | 5,455,872 | **14,995,456** |

The historical channel costs **2.70× the whole `exact` retrieval structure** at 10,000
memories and 2.57× at 1,000, against a ≤2× ceiling. Stemming itself costs nothing —
`stem` is *smaller* than `exact` (0.98×). Nearly three quarters of the added bytes are
`revision_index`, the row table, not the FTS index over it: 6.8 MB of namespace, actor,
ids, kind, tags and timestamp for 19,950 superseded revisions.

**The relative retrieval cost is stable; the ratio against the anchor is not.** Across all
twelve block sets the variant measured **1.94×–2.35×** the stage baseline at 10,000 and
1.07×–2.13× at 1,000 — the historical channel roughly doubles retrieval p95. The ratio
against the anchor swung 1.24×–3.46× at 10,000 over the same blocks, because the anchor
arm itself swung between 14.8 and 37.3 ms. Two independently selected maxima again mask
that spread: the registered statistic reports 1.4396× for a cell whose worst *pair* is
3.4556×.

**This run's machine was materially slower than T1-C's.** T1-C measured `exact` at a
19.0476 ms worst block at 10,000; the same arm here reached 37.2549 ms. Absolute figures
from the two runs are not comparable, and the ≤50 ms failure is therefore **not**
established as a property of the variant independent of machine state — the variant
exceeded 50 ms in two of six blocks at 10,000 (53.6327 and 51.2873, against 37.8674 at
best). What *is* established is that the historical channel roughly doubles retrieval
cost, and that on this machine that was enough to cross an absolute ceiling the baseline
did not cross.

**The paired reading fails where the registered one passes, for writes.** Registered:
`revise` ≤1.3754×. Paired worst: **2.6609×** at 10,000 and 2.4294× at 1,000 — over the
≤2× write ceiling. Against the stage baseline the worst paired `revise` ratio is 3.0437×.
Maintaining a second index on every `revise` is real work, and the registered statistic
understates it exactly as [T1-C's correction](results-dev-t1c.md) said it can.

### Disclosures about how this run was made

- A **pilot** ran first at 1,000 memories only, with two arms, to check the harness. Its
  record is kept as `results-dev-t2-perf-pilot-1000.json` rather than deleted. It measured
  storage at 2.5743× — the complete run's 2.5676× — and it is not part of the verdict.
- The complete run started with an **uncommitted working tree**; the record lists the six
  paths, and the pinned revision is `c86ec66`. The harness and the quality results were
  committed immediately afterwards, in `5cc9045`.
- The complete schedule ran **once**, with no early stopping, no dropped blocks and no
  retries.
- Opening a copied fixture under the variant builds both indexes: 0.26–0.37 s at 10,000
  against 0.05 s for the stage baseline and 0.001 s for the anchor. That work is outside
  the timed region and is reported, not corrected — the same property T1-C recorded.

## Verdict — no variant clears the registered rule

| Variant | Quality clauses 1–4, 6 | Ceilings (clause 5) | Outcome |
| --- | --- | --- | --- |
| `history_headfirst` | fails 1 (`dq18`) | not measured | **not promoted** |
| `history_paired` | fails 3 and 4 | not measured | **not promoted** |
| `history_cued` | **all pass** | **fails storage in 4/4 cells; fails ≤50 ms at 10,000** | **not promoted** |

Under the registered decision rule, **`stem` with head-only candidate generation stands as
T2's outcome, and T3's baseline is `stem`.** The historical channel is not shipped, not
enabled by default, and not carried into v2.

## What T2 established, and what it did not

- **The mechanism works.** `history_cued` recovered every registered historical case —
  including a revision twenty-one steps back — resolved each to the correct live head with
  matched-revision provenance, delivered nothing from a forgotten memory, and lost no
  recall or rank anywhere else. Candidate generation over history is not the hard part.
- **Deciding *which* revision answers is the hard part**, and one bit of query text decided
  it here. `history_paired` and `history_cued` differ only in that bit, and it is the whole
  difference between failing two controls and passing them. A cue list authored against 24
  development queries is not evidence that the bit generalises.
- **The cost is in the row table, not the index.** Any follow-up should attack
  `revision_index` first: it is 6.8 MB of the 9.4 MB added at 10,000 memories, and most of
  its columns are duplicated from `revisions`.
- **`history_window3` was never priced.** It was registered, then deferred by the
  pre-run amendment. Whether a bounded window brings storage under 2× is unmeasured.
- **Nothing here is a v2 result**, and no v2 regression check was run: there is no
  promotion to check.
- **The `history_budget` attribution never fired.** Five slots were always enough on a
  24-query corpus, so that path is implemented and untested by measurement.
- **Two historical targets and three controls remain a smoke test.** A variant that
  recovers `dq16`, `dq18` and `dq22` has been shown to recover those three.
