# Nexus Memory — milestone A: durable exact memory

Approved scope: implement the first milestone of the Nexus Memory research plan. Build a runnable Python 3.12+ local stdio MCP service, without claiming semantic retrieval or superiority that has not been measured. The user approved building and requested autonomy, OOP, compact storage, scientific reasoning, and selective scalable tests.

## Shared contracts and decisions

- Source layout: `src/nexus_memory/{domain,storage,memory,transport}/`; no deep inheritance. Frozen domain dataclasses, a repository protocol, SQLite adapter, and service with a thin MCP adapter.
- A trusted server launch binds `Scope(namespace, actor)`; neither principal component is supplied by an MCP tool. Local OS-user trust boundary, no remote/team authentication claim. Repository queries and operation receipts are scoped by both components.
- `MemoryInput(content, kind='observation', tags=(), source_uri=None, snapshot=None)` is a complete revision. Kinds: observation, decision, constraint, procedure, failure. Content is nonempty, exact UTF-8, <=65536 bytes. Tags normalize strip/lower, deduplicate/sort, max16, each matches `[a-z0-9][a-z0-9._/-]{0,63}`. Source URI <=2048 characters and snapshot <=256 characters are optional caller-asserted metadata, never fetched or verified. Revisions replace all fields; omitted optional fields reset to defaults.
- Keep exact source bytes once in scoped content-addressed SQLite blobs; SHA-256 is an index and raw bytes must be compared before dedup reuse. Do not normalize body text. Historical metadata and revisions are immutable. Tags do not grant authority or define identity.
- Services: `record(input, idempotency_key) -> WriteReceipt`; `get(memory_id, revision_id=None) -> MemoryView`; `revise(memory_id, expected_revision_id, input, idempotency_key) -> WriteReceipt`; `forget(memory_id, expected_revision_id, idempotency_key) -> WriteReceipt`; `status() -> StoreStatus`.
- Service is constructed with a repository and trusted Scope. Results are immutable values. Receipt: memory_id, revision_id, operation_id, durable_seq, operation. View: memory_id, revision_id, parent_revision_id, content, kind, tags, source_uri, snapshot, created_at, current_revision_id. Status names distinguish exact durability from unavailable derived indexes.
- Expected errors share `NexusError` with stable `.code`, including invalid input, not found, revision conflict, idempotency conflict, unsupported schema, and storage integrity. Public errors must not include SQL, DB paths, raw content, other scope details, or stack traces.
- A scoped idempotency key (nonempty <=128 characters) spans all mutation kinds. Canonical request digest includes operation kind, target, expected revision, and every normalized input field. Same key + same digest returns the original receipt before checking current revision or tombstone; changed payload raises conflict. Receipts never contain memory text. Retain receipts for the namespace lifetime; no expiry in A.
- Writes atomically commit body, memory/revision, original receipt, and ordered outbox event using SQLite WAL + synchronous=FULL, foreign keys, busy timeout. Compute input digests outside the write lock. `BEGIN IMMEDIATE` plus expected-head comparison gives CAS semantics across connections.
- Forget is a logical tombstone; all normal current/historical reads become not found. Never resurrect a forgotten memory. Retry of the original operation returns its receipt only. Physical erasure and index workers are later milestones. Outbox event payloads use IDs, not content copies.
- SQLite-backed schema migration is versioned and transactional. Refuse unknown future versions. Transactions roll back on exceptions. Do not run a worker or make network/model calls on the write path.
- Require SQLite >=3.51.3, fixing the documented WAL-reset race. The development runtime is 3.53.1. Earlier patched backport branches are deliberately outside this milestone's supported baseline; do not add a replacement SQLite binary dependency.
- Prefer one connection per repository operation or otherwise explicitly thread-safe access, as MCP sync tools can execute on worker threads. No silent global connection shared across threads.
- Server entrypoint `python -m nexus_memory` and installed `nexus-memory`; launch requires `--namespace`, optional `--actor` default local, and optional `--db` with platform user-data default. Logs use stderr. SDK version must be the version actually installed and verified; keep a reproducible lock.
- Five MCP tools exactly: record/get/revise/forget/status. Structured object outputs and stable domain error codes. Descriptions state full replacement revisions, logical deletion, required reuse of idempotency keys, unverified source/snapshot metadata, and stored text is data, never instructions.

## Task 1 — durable core

Implement immutable domain types/validation/errors, repository protocol, SQLite migration and adapter, and scoped MemoryService. Write meaningful failing tests first and preserve red/green evidence in the implementation report. Cover lifecycle/history, duplicate and changed retries, retry after intervening revision/deletion, scope isolation, CAS conflict, logical deletion, blob dedup/exact bytes, transactional outbox, and future-schema rejection. Use a compact state machine or generated lifecycle model rather than many redundant case tests. Include independent-connection concurrent writes and subprocess crash recovery at before/after commit boundaries; claim only the tested process-crash model. Test only core behavior here; transport is task 2. Commit code and tests.

## Task 2 — real MCP integration and packaging

Implement CLI, MCP server and schemas against the installed official SDK. Add a real subprocess stdio client test covering listing tool schemas, status, record/retry/get/revise/conflict/forget/not-found. Verify schemas reject principal injection and invalid payloads and correctly return structured output/error status. Run the full meaningful suite, build/install a wheel, and exercise the installed entrypoint. Write README install/run/client configuration and architecture/proof obligations with limits. Commit changes.

## Task 3 — independent review and delivery

Review the complete diff for spec compliance and correctness, focusing on idempotency, scope/old revisions, transaction boundaries, concurrency, migration, CLI/wire behavior, and claims. Resolve material findings, rerun relevant tests, then run the full suite once. Record actual test output and runtime/dependency versions. Package source, lock, tests, and documentation without runtime caches, databases or secrets. Save the deliverable and provide a concise run command and limitations. No external deployment is part of this milestone.

## Evidence standard

Prove conditional safety invariants over the defined state machine, and exercise them with model/property/concurrency tests. Do not present tests as general proofs, process exits as power-failure validation, or finite timing samples as universal latency bounds. Relevance, repository snapshot validation, lexical/vector retrieval, token-budget optimization, physical erasure, and fleet-scale performance remain future milestones.
