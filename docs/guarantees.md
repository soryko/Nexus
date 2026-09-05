# Conditional guarantees and measurement boundaries

These are proof obligations for milestone A, together with implementation evidence. They are not a claim that tests constitute a proof of all executions. They assume the caller uses the supported service, the host/database administrator is trusted, and SQLite and the filesystem satisfy their documented contracts.

## State and trust boundary

A scope is the pair `(namespace, actor)`, bound when the local service starts. Let state be `(B, M, R, I, O)`: exact content blobs, logical memories/heads/tombstones, immutable revisions, idempotency receipts, and an ordered outbox. Every supported mutation changes this state in one database transaction. An acknowledged result contains IDs and a durable sequence, not a second copy of the content.

The scope is a local partition, not authentication against another process with permission to open the database. A client cannot select another scope in MCP tool arguments. The operating system controls who may launch a process and read its files. Source URIs and snapshot strings are caller assertions; they do not prove source integrity, repository identity or commit validity.

## G1. At most one committed mutation per scoped operation key

Let `K = (namespace, actor, idempotency_key)` and let `D(c)` be the canonical command digest, including operation type, target, expected revision and normalized fields. There is a unique database row for each `K`.

Writers acquire `BEGIN IMMEDIATE`. Within that serialized write transaction, an existing `(K, D)` returns its original receipt. An existing `(K, D')`, where `D' != D`, rejects the request. Otherwise the transaction creates the mutation, receipt and event together. Induction over successful commits gives at most one committed mutation per `K`: the first commit creates its receipt, and every later transaction observes that receipt before it could create a second mutation. A rollback creates neither.

The payload-matching part assumes no collision in the command digest. This is computational, not unconditional equality of arbitrarily long inputs. Receipt retention is permanent in this version: deleting a receipt would break the lifetime guarantee. If a client loses the response after commit, retrying the same command/key recovers the original receipt. A retry after a later edit or deletion still returns that receipt; it does not repeat the state change.

## G2. No lost update through revision comparison

For an active memory with head `h`, a new revision or tombstone is allowed only when the supplied expected head equals `h`. The check and change occur under the same write transaction. Consider two distinct commands expecting `h`: if the first commits a new head or tombstone, the second subsequently observes that changed state and fails. Thus at most one distinct mutation can consume a given active head. A retry of the winning command is covered by G1.

This guarantees optimistic concurrency control, not that the content of a successful edit is correct. A client that deliberately fetches the new head and submits a replacement is authorizing a new edit.

## G3. Scoped reads and tombstone exclusion

At the database snapshot used for a read, define the eligible memories as those with the service's exact scope and no tombstone. Current and historical reads must first join through that eligible logical memory, then select the requested revision belonging to that memory. Consequently every returned revision belongs to an eligible memory, including when a caller guesses a foreign or old revision ID.

A read concurrent with deletion can linearize before deletion and return its previous snapshot. A read begun after the tombstone commit cannot return the memory. This cannot retract content already delivered to an agent, and it does not physically erase historical bytes. Direct database access lies outside this API guarantee.

## G4. Atomic durable receipt and outbox

The receipt, content reference, revision/head mutation and outbox event share one transaction, so a supported state cannot expose an acknowledged write without its corresponding event. Before commit, a process exit leaves no committed mutation. After commit, a process exit followed by recovery preserves the committed receipt and state under SQLite's contract.

`synchronous=FULL` is required on each connection. In WAL mode it requests synchronization at commits. Tests deliberately exit a subprocess before and after the transaction boundary. They validate process-crash recovery only; they do not simulate faulty disk firmware, arbitrary power cuts or a filesystem that ignores flushes. See [SQLite synchronization settings](https://www.sqlite.org/pragma.html#pragma_synchronous).

The outbox is durable pending work. No worker is shipped yet, so an event being present does not mean any lexical/vector index processed it. Delivery of a future worker will be at least once; consumers must be idempotent. A transaction's IDs and sequence are sufficient for a future worker to load authoritative state and skip tombstoned memories.

## G5. Exact-byte deduplication

For each scope, a digest identifies a candidate existing blob; reuse is allowed only after comparing the complete UTF-8 bytes. A collision with unequal bytes must fail closed instead of silently merging contents. Therefore blob reuse preserves exact content even if the hash collides. This guarantee differs from digest-only command matching in G1.

If `n` hashes behave as uniform 256-bit values, the union bound gives collision probability at most `n(n-1) / 2^257`. Uniformity is an assumption and this probability is not proof of semantic equivalence. Body whitespace and Unicode code points are not normalized. Tag normalization only affects bounded categorical metadata.

Let unique bodies have sizes `s_1,...,s_u` and appear in `r_1,...,r_u` revisions. Raw body duplication would store `sum(r_i*s_i)` body bytes. Deduplication stores `sum(s_i)` body bytes, saving `sum((r_i-1)*s_i)` before hash, reference, index and page overhead. With no repetition, this mechanism saves no body bytes and adds metadata. It is not a universal compression guarantee.

## G6. Lookup work and limits

An indexed exact lookup in a balanced B-tree takes logarithmic search work under the usual B-tree model. Returning a body with `L` bytes necessarily requires at least order `L` work to read/serialize it. The service's local path is approximately:

`T_get = T_queue + T_connect + T_index + T_body + T_decode + T_MCP`

No term has a universal millisecond bound on a shared host. A connection-per-operation design initially favors simple transaction/thread ownership; measurements must determine whether a bounded connection strategy is worth its lifecycle complexity. SQLite permits only one writer at a time, and `BEGIN IMMEDIATE` obtains that write transaction before dependent reads. See [SQLite isolation](https://www.sqlite.org/isolation.html).

No relevance theorem applies to exact-ID retrieval. Lexical candidate recall, embeddings, ranking, token budgets and coding-task outcomes require later implementations and held-out datasets. This milestone establishes the authority on which they can safely depend.

## Storage and scalability critique

The complete storage budget includes the database, WAL and shared-memory sidecars, revisions, idempotency receipts, indexes, metadata, and pending outbox. Tombstones and permanent receipts grow with history. Compact body storage does not make total storage bounded. A later retention policy must explicitly trade historical fidelity and retry lifetime against disk use.

Use the database on a local filesystem. WAL requires cooperating processes on the same host. A live WAL is part of the database state; copy a live database using SQLite's backup API, or shut down all clients cleanly before copying it. Do not copy only the live main file. SQLite documents a WAL-reset race affecting older releases; Nexus requires >=3.51.3, and was developed on 3.53.1. See [SQLite WAL behavior and the fixed race](https://www.sqlite.org/wal.html).

There is no automatic extraction, model call, source fetching, vector database, per-tag index, or compression codec in the write path. This keeps the first durability boundary small. Choosing a retrieval engine without a measured workload would add cost before establishing its value.

## Focused verification strategy

| Risk | Evidence required |
| --- | --- |
| Invalid state after mixed operations | Generated lifecycle/state model |
| Duplicate retries or payload substitution | Original receipt and conflict checks, including after later edits/deletion |
| Concurrent lost update | Independent connections racing the same expected head |
| Partial acknowledged writes | Subprocess exits around commit, reopen, receipts/events checked |
| Disclosure through history | Foreign scope and old revision reads after deletion |
| Silent byte changes | Whitespace/Unicode preservation, dedup collision check |
| Protocol drift | Official SDK client over a real subprocess stdio connection |
| Installation loses migration files | Built wheel installed and exercised independently of source imports |

Actual executed checks and any measured timing/storage results belong in `verification.md`, so design targets cannot be confused with observations.
