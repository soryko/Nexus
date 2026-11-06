# Nexus Memory Atlas Implementation Plan (v2)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task by task. Check each step after verifying it.

**Goal:** Let the owner of an enrolled project find a memory among thousands and inspect its content, revisions and file citations.

**Architecture:** The installed project launcher resolves and revalidates the checkout. A loopback Python server serves a scoped SQLite reader and a bundled browser client. The server counts and pages results. The browser renders only the selected neighborhood.

**Tech Stack:** Python 3.12+, SQLite 3.51.3+ with FTS5, standard-library HTTP, HTML, CSS, JavaScript modules and SVG. Use pytest for backend tests and Playwright with Chromium as an optional development tool for browser tests, run in its own CI job. Keep the runtime dependency list unchanged.

**Spec:** [Memory Atlas design v2](../specs/2026-11-04-memory-atlas-design-v2.md)

**Changes from v1 of this plan** (from the critical review): receipt evidence and interface nodes removed; Task 0 added so the plan builds on committed code; read path, snapshot, tombstone, paging, launcher, security, CI and benchmark contracts made explicit; Task 5 split into six.

## Global constraints

- Bind one existing enrolled checkout, database, namespace and actor per launch. Recheck visibility on each read. Never enroll, migrate, reindex, record anything, or serve a forgotten memory.
- Start with 25 neighbors total. Across expansions display at most 100 nodes and 150 edges. Counts, filters and search apply to the entire eligible set.
- Preserve Nexus lexical ordering, exact revision identity and project isolation. Show no agent-activity data of any kind.
- Release fixture: 10,000 active memories, one file with at least 1,000 current citations, isolated memories, revisions, forgotten data and an overlapping second scope. Cold overview target ≤2 seconds. Warm selection-to-visible-neighborhood-and-summary target p95 <300 ms over at least 100 interactions on distinct targets. Targets, not claims, until measured.
- Count definitions: `matched` = items satisfying query and filters; `total` = unfiltered set the list draws from (project: all active in scope; hub: all current citing memories); `omitted = matched − drawn`.

## Review focus

1. A store replaced at the same path or retired during a session invalidates the binding (Task 1).
2. A historic citation and a current citation to the same path at different commits remain distinct, and the same path and commit under two repositories remain distinct (Task 2).
3. Saved content containing markup and hostile localhost requests cannot run script or read project data (Tasks 4 and 5c).
4. After any write expires a cursor, Back must not redisplay stale counts, cached content or a forgotten selection (Tasks 2 and 5e).
5. No query path leaks a forgotten memory, and one request never mixes snapshots (Task 2).

## File map and starting point

| Area | Files | Responsibility |
|---|---|---|
| Scope and store | `integrations/project_scope/{launcher,project_scope}.py`, `src/nexus_memory/storage/sqlite.py`; new `src/nexus_memory/atlas/{__init__,binding}.py` | Launch authority, lifetime checks and read-only connections |
| Queries | New `src/nexus_memory/atlas/queries.py` | Counted pages, visible revisions, citation hubs, neighborhoods, revision comparison |
| Delivery | New `src/nexus_memory/atlas/server.py`, `src/nexus_memory/atlas/static/{index.html,atlas.css,app.js,graph.js}` | Authenticated local HTTP, application state, accessible graph and inspector |
| Verification | New `tests/atlas/{conftest,test_binding,test_queries,test_http,test_ui}.py`, `benchmarks/atlas/run.py`; existing `pyproject.toml`, `.github/workflows/tests.yml`, `README.md`, `integrations/project_scope/README.md` | Disposable stores, browser tests, repeatable timings, launch instructions |

The ordinary `SQLiteRepository` constructor calls `_migrate()`; the viewer needs a separate open path.

## Known limits

- **Replaced checkout without a birth time.** The binding pins the Git common directory by device, inode and birth time, the same identity the project-scope registry records. Linux filesystems expose no birth time through `os.stat`, so a checkout deleted and recreated so that the new directory reuses the old inode is not detected there; on macOS it is. The binding tests create the replacement while the old directory still exists, which proves the property that can be proved. Closing the gap needs `statx` birth times or a content-based identity, in the project-scope integration rather than in Atlas.

## Task 0 · Commit the prerequisites

The project-scope integration (`integrations/`, `tests/tools/test_project_scope.py`, `tests/transport/test_project_isolation_e2e.py`, `tools/retire_legacy_store.py` and its test) is untracked. Atlas depends on it.

- [ ] Confirm with the owner which of these are ready to commit; leave the handoff measurements and `docs/measurements/*` out of this plan entirely.
- [ ] Commit the integration and its tests on their own, with `.venv/bin/python -m pytest -q tests/tools tests/transport` green first. Verify `git status` shows no per-machine files staged.
- [ ] Decide and record how the installed launcher gets the new `atlas` subcommand: the installed copy under `project-scopes-v1/bin` is a separate file, so Task 6 re-copies it explicitly. State how the wheel is expected to ship (or deliberately not ship) `integrations/`, and test that the `atlas` package and its `static/` files are in the wheel.
- [ ] Do not start Task 1 until `git status` shows these files committed on the working branch.

