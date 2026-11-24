# Nexus project memory across Codex, Claude Code, Antigravity and DSH

**Status: revision 2, approved by the owner on 24 November 2026.** Revision 1 is kept as [cross-host-project-memory.r1.md](cross-host-project-memory.r1.md). The changes come from a design review held with the owner the same day; the decision log is in §9. Approval authorizes what §1 lists (the §6 consolidation and isolated implementation and testing) and nothing in §8: no rollout is authorized by this document.

## 1. Outcome and approval boundary

Approve a folder once. Its Git projects get isolated recall without four separate enrollments. Trust one particular repo for automatic maintenance once, and the preference follows it across compatible hosts. The user can always tell which project is active, what can be saved, and why a write occurred.

This plan is **Stage B of Nexus memory v2** (`feat/nexus-memory-v2`, 8b3ea5b). It builds on v2's owner catalog, private grant tickets and fence-serialized publication and revocation. It does not build a second access-control layer beside them.

Approval of this plan would authorize the branch consolidation in §6 (one PR at a time, each merged only on the owner's word), and implementation and testing in isolated checkouts, profiles and stores. It would **not** authorize enrolling a real folder, enabling writes on an owner repo, replacing an installed plugin, changing a live profile, migrating existing memories, paid model calls, or publishing a release. Those need a separate rollout decision. Until plan approval: stop here.

## 2. Owner experience

- **Outside an approved folder:** Nexus stays off. Offer "Enable for this repo" and, separately, "Enable recall for repos under this folder." No recursive scan, mass enrollment, or background ingestion. Approval controls project memory, not the coding host's own trust prompts or plugin installation.
- **First open under an approved folder:** after the host identifies one trusted checkout, lazily register its opaque project identity and create its project space. Show `Nexus · <project> · Recall only`. The same prompt offers a separate, never preselected choice: "Also let Nexus maintain memory for this repo." Two hosts opening it simultaneously must resolve the same registration. An empty store can legitimately return nothing.
- **Recall only (default):** search and read. An explicit save request typed by the owner in their own prompt (for example "Remember that …"), seen by the host's prompt hook, is saved; it counts as an owner action. A save requested by the model through a tool is refused. Never silently upgrade the repo.
- **Recall + maintain:** an explicit per-repo opt-in shared across hosts. A folder never grants it. Automatic creation follows the capture rule in §3. A proposed change to an existing record names the exact record and requires owner confirmation. A host without confirmation UX may show the proposal and leave it unsaved. No automatic forgetting.
- **Disable:** a per-repo denial overrides folder defaults. Stop new recalls, deliveries and writes, including queued work in already-open sessions. Preserve records; explain that already-displayed text and old session logs cannot be recalled. Re-enabling starts a new policy generation; old queued actions do not resume.
- **Controls:** one owner-facing interface for status, folder approval, repo override and disable; CLI first, native host shortcuts later. Ordinary model tools, repository files and recalled text must not grant permissions. Status shows effective mode, binding, build/capabilities, worker permissions and any degraded behavior (including why a repo is unsupported), without exposing secrets.

There is no preview mode for the owner in v1. Preview exists only as a dry-run switch for the Phase 4 measurement.

## 3. Shared implementation contract

**Project identity.** Extend the existing [project registry](../../integrations/project_scope/README.md) and v2's project spaces, not four separate path matchers. Resolve from the host-attested workspace or owner pin and the verified Git common directory, not the prompt, basename, remote URL or last-opened project. Linked worktrees of an enrolled repo share its project space but verify Git references against the active worktree. An independent clone gets a separate identity even with the same remote. Nested repositories resolve to their own verified identity and never inherit the outer repo's records. Unknown roots, conflicting signals, escaped paths, unsupported non-Git workspaces and stale identity yield no memory until resolved.

- **Folder containment:** lazy enrollment requires both the opened worktree and its main checkout (the common directory's parent) to be inside an approved folder after canonical resolution. A symlink inside an approved folder must not enroll a checkout outside it.
- **Moves:** before minting a new identity, look up the (device, inode, birth time) of the common directory across existing entries. A match at another path is refused with "moved: run recover". Extend `recover` from device-only drift to owner-confirmed path moves, keeping the same project ID and store. Never silently replace a missing or retired store.
- **Unsupported in v1:** submodules, `--separate-git-dir` repositories and bare repositories. Status names the reason. This matches what `checkout_dirs` refuses today.
- **Group-writable Git directories on Linux:** today any group-writable common directory is refused, so users with a umask 002 login shell (the Ubuntu default for user-private groups) get no memory on any repo. Accept group write only when the directory's group is the user's private group with the user as its sole member, verified at check time. Refuse any other group-writable directory.
- **Registry version 2:** new state bumps the registry to version 2. Older runtimes refuse it (`read_registry` already rejects any other version), so an old build fails closed rather than writing under a mode it does not understand. Every host upgrades together at rollout.

**Enforcement.** All managed reads/writes, host hooks, direct MCP calls and viewers pass through v2's fence. This includes adapters that now open SQLite directly. A launcher-time check alone is insufficient.

- **Repo mode lives in v2's catalog** as a ceiling on the project space (`recall` or `maintain`, plus a policy generation). A session grant can never exceed it. The mode and generation are checked under the same catalog `BEGIN IMMEDIATE` lock that `publish` already holds while it writes.
- **Disable** sets the space's mode and bumps the epoch of every session on that space under that lock. After disable is acknowledged no old-generation write may commit; one already committed stays committed.
- **Roles:** only the root session of a host may write. Claude Code subagents, DSH workers and other delegated sessions hold read-only grants in v1; a hook cannot raise them.
- If authorization cannot be verified, the coding turn continues without Nexus. Scope of guarantee: managed integrations, not arbitrary software running as the OS owner.

**Recall.** Once per eligible human prompt, search the current project using bounded lexical input. Skip private/no-memory requests, trivial input and synthetic follow-ups. Show bounded, clearly attributed evidence; IDs-only remains a supported host mode. When delivering body text, check the pinned revision is still current and permitted at delivery. **A hard 4-second ceiling** applies to automatic recall; past it, the turn continues without Nexus. A timeout or changed record means no evidence from that result, not an unbounded retry. The p95 overhead budget is set from the Phase 0 baseline measurement. Keep explicit history inspection separate. Instrument offers, confirmed reads, skips and latency separately; a hit alone does not establish that a model used a fact. v2's evidence graph stays opt-in and outside this plan.

**Capture and correction.** Consider at most one candidate per completed root turn.

- **Eligibility (hybrid):** the model may draft a candidate, but it is created automatically only if its body contains, verbatim after whitespace normalization, a span of about six or more words from the owner's own text in the human prompt of the same root turn. Text inside pasted blocks, quoted lines and code fences does not count. Neither does text that also appears in recalled memory or tool output earlier in the session. The model may add only a title, tags and Git references it has verified. A candidate that fails the check becomes a proposal the owner confirms, never a silent create.
- Never auto-create from recalled text, assistant speculation, tool output, transcripts or a routine successful check. Exclude private/no-save turns and likely secrets before any durable write; a heuristic is not a secrecy guarantee.
- Exact repeats skip. Lexical similarity can nominate an existing decision, never prove identity. An ambiguous match produces a proposal, not a create or overwrite.
- Confirmation shows old/new text and project; write against the expected revision, preserving intended metadata and references. A conflict invalidates confirmation: reread and present the difference again. Autonomous revisions based on stable decision identifiers are a later proposal, not a v1 dependency.

**Receipts and failure handling.** Attribute writes to project, host, session event, operation, memory ID and revision; keep receipt telemetry content-free. Reading the saved body is an explicit authorized operation. Reuse v2's operation identity: persist the operation before sending a mutation (as the DSH provider's journal already does; MCP hosts need the same host-side persistence), and an identical permitted retry uses the same key and payload — a changed command is a new decision. After an ambiguous outcome or project rebinding, pause the operation; reconcile against the verified original store if supported, otherwise report unresolved. Do not generate a new key, repeat from another host, or claim failure/success without evidence. An idempotency key protects one operation, not two hosts independently deciding to save the same fact: serialize the exact-duplicate check and create per project under the fence. Do not promise semantic deduplication.

## 4. Work specific to each host

| Host | Preserve | Change and prove |
|---|---|---|
| Antigravity | Attested roots/pin, short excerpts, workspace diagnostics and Studio | v2's launcher refuses Antigravity (`integrations/project_scope/launcher.py:64`, added in `e2e157b`, reason not documented). First establish why. Then mint a session grant from the host-attested workspace that `host_project` already verifies, and refuse when it is absent. Route prompt hooks and direct memory operations through the fence. Studio and MCP must display the same binding or clearly say unavailable. Show the receipt after a qualifying Stop; never treat a nudge as a write. |
| Claude Code | Model-led drafting and topic grouping from `feat/claude-autonomous-memory` | The Stop hook's model-drafted save is subject to the §3 eligibility check; otherwise it is a proposal. It cannot bypass recall-only. Identify supported installed artifact vs. hooks-only plugin; both obey the owner mode. Raise the recall hook timeout from 2 s to the 4 s ceiling. Topic spaces are optional organization, not project identity. Subagents are read-only. |
| Codex | Bounded prompt lookup, admitted-ID reads and deliberate broad exploration | Perform duplicate lookup inside the authorized maintenance path without opening broad model search. Prove which completion event the supported build actually delivers. If none is reliable, keep recall plus explicit owner save and label automatic maintenance unavailable in the capability matrix. |
| DSH | Revision-checked body delivery, receipts, restricted workers, saved-revision Inspector | Build the host-side writer for v2's DSH provider (`forAuditedHost`). Today no DSH profile, harness or plugin references it, so no real DSH save is possible. Replace the fixed profile-wide store (`~/Library/Application Support/Nexus Memory/dsh/deepseek-harness.sqlite3`, capture on, one record as of 2026-11-24) with verified session project binding. Atlas and Inspector open only the authorized original store; on rebinding, refuse rather than guess. Remove routine check results from automatic capture. The old shared record stays unassigned, untouched and invisible to project recall. No automatic import. The DSH integration code lives outside this repo (`deepseek-harness/.agents/integrations/nexus-memory`, `~/dsh-plugins/nexus-mnemon-integration`); Phase 0 records its revisions. |

## 5. Build sequence and gates (after approval)

| Phase | Deliverable | Gate before continuing |
|---|---|---|
| 0 — Consolidate and freeze the baseline | Branch consolidation per §6. Then record source revision, installed artifact identity, schema and event capabilities for each host, including the external DSH repos. Document the Linux git requirement (newer than Ubuntu 24.04's 2.43, which lacks `--no-lazy-fetch`). | v2's Linux gates pass on GitHub CI on the v2 PR. A local Ubuntu 24.04 replay on 2026-11-24 already passed: suite 840 passed, mutations 36, lifecycle 9/9, Stage A drills, fresh install and source removal, DSH provider gate. Atlas UI was 38/39; the one failure, `test_oversize_revision_is_reported_not_compared`, is flaky on `main` too and needs a root cause or quarantine with a ticket. Reproduce existing happy paths in disposable stores. Identify missing callbacks explicitly and record baseline recall latency to set the p95 budget. |
| 1 — Registry and access policy | Registry v2; folder approval and lazy enrollment; move detection and extended `recover`; private-group acceptance; repo mode and generation in v2's catalog with fence-checked disable; root-only writes; owner controls and effective-mode status; Antigravity session grants. | Pass identity, mode, concurrency and restart tests without relying on host instructions, including under umask 002 and 022 on Linux. No wrong-project IDs, excerpts or bodies. |
| 2 — DSH isolation and first real save | Audited-host writer for the v2 DSH provider and project-bound DSH root/worker/viewer in a disposable profile; shared data excluded. | **Entry:** the first real writable DSH save in a disposable profile, which closes v2's last HOLD item. It needs a model turn, so the owner approves that spend before it runs. **Exit:** simultaneous unrelated sessions remain isolated; session switch, old receipt, worker write attempt and missing root behave as specified. |
| 3 — Four-host recall | Integrate Antigravity, Claude and Codex with the same policy and the verified DSH route. Produce a per-build capability matrix and replayable fixtures. | One synthetic record visible to authorized hosts only. Run at least one real disposable-host interaction per claimed host feature; unit tests or a mounted plugin alone do not establish support. |
| 4 — Maintenance dry run | One create/propose/skip policy with the §3 eligibility check, privacy checks, conflict and duplicate behavior, metadata-only receipts, run in dry-run mode. | A labeled fixture set of at least 50 items, including ordinary chatter, owner decisions, corrections, pasted text and misleading near-matches. Automatic creates reach at least 90% precision; recall is reported, not gated. Zero wrong-project proposals, wrong-target updates, or restricted content retained. No writes from the dry run. |
| 5 — Opt-in writes in a pilot | Auto-create for eligible facts, confirmed revisions, explicit-only deletion, reconciliation and rollback. | Pass the end-to-end handoff and failure tests in §7. Present artifact versions, coverage gaps, dry-run error counts and latency results to the owner. Stop for separate live rollout approval. |

## 6. Branch consolidation (Phase 0)

Counts are as of 2026-11-24, relative to `main`.

1. Commit this checkout's uncommitted and untracked work (34 modified files, 17 of them identical to `feat/antigravity-plugin`, plus about 100 untracked files outside `node_modules`) to a salvage branch first, so nothing exists only on disk.
2. One PR per branch, merge commits only (records cite branch SHAs), nothing pushed or merged without the owner's word for that PR, in this order: PR #26 `feat/recall-visible-line` → `feat/claude-autonomous-memory` → `feat/antigravity-plugin` → `feat/nexus-memory-v2`. The live branches overlap in `mcp_server.py`, `sqlite.py`, `service.py` and `launcher.py`; expect conflicts.
3. Bring in `feat/claude-hooks-plugin`, `docs/p1-overflow-range-note`, and `docs/p1-claude-auto-closeout` once the owner signs it. Review `docs/agent-baseline-protocol` before deciding. Leave PR #6 (`docs/a2-calibration`) parked and `feat/project-scope-integration-old` unmerged as superseded.
4. No branch or worktree deletion in this pass. The 22 branches already contained in `main` are a separate cleanup decision.

## 7. Release-blocking acceptance tests

1. **Four-host handoff:** Claude saves the owner's staging database decision; Antigravity recalls it; a host with a working proposal path proposes a correction from SQLite to PostgreSQL; the owner confirms the named record; DSH reads its current revision. Expect exactly one record and two revisions. Verify old text only through an explicit history or saved-revision read. The capability matrix states which hosts can propose natively; a host that cannot uses its documented deliberate correction route, and the limitation is disclosed.
2. **No false merges:** "staging database" and "staging test database" stay separate. A similar phrase, topic label or shared Git remote must never authorize revising another record.
3. **Isolation:** linked worktree sees the decision; unrelated same-name repo and second clone do not, including by exact ID. Exercise macOS path aliases/case, symlinks, prefix collisions, nested repos, refused submodules, moved checkout (refused until recovered), missing root, retired store, and Linux umask 002 with a private group and with a shared group.
4. **Opt-out races:** disable from one host while another has recalled-but-not-delivered text and a third has a write waiting. Pending delivery/commit are rejected; open sessions need no restart; re-enable does not replay old work. Existing history survives.
5. **Faults and rights:** duplicate callbacks, concurrent creates, two corrections to one revision, timeout after send, restart, missing end event, missing registry, an old-version runtime against registry v2, and subagent or worker writes. No extra committed revision, no blind retry, no fabricated receipt; missing hooks are shown as unavailable.
6. **Privacy and usefulness:** a recalled instruction cannot select a project or create a fact. Pasted text, private input, a synthetic credential and test logs produce no automatic record or content-bearing receipt. An empty or slow store cannot block normal coding beyond the 4-second ceiling. Inspect actual injected context and receipt contents, not just tool-call counts.

## 8. Rollout and rollback (not pre-authorized)

Start with a disposable pilot, then one owner-selected repo in recall-only; offer maintenance per repo after that. At rollout every enrolled v1 store is migrated in place to v2 after a verified backup, and every repo starts in recall-only. Nothing keeps today's autonomous Claude behavior without a fresh per-repo opt-in. Migrated stores cannot be opened by `main`-branch runtimes, so every host upgrades together; rollback is restoring the verified snapshot, and writes after the snapshot are lost. Disable maintenance to halt writes; do not roll back by redirecting sessions to the old shared DSH database. Revocation, isolation, wrong-target writes, leaked sensitive content or unresolved store identity are stop conditions. Report the affected build and operation without echoing memory text. No claim of improved coding outcomes from conformance tests alone.

## 9. Decision log (owner review, 24 November 2026)

| Topic | Decision |
|---|---|
| Foundation | Build on `feat/nexus-memory-v2`; graph stays opt-in and out of scope. |
| Policy location | Repo mode and generation in v2's catalog, capping session grants, checked under the fence lock. |
| Scope | Automatic maintenance is in v1; maintenance is per repo, never granted by a folder; offered at first open, never preselected. |
| Preview | Measurement-only dry run, not an owner-facing mode. |
| Explicit save | Allowed in recall-only when the owner types it in their own prompt; model-requested saves refused. |
| Capture | Hybrid: model drafts; auto-create only with a verbatim owner span of about six words from the same root turn; otherwise a proposal. Subagents and workers never write. |
| Identity | Both worktree and main checkout inside the approved folder; moves refused until recovered; submodules unsupported; private-group group-write accepted on Linux; registry version 2 fails old builds closed. |
| Gates | Dry run at least 50 items and 90% create precision; any host may propose in the handoff test; 4 s recall ceiling; Linux gates in Phase 0; first real DSH save opens Phase 2 with owner-approved spend. |
| Migration | v1 stores migrate in place at rollout with a verified backup, starting in recall-only. DSH's single shared record stays untouched until Phase 2. |
| Consolidation | Salvage branch first; one PR per branch with merge commits in the order in §6; no deletions. |

**Decision (24 November 2026):** approved: this v1 scope, the consolidation in §6 and the isolated implementation gates. PR #26 was already merged when approval was given, so consolidation resumes at `feat/claude-autonomous-memory`. Approval does not turn on Nexus in any real repository.
