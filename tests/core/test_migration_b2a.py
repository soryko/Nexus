"""Migration 004 is additive and transactional: tables only, no row rewritten, rollback to 3."""
from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from nexus_memory.storage import SQLiteRepository

TABLES_BEFORE = (
    "blobs", "memories", "revisions", "receipts", "outbox", "head_index", "tags", "head_tags",
    "index_state", "search_profile",
)


def _v3_database(path: Path, memories: int = 3) -> None:
    """A real schema-version-3 database built from the frozen 001-003 scripts."""
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode = WAL")
        for name in ("001_initial.sql", "002_search.sql", "003_search_profile.sql"):
            for statement in files("nexus_memory.storage.migrations").joinpath(name).read_text().split(";"):
                if statement.strip():
                    db.execute(statement)
        db.execute("PRAGMA user_version = 3")
        for number in range(memories):
            body = f"body {number}".encode()
            db.execute("INSERT INTO blobs(namespace,actor,digest,body) VALUES('a','local',?,?)", (number.to_bytes(4, "big"), body))
            blob_id = db.execute("SELECT max(id) FROM blobs").fetchone()[0]
            db.execute("INSERT INTO memories VALUES('a','local',?,?,0)", (f"m-{number}", f"r-{number}"))
            db.execute(
                "INSERT INTO revisions VALUES('a','local',?,?,NULL,?,'observation','[]',NULL,NULL,'2026-01-01T00:00:00+00:00')",
                (f"m-{number}", f"r-{number}", blob_id),
            )
            db.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES('a','local',?,'{}')", (f"op-{number}",))
            seq = db.execute("SELECT max(durable_seq) FROM outbox").fetchone()[0]
            db.execute(
                "INSERT INTO receipts VALUES('a','local',?,?,?,?,?,?,'record',1)",
                (f"key-{number}", f"digest-{number}", f"m-{number}", f"r-{number}", f"op-{number}", seq),
            )
            row = db.execute(
                "INSERT INTO head_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,created_at,durable_seq,has_parent)"
                " VALUES('a','local',?,?,?,'observation','[]','2026-01-01T00:00:00+00:00',?,0)",
                (f"m-{number}", f"r-{number}", blob_id, seq),
            ).lastrowid
            db.execute("INSERT INTO head_fts(rowid,body) VALUES(?,?)", (row, body.decode()))
    finally:
        db.close()


def _dump(path: Path) -> dict[str, list[tuple]]:
    db = sqlite3.connect(path)
    try:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in TABLES_BEFORE}
    finally:
        db.close()


def test_migration_004_creates_tables_only_and_rewrites_no_row(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    _v3_database(path)
    before = _dump(path)

    SQLiteRepository(path)

    assert _dump(path) == before
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"repositories", "repository_checkouts", "revision_references"} <= tables
        for table in ("repositories", "repository_checkouts", "revision_references"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        db.close()


def test_failed_migration_004_rolls_back_to_version_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "memory.sqlite3"
    _v3_database(path)
    before = _dump(path)
    original = SQLiteRepository._script

    def sabotage(name: str) -> str:
        script = original(name)
        if name == "004_repositories.sql":
            script += ";CREATE TABLE repositories(duplicate INTEGER)"  # fails after the real statements ran
        return script

    monkeypatch.setattr(SQLiteRepository, "_script", staticmethod(sabotage))
    with pytest.raises(Exception):
        SQLiteRepository(path)

    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"repositories", "repository_checkouts", "revision_references"} & tables
    finally:
        db.close()
    assert _dump(path) == before
