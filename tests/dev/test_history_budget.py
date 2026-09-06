"""The registered history budget, enforced in the development retrieval path.

The budget has two dimensions and both bind: **at most five distinct memories**, and **at
most twenty revisions of any one of them**. A revision matched directly by the historical
index is reachable when it lies outside the twenty most recent, and it consumes that
memory's allowance — enumerating twenty and then fetching an older one would touch
twenty-one.

The development corpus never saturates either dimension, so these tests build fixtures
that do. This exercises `benchmarks/dev/budgeted_retrieval.py`, not the shipped package.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks" / "dev"
sys.path.insert(0, str(BENCHMARKS))

from budgeted_retrieval import (  # noqa: E402
    HISTORY_MEMORIES, HISTORY_REVISIONS, retrieve,
)
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402


def _service(path: Path) -> MemoryService:
    return MemoryService(
        SQLiteRepository(path, index_profile="stem", history_profile="all"),
        Scope("dev", "local"),
    )


def _memory_with_history(service: MemoryService, key: str, first: str, rest: list[str]) -> str:
    receipt = service.record(MemoryInput(first), f"{key}-0")
    for step, content in enumerate(rest, start=1):
        receipt = service.revise(receipt.memory_id, receipt.revision_id,
                                 MemoryInput(content), f"{key}-{step}")
    return receipt.memory_id


@pytest.fixture
def deep(tmp_path: Path) -> tuple[MemoryService, str]:
    """One memory, 25 revisions, whose distinctive term survives only in the first."""
    service = _service(tmp_path / "memory.sqlite3")
    memory_id = _memory_with_history(
        service, "deep",
        "telemetry came from the beaconcast agent",
        [f"telemetry comes from the metrics agent at {step} percent" for step in range(1, 25)],
    )
    return service, memory_id


def test_a_matched_deep_revision_consumes_the_memorys_revision_allowance(deep) -> None:
    service, memory_id = deep
    listed = [entry.revision_id for entry in service.history(memory_id, limit=HISTORY_REVISIONS).entries]
    matched = service.search_history(SearchQuery(query="beaconcast", limit=20))[0].revision_id
    assert matched not in listed, "the case is only meaningful if enumeration misses it"

    out = retrieve(service, "beaconcast", policy="history_headfirst")

    revisions = out.revisions_by_memory[memory_id]
    assert len(revisions) == HISTORY_REVISIONS, "twenty, not twenty-one"
    assert matched in revisions, "the matched revision is the one that must survive"
    assert listed[-1] not in revisions, "the oldest enumerated revision gives up its place"
    assert len(out.discovered_revisions) == HISTORY_REVISIONS


def test_generic_expansion_never_exceeds_twenty_revisions_for_one_memory(deep) -> None:
    service, memory_id = deep
    out = retrieve(service, "metrics agent percent", policy="history_headfirst")
    assert out.revisions_by_memory[memory_id] == [
        entry.revision_id for entry in service.history(memory_id, limit=HISTORY_REVISIONS).entries]
    assert len(out.revisions_by_memory[memory_id]) == HISTORY_REVISIONS


def test_history_claims_beyond_five_memories_are_denied_and_not_delivered(tmp_path: Path) -> None:
    """Seven memories carry the term only in history; five may be expanded, no more."""
    service = _service(tmp_path / "memory.sqlite3")
    memory_ids = [
        _memory_with_history(service, f"m{index}",
                             f"the {index} pipeline ran on the pipehold buffer",
                             [f"the {index} pipeline runs on the scheduler"])
        for index in range(7)
    ]

    out = retrieve(service, "pipehold", policy="history_headfirst")

    assert len(out.pool_from_history) == 7, "all seven are candidates; the budget binds later"
    assert out.history_slots_used == HISTORY_MEMORIES
    assert len(out.revisions_by_memory) == HISTORY_MEMORIES
    assert len(out.history_budget_denied) == 2
    assert set(out.history_budget_denied) <= set(memory_ids)
    for denied in out.history_budget_denied:
        assert denied not in out.delivered, "a denied claim has no slot to pay for delivery"
    assert len(out.delivered) == HISTORY_MEMORIES
