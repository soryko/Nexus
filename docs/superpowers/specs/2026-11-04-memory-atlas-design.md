# Nexus Memory Atlas

Approved design · 4 November 2026

## What Sory should be able to do

Open one enrolled project locally, find a memory among thousands, read what it says, and follow evidence of how it was stored or retrieved. When a file is shared by hundreds of decisions, the user should be able to select the interesting ones without moving hundreds of dots out of the way.

## Project map and local graph

- **Project map.** Start with counted groups by current memory kind. Each active memory belongs to exactly one group, including memories without links. Size groups by count with a capped visual range and print the exact count. Selecting a group opens its searchable, paginated list. Groups are navigation controls. Draw no relation between groups based on membership or proximity.
- **Find.** Search current revision content with Nexus lexical search, or open a memory by its exact ID. Offer kind, tag, and verified repository/path filters. Combine filters consistently with Nexus search. The search box and result count cover the entire active filtered project, including memories outside the visible graph. Empty queries browse by recency. Indicate active filters and offer a single reset.
- **Focus.** Opening a result centers its current revision and inspector. The default graph shows direct recorded connections. Select a file to explore its citing memories, or an interface to explore its observed events in the project. The visible node and edge budgets apply across all expansions. Start with 25 neighbors and hard limits of 100 graph nodes and 150 edges. Label omitted results, for example “25 of 840 citing memories”. Show the rest in a paged, keyboard-accessible list with search. Choosing an item can recenter the graph even when the budget is full. Order neighbor lists by newest supported event or revision timestamp within the relation type, unknown last, then stable ID. Keep relation types separately accessible so a high-degree type cannot hide another type.
- **Orientation.** Support pan, zoom, fit, and back to the last selection. Show names on focus or hover, then reveal more at close zoom when they fit. Keep existing node positions stable on expansion. Zoom changes presentation only. Opening a group or expanding a relationship is deliberate. Back restores search, filters, list position, camera, selection, and expansions while revalidating visibility. Provide keyboard controls and a text path to every graph action.

The navigation default is one focus at a time. Choosing a citing memory recenters on it. Back restores the file list at the same page. A file or interface opens with all its in-scope neighbors and its own clearly labeled filter, initially empty. Previous project-search filters remain in the project view when the user returns. Show “12 matching of 840 citing memories” when the hub filter narrows the list.

The overview/local split draws on [Obsidian Graph view](https://obsidian.md/help/plugins/graph). Nexus edges need a record explaining their meaning. Screen position and an overlapping word are insufficient.

## Inspect and follow evidence

| Selection | Evidence and behavior |
|---|---|
| Memory | Show stored text, kind, tags, source if present, and IDs. Use a bounded first-line excerpt as a display title. Date added comes from the initial revision. Show the current revision date separately. |
| Revision | Expand the parent chain on demand. Show the selected revision and a text comparison with its parent. When displaying an older revision, mark it as historical and display its own references and observed actions. |
| File | Use the recorded repository identity, path and commit as the node key. Expand to current revisions that cite that exact triple. Opening older history can show historical citations, explicitly labeled. |
| Interface | Draw “written by” or “fetched by” only for an attributable event joined to this project and exact revision. Selecting an edge shows the event, source and available time precision. A shared actor, project registration, or search hit cannot establish a fetch. |
| Last observed fetch | For a memory, use the latest evidenced agent fetch across its visible revisions and identify which revision was fetched. For a revision, restrict to that revision. Missing evidence displays **Unknown**. Opening or refreshing Atlas never records an agent fetch. |
| Find related memories | Open a separate list of current memories that share a selected tag or match a user-selected term from the content. Explain the overlap, exclude the selected memory, and preserve normal search ordering. Keep these matches out of the recorded graph. |

## Data and operating boundary

Ship a dedicated local browser viewer launched for one enrolled checkout. Resolve the trusted project scope at launch using the project-scope integration. Pin the store, namespace and actor for the session. Fail closed if that binding is unavailable, ambiguous, retired, or changes. Bind the viewer to loopback with a per-launch access secret, validate host/origin, serve assets locally and render stored text as text. No write tools, parallel corpus, external services, or cross-project aggregation.

Use SQLite read-only access against a supported existing store. Preserve Nexus search, history, visibility and cursor semantics without invoking startup migrations, enrollment, reindexing or other write paths. Scope counts, reverse citations, groups and lists identically. Exclude forgotten records and their derived counts and edges. Default to a consistent read per request. Revalidate on navigation and explicit refresh. If results changed during paging, restart the affected page and tell the user. Preserve selected IDs only while visible.

Version one works with the memory store alone. Core schema v4 has revision creation times and Git evidence but no general per-client fetch log. An optional, explicitly supplied owner-local evidence source can enrich it. Initial supported source is the native Codex–Claude handoff receipt format already in this checkout. Validate its scope against the bound project and match its memory/revision IDs to visible records before displaying a handoff. Use receipt-day precision and label evidence as receipt-reported. Incomplete or mismatched records contribute no edges or dates. Do not ingest arbitrary project JSON, assume production clients have tracing, or silently attach test fixtures to real projects. Ongoing instrumentation across hosts is separate work.

## Scale and acceptance

The following are proposed budgets, to measure during implementation. Use server-side counts and paged neighborhood queries. Transfer summaries for visible nodes and load full text only on selection. Never send all bodies or construct a full per-frame simulation. Grouped counts, lists and independent memory nodes must work even if there are zero recorded relationships.

1. Use a reproducible synthetic project of 10,000 active memories with revisions, isolated memories, and a file cited by at least 1,000 current memories. Add a forgotten memory and a different scope with overlapping terms and paths. Document the test laptop, browser, cache state, corpus size and run count.
2. From a fresh launch, target a usable counted overview within 2 seconds on the recorded development laptop. Across at least 30 warm interactions, target p95 under 300 ms from selection to visible first neighborhood and inspector summary. Load very long bodies and history separately. Report results before making a performance claim.
3. Verify a user can find a memory outside the first page, open a dense file hub, see omitted counts, select the last result, and go Back without losing their place. Confirm the client stays within both graph budgets before and after repeated expansion. Verify the same journey by keyboard and at a narrow viewport.
4. Check unknown fetch dates, day-only evidence, historical vs current revisions, repeated inspector visits, store changes during pagination, forgotten records, and scope isolation. A fixture lacking evidence must never acquire an interface edge by inference.

## Approaches and decisions

The user chose a local explorer over a portable snapshot. The live local store answers what is retrievable now. A snapshot could preserve a handoff for sharing, but it would age between exports. The approved default is a counted project map with bounded local exploration. A global force graph that draws every memory could show overall density, at the cost of making individual neighbors and labels harder to inspect.

Leave the renderer, layout algorithm, read-query implementation and indexes to the builder within the read-only and performance constraints. No memory editing, semantic linking, graph database, or shared deployment in this version.
