"""The T2 historical channel: candidate generation over superseded revisions.

The shipped default builds none of this. These tests fix what the optional profile must
do — and, more importantly, what it must never do: surface content from a forgotten
memory, or answer at all when its index is empty.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


def _service(path: Path, history_profile: str = "all", namespace: str = "b1") -> MemoryService:
    return MemoryService(
        SQLiteRepository(path, index_profile="stem", history_profile=history_profile),
        Scope(namespace, "local"),
    )


def _superseded(service: MemoryService, old: str, new: str, key: str) -> tuple[str, str, str]:
    """A memory whose first revision says `old` and whose head says `new`."""
    first = service.record(MemoryInput(old), f"{key}-0")
    second = service.revise(first.memory_id, first.revision_id, MemoryInput(new), f"{key}-1")
    return first.memory_id, first.revision_id, second.revision_id


def _rows(path: Path, table: str) -> int:
    db = sqlite3.connect(path)
    try:
        exists = db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if exists else -1
    finally:
        db.close()


def test_default_profile_builds_no_historical_structure(tmp_path: Path) -> None:
    """The shipped default pays nothing for a feature it does not use."""
    path = tmp_path / "memory.sqlite3"
    service = MemoryService(SQLiteRepository(path), Scope("b1", "local"))
    _superseded(service, "the orchestrator is called flowreel", "the orchestrator is called assetline", "k1")

    assert _rows(path, "revision_fts") == -1
    assert _rows(path, "revision_index") == -1
    assert _rows(path, "history_profile") == -1
    assert service.search_history(SearchQuery(query="flowreel", limit=20)) == ()


def test_historical_hit_carries_the_matched_revision_and_its_live_head(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    memory_id, old_revision, head = _superseded(
        service, "the orchestrator is called flowreel", "the orchestrator is called assetline", "k1")

    assert service.search(SearchQuery(query="flowreel", limit=20)).hits == ()
    hits = service.search_history(SearchQuery(query="flowreel", limit=20))
    assert len(hits) == 1
    assert (hits[0].memory_id, hits[0].revision_id, hits[0].current_revision_id) == (
        memory_id, old_revision, head)


def test_history_reaches_a_revision_outside_the_twenty_most_recent(tmp_path: Path) -> None:
    """Direct match, not enumeration: the twenty-revision cap bounds listing history."""
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    receipt = service.record(MemoryInput("telemetry came from the beaconcast agent"), "k-0")
    for step in range(1, 25):
        receipt = service.revise(receipt.memory_id, receipt.revision_id,
                                 MemoryInput(f"telemetry comes from the metrics agent at {step} percent"),
                                 f"k-{step}")

    listed = {entry.revision_id for entry in service.history(receipt.memory_id, limit=20).entries}
    hits = service.search_history(SearchQuery(query="beaconcast", limit=20))
    assert len(hits) == 1
    assert hits[0].revision_id not in listed, "the case is only meaningful if listing misses it"
    assert service.get(hits[0].memory_id, hits[0].revision_id).content.startswith("telemetry came from")


def test_window_profile_keeps_only_the_recent_window(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path, history_profile="window3")
    receipt = service.record(MemoryInput("telemetry came from the beaconcast agent"), "k-0")
    for step in range(1, 6):
        receipt = service.revise(receipt.memory_id, receipt.revision_id,
                                 MemoryInput(f"telemetry comes from the metrics agent at {step} percent"),
                                 f"k-{step}")

    assert _rows(path, "revision_index") == 3
    assert service.search_history(SearchQuery(query="beaconcast", limit=20)) == ()
    assert service.search_history(SearchQuery(query="metrics", limit=20))


def test_forgetting_removes_a_memory_from_the_historical_index(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    memory_id, _, head = _superseded(
        service, "jobs were held in the pipehold buffer", "jobs are queued by the scheduler", "k1")
    assert service.search_history(SearchQuery(query="pipehold", limit=20))

    service.forget(memory_id, head, "forget-1")

    assert _rows(path, "revision_index") == 0
    assert service.search_history(SearchQuery(query="pipehold", limit=20)) == ()


def test_stale_historical_row_cannot_surface_forgotten_content(tmp_path: Path) -> None:
    """Deletion on forget is one guard; the eligibility join is the other."""
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    memory_id, old_revision, head = _superseded(
        service, "jobs were held in the pipehold buffer", "jobs are queued by the scheduler", "k1")
    service.forget(memory_id, head, "forget-1")

    db = sqlite3.connect(path)
    try:  # forge the row that deletion removed, pointing at the tombstoned memory
        db.execute(
            "INSERT INTO revision_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,created_at)"
            " SELECT ?,?,?,?,blob_id,kind,tags_json,created_at FROM revisions WHERE revision_id=?",
            ("b1", "local", memory_id, old_revision, old_revision),
        )
        seq = db.execute("SELECT seq FROM revision_index WHERE revision_id=?", (old_revision,)).fetchone()[0]
        db.execute("INSERT INTO revision_fts(rowid,body) VALUES(?,?)",
                   (seq, "jobs were held in the pipehold buffer"))
        db.commit()
    finally:
        db.close()

    assert service.search_history(SearchQuery(query="pipehold", limit=20)) == ()


def test_negative_control_empty_indexes_include_the_historical_one(tmp_path: Path) -> None:
    """From T2 the control must clear *every* candidate-generating index.

    Clearing the head index alone would leave a variant answering entirely from history
    while the control still passed, which would prove nothing.
    """
    path = tmp_path / "memory.sqlite3"
    service = _service(path)
    _superseded(service, "the orchestrator is called flowreel", "the orchestrator is called assetline", "k1")
    assert service.search(SearchQuery(query="assetline", limit=20)).hits
    assert service.search_history(SearchQuery(query="flowreel", limit=20))

    db = sqlite3.connect(path)
    try:
        for table in ("head_fts", "head_tags", "head_index", "revision_fts", "revision_index"):
            db.execute(f"DELETE FROM {table}")
        db.commit()
        heads = db.execute("SELECT count(*) FROM memories WHERE tombstoned=0").fetchone()[0]
        revisions = db.execute("SELECT count(*) FROM revisions").fetchone()[0]
    finally:
        db.close()

    assert heads and revisions, "authoritative data must still exist for this control to mean anything"
    assert service.search(SearchQuery(query="assetline", limit=20)).hits == ()
    assert service.search_history(SearchQuery(query="flowreel", limit=20)) == ()


def test_switching_the_history_profile_rebuilds_the_index(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    service = MemoryService(SQLiteRepository(path, index_profile="stem"), Scope("b1", "local"))
    _superseded(service, "the orchestrator is called flowreel", "the orchestrator is called assetline", "k1")
    assert _rows(path, "revision_index") == -1

    reopened = _service(path)
    assert len(reopened.search_history(SearchQuery(query="flowreel", limit=20))) == 1

    closed = MemoryService(SQLiteRepository(path, index_profile="stem"), Scope("b1", "local"))
    assert _rows(path, "revision_index") == -1
    assert closed.search_history(SearchQuery(query="flowreel", limit=20)) == ()