## Task 1 · Open only the enrolled store

**Contract:** In `binding.py`, `open_binding(checkout: Path, resolve_project: Callable) -> AtlasBinding`. The installed launcher supplies its existing `resolve`. `binding.reader()` returns a read-only `SQLiteRepository` after checking registration, main-checkout identity, store device/inode and pinned scope. Add `SQLiteRepository.open_readonly(path)`; keep normal constructor behavior.

- [ ] Add `test_binding.py`. For an unenrolled checkout, absent store, unsupported schema, replaced file and retired store assert refusal. Build the retired fixture with the real `tools/retire_legacy_store.py` marker (`user_version` 2147483647 plus `nexus_retired`), not a faked number. For a valid linked worktree assert the same project ID as its main checkout. Freeze a live WAL reader/writer fixture, check that committed WAL data is visible, and spy on migration and profile-rebuild methods to assert zero calls. Attempt a write through the viewer connection and assert SQLite refuses it.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_binding.py` and observe failure for the missing entry point.
- [ ] `open_readonly` is a separate construction path that keeps the SQLite-floor check and profile validation but skips `_migrate()`. Override `_connect()` for read-only instances: URI-escaped `file:…?mode=ro`, `query_only=ON`, same timeout and pragmas otherwise. Every inherited read method (`get`, `history`, `search`, `status`) goes through `_connect()`, so assert that no connection from a read-only instance can write. Read existing schema and index profiles without repair, including the absent optional history-profile table. Do not use SQLite immutable mode on a live WAL store. Check what read-only WAL needs from `-shm` and test it on the real fixture.
- [ ] Two-tier binding check, designed in now (the `binding.reader()` interface is the same either way). **Full check** (`resolve`, runs `git rev-parse`, ~28 ms): at launch, on navigation and on explicit refresh. **Quick check**, no subprocess, at request start and again before the response is released: re-stat the pinned identities (common dir device/inode/birth_time, `.git` marker, linked-worktree admin dir and backlink, registry file inode/size/mtime, database file and `-wal/-shm/-journal` sidecars: regular, single link, owner, mode) and read `PRAGMA user_version` plus the retirement marker inside the request’s snapshot. Retirement is committed on the original inode (see `integrations/project_scope/README.md`), so stat identity alone cannot detect it; the in-snapshot schema check is required. No time-based cache. Compare nanosecond timestamps (`st_mtime_ns`, `st_ctime_ns`) and stat the database path both before and after the request’s snapshot; a mismatch refuses the response. At the full check, pin the resolved target of the worktree backlink and compare its file identity in the quick check (the quick check never re-runs git).
- [ ] Differential test: for each mutation (swap database at the same path, replace `registry.json`, remove the project from the registry, replace the `.git` marker, move the checkout, prune the linked worktree, retire the store in place) assert the quick check and a fresh full `resolve` both refuse. A mutation that only the full check catches is a bug in the quick check. Task 6 separately reports the measured cost of each tier.
- [ ] Run the binding tests plus `tests/tools/test_project_scope.py` and `tests/core/test_search_profiles.py`. Commit the binding, storage change and tests when green.

## Task 2 · Count, search and traverse visible revisions

**Contract:** `AtlasQueries(binding)` in `queries.py` exposes `overview()`, `search(query: SearchQuery)`, `inspect(memory_id, revision_id=None)`, `body(memory_id, revision_id)`, `history(memory_id, cursor=None)`, `compare(memory_id, revision_id)` and `citations(repository_id, path, commit_oid, query: SearchQuery)`. Search and hub pages return `items, matched, total, cursor, generation`. Use the existing exact get for an ID, even if its text would not match. Full bodies are fetched separately. `compare` diffs a revision with its parent on the server, with a size cap and a “too large” result.

- [ ] Add a transaction-scoped read API (for example `with repo.read_snapshot() as db`) and refactor `get`, `history`, `search` and `status` into `_in(db, …)` helpers so one Atlas request, including `inspect`, runs on one snapshot. Existing public methods keep their behavior and return types.
- [ ] In `test_queries.py` assert `sum(overview.counts) == active_memories`; orphaned memories count once; all search pages concatenate to core search order for all four index profiles; tag/kind/path/term filters combine before counting. Cover `matched` and `total` on first, final and empty pages.
- [ ] Citations: assert a hub returns only current revisions citing the requested triple; two commits at the same path stay distinct; the same path and commit under two different repository IDs stay distinct; a hub for a repository that is not the bound checkout’s works without calling `bind_checkout`. Core’s query filter only knows `any` and `bound`, but `SQLiteRepository.search` takes the resolved repository ID as an argument, so the hub passes the ID it wants with `repository="bound"` and needs no new predicate and no `bind_checkout`. Earlier revisions’ content and references are available only through `history` / `inspect(old revision)`.
- [ ] Forgotten data: enumerate every new SQL path (overview, hub, root-revision time, `body`, `history`, `compare`, related, `inspect` of an old revision) and give each a test that forgets a memory and asserts it is absent from counts, lists, hubs, bodies and comparison. Every new query must join `memories` with `tombstoned = 0`; forgotten records keep their revision and reference rows in the store.
- [ ] Snapshot consistency: forget a memory between the internal reads of `inspect` and assert the response is either entirely visible or entirely refused.
- [ ] Paging: the store-wide generation advances on every record, revise and forget, so cursors expire on unrelated writes. Define and test the restart contract: lexical lists restart from page 1 and report that they did; browse lists may resume from the last durable position. Test both an unrelated `record()` on page 20 of 400 and a `forget`. Return 409 semantics to the HTTP layer as a typed error.
- [ ] `revision_references` is keyed by (namespace, actor, memory, revision, commit, path), so a reverse lookup by (repository, path, commit) has no supporting index. Add an `EXPLAIN QUERY PLAN` test on the 1,000-citation hub and report scan cost on the 10,000-memory fixture. Adding an index is out of scope for v1: if the budget is missed, report it and propose a migration separately.
- [ ] Related matches: apply the selected term/tag, exclude the selected memory before pagination and keep search order. `tags` is a global table, so every tag query must scope through the head join, never through `tags` alone; test an overlapping tag in a second scope and on a forgotten memory.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_queries.py` first to see the contracts fail, implement, then run it and `.venv/bin/python -m pytest -q tests/core`. Avoid loading blobs just to count or list. Commit counted reads and compatibility tests.

