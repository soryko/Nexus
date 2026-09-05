# Milestone A verification evidence

Measured on 2026-09-05 with Python 3.12.13, SQLite 3.53.1, MCP 2.1.1, pytest 9.1.1 and Hypothesis 6.167.1. The environment was Linux x86_64 with glibc 2.39. Hardware and filesystem performance were not controlled.

## Durable core

The reviewed core at commit `a801db0` passed 19 tests in 1.27 seconds in an independent controller run. Focused checks cover lifecycle/history, scoped retries, changed-payload rejection, tombstones, content preservation, digest collision behavior, CAS and same-key races across connections, simultaneous first initialization in eight processes, consistent status snapshots, safe storage errors, connection closure and unsupported runtime/schema rejection.

The compact Hypothesis model generates up to 12 examples of eight revise/forget steps, checking the expected head/content or deleted state after transitions. Separate targeted tests exercise replay and concurrency properties. These are finite implementation checks, not a proof over arbitrary schedules.

Two child-process tests call `os._exit` immediately before or after commit, bypassing rollback/finally cleanup. The parent reopens the database and checks content blobs, logical memories, revisions, receipts and outbox for the expected all-or-none state, then checks committed receipt replay. This tests abrupt process death, not physical power failure.

Review found and resolved a first-start migration race, nondeterministic read-connection closure, mixed-snapshot status reads and connection failures outside the sanitized error boundary. It also exposed a body-containing unique index that duplicated content bytes; the final schema indexes scope and digest only, then compares raw bytes. The first crash test used `SystemExit`, which allowed cleanup; it was replaced with the abrupt-exit tests above. Core re-review approved both specification compliance and code quality.

## Exact retrieval and space baseline

Reproduce from the project directory after installation:

```bash
uv run --frozen python benchmarks/exact_store.py --records 1000 --reads 500 --output docs/benchmark-1000.json
uv run --frozen python benchmarks/exact_store.py --records 10000 --reads 1000 --output docs/benchmark-10000.json
```

Each command runs two workloads sequentially, with deterministic 1,024-byte bodies and seed 20260905. The unique workload has one distinct body per record. The repeated workload has one distinct body per ten records. Every record has its own identity, revision, receipt and outbox event. Reads sample known IDs after ingestion and verify exact returned contents. SQLite integrity checks returned `ok` for all four databases.

| Records | Distinct bodies | Median exact get | p95 exact get | p99 exact get | Total files after workload |
| --- | --- | --- | --- | --- | --- |
| 1,000 | 1,000 | 0.225 ms | 0.368 ms | 0.521 ms | 2,580,480 bytes |
| 1,000 | 100 | 0.232 ms | 0.415 ms | 0.542 ms | 1,294,336 bytes |
| 10,000 | 10,000 | 0.245 ms | 0.455 ms | 0.882 ms | 25,452,544 bytes |
| 10,000 | 1,000 | 0.227 ms | 0.387 ms | 0.539 ms | 12,447,744 bytes |

These timings include the local service and connection lifecycle. They exclude MCP transport, client startup, queueing and any relevance computation. The OS cache was warm; no cold-cache flush, peak RSS/disk measurement, controlled device specification or confidence interval is provided. Generated UUIDs and B-tree page occupancy can vary, so reruns need not reproduce identical file sizes. Tail quantiles use the nearest-rank definition on 500 or 1,000 samples. The four point estimates are a reproducible starting baseline, not latency guarantees or evidence of superiority over another system.

With ten repetitions per distinct body, stored body bytes are exactly 90% lower than storing the body once per record: 102,400 rather than 1,024,000 bytes at 1,000 records; 1,024,000 rather than 10,240,000 bytes at 10,000 records. The *complete* repeated-workload databases are about 49.8% and 51.1% smaller than their unique-body counterparts. This is a workload comparison, not a universal savings factor: receipts, revisions and indexes remain, and unique bodies incur metadata overhead. Main database plus WAL plus SHM were counted at measurement time; sidecars were zero after the per-operation connections closed. Transient WAL peaks were not measured.

Raw observations, including record-write quantiles and scoped counts, are in [benchmark-1000.json](benchmark-1000.json) and [benchmark-10000.json](benchmark-10000.json). The harness lives in [exact_store.py](../benchmarks/exact_store.py).

## Delivery gates

The complete suite passed **22 tests in 3.99 seconds** in a controller run after transport hardening. The official SDK client connected to a real subprocess, listed exactly five tools with structured schemas, and exercised record/retry/get/revise/conflict/forget/history-not-found. It verified rejection of injected namespace arguments and safe errors for SDK and domain validation failures. Additional CLI checks covered help, sanitized startup failure, cwd-independent defaults and concurrent storage preparation. The transport test first failed at the missing subprocess entrypoint, then passed after implementation; focused red tests also exposed domain-error mapping and relative-data-path bugs before their fixes.

`uv build --wheel` succeeded. The wheel was installed into a separate virtual environment with cached dependencies, then exercised from outside the source tree. The controller verified that imports resolved to that environment's `site-packages`, not the editable checkout. The installed `nexus-memory --help` succeeded, and an official MCP client completed record, exact get, revision, deletion, historical exclusion and original-receipt replay. Final status was zero active memories, one forgotten memory, two revisions and three pending outbox events. The wheel includes `001_initial.sql` and is 15,507 bytes, excluding all third-party dependencies.

Verified commands:

```bash
uv run --frozen pytest -q
uv build --wheel
```

`git diff --check` and Python compilation checks also passed. This validation was performed on Linux only; the Windows and macOS data-path branches have not been exercised on those operating systems. No remote deployment or external repository push was performed.

The final independent review approved Milestone A specification compliance, MCP integration compliance and code quality with no remaining actionable findings. It inspected the core, transport, schema, CLI, lock/package declarations, proof claims and benchmark method. The controller separately performed the installed-wheel gate described above. The new project remains on its local `feat/durable-memory` branch; delivery is a source archive with tests, lock and documentation.
