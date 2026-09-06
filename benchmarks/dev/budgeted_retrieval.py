"""The budgeted retrieval path, shared by the quality and performance harnesses.

Both harnesses must exercise *the same* path, or they time and gate different things.
Before this module existed they did not: the quality harness counted content plus
serialized provenance against the delivered-byte budget, while the performance harness
counted body bytes only. Different byte accounting delivers a different number of items,
which means a different number of `get()` calls inside the timed region — so the
performance measurement was not timing the path the gate governs.

Two rules keep it that way:

* **Label-free.** Nothing here may see a query id, a grade, a known answer id or any
  other judgment. Selection that consults labels is not selection, and a timed section
  that computes grades is not timing retrieval.
* **Diagnostics stay outside.** The over-budget probe that separates "never a candidate"
  from "a candidate the pool limit cut off" is a reporting aid, not part of the budget.
  It runs outside this function and outside any timed section.

Budgets are registered in benchmarks/eval/v2-development-queries.md. Changing one
invalidates every comparison measured under it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from nexus_memory.domain.models import SearchQuery

# Registered budgets.
POOL_LIMIT = 20             # distinct eligible memories in the candidate pool
HISTORY_MEMORIES = 5        # candidate memories whose history may be expanded
HISTORY_REVISIONS = 20      # revisions per expanded memory
DELIVERED_ITEMS = 5         # evidence items delivered
DELIVERED_BYTES = 8 * 1024  # 8,192 UTF-8 bytes of text *and* provenance
FUSION_HITS = 40            # T2 only: raw index hits taken in total, 20 per channel
HISTORY_HITS = 20           # T2 only: the historical channel's own half of that

# Registered T2 policies. `baseline` is the pinned `stem` control and spends no
# configuration-search slot; the other three are the predeclared variants. A policy name
# that is not here cannot be run, which is what stops a fourth variant appearing by
# accident. Registered in benchmarks/dev/t2-predeclaration.md, amended once — before any
# variant ran — and never after.
HISTORY_POLICIES = ("baseline", "history_headfirst", "history_paired", "history_cued")

# The history index each policy requires. `none` builds and maintains nothing.
POLICY_HISTORY_PROFILE = {
    "baseline": "none",
    "history_headfirst": "all",
    "history_paired": "all",
    "history_cued": "all",
}

# `history_cued` only. Fixed here before the run: a query asking what something *was*
# may receive a superseded revision beside the head; a query asking what it is *now* may
# not. These are properties of the query text — no label, no grade and no answer id is
# consulted — and the list is English-specific and trivially gameable, which is a stated
# weakness of the variant rather than a hidden one.
PRIOR_CUES = frozenset({"was", "were", "before", "previously", "used", "former", "formerly",
                        "old", "earlier", "originally", "changed", "past", "prior"})
PRESENT_CUES = frozenset({"now", "current", "currently", "today", "latest"})

# Registered T3 selections. `all` is the pinned baseline — deliver the pool in rank order
# until a budget stops it — and spends no configuration-search slot. The other three are
# the predeclared variants. Selection may reorder or reject; it may not change what became
# a candidate, and it never sees a label.
SELECTIONS = ("all", "cutoff_25", "cutoff_40", "dominant_top_2x")
CUTOFF_FRACTION = {"cutoff_25": 0.25, "cutoff_40": 0.40}
DOMINANCE = 2.0             # `dominant_top_2x`: how far ahead the best hit must be


def selected_prefix(ranks: list[float | None], selection: str) -> int:
    """How many of the pool's leading members selection is willing to deliver.

    `ranks` are BM25 scores in pool order, most relevant first and negative — SQLite's
    bm25() returns smaller numbers for better matches, so magnitude is quality. A pool
    with no lexical scores at all (an empty-text query) is not something selection can
    judge, and it is left alone.
    """
    if selection == "all":
        return len(ranks)
    usable = [rank for rank in ranks if rank is not None]
    if len(usable) < 2:
        return len(ranks)
    best = abs(usable[0])
    if best == 0:
        return len(ranks)
    if selection in CUTOFF_FRACTION:
        floor = CUTOFF_FRACTION[selection] * best
        keep = 1
        for rank in usable[1:]:
            if abs(rank) < floor:
                break
            keep += 1
        return keep
    # `dominant_top_2x`: a different shape of judgment. Rather than asking how good a hit
    # is in absolute terms, ask whether the query had one clear winner. If the best hit is
    # at least twice the second, deliver it alone; otherwise the query did not discriminate
    # and nothing here licenses throwing candidates away.
    second = abs(usable[1])
    return 1 if second == 0 or best / second >= DOMINANCE else len(ranks)


def _tokens(text: str) -> set[str]:
    return {token for token in "".join(c.lower() if c.isalnum() else " " for c in text).split()}


def wants_prior_evidence(text: str) -> bool:
    """Whether a query asks about a former state. Label-free by construction."""
    tokens = _tokens(text)
    return bool(tokens & PRIOR_CUES) and not (tokens & PRESENT_CUES)


def provenance_bytes(view) -> int:
    """Serialized provenance delivered alongside an item's content.

    The registered budget counts provenance, so the measured path must serialize it.
    Kept here rather than in either harness so neither can drift from the other.
    """
    blob = json.dumps(
        {"memory_id": view.memory_id, "revision_id": view.revision_id,
         "kind": view.kind, "tags": list(view.tags)},
        separators=(",", ":"),
    )
    return len(blob.encode("utf-8"))


@dataclass
class Retrieval:
    """What the budgeted path produced. No grades, no judgments, no query identity."""

    pool: list[str] = field(default_factory=list)
    pool_provenance: dict[str, str] = field(default_factory=dict)
    pool_from_history: list[str] = field(default_factory=list)
    pool_rank: dict[str, float | None] = field(default_factory=dict)
    selected: int = 0
    paired_revisions: dict[str, str] = field(default_factory=dict)
    history_budget_denied: list[str] = field(default_factory=list)
    revisions_by_memory: dict[str, list[str]] = field(default_factory=dict)
    fusion: dict = field(default_factory=dict)
    expanded: list[str] = field(default_factory=list)
    discovered_revisions: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    delivered_provenance: dict[str, str] = field(default_factory=dict)
    delivered_bytes: int = 0
    delivered_item_bytes: dict[str, int] = field(default_factory=dict)
    history_slots_used: int = 0
    stopped_by: str | None = None


def retrieve(service, text: str, policy: str = "baseline", selection: str = "all") -> Retrieval:
    """Pool, bounded history expansion, bounded delivery — the whole budgeted path.

    This is the function the retrieval gate governs and the only one the performance
    harness times. `policy` selects candidate generation and delivery: `baseline` is
    head-only and is what T1 measured; the three T2 variants add a second channel over
    superseded revisions. The budgets below are shared by every policy, so a variant
    cannot win by spending a different one.

    Label-free throughout: no query id, grade, or known answer reaches this function.
    """
    if policy not in HISTORY_POLICIES:
        raise ValueError(f"unregistered retrieval policy: {policy}")
    if selection not in SELECTIONS:
        raise ValueError(f"unregistered selection: {selection}")
    out = Retrieval()

    # --- channel one: current heads ---------------------------------------------------
    page = service.search(SearchQuery(query=text, limit=POOL_LIMIT))
    for hit in page.hits:
        if hit.memory_id not in out.pool:
            out.pool.append(hit.memory_id)
            out.pool_provenance[hit.memory_id] = hit.revision_id
            out.pool_rank[hit.memory_id] = hit.lexical_rank
    head_memories = set(out.pool)

    # --- channel two: superseded revisions --------------------------------------------
    # Scope and forgotten-status filtering happen inside the query, so a row that would
    # be filtered never occupies one of these slots.
    history_hits = ()
    if POLICY_HISTORY_PROFILE[policy] != "none":
        history_hits = service.search_history(SearchQuery(query=text, limit=HISTORY_HITS))

    collapse = 0
    history_only: list[str] = []
    best_historical: dict[str, str] = {}
    for hit in history_hits:
        if hit.memory_id in best_historical:
            collapse += 1                      # a second revision of a memory already seen
            continue
        best_historical[hit.memory_id] = hit.revision_id
        if hit.memory_id in head_memories:
            collapse += 1                      # the same memory, reached down both channels
        else:
            history_only.append(hit.memory_id)

    # Strict head priority: every head hit in rank order, then history-only memories in
    # theirs. A short channel is never refilled from the other one.
    for memory_id in history_only:
        if len(out.pool) >= POOL_LIMIT:
            break
        out.pool.append(memory_id)
        out.pool_from_history.append(memory_id)
        out.pool_provenance[memory_id] = best_historical[memory_id]

    # `history_paired` and `history_cued` may deliver a superseded revision *beside* the
    # head of a memory both channels matched. `history_cued` does so only when the query
    # asks about a former state.
    paired_allowed = policy in ("history_paired", "history_cued") and (
        policy != "history_cued" or wants_prior_evidence(text)
    )
    if paired_allowed:
        for memory_id in out.pool:
            if memory_id in head_memories and memory_id in best_historical:
                out.paired_revisions[memory_id] = best_historical[memory_id]

    out.fusion = {
        "policy": policy,
        "head_hits": len(page.hits),
        "history_hits": len(history_hits),
        "history_channel": POLICY_HISTORY_PROFILE[policy],
        "duplicate_collapse": collapse,
        "head_truncated": page.cursor is not None,
        "history_truncated": len(history_hits) == HISTORY_HITS,
        "head_shortfall": max(0, POOL_LIMIT - len(page.hits)),
        "history_shortfall": (max(0, HISTORY_HITS - len(history_hits))
                              if POLICY_HISTORY_PROFILE[policy] != "none" else None),
        "pool_from_history": len(out.pool_from_history),
        "paired": len(out.paired_revisions),
    }

    # --- history expansion, five memories deep ----------------------------------------
    # A revision this query actually needs claims a slot before generic expansion does:
    # a memory that reached the pool on a superseded match, and a paired revision that
    # will be delivered beside its head, have already spent history to be here.
    claims: list[str] = list(out.pool_from_history)
    claims += [memory_id for memory_id in out.pool
               if memory_id in out.paired_revisions and memory_id not in claims]
    granted = claims[:HISTORY_MEMORIES]
    out.history_budget_denied = claims[HISTORY_MEMORIES:]
    remaining = HISTORY_MEMORIES - len(granted)
    expandable = [hit.memory_id for hit in page.hits
                  if hit.has_earlier_revisions and hit.memory_id not in granted]
    out.expanded = granted + expandable[:remaining]
    for memory_id in out.expanded:
        # The budget has two dimensions and both bind: at most five distinct memories,
        # and at most twenty revisions of any one of them.
        #
        # The revision dimension binds the **reads**, not the recorded list. A directly
        # matched revision is reachable when it lies outside the twenty most recent, and
        # it consumes that memory's allowance, so its slot is reserved *before*
        # enumeration: nineteen enumerated plus the match is twenty distinct revisions
        # touched. Enumerating twenty and then fetching an older one touches twenty-one,
        # whatever the list is afterwards truncated to.
        matched = (out.pool_provenance.get(memory_id) if memory_id in out.pool_from_history
                   else out.paired_revisions.get(memory_id))
        reserved = 1 if matched is not None else 0
        entries = [entry.revision_id for entry in
                   service.history(memory_id, limit=HISTORY_REVISIONS - reserved).entries]
        if matched is not None and matched not in entries:
            entries = entries + [matched]
        assert len(entries) <= HISTORY_REVISIONS
        out.revisions_by_memory[memory_id] = entries
        out.discovered_revisions.extend(entries)
    out.history_slots_used = len(out.expanded)
    assert out.history_slots_used <= HISTORY_MEMORIES

    # --- selection, then delivery -----------------------------------------------------
    # Selection reads retrieved evidence only — the pool's own BM25 scores — and can only
    # deliver less than the budget allows. It cannot add a candidate, reach past the pool,
    # or consult anything a label touched.
    out.selected = selected_prefix([out.pool_rank.get(memory_id) for memory_id in out.pool],
                                   selection)
    if out.selected < len(out.pool):
        out.stopped_by = "selection"

    denied = set(out.history_budget_denied)
    for position, memory_id in enumerate(out.pool):
        if position >= out.selected:
            break
        if len(out.delivered) >= DELIVERED_ITEMS:
            out.stopped_by = "delivered_item_cap"
            break
        # A memory that entered the pool on a superseded match delivers *that* revision;
        # everything else delivers its head. `revision_id=None` means the head.
        from_history = memory_id in out.pool_from_history
        if from_history and memory_id in denied:
            continue                            # no history slot left to pay for it
        revision_id = out.pool_provenance.get(memory_id) if from_history else None
        if not _deliver(service, out, memory_id, revision_id):
            break
        paired = out.paired_revisions.get(memory_id)
        if paired is not None and memory_id not in denied:
            if len(out.delivered) >= DELIVERED_ITEMS:
                out.stopped_by = "delivered_item_cap"
                break
            if not _deliver(service, out, memory_id, paired, extra=True):
                break

    return out


def _deliver(service, out: Retrieval, memory_id: str, revision_id: str | None,
             extra: bool = False) -> bool:
    """Deliver one item under the byte budget. False means the budget stopped it.

    A memory can be delivered twice — its head and one superseded revision — under the
    paired policies, so items are keyed by the revision actually delivered rather than by
    the memory, and both keys carry their own bytes.
    """
    view = service.get(memory_id, revision_id)
    size = len(view.content.encode("utf-8")) + provenance_bytes(view)
    if out.delivered_bytes + size > DELIVERED_BYTES:
        out.stopped_by = "delivered_byte_cap"
        return False
    key = f"{memory_id}@{view.revision_id}" if extra else memory_id
    out.delivered.append(memory_id if not extra else key)
    out.delivered_provenance[key] = view.revision_id
    out.delivered_bytes += size
    out.delivered_item_bytes[key] = size
    return True
