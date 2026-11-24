# On-demand Git reference comparison at HEAD

Status: implemented in the checkout as the opt-in MCP tool `compare_at_head`; see the [usage guide](../../daily-use.md). Not installed or enabled in existing server processes by this change. Independent of the [recall plan](../plans/2026-11-21-recall-quality.md). Existing [provenance rules](../../daily-use.md) remain authoritative: verifying an object at a commit does not verify prose or present-day relevance.

**User question:** Does the object recorded at this exact path still match the one in the bound checkout's current HEAD? The action is “Compare at HEAD”, not “Verify memory”. Always show “excludes working tree and index”.

**Read contract:** Given an authorized memory ID, revision ID and reference ordinal, first verify the revision is readable in the bound scope and that the reference belongs to it. Never accept arbitrary checkout paths or refs from the caller. Resolve HEAD to one commit once per request. Look up the exact recorded path in that commit's tree; compare recorded object OID and entry type, without dereferencing symlinks or following renames. Return reference commit and observed HEAD OID on success. Object equality does not assert identical permissions or unchanged surrounding code. No network fetch, file-body disclosure, write, or read-time mutation. Do not run it automatically on search.

| Outcome | Test and display |
|---|---|
| Matches at HEAD | Same path, type and OID, including when local edits are staged or unstaged. No “current” or “true” badge. |
| Changed at HEAD | Same path, different OID or type. Does not say the decision is obsolete. |
| Missing at HEAD | Tree lookup completed successfully but path is absent; a rename is missing at the old path. |
| Cannot check | Unbound or mismatched repository, unborn HEAD, timeout, missing Git objects, unsupported reference or Git failure. Never present an error as missing. |

**Access tests:** foreign or forgotten targets return ordinary not-found without path/commit information. Unverified source strings are ineligible. Repeat against detached HEAD, a branch switch during the request, and two worktrees: report the commit actually inspected in the bound worktree. A dirty file whose committed object still matches must never appear as a verified working file. Test unchanged receipts, revisions and search results before/after. Ship only behind explicit user action after all outcomes are exercised in temporary repositories; otherwise leave comparison unavailable.
