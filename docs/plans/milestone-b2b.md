# B2b — reference filters on `search`

**Status: reviewed 2026-09-06 and frozen for implementation.** This document fixes the semantics of the second B2 slice *before* its tests are written, so the acceptance tests check a contract rather than describe an implementation. Changes after approval are recorded as dated amendments below, never as edits in place.

B2b adds **filters over recorded reference evidence** to `search`. It adds no new evidence, no new verification, and no new isolation. Ordering in B2: [B2a](milestone-b2a.md) is repository identity plus commit/path verification and is closed at `79e0a0d`; symbol-aware retrieval comes after this slice and B2b accepts no symbols.

Every claim is marked **measured**, **assumed** or **decided**. Behaviour attributed to the current implementation was read at `2a88052`, and re-read unchanged at `6e71d12` (`git diff 2a88052 HEAD -- src/` is empty).

## 1. What a reference filter is, and what it is not

**Decided.** A reference filter narrows a result set *within* a `Scope`. It is a predicate over rows already in `revision_references`, written there by a verified B2a write.

- It never re-verifies. Evidence is immutable and is what the filter reads; nothing is re-checked against the working tree, the index, or the repository, on any read path.
- **`search` never spawns `git`.** B2a's compatibility contract makes verification the only thing that shells out, so a filtered search must work identically with `git` missing, broken, or unbound. This is the reason several decisions below go the way they do, and it is not negotiable within this slice.
- It cannot widen what a scope can see. `Scope` remains the isolation boundary — B2a corrected an over-promise here and that correction stands. A filter that returned a memory the same scope could not otherwise reach would be a defect, not a feature.
- It does not change ranking. Filters decide eligibility; they do not contribute to `rank`.

## 2. The conflict with B2a, stated and resolved

B2a §1 says, of the binding:

> **No tool call can select, override or create a repository binding.** The MCP schemas carry no repository, commit-scope or path-root argument, and injected fields are rejected the way injected `namespace` already is.

Read literally, "no commit-scope or path-root argument" forbids this entire slice. **Decided, and confirmed by the reviewer on 2026-09-06 (amendment 1):** that sentence governs *what a request may point verification at*, not *what a request may ask about evidence already recorded*. The distinction is load-bearing and is what every rule in §3 preserves:

- A filter selects **nothing**. It cannot bind a repository, cannot change which repository a write is verified against, and cannot cause any repository to be read.
- A `commit` filter is **not** a commit-scope. It does not mean "resolve this and check against it" — that is a verification argument and remains forbidden. It means "this exact object id appears in stored evidence".
- A `path` filter is **not** a path-root. It does not scope, restrict or root anything; it matches recorded pathnames.
- The prohibition on injected fields is unchanged and still applies to `namespace`, `actor`, `repository_id` and every other identity field.

If the reviewer reads B2a's sentence as absolute, B2b as specified cannot proceed and the slice needs re-scoping rather than re-wording; that is the reason this is §2 and not a footnote.

## 3. The filter surface

**Decided.** Four optional arguments on `search`, all defaulting to "no restriction". They are ordinary query fields: they enter the request the way `tags_all` does, and they enter the cursor fingerprint the way `tags_all` does (§6).

| Argument | Type | Meaning |
| --- | --- | --- |
| `repository` | `"any"` \| `"bound"`, default `"any"` | `"bound"` restricts to evidence recorded under the `repository_id` bound at launch |
| `reference_paths` | up to 32 strings | Byte-exact match against a recorded pathname |
| `reference_path_prefix` | one string | Segment-aligned directory prefix of a recorded pathname |
| `reference_commits` | up to 32 strings | Byte-exact match against a recorded `commit_oid` |

### `repository` names no repository

**Decided.** The argument admits two constants and no identifier. A caller cannot write a `repository_id`, cannot enumerate them, and cannot learn one that is not already in a hit it was entitled to. `"bound"` means "whatever this process is bound to", which the caller cannot choose — exactly the property B2a's rule protects.

