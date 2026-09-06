# T2 predeclaration — corpus extension, variants and decision rule, recorded before any variant was run

Development data. **Nothing here is a v2 result.**

| | |
| --- | --- |
| Build | `2e49717` |
| Corpus | `corpus-dev2.json` · sha256 `73df79f8…40ff0736` (recorded in `results-dev-t2-baseline.json`) |
| Baseline | pinned `stem` (T1-C), policy `baseline` — head-only candidate generation, unchanged selection |
| Interpreter | `.venv-sqlite/bin/python`, SQLite 3.53.4 |
| Budgets | pool 20 · history 5×20 · delivered 5 items / 8 KiB incl. provenance · fusion input ≤40 raw hits · ≤3 variants |

The baseline is a control, not one of the three variants, so measuring it first spends no configuration-search budget. This file was committed **before the first variant existed as code**.

## The corpus extension — why dev1 was not enough

`corpus-dev1.json` carries two historical-vocabulary cases (`dq16`, `dq18`) and one control (`dq17`). Three registered T2 requirements cannot be tested on those alone:

| Registered requirement | Why dev1 cannot test it |
| --- | --- |
| "Resolve each to the correct **eligible** current head. A match that cannot be resolved to a live, in-scope head is not a recovery." | Every dev1 memory is live. An implementation that indexes revisions **without** the authoritative eligibility join passes every dev1 case. |
| "…including when that revision lies **outside the most recent twenty**." | The deepest dev1 history is two revisions. |
| "Historical matching must not automatically prefer obsolete evidence." | `dq17` tests this on an identifier (`mux2`). It is not tested where head and history share almost all their vocabulary. |

`corpus-dev2.json` is `corpus-dev1.json` **plus** two memories and four queries. No dev1 memory, revision, query or label is edited, so `corpus-dev1.json` stays frozen and `results-dev-t1.json` stays reproducible against it.

| New item | What it is | What it tests |
| --- | --- | --- |
| `d25` | Two revisions, then **forgotten**. The superseded revision still contains `pipehold`. | Eligibility. The row exists in `revisions`; nothing may deliver it. |
| `d26` | 22 revisions. `beaconcast` appears only in `d26r1`, twenty-one revisions back. | The out-of-twenty clause. |
| `dq21` "pipehold" | Unanswerable: the only carrier was forgotten. | Correct behaviour is **zero results**. |
| `dq22` "beaconcast" | Answer is `d26r1`. | Reachability and charging of a deep revision. |
| `dq23` "how long does caption turnaround take now" | Answer is `d22`'s **current head** (6 hours). | Head must win where head and history nearly share vocabulary. |
| `dq24` "assetline" | Answerable from `d20`'s current head. | The pair for `dq21`. A history channel tuned to return nothing scores correctly on `dq21` and fails here. |

Authoring followed the rule that governed dev1: implementation-side, not blind-assessed, written before any variant existed, and frozen at this commit.

## Registered interpretation — the out-of-twenty clause

The charter says retrieving a historical revision that supplies the answer "counts against the same history-expansion budget — including when that revision lies outside the most recent twenty." That is read as: **a revision matched directly by a historical index is reachable, and delivering it consumes one of the five history-expansion slots.** The twenty-revision limit bounds *enumerating* a memory's history; it does not put a directly matched revision out of reach. The other reading would make `dq22` unrecoverable by construction, which is a rule about the harness rather than about retrieval.

## Baseline behaviour — `stem`, policy `baseline`, on `corpus-dev2.json`

Recorded in `results-dev-t2-baseline.json`. This is the row every variant is measured against.

| Case | Baseline | What is wrong with it |
| --- | --- | --- |
| `dq16` "flowreel" | pool **0**, nothing delivered | Head-only search never gives history a memory id to expand. |
| `dq18` caption turnaround before it changed | pool 20, `d22r1` **discovered**, `revision_delivered_recall` **0.0** | History expansion returned the revision id; the 48-hour text was never delivered. A surviving id is discovery, not evidence. |
| `dq22` "beaconcast" | pool **0**, nothing delivered | Same shape as `dq16`, twenty-one revisions deep. |
| `dq21` "pipehold" | pool **0** | Correct — and trivially so: nothing can go wrong until a historical index exists. |
| `dq17` `mux2` | `d21` rank 1 | Correct. |
| `dq23` turnaround now | `d22` rank 1 | Correct. |
| `dq24` "assetline" | `d20` rank 1 | Correct. |

Aggregates: revision **discovery** recall **0.3333** (1 of 3), revision **delivered** recall **0.0**. Delivered bytes — grade 0: 18,185 · grade 1: 785 · grade 2: 5,212. Retrieval structures: 49,152 bytes (`head_fts` 24,576 · `head_index` 12,288 · `head_tags` 12,288).

