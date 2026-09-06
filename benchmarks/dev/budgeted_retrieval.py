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
    expanded: list[str] = field(default_factory=list)
    discovered_revisions: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    delivered_bytes: int = 0
    delivered_item_bytes: dict[str, int] = field(default_factory=dict)
    stopped_by: str | None = None


def retrieve(service, text: str) -> Retrieval:
    """Pool, bounded history expansion, bounded delivery — the whole budgeted path.

    This is the function the retrieval gate governs and the only one the performance
    harness times.
    """
    out = Retrieval()

    page = service.search(SearchQuery(query=text, limit=POOL_LIMIT))
    for hit in page.hits:
        if hit.memory_id not in out.pool:
            out.pool.append(hit.memory_id)
            # Head-only search in this build always matches the current revision. The
            # field exists so T2 can record a superseded match without changing the
            # accounting around it.
            out.pool_provenance[hit.memory_id] = hit.revision_id

    # Selection uses retrieved evidence only: rank order, and the has_earlier_revisions
    # flag the search itself reports.
    expandable = [hit.memory_id for hit in page.hits if hit.has_earlier_revisions]
    out.expanded = expandable[:HISTORY_MEMORIES]
    for memory_id in out.expanded:
        history = service.history(memory_id, limit=HISTORY_REVISIONS)
        out.discovered_revisions.extend(entry.revision_id for entry in history.entries)

    for memory_id in out.pool:
        if len(out.delivered) >= DELIVERED_ITEMS:
            out.stopped_by = "delivered_item_cap"
            break
        view = service.get(memory_id)
        size = len(view.content.encode("utf-8")) + provenance_bytes(view)
        if out.delivered_bytes + size > DELIVERED_BYTES:
            out.stopped_by = "delivered_byte_cap"
            break
        out.delivered.append(memory_id)
        out.delivered_bytes += size
        out.delivered_item_bytes[memory_id] = size

    return out
