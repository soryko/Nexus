# Nexus Memory — experimental developer release

**Experimental.** It stores your bytes and gives them back after a restart, and that part is
tested. **No benefit to a coding agent has been shown**, and the one held-out comparison that
has run did not find one. Install it if you want durable local memory over MCP and are
willing to judge for yourself whether it helps.

This page is meant to be followable from outside the repository, with no knowledge from a
previous session. Every command below was run; where a result is specific to the host that
ran it, it says so.

---

## 0. Before you start

| | |
| --- | --- |
| Python | **3.12+** — `requires-python` |
| **linked** SQLite | **3.51.3+** — `SQLiteRepository.MINIMUM_SQLITE`, enforced at startup |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | builds the environment |
| git | only to clone; also needed for `--repo` reference verification |

The two runtime floors are **independent, and meeting one does not imply the other.** The
number that matters is the SQLite your *interpreter is linked against* — not its Python
version, not where you installed it from, and not the `sqlite3` on your PATH, which is a
different program. Ask the interpreter:

```bash
/path/to/python3 -c "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
```

Builds of the same version from the same distributor have been seen on both sides of the
floor, so this page names no safe distributor. The installer probes and decides.

A below-floor interpreter fails **quietly** where it hurts: the server writes
`startup_error: unsupported_runtime` to its stderr and exits, and an MCP client sees only a
closed transport with no reason attached. That is what §1 and §2 exist to prevent.

### Get the source

```bash
git clone https://github.com/soryko/Nexus.git
cd Nexus
git checkout v0.1.0a1     # PROPOSED prerelease tag — not published yet; see §7
```

> **The tag does not exist yet.** Until the prerelease is published, use the default branch
> and expect it to move. Install from a tag, never from a long-running evaluation branch.

## 1. Install

```bash
python3 tools/install.py
```

It probes candidate interpreters, prints **both** numbers for each, builds `.venv` from the
first that passes both floors, then **re-checks the environment it built** rather than
assuming the venv inherited what the candidate had.

| flag | |
| --- | --- |
| `--python /abs/path/to/python3.13` | use this interpreter **and no other** — never substituted |
| `--venv /abs/path` | build somewhere other than `./.venv` |
| `--check-only` | report and build nothing; works on a host without uv |

Exit `0` built and verified · `1` nothing meets both floors · `2` the build could not run, or
ran and produced something below a floor.

If nothing passes, install an interpreter that does and pass it with `--python`.

### A measured Linux recipe

The pair CI pins, and the one the fresh-install job runs on `ubuntu-24.04`:

```bash
curl -LsSf https://astral.sh/uv/0.11.21/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.13.14
python3 tools/install.py --python "$(uv python find --system 3.13.14)"
```

`--system` matters: without it, `uv python find` returns the interpreter of any `.venv` in the
current directory, so on a second run it hands back the venv you are about to rebuild
instead of the managed interpreter you asked for.

### A measured macOS recipe

Measured on macOS 26.1 (arm64) with uv 0.11.21 — same commands:

```bash
uv python install 3.13.14
python3 tools/install.py --python "$(uv python find --system 3.13.14)"
```

That built `python 3.13.14, sqlite 3.53.1`, above both floors.

> [!IMPORTANT]
> **Homebrew's `python@3.13` did not work on this host, for a reason unrelated to the floors.**
> The interpreter itself passes them comfortably (`3.13.15`, SQLite `3.53.4`) — but on macOS
> 26.1 this build returns an empty `platform.mac_ver()`, and **uv refuses it outright**:
> `Broken Python installation, platform.mac_ver() returned an empty value`. Both the
> `/opt/homebrew/bin` symlink and the real framework path were refused. If you hit this, use
> a uv-managed interpreter as above. The installer will still *probe* the Homebrew build and
> report it as passing, because it does pass — the refusal comes from uv at build time.
>
> This is a recurring environment fault on this platform rather than a new one: the same
> `platform.mac_ver()` refusal is recorded against a different Homebrew interpreter in
> `docs/plans/milestone-b2a.md`. It is not a reason to lower the SQLite floor.

## 2. Verify the installation

**Do this before you touch a client.** An MCP client is the worst place to discover a runtime
problem, because it shows you a closed transport and nothing else.

```bash
.venv/bin/python tools/check_install.py --server "$PWD/.venv/bin/nexus-memory"
```

