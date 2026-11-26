# A04 — exact operation identity

Review base: `cbb49a75c32f4253924e74cb2580e5b138721453` on `feat/nexus-memory-v2`. Scope: Stage A lines 100–111, NXS-03. Uncommitted for lead review. Tests used disposable synthetic stores only.

## Named gate

```sh
uv run pytest -q tests/spaces/test_operation_identity.py tests/core/test_memory_core.py tests/core/test_reference_compatibility.py tests/core/test_b2a_mutations.py
```

- **Red, before source changes:** exit 1; 5 failed, 62 passed, 1 skipped (13.14 s). The four required scenarios and the wrong-scope/space/key scenario all ran and stopped at the missing `WriteContext` import after opening the synthetic destination. This establishes the missing interface, not five independently reproduced storage failures.
- **Green, before review:** exit 0; 68 passed, 1 skipped (12.82 s).
- **Review red, before repair:** `uv run pytest -q tests/spaces/test_operation_identity.py -k 'independent or collision or unicode' --tb=short` exited 1 (7 failed, 6 deselected). All five receipt cases had the wrapper hash instead of the frozen hash; the mixed local/foreign source list committed; SQLite returned spaced, ASCII-escaped metadata. A separate `-k unpersisted` run exited 1 (1 failed, 13 deselected): forget accepted revision metadata it could not persist.
- **Green, repaired named gate:** exit 0; 76 passed, 1 skipped (13.61 s). The skip is the opt-in full mutation matrix (`NEXUS_MUTATION_MATRIX=1`); designated mutation controls ran. No full-suite or cross-platform run is claimed.

## What the evidence establishes

- `test_retry_survives_new_process_and_session`: a second Python interpreter opens the same database with the same stream/key but a fresh session. Every receipt field matches. There is one record revision, event, mapping and receipt, with original session/run/event attribution. Changed content or expected revision conflicts; revise and forget also replay, and forget adds no revision provenance.
- `test_legacy_key_cannot_capture_broker_operation`: a prefixed historical key survives schema-4 migration. The broker can use the identical logical key independently. An occupied UUID forced into the first allocator draw is skipped.
- `test_sidecars_rollback_with_write`: a trigger aborts the operation mapping insert after the receipt and provenance inserts. Neither the mutation, the event, the receipt nor any sidecar survives.
- `test_replay_skips_git_verification`: two preparations observe different synthetic Git evidence. Once one commits, the other returns the winner's receipt and evidence; a subsequent retry never invokes verification.
- Additional checks reject mismatched service scope, partition space or operation key, and reject a prepared publication after its local source revision changes.

## Transaction boundary

Legacy calls retain their signatures, receipt keys and v1/v2 digest functions. They do not query schema-5 tables. Broker preparation wraps the legacy digest/version with the exact family, space and revision metadata/lineage, but not transient attribution. A mapping lookup precedes Git verification. Under `BEGIN IMMEDIATE` the destination checks its partition and mapping again, selects an unused UUID receipt key, checks applicable source heads and CAS, and commits the mutation, indexes, outbox, receipt, mapping and provenance together. A replay does not update attribution or revalidate the original sources.

## Review repair and limits

- The UUID-keyed legacy receipt stores the frozen request digest with version 1 (including forget) or 2 (reference-bearing record/revise). Only `operation_keys` stores the independent exact-command v1 hash. The Unicode test reopens SQLite and reconstructs the command hash from partition, receipt, mapping and revision provenance; it also compares the actual sidecar strings to compact sorted non-ASCII-escaped JSON.
- On a new write, every source must be in the destination space and name the current non-tombstoned revision of that memory in the bound partition under the write lock. A mixed local/foreign list is rejected even when the foreign IDs match a real local memory and revision. The error includes no identifiers, and the destination retains only the setup write. No other partition, store, catalog, grant or external authority was consulted; cross-space publication and revocation fencing remain unsupported.
- Forget has no revision sidecar. Its broker context must omit compatibility metadata, publication sources and visibility role rather than hash revision-only inputs that cannot be reconstructed. Operation attribution is still retained. No schema change or backfill of receipts from the faulty uncommitted implementation is included.
