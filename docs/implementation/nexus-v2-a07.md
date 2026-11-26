# A07 lead-review receipt

Base `e621020413813f009246a463359b763e2f32ad98`, `feat/nexus-memory-v2`. Working-tree changes only. The test fixture makes commits exclusively in pytest temporary Git repositories to compute real release, main and unseen OIDs.

## Reproduction

Run with `UV_OFFLINE=1` and the existing local environment:

```
uv run pytest -q tests/graph/test_evidence.py tests/graph/test_support.py tests/graph/test_temporal.py tests/core/test_reference_compatibility.py tests/core/test_evidence_loading.py
```

- Red before implementation: exit 1, 4 failed on the missing graph evidence/eligibility modules, 57 passed. Additional retention test first failed at the absent `GraphService.retain` method.
- Final named gate after review fixes: exit 0, **69 passed in 2.77s**. Includes restart/offline reads of SHA-1 and SHA-256 code spans, limits and invalid inputs, withdrawal CAS, original-dependency invalidation, retained-support cutoffs, tombstones, scoped replacement chains, unknown applicability/validity and forged projection/cycle negatives.
- Adjacent regression: `uv run pytest -q tests/graph/test_mutations.py tests/graph/test_resolution.py tests/projections/test_jobs.py tests/core/test_memory_core.py`: exit 0, **45 passed in 6.74s**.

## Review findings

Five focused tests were added before modifying implementation. Running `pytest -q tests/graph/test_temporal.py tests/graph/test_support.py -k 'retained_exact or hide_future or chained_cutoffs or rechecks_resolution or derived_span'` initially exited 1 (5 failed for the reasons below, 5 deselected). All five now pass in the named gate.

- **Retained replacement — confirmed (§7).** Before the fix the predecessor became eligible despite retention. Exact replacement base support may use an applicable retention attestation; retention does not make the corrected-away revision a selected answer or move the control edge.
- **Future premises — confirmed (§6.2, §7).** A September revision absent from an August view still returned a premise ID. The immutable revision's graph sequence now bounds both reads.
- **Chained selection — confirmed inconsistent (§6.2, §7).** The same cutoff selected `(memory, graph) = (5, 0)` via an earlier view versus `(5, 1)` directly. The spec pins the transaction and defines absolute query times, not a separate chaining API. Here each call reselects from the original pin, not a new connection or an intersection with the previous cutoff. Post-pin graph and memory writes are excluded.
- **Derived span — confirmed (§6.3).** A new snapshot still returned derived bytes after withdrawal even though proof evaluation rejected the dependency. Raw evidence enumeration now checks transitive original visibility; unrelated evidence survives, and an already-open read stays pinned.
- **Correction generation — confirmed (§6.3, §7).** Correction succeeded after a merge with no revalidation. The default now checks the resolution generation at the expected revision; an optional explicit current generation acknowledges revalidation. Stale checks roll back, successful replay precedes CAS, and stored endpoint IDs remain exact.

## Scope and limitations

Proofs run against one pinned, partition-checked SQLite snapshot, not a worker projection. Historical graph selection uses a causal event prefix; an event lacking a host timestamp (e.g. pre-A07 schema-6 experiments) cannot be dated retrospectively. There is no migration that invents historical event times. Memory selection uses scoped record/revise receipts, not the graph counter. Correction never retargets a control endpoint. If the exact replacement is no longer selected, it needs applicable retained support for that original endpoint to continue suppressing the predecessor.

The lease fixture uses local memory/graph services directly. Its separate receipts are not proof of a live DSH handoff, broker authorization, end-to-end traversal or a model outcome. No live data, network services, worker deployment, installed-package check, or full-suite run. The named checks are bounded evidence for lead review, not release certification.