**Decided.** `repository: "bound"` **while the process is unbound is `repository_unbound`, not an empty page.** An empty result is indistinguishable from "nothing matched", and a caller that cannot tell those apart will conclude the memories are gone. Launching without `--repo` is a supported mode, and in it this argument has no referent.

**Decided: a binding without git is still a binding.** "Unbound" means *no binding*, not *no verification*. The two are separate and already separate in the code.

**Measured, at `2a88052`.** `--repo` with `git` absent still binds: `main()` passes `git=None` into `bind_repository`, which discovers an already-registered checkout token and returns a `RepositoryBinding` with no verifier ([`transport/mcp_server.py:431`](../../src/nexus_memory/transport/mcp_server.py), [`storage/sqlite.py:683`](../../src/nexus_memory/storage/sqlite.py)). `MemoryService` holds `binding` and `verifier` as independent fields, and `status` already reports a `repository_id` from the binding while reporting `verification: "unavailable"` ([`memory/service.py:258`](../../src/nexus_memory/memory/service.py)).

**Decided.** `repository: "bound"` is therefore answerable whenever a binding exists, whether or not `git` is usable, and `repository_unbound` is raised **only** when there is no binding at all — launched without `--repo`, or launched with `--repo` against a checkout that could be neither identified by `git` nor found in `repository_checkouts`. Any other rule would make the read path's behaviour depend on `git`, which §1 forbids: the `repository_id` needed to answer this filter is already in the process and in every row it selects on. A caller that has recorded evidence, then lost `git`, must still be able to ask for its own repository's evidence back.

### `reference_paths` is byte-exact

**Decided.** Values are validated by `validate_reference_path`, the same function the write path uses — nonempty, valid UTF-8, at most 1024 bytes, no NUL, no leading or trailing `/`, no empty segment, no `.` or `..`. A value failing it is `invalid_reference`, with the same message discipline: it names nothing about the checkout.

Matching is **byte equality against the stored pathname**, which is stored as git reported it. No case folding, no Unicode normalisation, no separator translation. **Assumed:** a caller filtering by a path it recorded gets that path back, because both went through the same validator and the same byte-exact storage.

### `reference_path_prefix` matches whole segments only

**Decided.** `src/api` matches `src/api/handler.py` and matches the exact path `src/api`. It does **not** match `src/apiary.py`. The comparison is against `prefix + "/"`, plus the exact-equality case, never a bare string prefix.

**Decided.** No globs, no wildcards, no pathspec magic, no regular expressions. B2a strips pathspec magic before git sees a value precisely so caller text cannot become a pattern; introducing a pattern language on the read side would reintroduce that class of surprise on the other end, in a place where the blast radius is "wrong results" rather than "wrong evidence" — quieter, and therefore worse.

`reference_path_prefix` and `reference_paths` may both be supplied; see §4 for how they combine.

**Decided: 32 values, and it is deliberately not `MAX_REFERENCES`.** A revision carries at most 8 references, but a filter is a set of alternatives asked of the whole corpus, not the reference set of one revision — "any of these thirty paths" is an ordinary question where "thirty references on one memory" is not. The cap exists to bound the size of the generated predicate, and 8 would be a limit borrowed from an unrelated invariant.

### `reference_commits` are stored object ids, not revision specs

**Decided.** A value is 40 or 64 lowercase hex characters. `HEAD`, `main`, `v1.2`, `abc123`, `HEAD~3` and every other spec is `invalid_reference`.

The reason is §1: resolving a spec requires git, on a read path that must work without it. Accepting specs would make `search` succeed or fail depending on whether a repository is reachable, and would make the same query mean different things at different times — a filter that silently tracks a moving ref is a filter whose results cannot be reproduced.

**Decided: the width check applies under `repository: "bound"` and nowhere else.** The shape check — 40 or 64 lowercase hex — always applies. The *format* check, which rejects a width disagreeing with a repository's `object_format`, applies only when `repository: "bound"` is supplied, and then against the bound repository's format, read from the `RepositoryBinding` and so available without `git`.

