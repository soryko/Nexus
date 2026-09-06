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


class RecordingService:
    """A `MemoryService` that records the reads the retrieval path actually performs.

    The revision dimension of the budget binds reads, not the list the harness keeps
    afterwards. Asserting on `revisions_by_memory` alone cannot see the difference
    between enumerating nineteen and fetching a twentieth, and enumerating twenty and
    then fetching a twenty-first: both truncate to twenty. These tests therefore assert
    on the calls and on the revision ids those calls returned.
    """

    def __init__(self, service: MemoryService) -> None:
        self._service = service
        self.history_calls: list[tuple[str, int]] = []
        self.get_calls: list[tuple[str, str | None]] = []
        self.revisions_touched: dict[str, set[str]] = {}

    def _touch(self, memory_id: str, revision_id: str) -> None:
        self.revisions_touched.setdefault(memory_id, set()).add(revision_id)

    def history(self, memory_id: str, limit: int = 20, cursor: str | None = None):
        self.history_calls.append((memory_id, limit))
        page = self._service.history(memory_id, limit=limit, cursor=cursor)
        for entry in page.entries:
            self._touch(memory_id, entry.revision_id)
        return page

    def get(self, memory_id: str, revision_id: str | None = None):
        self.get_calls.append((memory_id, revision_id))
        view = self._service.get(memory_id, revision_id)
        self._touch(memory_id, view.revision_id)
        return view

    def search(self, query: SearchQuery):
        return self._service.search(query)

    def search_history(self, query: SearchQuery):
        return self._service.search_history(query)


def test_reads_for_one_memory_never_exceed_twenty_distinct_revisions(deep) -> None:
    """The matched revision's slot is reserved before enumeration, not after it."""
    service, memory_id = deep
    matched = service.search_history(SearchQuery(query="beaconcast", limit=20))[0].revision_id
    listed = [entry.revision_id for entry in service.history(memory_id, limit=HISTORY_REVISIONS).entries]
    assert matched not in listed, "the case is only meaningful if enumeration misses it"

    recorder = RecordingService(service)
    out = retrieve(recorder, "beaconcast", policy="history_headfirst")

    assert recorder.history_calls == [(memory_id, HISTORY_REVISIONS - 1)], \
        "nineteen enumerated, one slot held back for the revision that matched"
    touched = recorder.revisions_touched[memory_id]
    assert len(touched) <= HISTORY_REVISIONS, f"touched {len(touched)} distinct revisions"
    assert matched in touched, "the matched revision is the one the reservation pays for"
    assert set(out.revisions_by_memory[memory_id]) <= touched, \
        "no revision may be reported that was never read"
    # Delivery of the matched revision spends no further revision: it was already read.
    assert (memory_id, matched) in recorder.get_calls
    assert len(touched) == HISTORY_REVISIONS


def test_generic_expansion_reads_twenty_and_holds_nothing_back(deep) -> None:
    """With no matched revision to pay for, the full twenty are enumerated."""
    service, memory_id = deep
    recorder = RecordingService(service)
    retrieve(recorder, "metrics agent percent", policy="history_headfirst")

    assert recorder.history_calls == [(memory_id, HISTORY_REVISIONS)]
    assert len(recorder.revisions_touched[memory_id]) == HISTORY_REVISIONS


def test_no_memory_is_read_beyond_the_budget_across_a_saturated_pool(tmp_path: Path) -> None:
    """Seven history-only memories: five expanded, and none read past twenty revisions."""
    service = _service(tmp_path / "memory.sqlite3")
    for index in range(7):
        _memory_with_history(service, f"m{index}",
                             f"the {index} pipeline ran on the pipehold buffer",
                             [f"the {index} pipeline runs on the scheduler step {step}"
                              for step in range(30)])

    recorder = RecordingService(service)
    out = retrieve(recorder, "pipehold", policy="history_headfirst")

    assert out.history_slots_used == HISTORY_MEMORIES
    assert len({memory_id for memory_id, _ in recorder.history_calls}) == HISTORY_MEMORIES
    for memory_id, touched in recorder.revisions_touched.items():
        assert len(touched) <= HISTORY_REVISIONS, f"{memory_id} read {len(touched)} revisions"
    for memory_id, limit in recorder.history_calls:
        assert limit == HISTORY_REVISIONS - 1, "each expanded memory matched a revision"
