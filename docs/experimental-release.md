# Nexus Memory — experimental developer release

**Experimental.** It stores and returns your bytes reliably, and that is the part that is
tested. **No benefit to a coding agent has been shown**, and the one held-out comparison that
has run did not find one. Install it if you want durable local memory over MCP and are
willing to evaluate for yourself whether it helps.

---

## 1. Install

Two independent runtime floors, and meeting one does not imply the other:

| | |
| --- | --- |
| Python | **3.12+** (`requires-python`) |
| linked SQLite | **3.51.3+** (`SQLiteRepository.MINIMUM_SQLITE`, enforced at startup) |

A Python that meets the version floor can still link an older SQLite. uv's managed CPython
builds link SQLite 3.50.4 on macOS, so a plain `uv sync` can produce an environment that
**cannot start this server** — and the failure is quiet, because `startup_error:
unsupported_runtime` goes to the server's stderr, where an MCP client is not looking. The
client just sees the transport close.

```bash
python3 tools/install.py
```

It probes candidate interpreters, prints **both** numbers for each, builds `.venv` from the
first that passes, and then re-checks the environment it built rather than assuming the venv
inherited what the candidate had. `--python /path/to/python3.13` pins one;
`--check-only` reports without building.

If nothing passes, install an interpreter that does — `brew install python@3.13`,
`apt install python3.13`, or equivalent — and run it again.

## 2. Verify the installation

```bash
.venv/bin/python tools/check_install.py
```

Nine checks against the **installed console command**, over MCP stdio, in three separate
processes: store a memory, let the process exit, start a new one against the same file, read
byte-identical content back, find it by search, then retry the write with the same
idempotency key and confirm the receipt replays **and no duplicate was written**.

That last one is the check a manual walkthrough usually skips: a retry that quietly writes a
second copy looks exactly like a successful retry from the caller's side.

Exit `0` pass, `1` fail, `2` nothing installed at that path.

## 3. Connect an agent

**Tested with Claude Code** (CLI 2.1.270). Both scopes were verified on macOS:

```bash
claude mcp add nexus-memory -s user -- \
  /absolute/path/nexus-memory/.venv/bin/nexus-memory --namespace my-repo --actor local
claude mcp get nexus-memory      # expect: Status: ✔ Connected
```

Or project-scoped, committed with the repository as `.mcp.json`:

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

> [!IMPORTANT]
> Two things that were observed rather than assumed:
> - A **project-scoped** server reports `⏸ Pending approval` until you start `claude`
>   interactively once and approve it. A user-scoped one connects immediately.
> - Defining the **same server name in two scopes** does not merge them; the client reports
>   a conflict and one endpoint wins. Use one scope, or distinct names.

Point `command` at the environment whose install check passed. Other MCP clients take the
same stdio shape, but only Claude Code has been tested.

## 4. Back up

The database is one SQLite file, wherever `--db` points (default: your platform's user-data
directory). `status` reports the path.

**Use the online backup API.** It is correct whether or not the server is running:

```bash
.venv/bin/python -c "
import sqlite3, sys
src = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
src.close(); dst.close()
print('backed up to', sys.argv[2])
" /path/to/nexus.sqlite3 /path/to/backup.sqlite3
```

Restore by pointing `--db` at the copy — verified here: a server started on the backup
returned the same content and the same active count.

**If you copy files instead**, stop the server first and copy `nexus.sqlite3` **together
with any `-wal` and `-shm` sidecars**. The store runs in WAL mode, so it can be three files;
copying the `.db` alone while a sidecar holds uncheckpointed data loses whatever is in it. In
the cases measured here no sidecar persisted between transactions and a plain copy did
reproduce the store — but that is an observation about this build's behaviour, not a
guarantee, and the backup API does not depend on it.

There is no automatic backup, no rotation and no restore tooling. This is a file you own.

## 5. Known limits

**Retrieval**
- Search covers **current revisions only**. A term that appears solely in a superseded
  revision will not find that memory; use `history` and `get`.
- Ranking is **BM25 lexical ordering, not relevance**. No embeddings, no semantic similarity,
  no learned ranking.
- A reference filter **narrows** a result set and never reorders it, adds no evidence and
  never re-verifies on read.
- Symbol indexing, automatic extraction and context-budget packing are not implemented.

**Evidence**
- **No retrieval-quality advantage over any other tool has been measured or is claimed.**
- The v2 evaluation set scores **this build alone** — 17 queries, AI-assessed and AI-audited
  labels with human authorisation, no human audit at label level, no comparison system.
- **The one held-out agent comparison found no benefit.** Over four tasks, three arms and 36
  arm-runs, the memory arm did not beat the no-memory baseline on any task and lost on one,
  while consuming 1.56× the baseline's combined input and cache-read tokens. A
  rendered-notes arm tied on all four — so the run supports "having the information helps"
  no better than "Nexus's retrieval helps". Heavily qualified, and **not in Nexus's favour**.

**Operational**
- **Single trusted user.** This milestone trusts whoever can read the database and launch the
  service. No remote authentication, no encryption at rest, no multi-tenancy.
- Namespace and actor **bind at launch** and cannot be overridden by a tool call.
- Without `--repo`, or with `git` missing, reference-carrying writes are refused with
  `verification_unavailable`. Everything else works.
- A verified reference records that an object existed at a commit. **It does not establish
  that the memory's claim about it is true.**
- Tested on macOS and Linux. Windows paths are handled in code but untested.

## 6. If something goes wrong

| Symptom | First thing to check |
| --- | --- |
| Client shows the server connecting then closing | `python3 tools/install.py --check-only` — almost always a runtime below one of the two floors |
| `startup_error: unsupported_runtime` | the SQLite floor, not the Python one |
| Writes refused with `verification_unavailable` | no `--repo` bound, or `git` not on PATH |
| A retry seems to have written twice | run `tools/check_install.py`; it asserts exactly this |
| Search misses something you know you stored | it may live only in a superseded revision — try `history` |
