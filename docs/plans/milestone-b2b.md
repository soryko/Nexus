# B2b — reference filters on `search`

**Status: draft for review. Frozen for implementation on approval.** This document fixes the semantics of the second B2 slice *before* its tests are written, so the acceptance tests check a contract rather than describe an implementation. Changes after approval are recorded as dated amendments below, never as edits in place.

B2b adds **filters over recorded reference evidence** to `search`. It adds no new evidence, no new verification, and no new isolation. Ordering in B2: [B2a](milestone-b2a.md) is repository identity plus commit/path verification and is closed at `79e0a0d`; symbol-aware retrieval comes after this slice and B2b accepts no symbols.

Every claim is marked **measured**, **assumed** or **decided**. Behaviour attributed to the current implementation was read at `2a88052`.

## 1. What a reference filter is, and what it is not

**Decided.** A reference filter narrows a result set *within* a `Scope`. It is a predicate over rows already in `revision_references`, written there by a verified B2a write.

- It never re-verifies. Evidence is immutable and is what the filter reads; nothing is re-checked against the working tree, the index, or the repository, on any read path.
- **`search` never spawns `git`.** B2a's compatibility contract makes verification the only thing that shells out, so a filtered search must work identically with `git` missing, broken, or unbound. This is the reason several decisions below go the way they do, and it is not negotiable within this slice.
- It cannot widen what a scope can see. `Scope` remains the isolation boundary — B2a corrected an over-promise here and that correction stands. A filter that returned a memory the same scope could not otherwise reach would be a defect, not a feature.
- It does not change ranking. Filters decide eligibility; they do not contribute to `rank`.

## 2. The conflict with B2a, stated and resolved

B2a §1 says, of the binding:

> **No tool call can select, override or create a repository binding.** The MCP schemas carry no repository, commit-scope or path-root argument, and injected fields are rejected the way injected `namespace` already is.

Read literally, "no commit-scope or path-root argument" forbids this entire slice. **Decided, and this needs the reviewer's explicit confirmation before the document is frozen:** that sentence governs *what a request may point verification at*, not *what a request may ask about evidence already recorded*. The distinction is load-bearing and is what every rule in §3 preserves:

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

**Decided.** `repository: "bound"` **while the process is unbound is `repository_unbound`, not an empty page.** An empty result is indistinguishable from "nothing matched", and a caller that cannot tell those apart will conclude the memories are gone. Launching without `--repo`, or with an unusable `git`, are both supported modes, and in both of them this argument has no referent.

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

**Decided.** When a repository is bound, a value whose width does not match its `object_format` is `invalid_reference`, mirroring B2a's OID-width check on the write path. When unbound, either supported width is accepted, because there is no format to check against.

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

## 7. What a hit carries

**Decided.** `SearchHit.references` continues to carry the **complete** reference set of the returned revision, not the subset that matched.

Returning only matching references was rejected: evidence is a property of the revision, and a hit that shows different evidence depending on the query makes the same memory look like two different memories, which is the failure mode B2a avoided by keeping stored evidence immutable and complete. A caller that wants to know *why* a hit matched can compare against its own filter; a caller that wants the memory's evidence must not have it silently truncated.

**Decided.** `match_reasons` gains one value, `references`, when any filter in §3 is supplied — consistent with `tags_all`, `tags_any` and `kind` already appearing there.

## 8. Errors

| Condition | Error |
| --- | --- |
| Path fails `validate_reference_path` | `invalid_reference` |
| Commit is not 40 or 64 lowercase hex | `invalid_reference` |
| Commit width disagrees with the bound `object_format` | `invalid_reference` |
| More than 32 values in `reference_paths` or `reference_commits` | `invalid_reference` |
| `repository: "bound"` with no repository bound | `repository_unbound` |
| Cursor presented with different filters | `cursor_expired` |

**Decided.** No new error code is introduced. Every condition above is an existing one used the way B2a uses it.

## 9. Storage, and one obligation this slice must discharge

**Measured, at `2a88052`.** `revision_references` has a primary key of `(namespace, actor, memory_id, revision_id, commit_oid, path)` and **no other index**. Its leading columns are the memory and revision, so a predicate on `path` or `commit_oid` alone has no usable index and scans.

**Decided.** B2b adds a migration (`005`) with indexes serving the §4 predicate, and the implementation must show `EXPLAIN QUERY PLAN` using them for each of: path-exact, path-prefix, commit-exact, and repository-restricted searches.

**Assumed, and deliberately not fixed here:** the exact index columns. Fixing DDL before measuring a query plan is how an index that is never used gets written down as a decision. The obligation is stated instead — **no filtered search performs a full scan of `revision_references`** — and the columns are chosen against a measured plan during implementation and recorded as an amendment.

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
12. **Search never spawns git.** Every filter behaves identically with `git` absent, with `git` present but exiting non-zero, and with the process unbound — except `repository: "bound"` when unbound, which is `repository_unbound`. Asserted with a `git` that fails the test if it is executed at all.
13. **Evidence is complete in a hit.** A filtered hit carries every reference of its revision, not only the matching ones.
14. **Rejections.** Each row of §8's table, asserted through the service and through the MCP schema.
15. **One real MCP round trip.** Record two referenced memories over subprocess stdio, filter for one, and assert no `repository_id`, verification target or pattern argument is accepted in any tool schema.
16. **Query plans.** Each of the four filter shapes uses an index on `revision_references`; asserted with `EXPLAIN QUERY PLAN`, so §9's obligation cannot be quietly dropped.

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

## 12. Explicitly out of scope

Symbols, in any form. Cross-revision and historical reference search. Re-verification or freshness checking on read. Resolving any revision spec. Remote repository access. Ranking changes of any kind, including boosting a hit because it matched a reference. Filtering `search_history` — it is a separate channel and would need its own contract.

## Amendments

None yet. This document has not been through review.