**Decided: under `repository: "any"`, both widths are accepted, including mixed within one list.** This is the default, and under it the filter reads evidence that may span several repositories: `revision_references` carries a `repository_id` per row, a scope can accumulate rows from more than one repository over its life, and those repositories may differ in `object_format`. Checking a caller's 64-hex value against the currently bound sha1 repository would make sha256 evidence in the same scope permanently unreachable through the default filter — a filter that hides rows because of what the *process* is bound to, not because of what the *evidence* says. `reference_commits=["<40 hex>", "<64 hex>"]` is a legal request under `"any"`, and matches rows of either format.

**Decided.** Under `repository: "bound"`, a list mixing widths is `invalid_reference` as a whole; the request is rejected rather than silently reduced to the values that happen to fit. Silently dropping a value would return a page that is correct for a query the caller did not send.

### Empty is absent, in every respect

**Decided.** "Defaulting to no restriction" is made exact here, because absent and empty reach the server as different JSON and must not reach the predicate as different queries.

| Supplied | Meaning |
| --- | --- |
| argument absent | no restriction |
| `repository: "any"` | no restriction — identical to absent |
| `reference_paths: []` | no restriction — identical to absent |
| `reference_commits: []` | no restriction — identical to absent |
| `reference_path_prefix: null` | no restriction — identical to absent |
| `reference_path_prefix: ""` | `invalid_reference` — `validate_reference_path` requires nonempty |

**Decided.** *Identical to absent* is the whole claim, not just "returns the same rows": same result set, same `match_reasons` (no `references`; see §7), and **the same cursor fingerprint**, so a cursor minted with `reference_paths: []` is accepted when the argument is absent and vice versa. A definition that agreed on rows but disagreed on the fingerprint would turn a client's choice of JSON encoding into a `cursor_expired`.

**Measured, at `2a88052`.** This is what the existing filters already do. `_normalized_tags` maps an empty input to `()`; `_fingerprint` serialises it as `[]` for absent and empty alike; `_reasons` tests truthiness, so an empty `tags_all` contributes no reason. Reference filters follow that behaviour rather than inventing a second convention next to it.

**Rejected: `[]` means "match nothing".** It is defensible — a caller that computed a filter list and got zero entries arguably wants zero results — but it makes an empty page the *correct* answer for an argument many MCP clients cannot reliably distinguish from omission, and it would contradict the shipped meaning of `tags_all: []` in the same call. A caller that wants "match nothing" can ask for a path that cannot exist; a caller surprised by "match nothing" gets no signal at all.

**Decided.** `repository` admits exactly `"any"` and `"bound"`. Any other value, including `null` where the client means absent, is `invalid_reference`; absence of the key is the way to say "no restriction".

## 4. Matching semantics

This section exists because "a memory referencing `src/api/handler.py` at commit `abc…`" has two readings, and the difference is invisible until it is wrong.

**Decided: all conditions must be satisfied by a single reference row.** A memory matches when it has **at least one** reference satisfying *every* supplied condition at once.

- A memory referencing `a.py` at commit `X` and `b.py` at commit `Y` **does not match** `reference_paths=["a.py"], reference_commits=["Y"]`.
- The alternative — conditions satisfied independently across different references — was rejected. It answers "does this memory mention both of these somewhere", which is not what the filter reads like and produces matches a caller cannot explain from the hit it gets back.

**Decided: within one argument, values are alternatives.** `reference_paths=["a.py","b.py"]` matches a reference to either. Across arguments, conditions conjoin. `reference_paths` and `reference_path_prefix` supplied together are satisfied by a reference matching **either** the exact set or the prefix, because both are the same question about the same column; that is the one deliberate exception to conjunction across arguments, and it is called out here because it is a surprise otherwise.

**Decided: existence, not join.** A memory matching on several references appears **once**, with one rank and one position. The predicate is an existence test, not a join producing a row per matching reference. A join would fan out results, corrupt the `(rank, seq)` keyset in §5, and make a page's size depend on how many references a memory happens to carry.

