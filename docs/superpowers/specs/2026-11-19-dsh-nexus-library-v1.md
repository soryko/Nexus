# Nexus Library for DSH — v1 implementation spec

**Design agreed; implementation and rollout not authorized by this document.** The agent uses memory autonomously; a human can see what was stored, where it lives, and why. Mnemon is the interaction reference, not a second database to mirror. [Library and inspector prototype](../../nexus-compartments-preview.html) · [move proposal](../../nexus-compartments-preview.html#proposal) · [chat-to-Atlas journey](../../nexus-atlas-journey-prototype.html). These are sample-data fixtures, not working backend contracts.

## What exists, and what does not

- **Nexus records:** exact current and historical revisions, lexical search, recorded file references and revision-checked writes. The [DSH integration](</Users/soko/Repos/deepseek-harness/.agents/integrations/nexus-memory/README.md#L26-L45>) documents a shared DSH partition, restricted read-only workers, bounded recall, and narrow capture of explicit requests or eligible successful checks. A working directory is not a space. Current capture is not a general-purpose Librarian.
- **Atlas:** the [store viewer design](2026-11-04-memory-atlas-design-v2.md) describes stored facts and direct relationships, not a log of what influenced the agent. Build a DSH-native client over authorized reads, not an iframe or a second MCP process. Verify the selected reader and session are bound to the same store and scope before displaying records.
- **New work:** durable home-space membership, per-session recall selection, collections, Librarian routing, placement history, move proposals and their receipts. Do not present a tag, repository path, or Mnemon Space as an existing Nexus home. For older records show **Home not assigned** until placement exists; never silently migrate by browsing.

## User-visible contract

Use the compact Mnemon workbench: underlined navigation, roughly 13 px body / 12 px metadata, graphite surfaces and a small amber home-space mark. Text labels distinguish state without color. Keep health metrics and performance evaluation out of this release.

| Surface | Required behavior |
|---|---|
| **Spaces** (landing) | Directory of named spaces with routing description, active-for-recall state and authorized counts. Selecting a space filters Memories; it never activates recall. |
| **Memories** | Efficient data-first list, scoped search and kind filters. Selecting a record leaves the list and scroll position intact. **Details** shows exact text, home, collections, recorded source and filing rationale when available. **History** shows actual revisions and differences; do not invent reasons. |
| **Collections** | Librarian-organized browsing paths to the same record, not copies or permissions. Only show records and counts the viewer may read. |
| **Chat → Atlas** | One quiet cue per turn with confirmed filing, grouped if needed. Open beside chat; **Open full Atlas** retains the selected record. Start with direct connections, explicitly expand neighbors, return to the originating turn. Distinguish collection links from citations. |
| **Proposed move** | In the inspector show **Not applied**, old and proposed homes, rationale, and concrete recall impact. **Keep here** dismisses this proposal without moving anything. **Move memory** requests the exact reviewed change. A failed action leaves the current home visible. |

## Storage and authority

**Nexus owns truth.** One stable memory ID, one home for every newly filed memory, zero or more collection memberships. Placement is versioned separately from memory text: moving a memory must not change a quoted revision or break a link. Collections neither expand access nor change recall eligibility. Record old/new placement, actor, decision, operation ID and result in a placement journal. Do not log raw draft text or transcript excerpts in activity metadata.

**The Librarian recommends; the host enforces.** In an explicitly enrolled scope, the parent may autonomously file an eligible new memory into a permitted home, create a space under policy for a recurring distinct subject, or organize collections. Prefer an existing matching space; use an authorized Inbox for uncertainty, otherwise abstain. No transcript capture, memory-directed instructions, worker writes, silent activation or inferred permission. Existing-record moves that affect recall require review; access widening is not granted by a move approval. No autonomous deletes, merges, or bulk migrations in v1.

A move proposal binds the memory ID, expected placement version, source, destination and applicable policy. Recheck all at commit. Commit membership and journal atomically; retries use the same operation identity. On conflict, show **Review updated move**, not an automatic retry. On timeout, show **Outcome unknown** until reconciled. **Filed** requires confirmation of both stored record and committed home; **Moved** requires a placement receipt. A successful record write alone proves neither its home nor its use in an answer.

## Delivery order and release checks

1. **Read-only DSH slice.** Verify live host/client contracts, then implement authorized list, inspector and revision reads. Use real records and their actual available metadata; label missing data or omit unsupported controls. Test isolated scopes, empty versus unavailable, keyboard navigation, narrow screens, and retaining selection/filter/scroll on return. No state change from browsing or replay.
2. **Disposable space and collection pilot.** Seed two spaces with different recall settings. File one memory, list it via two collections, then regroup the collections: the same ID, revision, citations, authorized visibility and recall eligibility must survive. Show one filing cue only after receipt; simulate duplicate delivery, partial filing, cancellation and restart. Do not silently enroll existing records.
3. **Disposable proposal pilot.** Test keep, accept, stale placement, changed policy, repeated click, concurrent writes and unknown outcome. Verify the actual eligible recall set before/after, not just the UI label. Reject access widening and attempts by the agent to approve its own move. Confirm proposal text is absent from unauthorized logs, exports and model context.
4. **Production gate, separate decision.** The [host gate report](../../measurements/2026-11-19-dsh-capture-host-gate.md#L19-L35) did not establish private ingress, independent human authorization or safe recovery against store replacement. Keep new write paths disabled until those guarantees are demonstrated in the intended environment and enrollment is explicitly authorized. This does not change the existing hook policy.

**First implementation ticket:** open a known, authorized Nexus memory from a confirmed existing record receipt in a read-only DSH inspector; show its exact saved revision, distinguish it from a newer current revision, and return to the originating turn. If home placement is unavailable, label the cue **Saved memory**, not **Filed in …**. Verify against a disposable store before enabling it in a profile.
