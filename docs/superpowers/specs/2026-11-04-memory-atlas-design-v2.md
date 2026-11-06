# Nexus Memory Atlas

Design v2 · 4 November 2026 · supersedes the approved design of the same date

**Changes from the approved design.** Receipt-based evidence is removed from version one: no interface nodes, no “written by / fetched by” edges, no “last observed fetch”, no `--receipt` option. The native handoff receipts have one file-level date, no per-event times, no schema version and no producer, so they cannot support the attribution rules this design requires. Instrumentation that records agent activity in the store is separate work, and Atlas can adopt it later. Version one shows only what the memory store itself records. Several contracts the review found vague are also made explicit: paging restart, count definitions, the repository key for files, and the revision comparison.

## What Sory should be able to do

Open one enrolled project locally, find a memory among thousands, read what it says, and follow how it was stored: its revisions and the files it cites. When a file is cited by hundreds of decisions, the user should be able to select the interesting ones without moving hundreds of dots out of the way.

## Project map and local graph

- **Project map.** Start with counted groups by current memory kind. Each active memory belongs to exactly one group, including memories without links. Size groups by count with a capped visual range and print the exact count. Selecting a group opens its searchable, paginated list. Groups are navigation controls. Draw no relation between groups based on membership or proximity.
- **Find.** Search current revision content with Nexus lexical search, or open a memory by its exact ID. Offer kind, tag, and verified repository/path filters. Combine filters consistently with Nexus search. The search box and result count cover the entire active filtered project, including memories outside the visible graph. Empty queries browse by recency. Indicate active filters and offer a single reset.
- **Focus.** Opening a result centers its current revision and inspector. The default graph shows direct recorded connections: the memory’s revisions and the files its current revision cites. Select a file to explore its citing memories. The visible node and edge budgets apply across all expansions. Start with 25 neighbors and hard limits of 100 graph nodes and 150 edges. Label omitted results, for example “25 of 840 citing memories”. Show the rest in a paged, keyboard-accessible list with search. Choosing an item can recenter the graph even when the budget is full. Order neighbor lists by revision timestamp, newest first, then stable ID. Keep relation types (cites, revision-of) separately accessible so a high-degree type cannot hide another type.
- **Orientation.** Support pan, zoom, fit, and back to the last selection. Show names on focus or hover, then reveal more at close zoom when they fit. Keep existing node positions stable on expansion. Zoom changes presentation only. Opening a group or expanding a relationship is deliberate. Back restores search, filters, list position, camera, selection, and expansions while revalidating visibility. Provide keyboard controls and a text path to every graph action.

The navigation default is one focus at a time. Choosing a citing memory recenters on it. Back restores the file list at the same page. A file opens with all its in-scope citing memories and its own clearly labeled filter, initially empty. Previous project-search filters remain in the project view when the user returns. Show “12 matching of 840 citing memories” when the hub filter narrows the list.

**Counts.** `matched` is the number of items satisfying the active query and filters. `total` is the size of the unfiltered set the list draws from: all active memories in scope for project search, all current citing memories for a file hub. `omitted` is `matched` minus the items drawn. The omitted label uses `matched`; “matching of” labels use `matched` and `total`.