## Task 3 · Bound neighborhoods

**Contract:** `neighbors(memory_id, revision_id=None, limit=25)` in `queries.py` returns the focus node plus its recorded connections, grouped by relation type (`revision-of`, `cites`), with per-type totals. No agent-activity data exists in v1.

- [ ] In `test_queries.py` add: the first 25 neighbors are distributed across relation types deterministically (newest revision timestamp first, then stable ID); a memory with 1,000 citing relations plus a few `revision-of` relations still shows both types in the first allowance and reports independent totals; a memory with no relations returns just itself; a forgotten neighbor is excluded and its edge disappears.
- [ ] Implement it over the Task 2 snapshot API. Expose `matched`, `total` and `omitted` per relation type.
- [ ] Run the query suite. Commit the neighborhood API.

## Task 4 · Deliver the viewer over loopback

**Contract:** Add installed-launcher command `python -I launcher.py atlas <checkout>`, using the installed Nexus interpreter. In `server.py`, `serve(binding) -> None` binds an ephemeral loopback port, prints the local launch address without the secret, opens the browser through a mechanism that does not put the secret in a visible argv, and stops on interrupt. Keep both resolver and launch authority outside the selected checkout.

- [ ] Launcher control flow: `main()` currently reads `args.runtime` for every non-`enroll` command, so branch on `atlas` before that, require an absolute checkout, and give atlas its own error handling. `NexusError` is a plain `Exception` and today escapes as a traceback; catch it and print an accurate message. A bind failure (`OSError`) must not be mislabeled as a scope error.
- [ ] In `test_http.py` use disposable loopback requests. Secret handling: deliver the secret in the URL fragment, which never reaches the server, a log or a `Referer`; page JS strips it with `history.replaceState` and exchanges it once with a POST for a session cookie. The cookie is `HttpOnly`, `SameSite=Strict`, `Path=/`, named with a per-launch random suffix so two launches on the same host do not overwrite each other; compare in constant time. Disable the default request-line logging of `BaseHTTPRequestHandler`, or log only method, route name and status, and assert that no secret, search term, cursor or path appears in captured stderr/stdout.
- [ ] Assert refusals: absent session, wrong `Host` (including a valid session cookie with a rebound hostname), foreign `Origin`, missing `Origin` on a state-changing route, traversal path, symlinked asset, non-GET/POST method. Exercise all reads, stale-cursor response, expired scope, and a store changed after launch.
- [ ] Assert response headers on every route: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, a self-only `Content-Security-Policy` with no inline script, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`. Serve a stored memory whose body contains `<script>` and `<img onerror>` and assert it is returned only as JSON text with a JSON content type.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_http.py` to demonstrate the missing server, then implement same-origin routes for overview, search, inspect, body, history, compare, citations, neighbors and related results. Allowlist bundled static assets. Reject browser-supplied scope or filesystem paths. Return 409 for expired paging, 404 for invisible identities and stop serving data after binding failure. Keep existing launcher commands intact. Package a minimal local index page so launch is testable in isolation.
- [ ] Run HTTP and project-scope tests; build a wheel and verify the `atlas` package and `static/` files are included. Commit launcher, HTTP server, initial asset and tests.

