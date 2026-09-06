# B2a — repository identity, reference verification and compatibility contract

**Status: frozen for implementation.** This document fixes the semantics of the first B2 slice *before* its tests are written, so the acceptance tests check a contract rather than describe an implementation. Changes after approval are recorded as dated amendments below, never as edits in place.

Ordering inside B2: **B2a** is repository identity plus commit/path verification. **B2b** is reference filters on `search`. Symbol-aware retrieval comes after both, and B2a neither accepts nor stores symbols — see [Explicitly out of scope](#explicitly-out-of-scope).

Git behaviour recorded here was measured on **git 2.54.0, darwin 25.1.0, 2026-09-06**, with the commands in [Measured git behaviour](#measured-git-behaviour). Every claim is marked measured, assumed or decided.

## 1. Repository identity

**Decided.** A repository is bound at launch, exactly like `Scope`, and is not addressable from a tool call.

- Launch takes an optional `--repo PATH` naming a local checkout. One repository per server process.
- The binding has an explicit, persisted **`repository_id`**: an opaque identifier minted once and stored in the database under the launch scope. It is the identity that references record. Neither a path, nor a namespace, nor a remote URL, nor a commit is that identity.
- The **association key** is the canonicalised absolute Git common directory (`git rev-parse --path-format=absolute --git-common-dir`, then `realpath`). A checkout whose key matches an existing row binds to that `repository_id`; an unknown key mints a new one.
- **Measured:** the main worktree and a linked worktree agree on the common directory and disagree on both `--show-toplevel` and `--absolute-git-dir`. **Measured, and new:** raw `--git-common-dir` returns `.git` from the main worktree and an absolute path from the linked one, so comparing its raw output equates nothing. `--path-format=absolute` is mandatory, and `realpath` on top of it, because a canonical string is the whole mechanism.
- **Independent clones never merge automatically.** A matching remote URL, a shared commit OID, an identical tree, or a similar path is not evidence of the same project. Two clones of the same upstream are two repositories until an operator says otherwise, and the association is an explicit local action, never an inference.
- **No tool call can select, override or create a repository binding.** The MCP schemas carry no repository, commit-scope or path-root argument, and injected fields are rejected the way injected `namespace` already is.
- Launch without `--repo`, or with `git` unavailable, is a supported mode: verification is **unavailable**, and `status` says so. Nothing is labelled verified in that mode.

**Decided, with the alternative recorded.** A structured reference is stored **only** with verification evidence. When verification is unavailable the write is refused with `verification_unavailable` rather than stored unchecked. The alternative — store it with an `unverified` evidence value — was rejected because it produces two kinds of stored reference that a later reader must remember to distinguish, which is the failure mode milestone A avoided by keeping `snapshot` plainly caller-asserted. A caller who wants unchecked provenance already has `source_uri` and `snapshot`.

## 2. Reference values

**Decided.** A reference is part of a revision, and a revision replaces all of its fields:

| Field | Meaning | Rule |
| --- | --- | --- |
| `path` | Repository-relative path | Required. Validated in the application before git is invoked |
| `commit` | Commit spec: a 40-hex OID or a ref name | Optional. Absent means the bound repository's `HEAD` |

- At most **8** references per revision. Normalised by `(commit, path)`, deduplicated, sorted. `revise` replaces the whole set; omitting it resets to empty, exactly like `tags`.
- **All or none.** If any reference in a request fails to verify, the whole write is refused. A revision never carries a partially checked set.
- Path validation is the application's job, not git's: non-empty, valid UTF-8, at most 1,024 bytes, no NUL, no leading or trailing `/`, no empty segment, no `.` or `..` segment, never absolute. **Measured:** `git ls-tree -- ../etc/passwd` fails with `'../etc/passwd' is outside repository at '<absolute on-disk path>'`, and `git cat-file -e HEAD:/etc/passwd` reports *"path exists on disk, but not in 'HEAD'"*. Git's stderr discloses both the checkout location and on-disk existence, so **git stderr must never reach a caller** and unsafe paths must be refused before git sees them.

**Stored per verified reference:** `repository_id`, resolved `commit_oid` (40 hex), repository-relative `path`, `object_oid`, `entry_type`, and `checked_at`. Reference rows are immutable and per-revision, like revisions themselves. **No repository file content is copied into the database** — identifiers and evidence only.

## 3. Verification protocol

**Decided.** Resolution happens **once**, then every check runs against the resolved commit:

1. Resolve the commit spec with typed resolution: `git rev-parse --verify <spec>^{commit}`. **Measured:** this resolves an annotated tag to its commit and refuses a tree with *"expected commit type, but the object dereferences to tree type"*. Record the 40-hex OID.
2. For each path, `git ls-tree --full-tree -z <commit_oid> -- <path>`, reading mode, type, object OID and the exact path back.
3. Nothing re-reads the ref. A ref that moves between step 1 and step 3 cannot split one request across two commits.

Two mandatory flags, both for correctness:

- **`--full-tree` is mandatory. Measured:** without it, `ls-tree` resolves the pathspec relative to the process working directory. From inside `sub/`, `-- file.txt` matched `sub/file.txt` and `-- sub/file.txt` matched nothing. That is a silent wrong answer in both directions — a false verification and a false absence — and it depends on where the server happened to be launched.
- **`-z` is mandatory**, so paths arrive NUL-terminated and unquoted rather than through git's quoting rules.

**Measured, and the reason absence is not read from exit status:** `ls-tree` for a path that is not in the commit exits **0 with empty output**; only a rejected pathspec exits non-zero (128). Absence is empty output. An implementation that keys on the exit code reports every missing path as success.

**Measured:** the blob OID from `ls-tree` equals `git hash-object` of the file's bytes, so `object_oid` is a stable content identity a later freshness check can compare against.

Verification runs **before** the write transaction opens. No subprocess is ever spawned while holding the write lock, matching milestone A's rule that digests are computed outside it. A failing or missing git verifier therefore cannot take the write path down; it only refuses reference-carrying writes.

There is no verification cache in B2a. Each request verifies its own references.

## 4. What "verified" means

**Decided.** Evidence states exactly what was checked, at the moment it was checked:

| Evidence | Meaning |
| --- | --- |
| `commit_resolved` | That commit object existed in the bound repository at `checked_at` |
| `path_resolved` | That path existed in that commit's tree, with that object OID and entry type |
| `content_unverified` | Nothing has been established about whether the memory's prose is true |

And, as plainly, what it does **not** establish:

- **Not the working tree.** The checkout on disk is never consulted. A dirty, stale or absent working file changes no evidence, and evidence implies nothing about what is on disk now.
- **Not currency.** Unchanged bytes do not mean the advice still holds. **Measured:** a commit from another branch resolved with `rev-parse --verify` while `merge-base --is-ancestor` reported it was not in `HEAD`'s history — resolvable is not reachable, and neither is current.
- **Not continuing.** Evidence is an observation, not a property. A commit can later be garbage-collected or rewritten; that a re-check would now fail does not retract what was recorded. Ancestry is deliberately not recorded, because it is mutable and would therefore be evidence about the repository's present state rather than about the commit.
- **Not authority.** A verified reference does not make the memory more trustworthy, and stored content remains data, never instructions.

## 5. Unsupported reference types

**Decided.** B2a supports **regular tracked files only** — modes `100644` and `100755`. Everything else is reported explicitly as `unsupported_reference_type`, distinct from "not found", so a caller is never told a path is missing when it exists as a kind Nexus declines to verify. **Measured** entry shapes: `120000 blob` (symlink), `160000 commit` (gitlink/submodule), `040000 tree` (directory).

## 6. Compatibility obligations

These are the promises milestone A and B1 already made, and this slice must not weaken any of them.

- **`source_uri` and `snapshot` are unchanged.** Still caller assertions, never fetched, never verified. New evidence never retroactively upgrades an old record, and the two mechanisms never cross-populate in either direction.
- **The version-1 digest is byte-stable.** A request carrying no references keeps hashing through the frozen `MemoryService._digest`, producing the same value it produced before this migration. A request carrying references uses a version-2 digest under a new function and a new `DIGEST_VERSION`; `receipts.digest_version` already exists to record which.
- **The digest covers caller-supplied reference input, never resolved OIDs.** This is not a detail. If a resolved commit entered the digest, a retry of `{path, commit: "HEAD"}` after `HEAD` moved would hash differently and raise an idempotency conflict instead of replaying — breaking milestone A's core promise through nothing the caller did.
- **A retry never invokes git.** Before verification, a read checks for a receipt under the same `(scope, key)` with the same digest and returns it directly. The authoritative replay check remains inside the write transaction, unchanged; the pre-check can only short-circuit to an already-committed receipt, so G1 is untouched. Consequence, and the obligation stated in the plan: a retry succeeds and returns its original receipt after `HEAD` has moved, after the object has been garbage-collected, with the repository detached, or with `git` missing entirely.
- **Original evidence is recoverable from a retry.** The replayed receipt names the original `revision_id`, and evidence is stored on the revision, so `get` returns exactly the evidence recorded at the original write. Receipt shape is unchanged; evidence was deliberately not added to `WriteReceipt`, which would have duplicated it into the receipts table and changed a stable structured output.
- **A new reference under an old key still conflicts.** Same key, genuinely different payload, different digest, `idempotency_conflict` — as before.
- **Outbox history keeps its meaning.** Reference-carrying writes emit the same event kinds, carrying IDs and not content. Events written under milestone A are not redefined.
- **Migration `004` is additive and transactional**, creating tables only, with rollback leaving the database at version 3. It rewrites no existing row. Its cost is reported rather than assumed, even though an additive migration with no backfill is expected to be cheap — *expected* is not *measured*.
- **B2b's filters change the search cursor fingerprint.** Adding `path` and `commit` to the query changes `_fingerprint`, so a cursor minted before the change is rejected as `cursor_expired`. That is the documented behaviour of an invalidated cursor, not a broken promise, and it is stated here so it is not discovered as a bug.
- **`status` gains repository and verification-mode fields.** Existing fields keep their names and meanings.
- **New error codes are stable and sanitized**, extending `NexusError`: `repository_unbound`, `verification_unavailable`, `commit_not_found`, `path_not_in_commit`, `unsupported_reference_type`, `invalid_reference`. As before, public errors carry no SQL, no database or checkout paths, no content, no other scope's details and no stack traces.

## 7. Components and storage

Small, and behind the service:

| Piece | Responsibility |
| --- | --- |
| Immutable reference values | `ReferenceInput`, `VerifiedReference`, evidence — frozen dataclasses in `domain` |
| `RepositoryVerifier` protocol | Resolve a commit spec once; check paths against that commit |
| `GitCliVerifier` adapter | The only code that spawns `git` or reads its output |
| Storage | `repositories`, `repository_checkouts`, `revision_references` — identifiers and evidence, no file bytes |

The verifier is a **separate interface** from the repository protocol, because it depends on an external process and must fail independently of storage. `MemoryView` and `SearchHit` gain a `references` field carrying evidence. **Reference filters (B2b) apply before the pagination limit**, with every other eligibility filter — taking a page first and filtering afterwards silently hides valid results, which milestone B already recorded as a correctness requirement.

## 8. Acceptance tests

Frozen with this contract, over temporary Git repositories created by the tests:

1. **Repository isolation.** Two clones of the same source, identical trees and commit OIDs, bound separately: distinct `repository_id`s, no automatic association from a shared remote URL or shared commits, and a reference recorded under one is not visible to a service bound to the other.
2. **Worktree association.** A main worktree and a linked worktree map to one `repository_id`, exercising the measured relative-versus-absolute `--git-common-dir` hazard rather than assuming it away.
3. **Changing refs.** A reference given a branch name records the resolved OID; moving the branch afterwards changes no stored evidence, and re-verification against the recorded OID still passes. A single resolution per request is asserted by counting verifier calls, not inferred.
4. **Dirty working tree.** Modify, delete and add files on disk without committing: evidence is unchanged, because the working tree is never consulted.
5. **Missing objects.** An unknown commit OID gives `commit_not_found`; a path absent from a real commit gives `path_not_in_commit` — and the test asserts the absence is detected from empty output, since the measured exit status is 0.
6. **Unsafe paths.** Absolute, `..` traversal, leading and trailing slash, empty segment, NUL: each refused in the application, and the surfaced error asserted to contain no on-disk path and no git text.
7. **Unsupported types.** Symlink, gitlink and directory each give `unsupported_reference_type`, distinguishable from not-found.
8. **Migration and retry.** Replay a receipt written before migration `004`; assert a reference-free request still produces the pinned version-1 digest by byte equality; same key plus a new reference conflicts; and a retry returns the original receipt and original evidence with a verifier that **raises if called at all**, after `HEAD` has moved and with the object unavailable.
9. **Verification unavailable.** Launched without `--repo` and with `git` absent: reference-carrying writes are refused with `verification_unavailable`, plain writes still succeed, and `status` reports the mode.
10. **One real MCP round trip.** Record with a reference over subprocess stdio, read the evidence back, and assert no repository, commit-root or path-root argument is accepted in any tool schema.

**Negative controls, wired from the first test** — as with retrieval, a suite that cannot fail measures nothing:

- A stub verifier that approves everything must make tests 1–7 **fail**. If they still pass, they are not testing verification.
- A repository with no commits, and a bound repository with the reference tables empty, must fail every assertion that claims evidence exists, catching assertions that pass vacuously.

## Measured git behaviour

git 2.54.0, darwin 25.1.0, 2026-09-06. Reproduce in a scratch repository:

```bash
git rev-parse --path-format=absolute --git-common-dir   # main vs linked worktree agree; raw form does not
git rev-parse --verify "<spec>^{commit}"                # typed resolution; refuses a tree
git ls-tree --full-tree -z <commit> -- <path>           # mode, type, oid, exact path
git ls-tree -r -t <commit>                              # 100644 / 100755 / 120000 / 160000 / 040000
git merge-base --is-ancestor <commit> HEAD              # resolvable is not reachable
```

| Observation | Consequence in this contract |
| --- | --- |
| Raw `--git-common-dir`: `.git` from the main worktree, absolute from a linked one | Canonicalise with `--path-format=absolute` plus `realpath`, or worktrees fail to associate |
| `<tree>^{commit}` refused; annotated tag dereferences to its commit | Typed resolution is the resolution step |
| Without `--full-tree`, pathspecs resolve relative to the process cwd | `--full-tree` is mandatory; launch directory must not change what verifies |
| Missing path: exit 0, empty output. Rejected pathspec: exit 128 | Absence is read from empty output, never from exit status |
| Blob OID equals `hash-object` of the bytes | `object_oid` is a stable content identity |
| `ls-tree` and `cat-file` stderr disclose the checkout path and on-disk existence | Validate paths in the application; never surface git stderr |
| Another branch's commit resolves but is not an ancestor of `HEAD` | Resolvable is not reachable, and neither implies current |

## Explicitly out of scope

Symbols — B2a accepts none, stores none and indexes none; if a later slice adds them they will be caller assertions, labelled as such, and symbol-aware retrieval is separate work after B2b. Also out: cross-revision search, content freshness re-checking on read, remote repository access, physical erasure, and any claim that a verified reference makes a memory correct.
