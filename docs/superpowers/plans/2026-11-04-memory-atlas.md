# Nexus Memory Atlas Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task by task. Check each step after verifying it.

**Goal:** Let the owner of an enrolled project find a memory among thousands and inspect its content, revisions, citations and evidenced agent activity.

**Architecture:** The installed project launcher resolves and revalidates the checkout. A loopback Python server serves a scoped SQLite reader and a bundled browser client. The server counts and pages results. The browser renders only the selected neighborhood.

**Tech Stack:** Python 3.12+, SQLite 3.51.3+ with FTS5, standard-library HTTP, HTML, CSS, JavaScript modules and SVG. Use pytest for backend tests and Playwright with Chromium as an optional development tool for browser tests. Keep the runtime dependency list unchanged.

**Spec:** [Approved Memory Atlas design](../specs/2026-11-04-memory-atlas-design.md)

## Global constraints

- Bind one existing enrolled checkout, database, namespace and actor per launch. Recheck visibility on each read. Never enroll, migrate, reindex, record an inspection, or serve a forgotten memory.
- Start with 25 neighbors total. Across expansions display at most 100 nodes and 150 edges. Counts, filters and search apply to the entire eligible set.
- Preserve Nexus lexical ordering, exact revision identity, date precision and project isolation. Show **Unknown** for missing fetch evidence.
- Release fixture: 10,000 active memories, one file with at least 1,000 current citations, isolated memories, revisions, forgotten data and an overlapping second scope. Cold overview target ≤2 seconds. Warm selection-to-visible-neighborhood-and-summary target p95 <300 ms over at least 30 interactions.

## Review focus

1. A store replaced at the same path or retired during a session invalidates the binding (Task 1).
2. A historic citation and a current citation to the same path at different commits remain distinct (Task 2).
3. A copied receipt with matching text or IDs in the wrong project never establishes a handoff (Task 3).
4. Saved content containing markup and hostile localhost requests cannot run script or read project data (Tasks 4 and 5).
5. After a write expires a cursor, Back must not redisplay stale counts, cached content or a forgotten selection (Tasks 2 and 5).

## File map and starting point

| Area | Files | Responsibility |
|---|---|---|
| Scope and store | Existing `integrations/project_scope/{launcher,project_scope}.py`, `src/nexus_memory/storage/sqlite.py`; new `src/nexus_memory/atlas/{__init__,binding}.py` | Launch authority, lifetime checks and read-only connections |
| Queries and evidence | New `src/nexus_memory/atlas/{queries,evidence}.py` | Counted pages, visible revisions, evidence joins and graph neighborhoods |
| Delivery | New `src/nexus_memory/atlas/server.py`, `src/nexus_memory/atlas/static/{index.html,atlas.css,app.js,graph.js}` | Authenticated local HTTP, application state, accessible graph and inspector |
| Verification | New `tests/atlas/{conftest,test_binding,test_queries,test_evidence,test_http,test_ui}.py`, `benchmarks/atlas/run.py`; existing `pyproject.toml`, `README.md`, `integrations/project_scope/README.md` | Disposable stores, browser tests, repeatable timings, launch instructions |

The project-scope integration and handoff measurements currently exist as local working-tree additions. At execution start, verify they are available in the implementation checkout. Preserve unrelated edits. Use a small synthetic receipt in tests and never require a private installation, untracked measurement file or actual user store in CI. The ordinary `SQLiteRepository` constructor calls `_migrate()`; the viewer must have a separate open path before it can read real data.

## Task 1 · Open only the enrolled store

**Contract:** In `binding.py`, `open_binding(checkout: Path, resolve_project: Callable) -> AtlasBinding`. The installed launcher supplies its existing `resolve`. `binding.reader() -> SQLiteRepository` revalidates the registration, main checkout identity, store device/inode, and pinned scope before opening. Add `SQLiteRepository.open_readonly(path: Path) -> SQLiteRepository` and preserve normal constructor behavior.