The overview/local split draws on [Obsidian Graph view](https://obsidian.md/help/plugins/graph). Nexus edges need a record explaining their meaning. Screen position and an overlapping word are insufficient.

## Inspect and follow evidence

| Selection | Evidence and behavior |
|---|---|
| Memory | Show stored text, kind, tags, source if present, and IDs. Use a bounded first-line excerpt as a display title. Date added comes from the initial revision. Show the current revision date separately. Render `source` as text, never as a link. |
| Revision | Expand the parent chain on demand. Show the selected revision and a text comparison with its parent, computed on the server from the two bodies, capped in size, with a clear “too large to compare” state. When displaying an older revision, mark it as historical and display its own references. |
| File | Key a file node by the recorded repository identity, path and commit. Expand to current revisions that cite that exact triple. Opening older history can show historical citations, explicitly labeled, through that older revision’s own inspector. |
| Find related memories | Open a separate list of current memories that share a selected tag or match a user-selected term from the content. Explain the overlap, exclude the selected memory, and preserve normal search ordering. Keep these matches out of the recorded graph. |

Version one draws no edge for agent activity (who wrote or fetched a memory). The store has no general per-client fetch log, and nothing in the viewer may suggest otherwise: no “last fetched”, no fetch dates, no inferred edges. Opening or refreshing Atlas never records anything.

## Data and operating boundary

Ship a dedicated local browser viewer launched for one enrolled checkout. The project-scope integration (launcher, scope resolver and tests) must be committed before Atlas work begins, and the installed launcher gains an `atlas` command. Resolve the trusted project scope at launch with that integration. Pin the store, namespace and actor for the session. Fail closed if that binding is unavailable, ambiguous, retired (the store carries the legacy-retirement marker written by `tools/retire_legacy_store.py`), replaced at the same path, or changes. Bind the viewer to loopback with a per-launch access secret that is never placed in a URL path or query and never logged, validate host/origin, serve assets locally and render stored text as text. No write tools, parallel corpus, external services, or cross-project aggregation.

Use SQLite read-only access against a supported existing store. Preserve Nexus search, history, visibility and cursor semantics without invoking startup migrations, enrollment, reindexing or other write paths. Scope counts, reverse citations, groups and lists identically. Exclude forgotten records and their derived counts and edges; every query that reads revisions, references or bodies directly must apply the same visibility rule as core search. Each request reads from one consistent snapshot. Revalidate on navigation and explicit refresh.

**Paging after change.** Core cursors are bound to a store-wide generation that any write advances, so an unrelated write expires them. When a cursor expires the viewer says so and restarts the affected list from its first page under the new generation; it does not pretend to resume. Recency-browse lists may instead resume after the last durable position when that is cheap, and say so. Preserve selected IDs only while still visible.

Version one works with the memory store alone. Core schema v4 has revision creation times and Git evidence. Nothing else is read: no receipts, project JSON, or test fixtures.

## Scale and acceptance

The following are proposed budgets, to measure during implementation. Use server-side counts and paged neighborhood queries. Transfer summaries for visible nodes and load full text only on selection. Never send all bodies or construct a full per-frame simulation. Grouped counts, lists and independent memory nodes must work even if there are zero recorded relationships.

1. Use a reproducible synthetic project of 10,000 active memories with revisions, isolated memories, and a file cited by at least 1,000 current memories. Add a forgotten memory and a different scope with overlapping terms and paths. Document the test laptop, browser, cache state, corpus size and run count.
2. From a fresh launch, target a usable counted overview within 2 seconds on the recorded development laptop. Across at least 100 warm interactions on distinct targets, target p95 under 300 ms from selection to visible first neighborhood and inspector summary. Report spread and archive raw samples. Load very long bodies and history separately. Report results before making a performance claim.
3. Verify a user can find a memory outside the first page, open a dense file hub, see omitted counts, select the last result, and go Back without losing their place. Confirm the client stays within both graph budgets before and after repeated expansion. A high-degree relation type must not hide a sparse one. Verify the same journey by keyboard and at a narrow viewport.
4. Check historical vs current revisions (labelled, with their own references), repeated inspector visits, store changes during pagination, forgotten records, and scope isolation. A fixture must never gain an agent-activity edge by inference, and the viewer must show none.

## Approaches and decisions

The user chose a local explorer over a portable snapshot. The live local store answers what is retrievable now. A snapshot could preserve a handoff for sharing, but it would age between exports. The approved default is a counted project map with bounded local exploration. A global force graph that draws every memory could show overall density, at the cost of making individual neighbors and labels harder to inspect.

Receipt-based activity evidence was dropped from version one (see top). Leave the renderer, layout algorithm and read-query implementation to the builder within the read-only and performance constraints. Index changes are not allowed in version one; if a hub query cannot meet its budget on the existing indexes, measure it and report, and propose a separate migration. No memory editing, semantic linking, graph database, or shared deployment in this version.
