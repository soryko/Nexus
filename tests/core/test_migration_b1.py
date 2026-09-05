from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_memory.domain.errors import UnsupportedRuntime
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


def _v1_database(path: Path) -> tuple[str, str, str]:
    """Build a real schema-version-1 database using only milestone A's migration."""
    from importlib.resources import files

    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode = WAL")
        script = files("nexus_memory.storage.migrations").joinpath("001_initial.sql").read_text()
        for statement in script.split(";"):
            if statement.strip():
                db.execute(statement)
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO blobs(namespace,actor,digest,body) VALUES('a1','local',x'00',?)",
                   (b"payments retry policy",))
        blob_id = db.execute("SELECT id FROM blobs").fetchone()[0]
        db.execute("INSERT INTO memories VALUES('a1','local','m-1','r-1',0)")
        db.execute(
            "INSERT INTO revisions VALUES('a1','local','m-1','r-1',NULL,?,'decision','[\"payments\"]',NULL,NULL,'2026-01-01T00:00:00+00:00')",
            (blob_id,),
        )
        db.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES('a1','local','op-1','{}')")
        seq = db.execute("SELECT durable_seq FROM outbox").fetchone()[0]
        db.execute(
            "INSERT INTO receipts VALUES('a1','local','original-key',?,'m-1','r-1','op-1',?,'record')",
            (_v1_digest(), seq),
        )
    finally:
        db.close()
    return "m-1", "r-1", "original-key"


def _v1_digest() -> str:
    """The digest milestone A would have computed for this exact request."""
    return MemoryService._digest("record", None, None, MemoryInput("payments retry policy", kind="decision", tags=["payments"]))


def test_migration_upgrades_v1_and_backfills_the_index(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    memory_id, revision_id, _ = _v1_database(path)

    SQLiteRepository(path)

    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        indexed = db.execute("SELECT memory_id,revision_id FROM head_index").fetchall()
        assert indexed == [(memory_id, revision_id)]
        assert db.execute("SELECT count(*) FROM head_fts").fetchone()[0] == 1
        tags = db.execute(
            "SELECT t.tag FROM head_tags ht JOIN tags t ON t.id=ht.tag_id"
        ).fetchall()
        assert tags == [("payments",)]
    finally:
        db.close()


def test_v1_receipt_still_replays_after_migration(tmp_path: Path) -> None:
    """Milestone A's core promise must survive the digest format gaining a version."""
    path = tmp_path / "memory.sqlite3"
    _, _, key = _v1_database(path)
    service = MemoryService(SQLiteRepository(path), Scope("a1", "local"))

    replayed = service.record(MemoryInput("payments retry policy", kind="decision", tags=["payments"]), key)

    assert replayed.memory_id == "m-1"
    assert replayed.revision_id == "r-1"
    assert replayed.operation_id == "op-1"
    assert replayed.operation == "record"


def test_v1_key_with_a_different_request_still_conflicts_after_migration(tmp_path: Path) -> None:
    from nexus_memory.domain.errors import IdempotencyConflict

    path = tmp_path / "memory.sqlite3"
    _, _, key = _v1_database(path)
    service = MemoryService(SQLiteRepository(path), Scope("a1", "local"))

    with pytest.raises(IdempotencyConflict):
        service.record(MemoryInput("a genuinely different body"), key)


def test_backfilled_v1_memory_is_searchable(tmp_path: Path) -> None:
    from nexus_memory.domain.models import SearchQuery

    path = tmp_path / "memory.sqlite3"
    memory_id, _, _ = _v1_database(path)
    service = MemoryService(SQLiteRepository(path), Scope("a1", "local"))

    hits = service.search(SearchQuery(query="payments")).hits
    assert [hit.memory_id for hit in hits] == [memory_id]
    assert {hit.memory_id for hit in service.search(SearchQuery(tags_all=("payments",))).hits} == {memory_id}


def test_failed_migration_leaves_the_database_at_version_one(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "memory.sqlite3"
    _v1_database(path)

    original = SQLiteRepository._apply_migration_002

    def explode(self, connection) -> None:
        original(self, connection)
        raise sqlite3.OperationalError("injected failure part way through migration")

    monkeypatch.setattr(SQLiteRepository, "_apply_migration_002", explode)
    with pytest.raises(Exception):
        SQLiteRepository(path)

    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "head_index" not in tables
        assert db.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    finally:
        db.close()


def test_outbox_events_written_under_v1_keep_their_meaning(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    _v1_database(path)
    before = sqlite3.connect(path)
    try:
        original = before.execute("SELECT durable_seq,operation_id,payload FROM outbox").fetchall()
    finally:
        before.close()

    SQLiteRepository(path)

    after = sqlite3.connect(path)
    try:
        assert after.execute("SELECT durable_seq,operation_id,payload FROM outbox").fetchall() == original
    finally:
        after.close()


def test_fts_capability_is_probed_by_execution(tmp_path: Path, monkeypatch) -> None:
    """Compile options are diagnostic; successful execution is the capability."""
    calls: list[str] = []
    real_probe = SQLiteRepository._probe_fts5

    def spy(self) -> None:
        calls.append("probed")
        real_probe(self)

    monkeypatch.setattr(SQLiteRepository, "_probe_fts5", spy)
    SQLiteRepository(tmp_path / "memory.sqlite3")
    assert calls == ["probed"]


def test_missing_fts_support_refuses_to_start(tmp_path: Path, monkeypatch) -> None:
    def unavailable(self) -> None:
        raise UnsupportedRuntime("SQLite FTS5 support is required")

    monkeypatch.setattr(SQLiteRepository, "_probe_fts5", unavailable)
    with pytest.raises(UnsupportedRuntime):
        SQLiteRepository(tmp_path / "memory.sqlite3")