**Decided: any reference filter implies reference-carrying.** A memory with no references cannot match any filter in §3. There is no "matches because it asserted nothing".

**Decided: head revisions only.** `search` returns head revisions, and the filter applies to the head revision's references. A memory whose *earlier* revision referenced `a.py` and whose current revision does not **does not match**. Cross-revision search is out of scope for B2b as it was for B2a; `history` and `get` with a `revision_id` reach earlier evidence, and `search_history` is a separate channel that this slice does not filter.

## 5. Filters, ordering and pagination

**Decided, and it is the property most worth testing.** Every filter is an *eligibility* condition, decided before any limit is applied.

- A request for `limit=20` that has 20 eligible matches returns 20 hits. It never returns fewer because some of the first 20 candidates were filtered out afterwards.
- The current implementation already applies `kinds`, `tags_all` and `tags_any` inside the inner query with the limit outside it, and comments that "every eligibility filter is applied before the limit, never after". Reference filters join that set. Nothing about this contract is new architecture; it is a promise that the architecture is not quietly abandoned for the harder predicate.
- Ordering is unchanged: `ORDER BY rank, seq`, with `rank` the BM25 score when there is query text and `-durable_seq` otherwise. Filters do not reorder, reweight or promote.
- The `more` determination stays `limit + 1` **over the filtered set**.

## 6. Cursors

**Decided, and this is the correctness point that matters most in the slice.** Every argument in §3 enters the cursor fingerprint.

The fingerprint currently covers `namespace`, `actor`, `query`, `advanced`, `tags_all`, `tags_any` and `kinds`. A cursor carries `(fingerprint, generation, rank, seq)` and is refused unless both the fingerprint and the generation match. If the reference filters are omitted from it, a cursor minted under one filter set is accepted under another, and the second page is drawn from a different result set than the first — silently, with no error, and with results that look plausible. The keyset makes this worse rather than better: `(rank, seq)` is meaningful in both result sets, so the page returned is well-formed and wrong.

- A cursor presented with any different filter value is `cursor_expired`.
- **Decided:** cursors minted before this slice do not survive the upgrade, because the fingerprint's input changes. This costs nothing that is not already spent: `generation` is bumped by every write, so a cursor already does not survive concurrent activity, and the documented contract already tells callers to restart a search on `cursor_expired`.
- Filters do not change the cursor's shape or its keyset. There is no filter state carried in the cursor beyond the fingerprint.

### `"bound"` enters the fingerprint resolved, not literal

**Decided, and it is the one place where fingerprinting the argument as written is not enough.** For `repository`, the fingerprint covers the **resolved `repository_id`** the cursor was minted under, not the string `"bound"`.

**Measured, at `2a88052`.** A cursor is `base64(json)` over `(fingerprint, generation, rank, seq)` and carries no process identity; `_fingerprint` covers `namespace` and `actor` but nothing about the binding. Nothing stops a cursor from being presented to a different process — that is the ordinary case for a client that restarts, and scope is already defended this way.

Two processes in the same `(namespace, actor)` scope bound to different repositories both send the literal `"bound"`. Fingerprinted literally, the cursor from the first is accepted by the second, and page two is drawn from a different repository's evidence than page one — the §6 failure exactly, with `(rank, seq)` still meaningful and the page still well-formed.

- The fingerprint payload gains the resolved identity **only when `repository: "bound"` is supplied**. Under `"any"` — and under absence, which §3 makes identical to it — the result set does not depend on the binding, so neither does the fingerprint, and the two continue to fingerprint alike.
- The identity is hashed, never carried in the clear: the cursor holds the SHA-256 digest, not the payload. §3's property that a caller cannot read, enumerate or learn a `repository_id` is preserved, and a caller cannot compare two cursors' digests to test whether two processes share a repository without also holding every other fingerprinted field.
- **Decided: argument validation precedes cursor validation.** `repository: "bound"` presented to a process with no binding is `repository_unbound`, with or without a cursor — never `cursor_expired`. The caller's problem is the binding, and the error that names it is the useful one.

