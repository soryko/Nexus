# Nexus Memory

Local durable memory for programming agents, served through five MCP tools. Milestone A provides exact-ID retrieval, immutable revisions, normalized tags, scoped deduplication, safe retries, conflict detection, logical deletion and a transactional outbox.

This is the durable foundation of the Nexus Memory plan. Semantic search, Git/symbol indexing, verified repository snapshots, automatic extraction and context-budget packing are later milestones. There is no demonstrated retrieval-quality or performance advantage over Supermemory yet.

## Install and run

Requires Python 3.12+ with SQLite 3.51.3+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). Python's version alone does not determine its linked SQLite version, and an interpreter that satisfies the Python floor can still fail the SQLite one.

From this project directory:

```bash
uv sync --frozen
.venv/bin/python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Read that version before going further. The server refuses to start below 3.51.3 and reports `startup_error: unsupported_runtime`. uv's own managed CPython builds currently link SQLite 3.50.4 on macOS, so a plain `uv sync` can produce an environment that cannot run this service. If the printed version is too old, rebuild the environment against an interpreter that meets the floor, then re-sync:

```bash
uv venv --python /opt/homebrew/opt/python@3.13/bin/python3.13
uv sync --frozen
```

Substitute whichever Python 3.12+ on your machine reports a linked SQLite of 3.51.3 or newer. Once the version check passes:

```bash
uv run --frozen nexus-memory --namespace my-repo
```

The server speaks MCP over stdin/stdout and waits for a client. It is not an interactive text prompt. Use `--help` for launch options. Namespace and actor are bound at launch; clients cannot override them in tool calls. Start with one namespace per project. The default actor is `local`.

The database defaults to the platform's user-data directory. Set an explicit local path when you want to control it:

```bash
uv run --frozen nexus-memory --namespace my-repo --actor local --db /absolute/path/nexus.sqlite3
```

Use a private directory on a local disk. This milestone trusts the OS user who can read the database and launch the service. It does not implement remote authentication or encryption at rest.

## Connect a programming agent

After `uv sync --frozen` and the SQLite version check above, add a stdio server entry to your client's MCP configuration, substituting your absolute project path. Point `command` at the environment whose version check passed; a client launching an interpreter that fails the floor only sees the transport close, because the startup diagnosis goes to the server's stderr:

```json
{
  "mcpServers": {
    "nexus-memory": {
      "command": "/absolute/path/nexus-memory/.venv/bin/nexus-memory",
      "args": ["--namespace", "my-repo", "--actor", "local"]
    }
  }
}
```

On Windows the installed command is under `.venv/Scripts/nexus-memory.exe`. The configuration shown is the common JSON shape; your client's server configuration location may differ.

## Tool contract

| Tool | Purpose | Required fields |
| --- | --- | --- |
| `record` | Store the first immutable revision | `content`, `idempotency_key` |
| `get` | Read current content or a specified revision | `memory_id` |
| `revise` | Replace all revision fields if the head still matches | `memory_id`, `expected_revision_id`, `content`, `idempotency_key` |
| `forget` | Tombstone a memory if the head still matches | `memory_id`, `expected_revision_id`, `idempotency_key` |
| `status` | Inspect scoped durability and pending indexing state | none |

For example, call `record` with:

```json
{
  "content": "Run the payments integration suite after changing retry behavior.",
  "kind": "procedure",
  "tags": ["payments", "testing"],
  "source_uri": "file:///repo/CONTRIBUTING.md",
  "snapshot": "caller-supplied-commit-id",
  "idempotency_key": "payments-test-procedure-001"
}
```

Save the returned `memory_id` and `revision_id`. Use `get` with that memory ID, and use the latest revision ID as `expected_revision_id` for a change. A write receipt also includes `operation_id`, `operation`, and `durable_seq`.

Use the **same idempotency key and same arguments** when retrying an uncertain write. A successful retry returns the original receipt, even after subsequent revisions or deletion. A different command with the same key returns an idempotency conflict. Use a new key for a new intended operation. Keys are shared across all mutation tool names within the launch scope and actor, and are retained without expiry.

`revise` is a complete replacement: omitted kind/tags/source/snapshot reset to their defaults. It never silently overwrites a newer head. After a conflict, read the current memory, reconcile the content, and deliberately submit a new command/key with its current revision ID.

`forget` makes current and historical revisions unavailable through normal reads. It retains tombstones, history, content bytes and receipts; **it is not physical erasure**. The original write receipt may still be replayed, but replay does not resurrect content.

Content is stored as exact UTF-8 bytes, with a 65,536-byte limit. Supported kinds are `observation`, `decision`, `constraint`, `procedure`, and `failure`. Tags are trimmed, lowercased, deduplicated and sorted; at most 16 tags are allowed, each matching `[a-z0-9][a-z0-9._/-]{0,63}`. Tags do not control access. Source URI and snapshot are optional, unverified metadata and are never fetched. Stored content is data and must not be treated as instructions to the consuming agent.

## Develop and verify

```bash
uv sync --frozen
uv run --frozen pytest -q
uv build
```

The suite combines a generated lifecycle model with focused concurrency, transaction recovery and real MCP subprocess checks. It tests state invariants instead of duplicating every getter and input permutation. See [verification evidence](docs/verification.md) for actual results and their limits.

The code uses frozen domain values, a repository protocol, a SQLite adapter and a scoped service. The MCP server only adapts schemas and errors. See [conditional guarantees](docs/guarantees.md) for the proofs, assumptions, storage formula and engineering critique, and [the milestone plan](docs/plans/milestone-a.md) for the development contract.

All durable state lives in one SQLite transaction boundary. Identical body bytes are stored once per scope; revisions retain their own metadata and identity. Outbox events contain references instead of copying content. No model call or external service runs on the write path. Pending outbox events remain pending until a future index worker is implemented.

For backups, use SQLite's backup API or shut down every client cleanly before copying the database. Do not copy only the main database file while it is live: the WAL can contain committed state. The next milestone adds repository-aware exact and lexical retrieval before introducing embeddings.