Nine checks against the **installed console command**, over MCP stdio, in **three separate
processes**: store a memory; let the process exit; start a new one against the same file;
read the content back and compare **UTF-8 bytes**; find it by search; then retry the write
with the same idempotency key and require that **every field of the receipt replays** and
that **no duplicate was written**.

Two of those are the ones a manual walkthrough skips. A retry that quietly writes a second
copy looks exactly like a successful retry from the caller's side, and only the count shows
it. And a payload compared with `==` on text can pass while coming back in a different
Unicode normal form, so the sample carries a newline, a tab, CJK, Cyrillic, a combining
sequence and an emoji outside the BMP, and the comparison is on bytes.

Exit `0` pass · `1` a check failed, or the command did not serve MCP · `2` nothing installed
at that path. `--json PATH` also writes the full report to a file.

Expected tail on success:

```
PASS: 9/9 checks
```

## 3. Connect Claude Code

**Verified with Claude Code CLI 2.1.270 on macOS 26.1, user scope**, against the environment
built in §1.

Use a **temporary, distinctly-named** entry to verify, so you cannot overwrite an entry you
already depend on:

```bash
claude mcp add nexus-memory-release-check -s user -- \
  /absolute/path/Nexus/.venv/bin/nexus-memory \
  --db /absolute/path/nexus-data/memory.sqlite3 \
  --namespace my-repo --actor local

claude mcp get nexus-memory-release-check     # expect: Status: ✔ Connected
```

`Status: ✔ Connected` is the evidence. A configuration that merely parses is not a
connection, and neither is `⏸ Pending approval`. When you are satisfied:

```bash
claude mcp remove nexus-memory-release-check -s user
```

then add your real entry under the name you want to keep.

Project scope is the alternative — a `.mcp.json` committed with the repository:

```json
{
  "mcpServers": {
    "nexus-memory": {
      "command": "/absolute/path/Nexus/.venv/bin/nexus-memory",
      "args": [
        "--db", "/absolute/path/nexus-data/memory.sqlite3",
        "--namespace", "my-repo",
        "--actor", "local"
      ]
    }
  }
}
```

> [!IMPORTANT]
> **Paths in JSON must be absolute and literal.** `~`, `$HOME` and `${VAR}` are shell and
> environment syntax; nothing expands them here, and a `~` in `command` is a file that does
> not exist. The server's own `--db` argument is not expanded either.
>
> Observations from client testing, recorded rather than assumed:
> - A **project-scoped** server reports `⏸ Pending approval` until you start `claude`
>   interactively once and approve it. **Pending is not connected.** A user-scoped entry
>   connects without that step — that is the scope verified for this candidate.
> - Defining the **same server name in two scopes** does not merge them: the client reports a
>   conflict and one endpoint wins. Use one scope, or distinct names.

Other MCP clients take the same stdio shape, but **only Claude Code has been tested.**

The server exposes seven tools and this release adds none: `record`, `revise`, `forget`,
`get`, `history`, `search`, `status`.

## 4. Where the database lives

Pass `--db /absolute/path/memory.sqlite3` and you always know. **This is the recommendation**,
and it is what §3 shows.

> [!WARNING]
> **`status` does not report the database path.** It never has — there is no path field in
> its output, on either status schema. An earlier version of this page said otherwise. If you
> did not pass `--db`, the defaults below are the only answer, and this release does not
> change the schema to add one.

Without `--db`, resolved at launch:

| platform | default |
| --- | --- |
| macOS | `~/Library/Application Support/Nexus Memory/memory.sqlite3` |
| Linux | `$XDG_DATA_HOME/nexus-memory/memory.sqlite3` — **only if `$XDG_DATA_HOME` is set and absolute**; otherwise `~/.local/share/nexus-memory/memory.sqlite3` |
| Windows | `%LOCALAPPDATA%` (else `%APPDATA%`, else home) `/Nexus Memory/memory.sqlite3` — untested |

A relative or empty `$XDG_DATA_HOME` is ignored rather than honoured, so on Linux the
fallback is what you get unless you set it properly.

## 5. Back up, update, recover

The store is one SQLite file plus, in WAL mode, up to two sidecars beside it.

### Back up with the online backup API

Correct whether or not the server is running, and the procedure this page recommends:

