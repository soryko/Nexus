import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "retire_legacy_store.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("retire_store", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def retire(database, archive):
    return subprocess.run([sys.executable, str(SCRIPT), "--database", str(database),
                           "--archive-dir", str(archive)], capture_output=True, text=True, timeout=15)


def test_retirement_fences_original_inode_and_is_repeatable(tmp_path):
    database = tmp_path / "memory.sqlite3"
    archive = tmp_path / "recovery"
    service = MemoryService(SQLiteRepository(database), Scope("api", "local"))
    receipt = service.record(MemoryInput("legacy original"), "one")
    inode = database.stat().st_ino
    result = retire(database, archive)
    assert result.returncode == 0, result.stderr
    original = Path(result.stdout.strip())
    assert original.parent == archive and database.stat().st_ino == inode
    assert os.stat(archive).st_mode & 0o077 == 0 and database.is_file()
    with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as reader:
        assert reader.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert reader.execute("SELECT memory_id FROM memories").fetchone()[0] == receipt.memory_id
    assert retire(database, archive).stdout == result.stdout


def test_active_sqlite_reader_prevents_exchange(tmp_path):
    database = tmp_path / "memory.sqlite3"
    archive = tmp_path / "recovery"
    SQLiteRepository(database)
    reader = sqlite3.connect(database)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM memories").fetchone()
        assert retire(database, archive).returncode != 0
        assert database.is_file()
        assert not list(archive.glob("*.sqlite3"))
    finally:
        reader.close()
    assert retire(database, archive).returncode == 0


def test_wrong_database_is_not_retired(tmp_path):
    database = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated(value)")
    assert retire(database, tmp_path / "recovery").returncode != 0
    assert database.is_file()


def test_competing_writer_cannot_commit_after_archive_copy(tmp_path, monkeypatch):
    module = _load_tool()
    database = tmp_path / "memory.sqlite3"
    service = MemoryService(SQLiteRepository(database), Scope("api", "local"))
    service.record(MemoryInput("before"), "example")
    original = module._copy_archive

    def copy_then_attempt(*args):
        checksum = original(*args)
        code = """import sqlite3, sys
db = sqlite3.connect(sys.argv[1], timeout=0.1)
try:
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE committed_after_copy(value)")
    db.commit()
except sqlite3.OperationalError as e:
    if 'locked' not in str(e): raise
    sys.exit(0)
sys.exit(1)
"""
        writer = subprocess.run([sys.executable, "-c", code, str(database)], timeout=5)
        assert writer.returncode == 0, "writer acquired the supposedly exclusive SQLite lock"
        return checksum

    monkeypatch.setattr(module, "_copy_archive", copy_then_attempt)
    module.retire(database, tmp_path / "recovery")


def test_failed_archive_validation_leaves_legacy_readable_and_no_published_archive(tmp_path, monkeypatch):
    module = _load_tool()
    database = tmp_path / "memory.sqlite3"
    service = MemoryService(SQLiteRepository(database), Scope("api", "local"))
    receipt = service.record(MemoryInput("recoverable"), "example")
    monkeypatch.setattr(module, "_digest", lambda _: "bad-checksum")
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.retire(database, tmp_path / "recovery")
    assert service.get(receipt.memory_id).content == "recoverable"
    assert list((tmp_path / "recovery").iterdir()) == []
