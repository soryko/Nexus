# Nexus Memory

**Local durable memory for programming agents, served over MCP.**

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/sqlite-3.51.3%2B-blue)](https://www.sqlite.org/)
[![MCP](https://img.shields.io/badge/mcp-2.1.1-blue)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/milestone-B2a%3A%20Git--verified%20references-orange)](docs/plans/milestone-b2a.md)

An agent stores a fact once and reads it back in a later session, on a different process, with the same bytes it wrote. Everything runs locally against one SQLite file. No model call, network request or external service touches the write path.

> [!IMPORTANT]
> **Search covers current revisions only.** A term that appears solely in a superseded revision will not find that memory — use `history` to browse a memory's revisions and `get` to read an older one. Ranking is BM25 lexical ordering, not relevance: there is no semantic similarity, no embeddings and no learned ranking. Repository-verified references are implemented and closed, but nothing filters or ranks by them yet — search takes no repository, path or commit argument. Symbol indexing, automatic extraction and context-budget packing are later milestones. **No retrieval-quality advantage over any other tool has been measured or is claimed.** The v2 evaluation set *is* now judged and this build is measured against it ([results](benchmarks/eval/results-v2.md)) — but that is one 17-query set scoring this build alone, with labels that are AI-assessed, AI-audited twice and human-authorised, with no human audit at label level. No comparison against another system has been run.

## What it gives you

- **Durable exact retrieval** — one SQLite transaction boundary, WAL with `synchronous=FULL`
- **Lexical search** — keyword, tag and kind filters over current revisions, indexed inside the write transaction
- **Browsable history** — every revision reachable with parent links and timestamps
- **Immutable revisions** — history is append-only; every revision keeps its own identity and metadata
- **Safe retries** — an idempotency key replays the original receipt instead of writing twice
- **Conflict detection** — compare-and-set on the head revision; a stale write is refused, never merged silently
- **Logical deletion** — a tombstone makes a memory unreachable through every normal read
- **Scoped isolation** — namespace and actor bind at launch, and no tool call can override them
- **Verified references** — a memory can cite paths at commits in a Git checkout bound at launch; each reference records the resolved commit, the object ID and the entry type as evidence that the object existed there, and nothing more

## Requirements

Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), with a Python whose **linked SQLite is 3.51.3 or newer**.

> [!WARNING]
> A Python that satisfies the version floor can still fail the SQLite one — the two are independent. uv's own managed CPython builds currently link SQLite 3.50.4 on macOS, so a plain `uv sync` can produce an environment that cannot start this server.

## Quickstart

```bash
uv sync --frozen
.venv/bin/python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Read that version before going further. If it is below 3.51.3, rebuild the environment against an interpreter that meets the floor and re-sync:

```bash
uv venv --python /path/to/python3.12-or-newer   # one whose linked SQLite is 3.51.3+
uv sync --frozen
```

Then launch:

```bash
uv run --frozen nexus-memory --namespace my-repo
```

The server speaks MCP over stdin/stdout and waits for a client. **It is not an interactive prompt** — an empty terminal is the expected result. Run `--help` for launch options.

Namespace and actor bind at launch and cannot be overridden by a tool call. Start with one namespace per project; the default actor is `local`.

The database defaults to your platform's user-data directory. Pin it explicitly when you want control:

```bash
uv run --frozen nexus-memory --namespace my-repo --db /absolute/path/nexus.sqlite3
```

Use a private directory on a local disk. This milestone trusts the OS user who can read the database and launch the service. There is no remote authentication and no encryption at rest.

To let memories cite files at commits, bind one local checkout:

```bash
uv run --frozen nexus-memory --namespace my-repo --repo /absolute/path/to/checkout
```

The checkout registers an opaque identity in `<git-dir>/nexus/checkout-token`, so every worktree of the repository binds to the same identity and a fresh clone is a new one; `--repo-id` associates a clone deliberately. References are verified with `git` at write time and never touch the working tree. Without `--repo`, or with `git` missing, everything else works and reference-carrying writes are refused with `verification_unavailable`; `status` reports the mode. See the [B2a contract](docs/plans/milestone-b2a.md) for exactly what a verified reference does and does not establish.

## Connect an agent

Add a stdio server entry to your MCP client's configuration, substituting your absolute project path:

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

On Windows the installed command is `.venv/Scripts/nexus-memory.exe`. Your client's configuration file location may differ; the JSON shape above is the common one.

> [!TIP]
> Point `command` at the environment whose SQLite check passed. A client launching an interpreter below the floor only sees the transport close, because the diagnosis (`startup_error: unsupported_runtime`) goes to the server's stderr where the client is not looking.

## Tools

| Tool | Purpose | Required fields |
| --- | --- | --- |
| `record` | Store the first immutable revision, optionally with verified references | `content`, `idempotency_key` |
| `get` | Read current content, or a specified revision | `memory_id` |
| `revise` | Replace all revision fields, references included, if the head still matches | `memory_id`, `expected_revision_id`, `content`, `idempotency_key` |
| `forget` | Tombstone a memory if the head still matches | `memory_id`, `expected_revision_id`, `idempotency_key` |
| `search` | Find current memories by terms, tags and kinds | none |
| `history` | List a memory's revision chain, newest first | `memory_id` |
| `status` | Inspect scoped durability, index state, the bound repository and the verification mode | none |

A `record` call looks like this:

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

Keep the returned `memory_id` and `revision_id`. Read with `get`, and pass the latest revision ID as `expected_revision_id` to change anything. A write receipt also carries `operation_id`, `operation` and `durable_seq`.

## Finding a memory

`search` returns bounded excerpts and the reason each hit matched, never whole bodies — fetch those with `get`. The workflow the milestone is built around:

```text
search "retry policy"      -> a hit, with has_earlier_revisions: true
history <memory_id>        -> the revision chain, newest first
get <memory_id> <revision> -> the superseded decision, in full
```

```json
{ "query": "payments retry", "tags_all": ["payments"], "kinds": ["decision"], "limit": 20 }
```

Filters combine with AND. `query` is literal text unless `advanced` is true, which enables FTS5 syntax. `tags_all` and `tags_any` are deliberately separate. An empty request returns recent memories.

Literal mode makes two separate commitments, and they are worth stating apart because one does not imply the other:

1. **FTS5 operators are inert.** `AND`, `OR`, `NEAR`, `*` and quotes in a literal query are matched as text, not obeyed as syntax.
2. **Multiple tokens combine disjunctively.** A document matches if it contains *any* query token; BM25 then orders the results. This is a recall-first policy, not a consequence of (1) — the same inert-operator rule could equally have been implemented conjunctively. Ask for a phrase or a conjunction through `advanced`.

> [!WARNING]
> Disjunction means B1 has **no abstention behaviour**. A question with no answer in the store still returns a confidently ordered list, because a common word matches something. Treat an empty result as informative and a non-empty result as *candidates*, not as an assertion that the answer is present.

> [!NOTE]
> `lexical_rank` is a BM25 ordering value, **not** a confidence or relevance score, and it is only comparable within one result set. BM25 depends on corpus statistics, so a concurrent write can change the ranking of documents that did not themselves change. Cursors are therefore bound to an index generation: after any write, a stale cursor returns `cursor_expired` and the search must be restarted rather than silently returning inconsistent pages.

A `history` entry records **what** changed — revision IDs, parent links, timestamps. It never carries a rationale, because Nexus does not infer *why* a change was made from the difference between two revisions. If the reason matters, record it as a memory.

## Semantics worth knowing

<details>
<summary><b>Retrying a write</b> — same key, same arguments</summary>

<br>

Reuse the **same idempotency key and the same arguments** when retrying a write whose outcome you are unsure of. A successful retry returns the original receipt, even after later revisions or deletion.

A different command under the same key returns an idempotency conflict — use a new key for a new intended operation. Keys are shared across every mutation tool within the launch scope and actor, and are retained without expiry.

</details>

<details>
<summary><b>Revising</b> — full replacement, never a silent overwrite</summary>

<br>

`revise` replaces the whole revision. Omitted `kind`, `tags`, `source_uri` and `snapshot` reset to their defaults rather than carrying forward.

It never overwrites a newer head. After a conflict, read the current memory, reconcile the content yourself, then deliberately submit a new command and key against the current revision ID.

</details>

<details>
<summary><b>Forgetting</b> — logical, not physical erasure</summary>

<br>

`forget` makes current and historical revisions unavailable through normal reads. It retains tombstones, history, content bytes and receipts. **It is not physical erasure.**

The original write receipt can still be replayed, but a replay never resurrects the content.

</details>

<details>
<summary><b>Validation</b> — content, kinds and tags</summary>

<br>

Content is stored as exact UTF-8 bytes, up to 65,536 bytes. It is never normalized.

Kinds are `observation`, `decision`, `constraint`, `procedure` and `failure`.

Tags are trimmed, lowercased, deduplicated and sorted. At most 16 per revision, each matching `[a-z0-9][a-z0-9._/-]{0,63}`. **Tags do not control access.**

`source_uri` and `snapshot` are optional, caller-asserted metadata. They are never fetched and never verified.

</details>

> [!CAUTION]
> Stored content is **data, not instructions**. A memory is written by whoever could reach the server, and a consuming agent must not treat retrieved text as a directive.

## Development

```bash
uv sync --frozen
uv run --frozen pytest -q
uv build
```

The suite pairs a generated lifecycle model with focused concurrency, transaction-recovery and real MCP subprocess checks, testing state invariants rather than every getter and input permutation. See [verification evidence](docs/verification.md) for actual results and their limits.

The code is a frozen domain layer, a repository protocol, a SQLite adapter and a scoped service; the MCP server only adapts schemas and errors. See [conditional guarantees](docs/guarantees.md) for the proofs, assumptions, storage formula and engineering critique, and [the milestone plan](docs/plans/milestone-a.md) for the development contract.

Identical body bytes are stored once per scope, while revisions keep their own metadata and identity. The lexical index is an external-content FTS5 table over a projection of active heads, maintained inside the same transaction as the write it describes, so it is never visible ahead of or behind the authoritative state. Search independently joins its results against the authoritative heads in one read snapshot: a stale index row cannot surface content. Outbox events carry references, not copies of content, and remain pending until a future embedding worker exists — milestone A's events keep their original meaning.

## Backups

Use SQLite's backup API, or shut every client down cleanly before copying. **Do not copy the main database file alone while it is live** — the WAL can hold committed state.

## Roadmap

Retrieval work done since B1 is measured but **not shipped**: the [development charter](benchmarks/eval/v2-development-queries.md) records `stem` morphology qualified as the development baseline, a historical-discovery prototype built and rejected at ~2.70x storage against a 2x ceiling, and an evidence-selection rule rejected after it removed a directly relevant answer on v2. The shipped default remains `exact` current-head search, and abstention remains unsolved.

**B2** adds repository context, in two slices. **B2a** — [contract](docs/plans/milestone-b2a.md) implemented and closed at `79e0a0d`, with its fifteen acceptance tests and eight mutation controls passing, under the documented limitation that repository verification is not atomic with the write — is a launch-bound repository identity that no tool call can override, and commit and path verification against committed Git objects, recording the resolved commit, repository-relative path, object ID and entry type. A verified reference establishes that the object existed at that path in that commit: not that the memory's prose is true, not that the working tree matches, not that the advice is current. **B2b** adds reference filters over that recorded evidence — filtering by repository, path and commit, adding no new evidence and never re-verifying on read. Its [contract](docs/plans/milestone-b2b.md) is reviewed and frozen; implementation has not started. Symbol-aware retrieval comes after both. Cross-revision search — finding a term that only ever appeared in a superseded revision — is a separate follow-up with distinct current and history modes, so outdated instructions are never mixed into ordinary results. Embeddings come after a lexical baseline has been measured, not before.