## 7. What a hit carries

**Decided.** `SearchHit.references` continues to carry the **complete** reference set of the returned revision, not the subset that matched.

Returning only matching references was rejected: evidence is a property of the revision, and a hit that shows different evidence depending on the query makes the same memory look like two different memories, which is the failure mode B2a avoided by keeping stored evidence immutable and complete. A caller that wants to know *why* a hit matched can compare against its own filter; a caller that wants the memory's evidence must not have it silently truncated.

**Decided.** `match_reasons` gains one value, `references`, when any filter in §3 **restricts** — a nonempty path list, a nonempty commit list, a prefix, or `repository: "bound"`. Arguments that §3 defines as identical to absent contribute no reason, matching `_reasons`' existing truthiness test for `tags_all`, `tags_any` and `kinds`.

## 8. Errors

| Condition | Error |
| --- | --- |
| Path fails `validate_reference_path`, including `reference_path_prefix: ""` | `invalid_reference` |
| Commit is not 40 or 64 lowercase hex | `invalid_reference` |
| Commit width disagrees with the bound `object_format`, **under `repository: "bound"` only** | `invalid_reference` |
| `repository` is any value other than `"any"` or `"bound"` | `invalid_reference` |
| More than 32 values in `reference_paths` or `reference_commits` | `invalid_reference` |
| `repository: "bound"` with **no binding** — not merely with verification unavailable | `repository_unbound` |
| Cursor presented with different filters, or minted under a different resolved `repository_id` | `cursor_expired` |

**Decided.** Where both apply, `repository_unbound` precedes `cursor_expired` (§6).

**Decided.** No new error code is introduced. Every condition above is an existing one used the way B2a uses it.

## 9. Storage, and one obligation this slice must discharge

**Measured, at `2a88052`.** `revision_references` has a primary key of `(namespace, actor, memory_id, revision_id, commit_oid, path)` and **no other index**. Its leading columns are the memory and revision, so a predicate on `path` or `commit_oid` alone has no usable index and scans.

**Measured, at `2a88052`.** That primary key is SQLite's only index on the table: `002_search.sql` creates `head_index_recent` and `head_tags_tag`, `004_repositories.sql` creates no explicit index at all, and the autoindex behind the primary key leads with `(namespace, actor, …)`. So a scope-qualified existence test correlated to an already-selected head revision has index columns available to it; a standalone predicate on `path` or `commit_oid` alone does not.

**Decided: the obligation is the invariant, not the migration.** The invariant is **no filtered search performs a full scan of `revision_references`**. A new index is a way of discharging it, not the obligation itself, and this document does not commit to one.

**Decided: measure the existing-index approach before adding storage.** The implementation first writes the §4 predicate against the schema as it stands and records `EXPLAIN QUERY PLAN` for each of the four filter shapes — path-exact, path-prefix, commit-exact, and repository-restricted. Migration `005` is written **only for the shapes the measurement shows the existing indexes cannot serve**, with its columns chosen against that plan. If the measurement clears all four, `005` is not written, and the plans are recorded as an amendment instead. Fixing DDL before measuring a query plan is how an index that is never used gets written down as a decision; this project has already declined a 2.70x storage cost on measured grounds, and paying a smaller one on unmeasured grounds would be the same error in the other direction.

**Decided: the measurement carries a negative control.** A query plan read off a table of a few dozen rows proves nothing — SQLite will scan a small table whatever indexes exist, so a "scan" verdict there is uninformative and an "index" verdict is luck. The plans are taken on a corpus large enough that the planner's choice is meaningful, and the same plans are taken with the candidate index absent, so the comparison shows the index changed the plan rather than that the plan was already fine. A candidate index whose presence does not change any of the four plans is not added.

## 10. Acceptance tests

Temporary repositories, real `git`, one real MCP round trip, as in B2a.

