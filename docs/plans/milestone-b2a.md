# B2a — repository identity, reference verification and compatibility contract

**Status: frozen for implementation.** This document fixes the semantics of the first B2 slice *before* its tests are written, so the acceptance tests check a contract rather than describe an implementation. Changes after approval are recorded as dated amendments below, never as edits in place.

Ordering inside B2: **B2a** is repository identity plus commit/path verification. **B2b** is reference filters on `search`. Symbol-aware retrieval comes after both, and B2a neither accepts nor stores symbols — see [Explicitly out of scope](#explicitly-out-of-scope).

Git behaviour recorded here was measured on **git 2.54.0, darwin 25.1.0, 2026-09-06**, with the commands in [Measured git behaviour](#measured-git-behaviour). Every claim is marked measured, assumed or decided.

## 1. Repository identity

**Decided.** A repository is bound at launch, exactly like `Scope`, and is not addressable from a tool call.

- Launch takes an optional `--repo PATH` naming a local checkout. One repository per server process.
- The binding has an explicit, persisted **`repository_id`**: an opaque identifier minted once and stored in the database under the launch scope. It is the identity that references record. Neither a path, nor a namespace, nor a remote URL, nor a commit is that identity.
- The canonicalised absolute Git common directory (`git rev-parse --path-format=absolute --git-common-dir`, then `realpath`) is a **locator, not an identity**. It answers "which checkouts are the same repository right now"; it does not survive a move, and it is reused by whatever is put at that path next. Identity is the persisted row; the locator is one mutable attribute of it.
- **Measured:** the main worktree and a linked worktree agree on the common directory and disagree on both `--show-toplevel` and `--absolute-git-dir`. **Measured, and new:** raw `--git-common-dir` returns `.git` from the main worktree and an absolute path from the linked one, so comparing its raw output equates nothing. `--path-format=absolute` is mandatory, and `realpath` on top of it, because a canonical string is the whole mechanism.
- Identity is carried by a **checkout token**: opaque random bytes written once to `<common-dir>/nexus/checkout-token`, with `(repository_id, token)` persisted in the database. The token is a registration, not an observation. **History is never consulted for identity** — see [why the first design was withdrawn](#why-history-derived-identity-was-withdrawn).
- **Registration writes local metadata.** It creates a file inside the shared Git directory. **Measured:** every worktree of a repository reads the same token, including a linked worktree on an orphan branch with an unborn `HEAD`; `git clone` does **not** carry it, so a clone registers as a new identity by default; `cp -R` of the checkout **does** carry it, so a filesystem copy or a restored backup claims the original's identity until its token is deleted. That is the documented limit, and deleting the token in the copy re-registers it as new.

Binding rules:

| Situation | Result |
| --- | --- |
| Token present, row exists | Bind that `repository_id`, updating the stored locator if the checkout moved |
| Token present, no row in *this* database | Adopt: mint a `repository_id` for that token here. Databases hold independent opinions about the same checkout |
| Token absent | Register: mint a `repository_id` and write the token |
| Shared Git directory not writable | Refuse to start with `repository_registration_failed`, naming no path |
| `--repo-id <id>` supplied, token maps elsewhere | Refuse with `repository_mismatch` |
| Additional clone | A distinct identity, because a clone has no token. `--repo-id` associates it deliberately, with the consequence that evidence recorded under one checkout is thereafter presented as belonging to the same repository |

- **Independent clones never merge automatically.** A matching remote URL, a shared commit OID, an identical tree, or a similar path is not evidence of the same project. Two clones of the same upstream are two repositories until an operator says otherwise, and the association is an explicit local action, never an inference.
- **No tool call can select, override or create a repository binding.** The MCP schemas carry no repository, commit-scope or path-root argument, and injected fields are rejected the way injected `namespace` already is.
- Launch without `--repo`, or with `git` unavailable, is a supported mode: verification is **unavailable**, and `status` says so. Nothing is labelled verified in that mode.

### The isolation boundary is `Scope`, not the repository

**Decided, and it corrects an over-promise in the first draft.** Receipts are keyed `(namespace, actor, idempotency_key)` and the digest covers caller input, not the binding. Two services in the **same** scope, bound to different repositories, submitting identical input under one key, therefore replay the same receipt — the existing lookup in `SQLiteRepository` decides that, and B2a does not change it.

So: a `repository_id` distinguishes and labels evidence; it does **not** partition memories. Within one scope, a memory recorded while bound to one repository is visible to a service bound to another, and B2b's reference filters narrow results without creating isolation. Making the repository an isolation boundary needs an ownership and replay contract that also answers what happens to reference-free memories, and that is not this slice. Where invisibility is required today, use a different `namespace` or `actor` — the boundary milestone A already proves.

**Decided, with the alternative recorded.** A structured reference is stored **only** with verification evidence. When verification is unavailable the write is refused with `verification_unavailable` rather than stored unchecked. The alternative — store it with an `unverified` evidence value — was rejected because it produces two kinds of stored reference that a later reader must remember to distinguish, which is the failure mode milestone A avoided by keeping `snapshot` plainly caller-asserted. A caller who wants unchecked provenance already has `source_uri` and `snapshot`.

### Why history-derived identity was withdrawn

The first draft derived a discriminator from the root commit of `HEAD`'s first-parent history. **Measured on git 2.54.0, both cases rejecting a legitimate repository:**

| Operation | Unchanged | Discriminator |
| --- | --- | --- |
| Linked worktree added on an orphan branch | Shared common directory | **Uncomputable** — `rev-list` on an unborn `HEAD` fails outright, so the check cannot even run |
| Shallow clone deepened by one commit | Common directory **and `HEAD`** (`d1c062a…` before and after) | **Changed** — `d1c062a…` to `2615b49…` |

Both would have produced `repository_mismatch` with no repository replaced. Git gives each worktree its own `HEAD` by design, and history depth is a fetch decision, so neither is a defect in the reproduction — they are what the design asked for. Branch history is not identity, and a token that is written rather than inferred cannot make this class of mistake.

## 2. Reference values

**Decided.** A reference is part of a revision, and a revision replaces all of its fields:

| Field | Meaning | Rule |
| --- | --- | --- |
| `path` | Repository-relative path | Required. Validated in the application before git is invoked |
| `commit` | Commit spec: a 40-hex OID or a ref name | Optional. Absent means the bound repository's `HEAD` |

- At most **8** references per revision. Normalised by `(commit, path)`, deduplicated, sorted. `revise` replaces the whole set; omitting it resets to empty, exactly like `tags`.
- **All or none.** If any reference in a request fails to verify, the whole write is refused. A revision never carries a partially checked set.
- Path validation is the application's job, not git's: non-empty, valid UTF-8, at most 1,024 bytes, no NUL, no leading or trailing `/`, no empty segment, no `.` or `..` segment, never absolute. **Measured:** `git ls-tree -- ../etc/passwd` fails with `'../etc/passwd' is outside repository at '<absolute on-disk path>'`, and `git cat-file -e HEAD:/etc/passwd` reports *"path exists on disk, but not in 'HEAD'"*. Git's stderr discloses both the checkout location and on-disk existence, so **git stderr must never reach a caller** and unsafe paths must be refused before git sees them.

**Object format is recorded, not assumed.** At bind, `git rev-parse --show-object-format` gives the repository's hash algorithm, stored on the repository row. OIDs are validated against it: 40 hex for `sha1`, 64 hex for `sha256`. **Measured:** a `--object-format=sha256` repository yields 64-character commit and blob OIDs. Any other format, or a bound repository whose format later differs, is refused — `invalid_reference` and `repository_mismatch` respectively. A fixed 40-hex assumption would have silently rejected every SHA-256 repository.

**Stored per verified reference:** `repository_id`, resolved `commit_oid`, repository-relative `path`, `object_oid`, `entry_type`, and `checked_at`. Reference rows are immutable and per-revision, like revisions themselves. **No repository file content is copied into the database** — identifiers and evidence only.

## 3. Verification protocol

**Decided.** Resolution happens **once**, then every check runs against the resolved commit:

1. Collect the **distinct effective commit specs** in the request. A reference with no `commit` takes `HEAD` as its effective spec, so an omitted spec and a literal `HEAD` are one spec, not two.
2. Resolve **each distinct spec exactly once**, with typed resolution: `git rev-parse --verify <spec>^{commit}`. **Measured:** this resolves an annotated tag to its commit and refuses a tree with *"expected commit type, but the object dereferences to tree type"*. Record the OID.
3. Every reference sharing a spec reuses that spec's resolved OID. For each path, `git ls-tree --full-tree -z <commit_oid> -- <path>`, reading mode, type, object OID and the exact path back.
4. Nothing re-reads a spec. One spec cannot split across two commits within a request.

**No atomic snapshot is promised across specs.** Eight references may legitimately name eight commits, and two different moving refs resolved microseconds apart may reflect different states of the repository. Each reference's evidence is exact about the commit it was checked against; the set of them is not a consistent snapshot of the repository, and nothing in this contract claims it is.

**`--full-tree` fixes the coordinate system; it does not make the argument a literal path.** `ls-tree` takes *pathspecs*, and a pathspec matches by prefix: **measured**, `ls-tree -r --full-tree HEAD -- sub` returned `sub/file.txt`. A successful exit with non-empty output therefore proves only that *something* matched. Acceptance requires all four of:

1. **Exactly one record** in the `-z` NUL-delimited output. `-z` is mandatory so paths arrive unquoted rather than through git's quoting rules.
2. **Byte-equal pathname.** The returned path must equal the requested path exactly. This, not reasoning about pathspec semantics, is what rejects a directory prefix, a stray wildcard character and a quoting surprise alike.
3. **Type `blob`.**
4. **Mode `100644` or `100755`.** Everything else is [unsupported](#5-unsupported-reference-types).

- **`--full-tree` is still mandatory. Measured:** without it, `ls-tree` resolves the pathspec relative to the process working directory. From inside `sub/`, `-- file.txt` matched `sub/file.txt` and `-- sub/file.txt` matched nothing. That is a silent wrong answer in both directions — a false verification and a false absence — and it depends on where the server happened to be launched.

**Measured, and the reason absence is not read from exit status:** `ls-tree` for a path that is not in the commit exits **0 with empty output**; only a rejected pathspec exits non-zero (128). Absence is empty output. An implementation that keys on the exit code reports every missing path as success.

### Execution controls

Every git invocation carries `--no-replace-objects --no-lazy-fetch --literal-pathspecs`, with `GIT_TERMINAL_PROMPT=0` in the environment. **Measured** (git 2.54.0, all three flags accepted together): these are not hygiene, they are two of the guarantees.

- **Replacement objects substitute silently.** With a `refs/replace/` ref mapping commit `C1` to a later commit, `ls-tree --full-tree C1 -- sub/file.txt` returned the *tampered* blob OID, while `--no-replace-objects` returned the true one. `rev-parse --verify C1^{commit}` reported `C1` either way, so the substitution is invisible in the commit id and would have been recorded as evidence about a commit whose real tree says otherwise.
- **"We never invoke `fetch`" does not establish local-only.** In a partial clone a missing object is fetched lazily by the read itself. `--no-lazy-fetch` makes a missing object an error, which is the answer this contract wants: verification fails rather than reaching the network. `GIT_TERMINAL_PROMPT=0` ensures it fails instead of blocking on a credential prompt.
- **`--literal-pathspecs`** removes pathspec magic (`:(glob)`, `:(exclude)`, …) from caller-supplied text, so a path is a path. It is belt-and-braces behind rule 2 above, which is what actually decides acceptance.

### Concurrency

Two requests under the same key can both miss the receipt pre-check, and — resolving `HEAD` independently — can verify against **different commits**. That is expected and harmless, because the pre-check decides nothing. The authoritative `(scope, key, digest)` check inside the write transaction is unchanged from milestone A and still decides: one request commits its revision and its evidence, the loser observes the committed receipt and replays it. The loser's verification result is **discarded, never merged** into the winner's revision, so evidence always describes the single observation made by the write that committed.

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
- **Not currency.** Unchanged bytes do not mean the advice still holds. **Measured:** a commit from another branch resolved with `rev-parse --verify` while `merge-base --is-ancestor` reported it was **not necessarily an ancestor of `HEAD`**. That is the exact claim, and it is not the same as unreachable: `HEAD` moves, other refs may reach the commit, and ancestry between two fixed commits is a different question from ancestry relative to a moving ref. Resolvable implies neither, and neither implies current.
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
- **Original evidence is recoverable from a retry, subject to G3.** The replayed receipt names the original `revision_id`, and evidence is stored on the revision, so `get` returns exactly the evidence recorded at the original write. The qualification matters: after `forget`, the receipt still replays — milestone A requires that — while `get` on that memory refuses, tombstone rules unchanged. A receipt carries no evidence and no content, so replay after deletion returns identifiers and resurrects nothing. Receipt shape is unchanged; evidence was deliberately not added to `WriteReceipt`, which would have duplicated it into the receipts table and changed a stable structured output.
- **Verification is never a startup dependency.** A missing, broken or unbound git leaves the server startable and every non-reference path working: `get`, `search`, `history`, `status` and receipt replay all read the database and never shell out. Bindings are persisted, so after a restart with git gone the `repository_id` and every stored reference are still readable, and evidence recorded earlier is still returned verbatim. Only *new* reference-carrying writes are refused, with `verification_unavailable`. A verifier that cannot run must never be able to make committed memories unreachable.
- **A new reference under an old key still conflicts.** Same key, genuinely different payload, different digest, `idempotency_conflict` — as before.
- **Outbox history keeps its meaning.** Reference-carrying writes emit the same event kinds, carrying IDs and not content. Events written under milestone A are not redefined.
- **Migration `004` is additive and transactional**, creating tables only, with rollback leaving the database at version 3. It rewrites no existing row. Its cost is reported rather than assumed, even though an additive migration with no backfill is expected to be cheap — *expected* is not *measured*.
- **B2b's filters change the search cursor fingerprint.** Adding `path` and `commit` to the query changes `_fingerprint`, so a cursor minted before the change is rejected as `cursor_expired`. That is the documented behaviour of an invalidated cursor, not a broken promise, and it is stated here so it is not discovered as a bug.
- **`status` gains repository and verification-mode fields.** Existing fields keep their names and meanings.
- **New error codes are stable and sanitized**, extending `NexusError`: `repository_unbound`, `repository_mismatch`, `repository_registration_failed`, `verification_unavailable`, `commit_not_found`, `path_not_in_commit`, `unsupported_reference_type`, `invalid_reference`. As before, public errors carry no SQL, no database or checkout paths, no content, no other scope's details and no stack traces.

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

1. **Distinct repository identities.** Two clones of the same source, identical trees and commit OIDs, bound separately: distinct `repository_id`s, no automatic association from a shared remote URL or shared commits, and evidence labelled with the identity that recorded it. **Invisibility is asserted with different scopes, never with different bindings** — within one scope the repository is not an isolation boundary, and a test claiming otherwise would encode a promise this contract does not make.
2. **Worktree association.** A main worktree and a linked worktree map to one `repository_id`, exercising the measured relative-versus-absolute `--git-common-dir` hazard rather than assuming it away.
3. **Changing refs, and resolution per distinct spec.** A reference given a branch name records the resolved OID; moving the branch afterwards changes no stored evidence, and re-verification against the recorded OID still passes. Resolution counts are asserted, not inferred: a request whose references name one spec — including a mix of omitted and literal `HEAD` — resolves **once**; a request naming three distinct specs resolves **three times**, and references sharing a spec share its OID.
4. **Registered identity.** A worktree on an orphan branch with an unborn `HEAD` binds to its repository's identity; deepening a shallow clone changes nothing; a moved checkout keeps its identity because the token moved with it; a fresh `git clone` is a new identity; a `cp -R` copy claims the original's identity, asserted as the documented limit rather than left to be discovered. A read-only Git directory gives `repository_registration_failed`, and `--repo-id` against a token that maps elsewhere gives `repository_mismatch`.
5. **Object format.** A SHA-256 repository records 64-hex OIDs end to end; a 40-hex assumption anywhere in the path fails the test.
6. **Dirty working tree.** Modify, delete and add files on disk without committing: evidence is unchanged, because the working tree is never consulted.
7. **Missing objects.** An unknown commit OID gives `commit_not_found`; a path absent from a real commit gives `path_not_in_commit` — and the test asserts the absence is detected from empty output, since the measured exit status is 0.
8. **Unsafe paths.** Absolute, `..` traversal, leading and trailing slash, empty segment, NUL: each refused in the application, and the surfaced error asserted to contain no on-disk path and no git text.
9. **Unsupported types.** Symlink, gitlink and directory each give `unsupported_reference_type`, distinguishable from not-found.
10. **One literal entry.** A directory path whose prefix matches a file is refused on byte-equality, not accepted because output was non-empty; a path containing pathspec-magic or wildcard characters matches only a file of exactly that name; a `refs/replace/` ref pointing a bound commit at another commit does **not** change the recorded object OID.
11. **Concurrent double resolution.** Two writers under one key, racing across independent connections with `HEAD` moving between them, commit exactly one revision and one evidence record; the loser replays the winner's receipt, and no evidence from the losing verification is stored.
12. **Migration and retry.** Replay a receipt written before migration `004`; assert a reference-free request still produces the pinned version-1 digest by byte equality; same key plus a new reference conflicts; and a retry returns the original receipt and original evidence with a verifier that **raises if called at all**, after `HEAD` has moved and with the object unavailable.
13. **Verification unavailable.** Launched without `--repo` and with `git` absent: the server starts, reference-carrying writes are refused with `verification_unavailable`, plain writes still succeed, and `status` reports the mode.
14. **Restart without git.** Write references with a working verifier, restart with git unavailable: bindings and evidence are still readable through `get` and `search`, receipts still replay, and a `forget`-then-replay returns the original receipt while `get` refuses.
15. **One real MCP round trip.** Record with a reference over subprocess stdio, read the evidence back, and assert no repository, commit-root or path-root argument is accepted in any tool schema.

**Negative controls, wired from the first test** — as with retrieval, a suite that cannot fail measures nothing:

A blanket "approve everything and watch it all fail" control was **withdrawn as invalid**: unsafe paths are rejected by application validation *before* the verifier runs, and the worktree and dirty-tree cases are supposed to succeed, so a stub that approves everything would leave several tests legitimately green and the control would prove nothing. Each mutation instead disables exactly one invariant and must fail exactly the test that protects it:

| Mutation | Must fail |
| --- | --- |
| Skip application path validation, let git see the path | Unsafe paths (8) |
| Treat empty `ls-tree` output as success | Missing objects (7) |
| Drop `--no-replace-objects` | One literal entry (10) |
| Accept a returned pathname that differs from the request | One literal entry (10) |
| Drop `--full-tree` | One literal entry (10), run from a subdirectory |
| Accept any mode or type | Unsupported types (9) |
| Re-resolve a spec per reference | Changing refs (3) |
| Verify inside the write transaction instead of before it | Concurrent double resolution (11) |

A mutation that fails *nothing* means the invariant is unguarded; a mutation that fails *everything* means the tests are coupled and are not isolating what they claim to.

## Measured git behaviour

git 2.54.0, darwin 25.1.0, 2026-09-06. Reproduce in a scratch repository:

```bash
git rev-parse --path-format=absolute --git-common-dir   # main vs linked worktree agree; raw form does not
git rev-parse --verify "<spec>^{commit}"                # typed resolution; refuses a tree
git ls-tree --full-tree -z <commit> -- <path>           # mode, type, oid, exact path
git ls-tree -r -t <commit>                              # 100644 / 100755 / 120000 / 160000 / 040000
git merge-base --is-ancestor <commit> HEAD              # ancestry relative to a moving ref
```

| Observation | Consequence in this contract |
| --- | --- |
| Raw `--git-common-dir`: `.git` from the main worktree, absolute from a linked one | Canonicalise with `--path-format=absolute` plus `realpath`, or worktrees fail to associate |
| `<tree>^{commit}` refused; annotated tag dereferences to its commit | Typed resolution is the resolution step |
| Without `--full-tree`, pathspecs resolve relative to the process cwd | `--full-tree` is mandatory; launch directory must not change what verifies |
| Missing path: exit 0, empty output. Rejected pathspec: exit 128 | Absence is read from empty output, never from exit status |
| Blob OID equals `hash-object` of the bytes | `object_oid` is a stable content identity |
| `ls-tree` and `cat-file` stderr disclose the checkout path and on-disk existence | Validate paths in the application; never surface git stderr |
| Another branch's commit resolves but is not an ancestor of `HEAD` | Record it as *not necessarily an ancestor of `HEAD`* — a claim about a moving ref, never as unreachable |
| `ls-tree -r --full-tree HEAD -- sub` returned `sub/file.txt` | A pathspec is not a literal path; acceptance needs byte-equal pathname, one record, blob, supported mode |
| A `refs/replace/` ref made `ls-tree` report a tampered blob OID for an unchanged commit id; `--no-replace-objects` reported the true one | Every invocation carries `--no-replace-objects` |
| git 2.54.0 accepts `--no-replace-objects --no-lazy-fetch --literal-pathspecs` together | A partial clone would otherwise fetch lazily on read, so local-only needs the flag, not a promise |
| A worktree on an orphan branch shares the common directory; `rev-list` on its unborn `HEAD` fails | History cannot be identity — a legitimate worktree could not even be checked |
| Deepening a shallow clone left `HEAD` at `d1c062a…` and moved the root commit to `2615b49…` | Identity is registered, not observed |
| Every worktree reads one `<common-dir>/nexus/checkout-token`; `git clone` does not carry it; `cp -R` does | Worktrees associate for free, clones are new by default, filesystem copies are the documented limit |
| `git init --object-format=sha256` gives 64-character commit and blob OIDs | Record the object format at bind; validate 40 or 64 hex against it |

## Development environment

B2a is developed and measured in the environment below, not in the project `.venv`. Recorded here because a result without its interpreter and SQLite version is not a result.

| | |
| --- | --- |
| Interpreter | CPython 3.13.15, Homebrew `python@3.13` |
| SQLite | 3.53.4 — above the 3.51.3 floor |
| Dependencies | `uv.lock`, unchanged: mcp 2.1.1, pytest 9.1.1, Hypothesis 6.167.1 |
| Baseline result | **89 passed**, whole suite, at `d97f09d` |
| Host | darwin 25.1.0, git 2.54.0 |

Recreate it by making a venv on that interpreter and pointing it at the locked dependencies already resolved in `.venv`:

```bash
/opt/homebrew/bin/python3.13 -m venv --without-pip <env>
echo "$PWD/.venv/lib/python3.13/site-packages" > <env>/lib/python3.13/site-packages/deps.pth
PYTHONPATH="$PWD/src" <env>/bin/python -m pytest -q
```

The project `.venv` links SQLite 3.50.4 and cannot run the suite — every storage test raises `unsupported_runtime`. `uv` additionally refuses the Homebrew interpreters here (`platform.mac_ver()` returns empty). Both are environment faults, tracked separately, and **neither is a reason to lower the SQLite floor**.

## Amendments

**2026-09-06, after review, before implementation.** Recorded rather than edited in place, per the freeze rule at the top.

1. The transactional receipt check is stated as authoritative for concurrent retries, including the case where both requests miss the pre-check and resolve `HEAD` to different commits.
2. "Git missing" is defined across restart: never a startup dependency, bindings and evidence readable without git, and receipt replay after `forget` qualified against G3.
3. The canonical common directory is demoted from identity to locator, with a discriminator, explicit rules for moved, replaced and cloned repositories, a `repository_mismatch` error, and the limit that a re-clone at the old locator is indistinguishable from a move.
4. Tree-entry acceptance is specified as one NUL-delimited record with a byte-equal pathname, `blob` type and a supported regular-file mode — non-empty output is no longer sufficient. Execution controls added for replacement objects, lazy fetching and pathspec magic, with the replacement-object substitution measured.

**2026-09-06, second review, before implementation.**

5. **The history-derived discriminator is withdrawn and replaced by a registered checkout token.** It rejected two legitimate states, both reproduced here: an orphan-branch worktree, where the check cannot even run against an unborn `HEAD`, and a deepened shallow clone, where `HEAD` and the common directory were unchanged while the root commit moved. Branch history is not identity.
6. **Repository isolation is corrected to match receipt scope.** `Scope` remains the isolation boundary; `repository_id` labels evidence and does not partition memories, because receipts are keyed `(namespace, actor, key)` over a digest that excludes the binding. Acceptance test 1 no longer claims cross-binding invisibility, and asserts it with distinct scopes instead.
7. **Resolution is once per distinct effective commit spec**, not once per request — eight references may name eight commits. An omitted spec and a literal `HEAD` are the same spec. No atomic snapshot is promised across specs.
8. **The blanket negative control is withdrawn as invalid** — path validation precedes the verifier and several cases are meant to succeed — and replaced by a table of targeted mutations, each required to fail one named test.
9. **Object format is recorded** rather than assumed: `sha1` or `sha256`, with OID width validated against it. A SHA-256 repository's 64-hex OIDs were measured.
10. **Reachability wording corrected** to *not necessarily an ancestor of `HEAD`*, which is a claim about a moving ref and not a claim of unreachability.

## Explicitly out of scope

Symbols — B2a accepts none, stores none and indexes none; if a later slice adds them they will be caller assertions, labelled as such, and symbol-aware retrieval is separate work after B2b. Also out: cross-revision search, content freshness re-checking on read, remote repository access, physical erasure, and any claim that a verified reference makes a memory correct.