Effect of the extension on dev1 queries, measured: `dq12` pool 15→16 and `dq13` pool 16→17. **No rank, no delivery and no byte total changed on any dev1 query.**

## The three variants — registered before any of them was written

All three share one new persistent structure, `revision_fts`: an FTS5 index over the bodies of **non-current** revisions, tokenised exactly as the head index for the profile in force (porter, since the baseline is `stem`). Every hit passes the authoritative eligibility join — tombstoned memory, wrong scope, or a revision that is now the head is filtered **inside the query**, so a filtered row never occupies one of the 40 fusion slots.

| # | Variant | Candidate generation | Fusion order |
| --- | --- | --- | --- |
| 1 | `history_headfirst` | head ≤20 hits + history ≤20 hits over **every** superseded revision | Strict head priority: all head hits in rank order, then history-only memories in theirs. |
| 2 | `history_interleaved` | Same index, same channels | One fused ordering by BM25 rank across both channels, best-of per memory. Head priority applies only as a tie-break. |
| 3 | `history_window3` | Index holds only the **3 most recent superseded revisions per memory** | `history_headfirst`'s ordering, unchanged. |

Variant 2 exists because best-of across two indexes combines BM25 scores computed over **different corpus statistics**; that is a stated heuristic, not a principled combination, and the same caveat already stands on the `dual` profile. Variant 3 exists to price the full-history index: it is **predicted to miss `dq22`**, because `beaconcast` lies twenty-one revisions back. That prediction is recorded here, before the run.

Shared delivery rules, identical across all three so that no variant wins by spending a different budget:

- A memory that entered the pool **via head** delivers its head, as today.
- A memory that entered **only via history** delivers the **matched revision's content**, with provenance `{memory_id, revision_id, current_revision_id}` — provenance counted against the 8,192-byte budget as always.
- Delivering a matched superseded revision **consumes one of the five history-expansion slots**. With no slot left it is not delivered, and the miss is attributed `history_budget`.
- Duplicate collapse, per-channel truncation and per-channel shortfall are recorded on every query. A short channel is **not** refilled from the other one.

## Decision rule — registered before the run

A variant is eligible for promotion only if **all** of these hold. The complete set runs once; failures keep their rows and their numbers.

1. **`dq16` and `dq18` recovered** — the matched superseded revision's *content* delivered inside the budget, with matched-revision provenance resolved to the correct current head. Discovery alone is not recovery.
2. **`dq21` delivers nothing**, and its pair **`dq24` still answers at rank 1**.
3. **`dq17` and `dq23` still answer from the current head at rank 1**, with no superseded revision delivered for either.
4. **No per-query loss** of candidate recall or delivered recall on `dq01`–`dq15`, `dq19`, `dq20`, `dq24` against the baseline row above; `dq08`–`dq11` answer ranks stay 1. Pool growth is recorded, not counted as failure.
5. **Resource ceilings**, against a fresh **paired `exact`** measured in the same run: retrieval worst-block p95 **≤50 ms and ≤1.5×**; record/revise p95 **≤2×**; persistent retrieval structures **≤2×**, counting `revision_fts` and everything it brings.
6. **Fusion input ≤40 raw hits**, with collapse, truncation and shortfall recorded.

Among variants that pass all six, the pinned winner is chosen in this order:

1. recovers `dq22` (the out-of-twenty case);
2. then **lowest added retrieval-structure bytes**;
3. then **lowest retrieval p95 ratio** against `exact`.

If no variant passes, **`stem` with policy `baseline` stands as T2's outcome** and T3's baseline is `stem`. A variant that recovers both target cases and fails a ceiling is a recorded result, not a winner.

## Controls, extended as the charter requires

From T2 the empty-index control clears **every candidate-generating index** — `head_fts`, `head_tags`, `head_index` **and** `revision_fts` — with authoritative rows and the head projection retained, and must still return zero results. A variant that adds an index without adding it to the control is a defect in the control.

## Known limits of this experiment

- Labels and both corpus extensions are implementation-side and were not blind-assessed. It is development data and is treated as such.
- Four historical cases and three controls are a smoke test. A variant that recovers `dq16`, `dq18` and `dq22` has been shown to recover *those three*.
- `dq22`'s deep history is one memory with 22 revisions. It shows that a directly matched deep revision is reachable and charged; it says nothing about how cost scales with history depth in general.
- BM25 fusion across two indexes with different corpus statistics is a heuristic, and variant 2's ordering rests on it.