- [ ] Add `test_binding.py`. For an unenrolled checkout, absent store, unsupported schema, replaced file and retired store assert refusal; for a valid linked worktree assert the same project ID as its main checkout. Freeze a live WAL reader/writer fixture, check that committed WAL data is visible, and spy on migration/profile rebuild methods to assert zero calls. Attempt a write through the viewer connection and assert SQLite refuses it.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_binding.py` and observe failure for the missing read-only entry point.
- [ ] Implement URI-escaped `mode=ro` connections with `query_only=ON`. Read existing schema and index profiles without repair, including the absent optional history-profile table. Do not use SQLite immutable mode on a live WAL store. Verify binding around connection acquisition and before releasing a response. Add fixture setup to `tests/atlas/conftest.py` using the normal writer on disposable paths.
- [ ] Run the binding tests plus `tests/tools/test_project_scope.py` and `tests/core/test_search_profiles.py`. Commit the binding, storage change and tests when green.

## Task 2 · Count, search and traverse visible revisions

**Contract:** `AtlasQueries(binding)` in `queries.py` exposes `overview()`, `search(query: SearchQuery)`, `inspect(memory_id, revision_id=None)`, `body(memory_id, revision_id)`, `history(memory_id, cursor=None)`, and `citations(repository_id, path, commit_oid, query: SearchQuery)`. Search and hub pages return `items, matched, total, cursor, generation`. For hubs, total is the unfiltered hub size. Use the existing exact get for an ID, even if its text would not match. Full bodies are fetched separately.

- [ ] In `test_queries.py` assert `sum(overview.counts) == active_memories`; orphaned memories count once; all search pages concatenate to core search order for all four index profiles; tag/kind/path/term filters combine before counting. With citations for two commits at the same path, assert a hub returns only current revisions citing the requested triple. Assert earlier revision content and references remain available only through history. Forget between pages and assert cursor expiration and absence from counts/inspection.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_queries.py` to see the new contracts fail.
- [ ] Factor the existing authoritative head join, lexical match legs and filters from `storage/sqlite.py` for reuse by page and count queries. Execute both in one read transaction; preserve existing core return types/cursors. Add read-only aggregate and reverse-reference SQL, generation-bound hub cursors, original creation time from the root revision, and bounded first-line titles. For related matches apply the selected term/tag, exclude self before pagination and retain search order. Test counts on final and empty pages. Avoid loading blobs just to count or list.
- [ ] Run the query suite and `.venv/bin/python -m pytest -q tests/core`. Commit counted reads and compatibility tests.

## Task 3 · Attribute observed interface activity

**Contract:** `ReceiptEvidence.load(path: Path | None, scope: Scope) -> ReceiptEvidence` in `evidence.py`. Join events to visible revisions through `AtlasQueries` on each request. Add `neighbors(memory_id, revision_id, limit=25)` and `interface_events(interface, query, cursor=None)`. Edge payloads identify relation, endpoints, source, date precision and revision. Missing optional evidence yields an empty event set.

- [ ] In `test_evidence.py` model reciprocal handoffs with synthetic IDs using the native receipt's `test_store.scope`, `handoffs`, `tool_sequence`, and `verified_reader_answer`. Assert a dated write and fetch only for a validated handoff. Check wrong scope, wrong revision, mismatched body, search-only, malformed dates, forgotten records and repeated inspections. Verify a fetch on an older revision is labeled in the memory summary and never attributed to a newer revision. Multiple equally precise latest-day fetches must not imply a within-day order.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_evidence.py` and verify failure before adding the adapter.
- [ ] Parse only an explicitly selected local receipt, enforce bounded input, accept the supported successful native format, and require matching project, exact revision/content and attributable operation. Reject a malformed file with a visible evidence status while allowing ordinary browsing. Derive the latest observed fetch from accepted events. Page interface hubs like file hubs. Distribute the initial neighbor allowance across available relation types with deterministic ordering (newest evidence first, unknown last, stable identity). Expose totals per relation.
- [ ] Run the evidence and query suites. Commit the adapter and neighborhood API.

## Task 4 · Deliver the viewer over loopback

**Contract:** Add installed-launcher command `python -I launcher.py atlas <checkout> [--receipt <path>]`, using the installed Nexus interpreter. In `server.py`, `serve(binding: AtlasBinding, evidence: ReceiptEvidence) -> None` binds an ephemeral loopback port, prints the local launch URL, opens it when possible, and stops on interrupt. Keep both resolver and launch authority outside the selected checkout.