```bash
.venv/bin/python - '/absolute/path/nexus-data/memory.sqlite3' '/absolute/path/backups/memory-2026-09-20.sqlite3' <<'PY'
import sqlite3, sys
from pathlib import Path

source, destination = Path(sys.argv[1]), Path(sys.argv[2])
if destination.exists():
    raise SystemExit(f"refusing to overwrite an existing backup: {destination}")
destination.parent.mkdir(parents=True, exist_ok=True)

# as_uri() percent-encodes the characters a URI would otherwise eat. A path built by
# string interpolation breaks on a space and breaks WORSE on '#', which starts a URI
# fragment: SQLite then opens a DIFFERENT, empty database and reports no error at all.
src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
dst = sqlite3.connect(destination)
with dst:
    src.backup(dst)
src.close(); dst.close()
print("backed up to", destination)
PY
```

Verified end to end on this host, against a seeded store in a directory whose name contained
spaces and parentheses, backing up to a filename containing `#`: two memories in, two
memories out, the same `memory_id` and `revision_id` values, the same `latest_durable_seq`,
and `search` finding the copy's content — read back by a **new server process started against
the backup**. The naive `f"file:{path}?mode=ro"` form failed on that same path, which is why
the snippet uses `as_uri()`.

**Restore** by pointing `--db` at the copy. There is no restore tooling; it is a file you own.

### If you copy files instead

Stop the server, and copy `memory.sqlite3` **together with any `-wal` and `-shm` sidecars.**

On this host the sidecars **do persist on disk after a clean exit**, with `-wal` at zero
bytes, and a copy of the main file alone did reproduce the store. That is an observation
about one build on one platform, **not a guarantee**: a `-wal` holding uncheckpointed data is
exactly the case where copying the main file alone loses writes, and you cannot tell by
looking. The backup API does not depend on any of this.

There is no automatic backup and no rotation.

### Update

```bash
# 1. back up first, with the procedure above
# 2. then
git fetch --tags
git checkout <the new tag>
python3 tools/install.py                       # rebuild the environment
.venv/bin/python tools/check_install.py --server "$PWD/.venv/bin/nexus-memory"
```

Restart the client's server process afterwards — a stdio server is launched by the client, so
the old process keeps running until the client restarts it. In Claude Code, `claude mcp get
<name>` after a restart shows what it is now running.

If an update misbehaves, check out the previous tag, rebuild the same way, and point `--db`
at the backup you took in step 1. There is no migration tooling and no automatic rollback.

## 6. Known limits

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
  while consuming 1.56× the baseline's combined input and cache-read tokens. A rendered-notes
  arm tied on all four — so the run supports "having the information helps" no better than
  "Nexus's retrieval helps". Heavily qualified, and **not in Nexus's favour**. Evidence:
  [CLOSEOUT-a1.md](https://github.com/soryko/Nexus/blob/f5e75cad6321640992fae87c49585e7ffb2b73b2/benchmarks/agent/CLOSEOUT-a1.md)
  on the evaluation branch, at an immutable commit.

**Operational**
- **Single trusted user.** This milestone trusts whoever can read the database and launch the
  service. No remote authentication, no encryption at rest, no multi-tenancy.
- Namespace and actor **bind at launch** and cannot be overridden by a tool call.
- Without `--repo`, or with `git` missing, reference-carrying writes are refused with
  `verification_unavailable`. Everything else works.
- A verified reference records that an object existed at a commit. **It does not establish
  that the memory's claim about it is true.**
- Linux and macOS are the release targets. Windows paths are handled in code but **untested**.

## 7. If something goes wrong

| Symptom | First thing to check |
| --- | --- |
| Client shows the server connecting then closing | `python3 tools/install.py --check-only` — almost always a runtime below one of the two floors |
| `startup_error: unsupported_runtime` in the server's stderr | the **SQLite** floor, not the Python one |
| `uv` refuses your interpreter as a "broken installation" | see the macOS note in §1; use a uv-managed interpreter |
| `uv is required to build the environment` | uv is not on PATH — install it, then rerun |
| The client launches a different build than you expect | `command` is a path, not a name; check it, and restart the client after an update |
| `⏸ Pending approval` | project-scoped entry — start `claude` interactively once, or use user scope |
| Writes refused with `verification_unavailable` | no `--repo` bound, or `git` not on PATH |
| A retry seems to have written twice | run `tools/check_install.py`; it asserts exactly this |
| Search misses something you know you stored | it may live only in a superseded revision — try `history` |
| A backup opened empty, with no error | the source URI was built by string interpolation and a `#` in the path truncated it — use the `as_uri()` snippet in §5 |