1. **Each matcher alone.** Exact path, segment-aligned prefix, exact commit, and `repository: "bound"` each return exactly the memories whose head revision carries a matching reference, and no others.
2. **The prefix boundary.** `src/api` matches `src/api/handler.py` and `src/api`; it does not match `src/apiary.py`. Asserted in both directions, because a bare string prefix passes the first half.
3. **One reference satisfies everything.** A memory referencing `a.py` at `X` and `b.py` at `Y` is not returned for `paths=["a.py"], commits=["Y"]`, and is returned for `paths=["a.py"], commits=["X"]`.
4. **Multiplicity does not duplicate.** A memory with three references matching the filter appears once, at one position.
5. **Filters precede the limit.** With 30 eligible memories and 30 ineligible ones interleaved by rank, `limit=20` returns 20 eligible hits — not 20 candidates of which some are eligible.
6. **Pagination is exhaustive and non-overlapping.** Paging a filtered search to exhaustion returns every eligible memory exactly once, in `(rank, seq)` order, with no duplicate and no gap.
7. **Cursors are bound to filters.** A cursor minted with one filter set and presented with another is `cursor_expired`, for each argument in §3 independently, including the change from absent to present.
8. **Cursors are still bound to writes.** A write between pages invalidates a filtered cursor exactly as it does an unfiltered one.
9. **Reference-free memories never match**, and are still returned by the same search with the filters removed.
10. **Head revisions only.** A memory revised to drop a reference is not returned by a filter on that reference, while `history` and `get` at the earlier revision still show it.
11. **Scope is still the boundary.** A filter never returns a memory from another `namespace` or `actor`, including when both scopes carry identical references under the same `repository_id`.
12. **Search never spawns git.** Every filter behaves identically with `git` absent, with `git` present but exiting non-zero, and with the process unbound — except `repository: "bound"` with no binding, which is `repository_unbound`. Asserted with a `git` that fails the test if it is executed at all.
13. **Evidence is complete in a hit.** A filtered hit carries every reference of its revision, not only the matching ones.
14. **Rejections.** Each row of §8's table, asserted through the service and through the MCP schema.
15. **One real MCP round trip.** Record two referenced memories over subprocess stdio, filter for one, and assert no `repository_id`, verification target or pattern argument is accepted in any tool schema.
16. **Query plans.** No filter shape full-scans `revision_references`; asserted with `EXPLAIN QUERY PLAN` on a corpus large enough for the plan to be meaningful, whichever index serves it. §9's obligation cannot be quietly dropped, and the test does not presuppose that a new index exists.
17. **A binding without git still answers `"bound"`.** A process bound to a registered checkout with `git` unavailable returns that repository's evidence for `repository: "bound"`, does not raise `repository_unbound`, and reports `verification: "unavailable"` from `status` in the same run.
18. **Cursors are bound to the resolved repository.** A cursor minted with `repository: "bound"` in a process bound to one repository is `cursor_expired` when presented to a process in the same scope bound to another; the same cursor under `repository: "any"` is unaffected by the binding. With no binding at all, `repository: "bound"` plus that cursor is `repository_unbound`, not `cursor_expired`.
19. **Mixed commit formats.** Under `repository: "any"`, a `reference_commits` list holding a 40-hex and a 64-hex value is accepted and matches evidence of either format recorded in the scope. Under `repository: "bound"`, a value of the width the bound repository does not use is `invalid_reference`, and a mixed list is rejected whole rather than reduced.
20. **Empty is absent.** For each argument, the empty form of §3 returns the same hits, the same `match_reasons` and the same cursor as omitting it — asserted by minting a cursor with the empty form and spending it with the argument absent — while `reference_path_prefix: ""` and an unrecognised `repository` value are `invalid_reference`.

## 11. Negative controls

As in B2a, each mutation must fail at least its designated test. A mutation that fails nothing means the invariant is unguarded, and is investigated for equivalence and execution coverage before that conclusion is drawn.

