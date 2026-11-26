# A05 review receipt — base 113c829

Scope: cooperating Stage A session publishers only. All tests use temporary synthetic catalogs and stores. No live host, adapter, model or external service was exercised.

## Gates

`UV_OFFLINE=1 uv run pytest -q tests/spaces/test_authority.py tests/spaces/test_publication_fence.py tests/spaces/test_mounts.py`

- Before implementation: 11 failed, 2 passed (exit 1). The new cases reached broker construction and found no authority module; the crash test observed EOF from its spawned child for that same missing import. The two existing mount tests stayed green. This was missing-interface red, not an observed broken ordering in an existing implementation.
- Additional behavioral red: a changed prepared payload passed admission instead of raising `InvalidInput` (exit 1). Rechecking the canonical request and wrapper under the fence fixed it. An intermediate incorrect import failed the gate and was corrected.
- Pre-review named gate: **15 passed in 3.07s** (exit 0). Additional catalog and A04 operation identity regressions: **20 passed in 0.30s** (exit 0). `git diff --check` passed. Not a full-suite run.

## Review blocker: receipt recovery after a source changes (§4.3)

- Red first: `UV_OFFLINE=1 uv run pytest -q tests/spaces/test_authority.py -k 'nonadmitted or ambiguous_commit' --tb=short` failed all three cases (exit 1). Receipt lookup raised `RevisionConflict` for a superseded head and `MemoryNotFound` for a tombstone. After a real destination commit with an injected lost acknowledgement, reconciliation returned `PublicationUnknown` because the write itself had advanced the cited head.
- Repair: the fence still checks current authority, flow, and the canonical operation digest. Exact receipt lookup reads only the digest-bound operation receipt, including on ambiguity. On a miss, the existing destination apply path checks cited heads in the writer transaction before mutation. A replay delivers the receipt, not the cited content.
- Green: the named A05 gate above passed **18 tests in 3.82s** (exit 0). In both source-change cases the failed staging slot could not drain; a new operation with the stale citation failed, while the original operation returned its stored receipt. Revocation then denied another retry. Only one derived operation and one catalog publication remained. The lost-acknowledgement case admitted the reconciled receipt with no duplicate revision. This injects a driver-level error after commit, not a physical SQLite failure.

## What the barriers established

The spawned publisher prepares an exact write outside the fence. Parent and child exchange bounded pipe acknowledgements, not sleeps. Revoke-before-admission left zero publications and revisions. Revoke-after-admission denied a new old-grant replay but the earlier admitted bytes still drained (one publication, one revision). A live publisher paused under the writer lock caused a bounded `authority_busy`, not successful revocation. Killing and joining it released the fence; retry then advanced the epoch. Killing after the destination receipt committed left zero catalog publications and one revision; retrying the same operation under current authority admitted one response with still only one revision.

Catalog commit, not staging, adapter return or network delivery, is enqueue. Drain uses a separate committed read and checks identity/digest. Reservations across queues share the process limit of 64 messages / 1 MiB. A real held destination lock produced a pre-effect busy result. A separate budget test consumed time on a controlled monotonic clock after catalog acquisition and inspected the actual destination busy timeout. A serialization failure after exact commit produced `publication_unknown`.

## Limits and integration contract

Catalog and store commits are **not atomic together**. Process exit loses volatile bytes, even with a catalog receipt. Do not infer delivery from a durable write, undo a committed write on revoke, or promise ordering from the instant a revocation was requested. Replay is a new publication under current authority. Process kill is not power-loss testing or fault injection into SQLite's physical commit.

Preparation (including Git checks) is external to admission. Trusted synchronous callbacks must finish promptly, perform their final source eligibility check in `validate_sources`, and use `admission.exact` or `admission.mutate` for destination effects. Generic mutation callbacks must include provenance, a digest-bound receipt and its lookup in the destination transaction; they must not open an independent transaction or commit the supplied connection themselves. The deadline bounds lock waits, not arbitrary Python execution. The delivered bytes must come from `drain`.

Same-space flow only; mixed exposure is not reset by switching. This is not an owner publication-grant path, graph route, job authority or host integration test. Actual graph-route checking belongs to A09. No cross-file WAL atomicity, fairness, transport delivery or protection against noncooperating OS-owner code is claimed. Lead review is pending; no commit was made.
