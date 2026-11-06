# Nexus Memory

**Local durable memory for programming agents, served over MCP.**

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/sqlite-3.51.3%2B-blue)](https://www.sqlite.org/)
[![MCP](https://img.shields.io/badge/mcp-2.1.1-blue)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/milestone-B3%3A%20browse%20candidate%20ordering-orange)](docs/plans/milestone-b3-candidate-ordering.md)

An agent stores a fact once and reads it back in a later session, on a different process, with the same bytes it wrote. Everything runs locally against one SQLite file. No model call, network request or external service touches the write path.

**License:** [PolyForm Noncommercial 1.0.0](LICENSE). Nexus is source-available.
Commercial use outside the license's express permissions requires a separate written
commercial license from Sory Kora. See [licensing and commercial requests](LICENSING.md),
including the license's permissions for qualifying noncommercial organizations.

> [!IMPORTANT]
> **Search covers current revisions only.** A term that appears solely in a superseded revision will not find that memory — use `history` to browse a memory's revisions and `get` to read an older one. Ranking is BM25 lexical ordering, not relevance: there is no semantic similarity, no embeddings and no learned ranking. Repository-verified references are implemented and closed, and `search` filters on that recorded evidence by repository, path and commit. Nothing *ranks* by it: a reference filter narrows a result set and never reorders it, adds no evidence and never re-verifies on read. Symbol indexing, automatic extraction and context-budget packing are later milestones. **No retrieval-quality advantage over any other tool has been measured or is claimed.** The v2 evaluation set *is* now judged and this build is measured against it ([results](benchmarks/eval/results-v2.md)) — but that is one 17-query set scoring this build alone, with labels that are AI-assessed, AI-audited twice and human-authorised, with no human audit at label level. No comparison against another system has been run.
> **Nor has any end-to-end benefit to an agent been shown.** The A1 held-out agent comparison
> is closed with a negative result ([closeout](https://github.com/soryko/Nexus/blob/f5e75cad6321640992fae87c49585e7ffb2b73b2/benchmarks/agent/CLOSEOUT-a1.md)): over
> four tasks and 36 arm-runs the memory arm did not beat the no-memory baseline on any task,
> lost on one, and consumed 1.56x the baseline's combined input tokens. A rendered-notes arm
> tied on all four. **A working, durable memory service is what this release claims; improved
> coding outcomes are not.** That evidence lives on the evaluation branch, linked above at an
> immutable commit, and is not part of this release.

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
> A Python that satisfies the version floor can still fail the SQLite one — the two are independent. Which SQLite an interpreter links is a property of that individual build, not of its Python version, its distributor or the `sqlite3` on your PATH, and builds from one distributor have been observed on both sides of the floor. So a plain `uv sync` can produce an environment that cannot start this server, and no distributor is assumed safe: `tools/install.py` probes each candidate and reports both numbers.

## Quickstart

**To use it.** This builds a normal installation in its own environment, so the checkout is
only needed to install and upgrade — not to run:

```bash
nexus_runtime="$HOME/.local/share/nexus-memory/venvs/0.1.0a2"
python3 tools/install.py --venv "$nexus_runtime"
"$nexus_runtime/bin/nexus-memory-check"
```

The first probes candidate interpreters, prints **both** version numbers for each, builds
the environment from the first that satisfies the Python **and** SQLite floors, and
re-checks the environment it built. The second is installed alongside the server and drives
it over MCP in three separate processes — store, restart, read back byte-identical, search,
retry without duplicating — exiting nonzero if any of it fails.

**To work on it.** `uv sync` installs the project *editable*, linked to this checkout, with
the development dependencies:

```bash
uv sync --frozen
.venv/bin/python -c "import sqlite3; print(sqlite3.sqlite_version)"
```

Read that version before going further. If it is below 3.51.3, rebuild the environment against an interpreter that meets the floor and re-sync:

```bash
uv venv --python /path/to/python3.12-or-newer   # one whose linked SQLite is 3.51.3+
uv sync --frozen
```

The two are not interchangeable: an editable environment stops working if this checkout
moves, which is why it is the developer path and not the user one.

**Installing to use it?** [`docs/experimental-release.md`](docs/experimental-release.md) is
the short path: install, verify, connect Claude Code (tested), back up, and the known limits
in one page.

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
      "command": "/absolute/path/.local/share/nexus-memory/venvs/0.1.0a2/bin/nexus-memory",
      "args": ["--namespace", "my-repo", "--actor", "local"]
    }
  }
}
```

On Windows the installed command is `<environment>\Scripts\nexus-memory.exe`. Your client's configuration file location may differ; the JSON shape above is the common one.

> [!TIP]
> Point `command` at the environment whose SQLite check passed. A client launching an interpreter below the floor only sees the transport close, because the diagnosis (`startup_error: unsupported_runtime`) goes to the server's stderr where the client is not looking.

### What the server tells the model, and what it records

Builds after `0.1.0a2` add three things. None changes the database schema, so they share a database with `0.1.0a2` clients in either order and at the same time.

- **Capture policy.** The default `--capture-policy explicit` sends instructions permitting memory changes only on the user's explicit request, including on a plain Codex MCP connection without hooks. When installing this build for the dedicated Claude pilot, pass `--capture-policy autonomous` to preserve the pilot treatment. Its original instruction text has SHA-256 prefix `e6733e856c06`. A build that includes the Codex adapter selects explicit capture automatically for `--codex-project-root`, even when the client skips its hooks. These rules guide the agent. The write tools stay available for authorized requests. `--no-instructions` suppresses the initialization text for either treatment. Status and every usage-log line include the effective `capture_policy` and the `instructions_digest` of the text actually sent.
- **Status.** `status` also reports the launch-bound `namespace` and `actor`, `service_version`, whether instructions are on and their digest, the usage-log state, and `database_location_fingerprint`: the first 12 hex characters of a SHA-256 of the database's resolved path. It tells two clients apart without disclosing the path. It identifies a location, not a store, and a predictable path can be guessed from it.
- **Usage log (opt-in).** `--usage-log /absolute/path/usage.jsonl` appends one JSON line per tool call: the time, a random server-instance id, the client's name and version, the tool, the outcome (`ok`, the error code, or `cancelled` when the client abandoned the call), the duration, and the opaque identifiers a *successful* call returned. Version 2 logs carry the receipt identifiers and a `replayed` flag from the store's idempotency check. A retry keeps its original receipt even when the first write took place before logging began. Search events carry the hit count and up to 20 hit IDs. It never records arguments, bodies, queries, tags, references or paths. Every line also carries `seq`, which counts write *attempts* per instance, so a gap is a lost line; the instructions state and digest; and the instance id. An instance opens with a `start` event and, on a clean shutdown, closes with a `stop` event carrying its counters and the number of calls still awaiting a logged outcome. A server instance is a process lifetime, not a conversation: one client process can serve several conversations.
- **Reading pilot counts.** The offline reporter counts confirmed new writes. Calls from older logs without a replay flag appear under `health.writes_without_disposition`. A nonzero `truncated_searches` means reuse candidates may be missing. `calls_in_flight_at_stop` counts calls whose outcomes may be missing, including writes committed just before shutdown. For older SIGTERM records, `sigterm_stops_without_inflight_count` marks that uncertainty.
- **Where it may write.** The destination is checked before the store is opened. It is refused if it is the database or one of its sidecar files (compared by spelling, case-insensitively, and by file identity), a symbolic link, a FIFO or anything else that is not a regular file, or a file owned by another user. Missing directories are created `0700`, the file is `0600` (an existing log is tightened to it), and a file created during a refused check is removed. If the log is moved or deleted while a server runs, it is re-created and a `reopened` event marks the join; a write that still fails is counted, never raised, and `status` reports `usage_log: failing`. Memory ids still link activity across events, so treat the log as private.

## Using it day to day

Connecting a client is not the same as getting anything out of it. Three milestones, worth
keeping apart because only the last one is evidence the service is doing something for you:

| Milestone | What it means | How you know |
| --- | --- | --- |
| **Installation verified** | the environment can serve MCP at all | `nexus-memory-check` passes |
| **Client connected** | your agent reached that server | the client lists the seven tools |
| **Fact reused** | a memory written in one session was found and used in a later one | you searched for it in a new session and it changed what you did |

The first two are setup steps you do once. The third is a habit.
[**Using Nexus during ordinary work**](docs/daily-use.md) is that habit written down -- what is
worth capturing, how briefly to consult, why a search hit is a candidate and not an answer, and
how to correct a memory that has gone stale. The canonical requests live in
[`examples/daily-use/`](examples/daily-use/) and are executed as-written by the test suite.

## Tools

| Tool | Purpose | Required fields |
| --- | --- | --- |
| `record` | Store the first immutable revision, optionally with verified references | `content`, `idempotency_key` |
| `get` | Read current content, or a specified revision | `memory_id` |
| `revise` | Replace all revision fields, references included, if the head still matches | `memory_id`, `expected_revision_id`, `content`, `idempotency_key` |
| `forget` | Tombstone a memory if the head still matches | `memory_id`, `expected_revision_id`, `idempotency_key` |
| `search` | Find current memories by terms, tags, kinds and recorded reference evidence | none |
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

`search` returns up to 240 Unicode characters of each body and the reason it matched. Use `get` for the complete stored text.

```text
search "retry policy"      -> a hit, with has_earlier_revisions: true
history <memory_id>        -> the revision chain, newest first
get <memory_id> <revision> -> the superseded decision, in full
```

```json
{ "query": "payments retry", "tags_all": ["payments"], "kinds": ["decision"], "limit": 20 }
```

Filters combine with AND. `query` is literal text unless `advanced` is true, which enables FTS5 syntax. `tags_all` and `tags_any` are deliberately separate. An empty request returns recent memories.

Four further filters narrow on the reference evidence a write already recorded: `repository` (`"any"` or `"bound"`, naming no repository), `reference_paths`, `reference_path_prefix` and `reference_commits`. Paths and commits are matched byte-exactly against stored values — no wildcards, globs or revision specs — and the filters read only what verification already established. They never re-verify, never read a repository, and cannot widen what the scope can see.

Literal mode makes two separate commitments, and they are worth stating apart because one does not imply the other:

1. **FTS5 operators are inert.** `AND`, `OR`, `NEAR`, `*` and quotes in a literal query are matched as text, not obeyed as syntax.
2. **Multiple tokens combine disjunctively.** A document matches if it contains *any* query token; BM25 then orders the results. This is a recall-first policy, not a consequence of (1) — the same inert-operator rule could equally have been implemented conjunctively. Ask for a phrase or a conjunction through `advanced`.

> [!WARNING]
> Disjunction means B1 has **no abstention behaviour**. A question with no answer in the store still returns a confidently ordered list, because a common word matches something. Treat an empty result as informative and a non-empty result as *candidates*, not as an assertion that the answer is present.

> [!NOTE]
> `lexical_rank` is a BM25 ordering value, **not** a confidence or relevance score, and it is only comparable within one result set. BM25 depends on corpus statistics, so a concurrent write can change the ranking of documents that did not themselves change. Cursors are therefore bound to an index generation: after any write, a stale cursor returns `cursor_expired` and the search must be restarted rather than silently returning inconsistent pages.

A `history` entry records **what** changed — revision IDs, parent links, timestamps. It never carries a rationale, because Nexus does not infer *why* a change was made from the difference between two revisions. If the reason matters, record it as a memory.

`history` takes two optional arguments beside the required `memory_id`: `limit` (default 20, between 1 and 100) and `cursor`, which pages through a chain longer than one request returns. A history cursor is bound to the memory it was minted for: presented for another memory in the same scope it is `cursor_expired`, as it is when one of its fields is malformed. It is **not** tamper-detecting — an edit substituting another well-formed value is accepted, and pages from the position that value names. Unlike a search cursor it carries no index generation, so a concurrent write does not invalidate it.

## Memory Atlas (read-only viewer)

A local browser viewer for one enrolled project's memories: counts by kind, search with filters, the full text and
revision history of a memory, and its recorded connections (the files it cites and the memories that cite them).
It is read-only: it opens the store in SQLite read-only mode, never enrolls, migrates or writes a memory, and shows
**no agent activity** (which tool an agent called, or when) in this version.

```sh
"$nexus_python" -I "$scope_bin/launcher.py" atlas "$main_checkout"          # opens your browser
"$nexus_python" -I "$scope_bin/launcher.py" atlas "$main_checkout" --no-browser   # prints a private launch file instead
```

The checkout must already be enrolled (see `integrations/project_scope/README.md`) and the interpreter must be the
installed Nexus one. Press Ctrl+C to stop. Keyboard and 360 px-wide screens are supported end to end.

**Privacy boundary.** The server listens on `127.0.0.1` on a random port, accepts only its own `Host`/`Origin`, and
needs a one-time secret that travels in a URL fragment (so it is never in a URL the server sees, a log or a `Referer`; the page posts it once in a request body) to receive an
`HttpOnly`, `SameSite=Strict` cookie. Every response carries `Cache-Control: no-store`, `Referrer-Policy:
no-referrer`, a self-only Content-Security-Policy without inline script, `X-Frame-Options: DENY` and `nosniff`.
Stored text is only ever shown as text. Request lines are not logged. The browser cannot name a scope or a path.
Reading a store in WAL mode makes SQLite leave an empty `-wal` and a `-shm` coordination file beside it; the
database file itself is untouched (see the measurements below).

**Known limits in this version.** The server can list memories that share a tag or term with one (`/api/related`) and
filter by verified repository, path and commit, but the page has no control for either yet. A memory's own relations
(files it cites, its other revisions) can be listed up to 100 each; beyond that they are counted but not pageable (the
citing-memories list of a file hub is fully pageable). The browser's Forward button is ignored; use the page's Back. A
store whose original recency backfill was never repaired is refused with a message: open it once with Nexus, then view it.
On Linux a checkout whose `.git` is deleted and recreated can reuse its inode and is not detected (macOS records a birth time).

**Measured** on one MacBook Pro (Apple M5, 16 GB, macOS 26.1, Chromium 151.0.7922.34, Playwright 1.62.0), 10,000
active memories, a file cited by 1,200 of them, 500 revised, 2,500 isolated, 50 forgotten and 300 foreign-scope
controls, the second of two full runs of `benchmarks/atlas/run.py` (commit `622e130`, clean tree), operating-system file
cache not flushed. Only the Chromium pane at 1024 px is timed except where a width is named.

| What | Measured (ms) | Target (ms) |
| --- | --- | --- |
| Launcher start to counted overview, 10 cold runs, p50 / p95 / max | 113.1 / 116.4 / 116.4 | p95 under 2000 |
| Selection to first neighborhood and inspector, 100 distinct targets, p50 / p95 / max | 31.2 / 32.7 / 39.7 | p95 under 300 |
| Same selections, data ready before the two paint frames, p50 / p95 | 14.5 / 16.4 | n/a |
| Next result page, all 499 advances from page 1 to page 500 of 500, p50 / p95 / max | 40.8 / 48.8 / 74.1 | none set |
| Same, first 10 / last 10 advances, p50 | 40.9 / 40.9 | none set |
| Next hub page of 48 (1,200 citing memories), p50 / p95: 1024 px, 360 px | 39.0 / 40.0, 39.1 / 46.8 | none set |
| Back to the previous view after the hub journey: 1024 px, 360 px | 50.5, 58.1 | none set |
| Binding check, p50 / p95: stat-only per request, full with git | 0.02 / 0.02, 11.3 / 19.1 | none set |

The first full run (an earlier commit of the same harness) gave a cold p95 of 176.7 ms and a warm selection p95 of
36.0 ms, so cold start varies by tens of milliseconds between runs; both runs are far inside the targets. Controls on
the same harness: two animation frames on an empty page take 16.7 ms p50 (part of every selection figure above), a
static no-op page took 14.8 ms p50 and a 10-memory store 101.6 ms p50 for the cold row. The visible graph never
exceeded 100 nodes and 99 edges. The harness checks the counts the viewer displays (10,000 active memories; 1,200
citing memories) so a scope or forgotten-memory leak invalidates the run. Raw samples and definitions:
`docs/measurements/2026-11-06-atlas-benchmark.json`. Reproduce with
`uv run --group atlas-ui python benchmarks/atlas/run.py --memories 10000 --interactions 100`.

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

**B2** adds repository context, in two slices. **B2a** — [contract](docs/plans/milestone-b2a.md) implemented and closed at `79e0a0d`, with its fifteen acceptance tests and eight mutation controls passing, under the documented limitation that repository verification is not atomic with the write — is a launch-bound repository identity that no tool call can override, and commit and path verification against committed Git objects, recording the resolved commit, repository-relative path, object ID and entry type. A verified reference establishes that the object existed at that path in that commit: not that the memory's prose is true, not that the working tree matches, not that the advice is current. **B2b** adds reference filters over that recorded evidence — filtering by repository, path and commit, adding no new evidence and never re-verifying on read. Its [contract](docs/plans/milestone-b2b.md) is reviewed and frozen; implemented and closed at `cdf51e7`. Symbol-aware retrieval comes after both. Cross-revision search — finding a term that only ever appeared in a superseded revision — is a separate follow-up with distinct current and history modes, so outdated instructions are never mixed into ordinary results. Embeddings come after a lexical baseline has been measured, not before.

**B3** — [contract](docs/plans/milestone-b3-candidate-ordering.md), implemented 2026-09-08 and merged at `b92c718` — orders browse candidate selection on the index rather than sorting every eligible row. Its measurements cover the browse shapes it measured and establish nothing about lexical search or about sparse reference filters.