- [ ] In `test_http.py` use disposable loopback requests. Assert a launch token is exchanged once for a per-launch HttpOnly, SameSite cookie and redirected to a clean URL. Assert an absent session, wrong Host, foreign Origin, traversal path or write operation is refused. Exercise all reads, stale-cursor response, expired scope, and absent evidence. Verify no token in request logs and no project bodies in unauthenticated responses.
- [ ] Run `.venv/bin/python -m pytest -q tests/atlas/test_http.py` to demonstrate the missing server.
- [ ] Implement same-origin GET routes for overview, search, inspect, body, history, citations, neighbors, events and related results. Allowlist bundled static assets. Reject browser-supplied scope or filesystem paths. Return 409 for expired paging, 404 for invisible identities and stop serving data after binding failure. Set no-store, no-referrer, a self-only content policy and frame protection. Keep existing launcher commands intact. Package a minimal local index page so launch is testable in isolation.
- [ ] Run HTTP and project-scope tests; build a wheel and verify the index is included. Commit launcher, HTTP server, initial asset and tests.

## Task 5 · Explore a large project without losing your place

**Contract:** `app.js` owns project filters, hub filter, selected IDs, generation, list cursor/scroll, camera and visible expansion IDs. `graph.js` exports `renderGraph(container, state, onSelect)` for bounded SVG rendering and keyboard-equivalent list actions. Persist navigation state in session memory. Back re-reads authorized data before displaying it.

- [ ] Add `test_ui.py` with a headless Chromium fixture. Assert that selecting a kind, searching beyond page one and opening a memory works with keyboard alone; blank and unlinked cases remain usable; a rendered HTML-like payload stays inert. Expand repeatedly and assert ≤100 nodes and ≤150 edges. Check fixed existing positions, pan/zoom/fit, count labels and accessible overflow pages. Select an older revision, check its parent diff, then navigate Back after a forget. Assert the stale text never reappears.
- [ ] Add a separate browser-test development group in `pyproject.toml` and update `uv.lock` for that group. Run `uv sync --group atlas-ui`, `uv run --group atlas-ui playwright install chromium`, then `uv run --group atlas-ui pytest -q tests/atlas/test_ui.py` and observe the missing UI behavior. The core test command must still work without browser tooling.
- [ ] Build the kind overview with exact counts and capped group size. Implement full-project search, filter reset, relation-specific expansion and summary/body loading; use text nodes for all stored content. Provide graph and list routes to the same selection, progressive labels, pan/zoom/fit and stable placement. Show omitted counts whenever either budget binds. Keep history/diff and explained keyword/tag discovery separate from evidenced edges. Restore filters, scroll, camera and expansion on Back. Give hubs a fresh filter, recenter on selection, and announce expired cursors before restarting the affected page. Ignore late responses from earlier selections.
- [ ] Run browser tests at desktop and 360px width, including unchanged last-fetch dates after inspection and “matching of total” hub counts. Verify packaged CSS and JS in the wheel. Commit assets, dev tooling and browser tests.

## Task 6 · Measure and release-check the installed path

**Contract:** `uv run --group atlas-ui python benchmarks/atlas/run.py --memories 10000 --interactions 30` creates a disposable enrolled project, launches the actual viewer and reports fixture counts, timing samples, p95, cache state, hardware/browser versions and maximum visible graph sizes.

- [ ] Add fixture assertions before the generator: exactly 10,000 active scoped memories, ≥1,000 distinct current citations to one file, at least one isolated memory, plus revisions, foreign-scope and forgotten controls. First run should fail until the fixture exists.
- [ ] Implement the generator and end-to-end timer. Measure fresh process start through visible counted overview; separately measure warm browser selection through rendered neighborhood and inspector summary. Exercise late result pages, a dense file hub and Back at both widths. Include a run without any receipts. Record measured values separately from targets; diagnose a missed budget before claiming readiness.
- [ ] Run the benchmark on a named development laptop and run `uv run --group atlas-ui pytest -q`. Install the built wheel into a clean environment and launch it via the installed scope integration from outside the source tree. Confirm the viewer performs no enrollment, migration or evidence writes.
- [ ] Document installation, launch, privacy boundary, evidence limits and measured results in `README.md` and `integrations/project_scope/README.md`. Commit the harness, assertions and verified instructions. Review the whole diff against the approved spec before proposing integration.