## Task 5 · Explore a large project without losing your place

**Contract:** `app.js` owns project filters, hub filter, selected IDs, generation, list cursor/scroll, camera and visible expansion IDs. `graph.js` exports `renderGraph(container, state, onSelect)` for bounded SVG rendering and keyboard-equivalent list actions. Persist navigation state in session memory. Back re-reads authorized data before displaying it. Each sub-task below is its own red/green cycle and its own commit.

**5a · Browser tooling and smoke test.**
- [ ] Add a `atlas-ui` dependency group in `pyproject.toml`, update `uv.lock`, mark `tests/atlas/test_ui.py` with a `ui` marker and deselect that marker in default `addopts` so bare `pytest -q` never imports Playwright. Add a separate CI job: `uv sync --frozen --group atlas-ui`, `playwright install --with-deps chromium`, run the `ui` marker, and fail if any selected test skipped. Pin the Playwright version.
- [ ] Add a Chromium smoke test that launches the viewer against the fixture and sees the overview. Run `uv sync --group atlas-ui`, then `uv run --group atlas-ui pytest -q -m ui tests/atlas/test_ui.py`, observe failure, implement, pass. Verify the default `pytest -q` still works without browser tooling. Commit.

**5b · Overview, search and list.**
- [ ] Tests: kind groups with exact counts and capped size; selecting a group opens a paged list; searching beyond page one and opening a memory work by keyboard alone; blank and unlinked memories remain usable; filter reset. Implement and commit.

**5c · Graph, budgets and placement.**
- [ ] Tests: expand repeatedly and assert ≤100 nodes and ≤150 edges before and after; existing node positions are unchanged after expansion; pan, zoom, fit; omitted-count labels whenever either budget binds; rendered HTML-like payloads stay inert (text nodes only). Implement and commit.

**5d · Hubs and inspector.**
- [ ] Tests: dense file hub shows “25 of 840”, pages to the last result, hub filter shows “12 matching of 840”; selecting the last result recenters even when the budget is full; a dense `cites` type cannot hide `revision-of`; the revision inspector shows the parent comparison, a “too large” state, the historical label and the older revision’s own references. Implement and commit.

**5e · Back, revalidation and stale responses.**
- [ ] Tests: Back restores search, filters, list position, camera, selection and expansions; after a forget, Back shows no stale text, counts, cached bodies or selection, including through the browser’s back/forward cache; after an unrelated write the viewer announces the expired cursor and restarts the affected list; late responses from an earlier selection are ignored. Implement and commit.

**5f · Keyboard and narrow viewport.**
- [x] Tests: the whole find → hub → last result → Back journey by keyboard with a text path for every graph action, and at 360 px width. Verify packaged CSS and JS are in the wheel. Commit.

## Task 6 · Measure and release-check the installed path

**Contract:** `uv run --group atlas-ui python benchmarks/atlas/run.py --memories 10000 --interactions 100` creates a disposable enrolled project, launches the actual viewer and reports fixture counts, timing samples, p95, spread, cache state, hardware/browser versions, maximum visible graph sizes and the cost of per-request binding checks.

- [ ] Add fixture assertions before the generator: exactly 10,000 active scoped memories, ≥1,000 distinct current citations to one file, at least one isolated memory, plus revisions, foreign-scope and forgotten controls. First run should fail until the fixture exists.
- [ ] Run a negative control: the same harness against a tiny store and against a static no-op page, to measure harness and Chromium-launch overhead separately from Atlas.
- [ ] Measure cold start in at least 10 runs, with cache state defined and recorded (fresh copy of the fixture, OS file cache state, browser profile cold) and process launch separated from first paint of the counted overview. Time inside the page with `performance.mark`. Measure at least 100 warm selections on distinct targets and report p50, p95, max and spread; archive the raw samples. Exercise late result pages, a dense file hub and Back at both widths. Record measured values separately from targets; diagnose a missed budget before claiming readiness.
- [ ] Run the benchmark on a named development laptop and run the default `pytest -q` plus the `ui` job. Re-copy the launcher into the installed bin, install the built wheel into a clean environment and launch via the installed scope integration from outside the source tree. Confirm the viewer performs no enrollment, migration or other writes (hash the store and its WAL sidecars before and after).
- [ ] Document installation, launch, privacy boundary and measured results in `README.md` and `integrations/project_scope/README.md`; state plainly that v1 shows no agent-activity evidence. Commit the harness, assertions and verified instructions. Review the whole diff against the design before proposing integration.