| Mutation | Must fail |
| --- | --- |
| Apply reference filters after the limit instead of before | 5 |
| Omit reference filters from the cursor fingerprint | 7 |
| Match the path prefix as a bare string prefix, without the segment boundary | 2 |
| Satisfy conditions across different references instead of one | 3 |
| Join `revision_references` instead of testing existence | 4, 6 |
| Resolve a commit value through `git` instead of matching stored bytes | 12, 14 |
| Return only the matching references on a hit | 13 |
| Answer `repository: "bound"` with an empty page when unbound | 12 |
| Apply the filter to any revision rather than the head revision | 10 |
| Answer `repository: "bound"` with `repository_unbound` when a binding exists but verification does not | 17 |
| Fingerprint the literal `"bound"` instead of the resolved `repository_id` | 18 |
| Check commit width against the bound `object_format` under `repository: "any"` | 19 |
| Drop the non-matching widths from a mixed list instead of rejecting the request | 19 |
| Treat an empty list as an unsatisfiable predicate instead of as absent | 20 |
| Fingerprint an empty list differently from an absent argument | 20 |
| Add `references` to `match_reasons` for a non-restricting argument | 20 |
| Check the cursor before the `repository` argument | 18 |

## 12. Explicitly out of scope

Symbols, in any form. Cross-revision and historical reference search. Re-verification or freshness checking on read. Resolving any revision spec. Remote repository access. Ranking changes of any kind, including boosting a hit because it matched a reference. Filtering `search_history` — it is a separate channel and would need its own contract.

## Amendments

### 1 — 2026-09-06, review corrections; document frozen

Reviewed against the implementation at `2a88052` (unchanged at `6e71d12`). §2's reading of B2a §1 is **confirmed**: the prohibition governs what a request may point verification at, not what a request may ask about evidence already recorded. The slice proceeds as scoped. Five corrections were applied in place, before the freeze:

1. **Cursors carry the resolved repository identity (§6).** The fingerprint covers the `repository_id` a `"bound"` cursor was minted under, not the literal string. Measured cause: cursors carry no process identity and `_fingerprint` covered no binding field, so two processes in one scope bound to different repositories would have accepted each other's `"bound"` cursors and paged across different evidence. The identity is hashed, never exposed. Argument validation was ordered ahead of cursor validation so an unbound process reports `repository_unbound` rather than `cursor_expired`.
2. **Mixed commit formats are permitted under `"any"` (§3, §8).** The width check is scoped to `repository: "bound"`. Measured cause: `revision_references` carries a `repository_id` per row and one scope can hold evidence from repositories of different `object_format`, so checking every value against the *bound* repository's format made sha256 evidence unreachable through the default filter. Under `"bound"` a mixed list is rejected whole, never silently reduced.
3. **A binding without git is still a binding (§3, §8, test 12).** `repository_unbound` now depends on the binding alone. Measured cause: `main()` binds with `git=None` from an already-registered checkout token, and `MemoryService` holds `binding` and `verifier` independently — so the draft's "an unusable `git` means no referent" contradicted both the code and §1's rule that the read path never depends on `git`.
4. **New indexes are conditional on measurement (§9, test 16).** The unconditional migration `005` is withdrawn. The obligation is the invariant — no filtered search full-scans `revision_references` — discharged first by measuring the existing indexes, with `005` written only for the shapes they cannot serve, and a negative control (plans taken with the candidate index absent, on a corpus large enough for the planner's choice to be meaningful) so an index that changes no plan is not added.
5. **Empty and default filters are defined once (§3, §7).** Absent, `"any"`, `[]` and `null` are identical in result set, `match_reasons` and cursor fingerprint; `reference_path_prefix: ""` and an unrecognised `repository` value are `invalid_reference`. This matches the shipped behaviour of `tags_all` rather than introducing a second convention beside it. `[]` as "match nothing" was considered and rejected.

Acceptance tests 17–20 and nine mutation controls were added to cover the five. Later changes are recorded as further amendments, never as edits in place.
