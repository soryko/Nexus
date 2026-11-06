from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_memory.atlas.binding import BindingUnavailable, open_binding
from nexus_memory.domain.errors import NexusError
from nexus_memory.domain.models import MemoryInput, SearchQuery
from nexus_memory.storage import SQLiteRepository

from conftest import RETIRE, git


def _digest(*paths: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}


def _sidecars(database: Path) -> list[Path]:
    return [database, *(Path(str(database) + s) for s in ("-wal", "-shm"))]


def _refused(callable_) -> bool:
    try:
        callable_()
    except (BindingUnavailable, NexusError):
        return True
    return False


def test_unenrolled_checkout_is_refused(enrolled) -> None:
    other = enrolled.new_checkout("other")
    with pytest.raises(BindingUnavailable):
        open_binding(other, enrolled.resolve)


def test_relative_checkout_is_refused(enrolled) -> None:
    with pytest.raises(BindingUnavailable):
        open_binding(Path("repo"), enrolled.resolve)


def test_absent_store_is_refused(enrolled) -> None:
    other = enrolled.new_checkout("fresh")
    enrolled.scope_module.enroll(other)  # enrolled, but no store was ever written
    with pytest.raises(BindingUnavailable):
        open_binding(other, enrolled.resolve)


@pytest.mark.parametrize("version", [3, 5, 99])
def test_unsupported_schema_is_refused_without_migrating(enrolled, version: int) -> None:
    other = enrolled.new_checkout("old")
    project_id = enrolled.scope_module.enroll(other)
    database = enrolled.resolve(other).database
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE marker(x)")
        db.execute(f"PRAGMA user_version={version}")
    before = _digest(database)
    with pytest.raises(NexusError):
        open_binding(other, enrolled.resolve)
    assert _digest(database) == before
    assert project_id


def test_linked_worktree_binds_to_the_main_checkouts_project(enrolled, tmp_path) -> None:
    worktree = tmp_path / "linked"
    git(enrolled.checkout, "worktree", "add", "-q", str(worktree))
    binding = open_binding(worktree.resolve(), enrolled.resolve)
    assert binding.project_id == enrolled.project_id
    assert binding.scope == enrolled.scope


def test_reader_sees_committed_wal_data_and_writes_nothing(enrolled, monkeypatch) -> None:
    # A live connection stops the last-close checkpoint, so the data stays in the WAL.
    keeper = sqlite3.connect(enrolled.database)
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    keeper.execute("SELECT count(*) FROM memories").fetchone()
    memory_id = enrolled.record("Committed in the write-ahead log only", "k-wal", kind="observation")
    wal = Path(str(enrolled.database) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0

    calls: list[str] = []
    for name in ("_migrate", "_apply_profile", "_apply_history_profile", "_backfill_index"):
        original = getattr(SQLiteRepository, name)

        def spy(self, *a, _name=name, _original=original, **k):
            calls.append(_name)
            return _original(self, *a, **k)

        monkeypatch.setattr(SQLiteRepository, name, spy)

    binding = open_binding(enrolled.checkout, enrolled.resolve)
    before = _digest(*_sidecars(enrolled.database))
    stats = {p: p.stat().st_mtime_ns for p in _sidecars(enrolled.database) if p.exists()}
    reader = binding.reader()
    view = reader.get(binding.scope, memory_id)
    assert view.content == "Committed in the write-ahead log only"
    assert reader.search(binding.scope, SearchQuery(query="write-ahead log", limit=5)).hits
    assert calls == []
    assert _digest(*_sidecars(enrolled.database)) == before
    assert {p: p.stat().st_mtime_ns for p in stats} == stats
    keeper.close()


def test_reader_connection_cannot_write(enrolled) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    reader = binding.reader()
    connection = reader._connect()
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO blobs(body) VALUES (x'00')")
    connection.close()
    with pytest.raises(Exception):
        reader.record(binding.scope, MemoryInput("must not be written"), "k-no", "digest")


def test_reader_adopts_the_stored_profiles_without_rebuilding(enrolled) -> None:
    enrolled.writer(index_profile="stem")  # database now carries the stem profile
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    reader = binding.reader()
    assert reader.index_profile == "stem"
    assert reader.history_profile == "none"
    with sqlite3.connect(f"file:{enrolled.database}?mode=ro", uri=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert "history_profile" not in names


def test_store_replaced_at_the_same_path_is_refused(enrolled) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    binding.reader()
    copy = enrolled.database.with_suffix(".copy")
    shutil.copyfile(enrolled.database, copy)
    os.chmod(copy, 0o600)
    os.replace(copy, enrolled.database)
    with pytest.raises(BindingUnavailable):
        binding.reader()


def test_retired_store_is_refused_even_though_the_inode_is_unchanged(enrolled, tmp_path) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    binding.reader()
    archive = tmp_path / "recovery"
    archive.mkdir(mode=0o700)
    subprocess.run([sys.executable, "-I", str(RETIRE), "--database", str(enrolled.database),
                    "--archive-dir", str(archive)], check=True, capture_output=True)
    with pytest.raises(NexusError):
        binding.reader()
    with pytest.raises(NexusError):
        open_binding(enrolled.checkout, enrolled.resolve)


def test_enrolling_another_project_does_not_invalidate_the_session(enrolled) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    binding.reader()
    enrolled.scope_module.enroll(enrolled.new_checkout("neighbour"))  # rewrites registry.json
    assert binding.reader().status(binding.scope)


def _swap_database(e):
    copy = e.database.with_suffix(".copy")
    shutil.copyfile(e.database, copy)
    os.chmod(copy, 0o600)
    os.replace(copy, e.database)


def _drop_from_registry(e):
    registry = e.database.parent / "registry.json"
    data = json.loads(registry.read_text())
    data["projects"].clear()
    temporary = registry.with_suffix(".new")
    temporary.write_text(json.dumps(data))
    os.chmod(temporary, 0o600)
    os.replace(temporary, registry)


def _replace_marker(e):
    # The old directory stays alive while the new one is made, so the two cannot share an inode.
    # Without a birth time (Linux), an immediate inode reuse is indistinguishable; see the plan's known limits.
    old, parked = e.checkout / ".git", e.checkout / ".git-parked"
    old.rename(parked)
    git(e.checkout, "init", "-q")
    shutil.rmtree(parked)


def _move_checkout(e):
    e.checkout.rename(e.checkout.with_name("moved"))


def _retire(e):
    archive = e.home.parent / "recovery"
    archive.mkdir(mode=0o700)
    subprocess.run([sys.executable, "-I", str(RETIRE), "--database", str(e.database),
                    "--archive-dir", str(archive)], check=True, capture_output=True)


@pytest.mark.parametrize("mutate", [_swap_database, _drop_from_registry, _replace_marker,
                                    _move_checkout, _retire], ids=lambda f: f.__name__.strip("_"))
def test_quick_and_full_checks_agree_on_every_mutation(enrolled, mutate) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    binding.reader()
    mutate(enrolled)
    assert _refused(binding.reader), "the per-request check missed the mutation"
    assert _refused(binding.full_check), "a fresh full resolve accepted the mutation"


def test_pruned_linked_worktree_is_refused(enrolled, tmp_path) -> None:
    worktree = (tmp_path / "linked")
    git(enrolled.checkout, "worktree", "add", "-q", str(worktree))
    binding = open_binding(worktree.resolve(), enrolled.resolve)
    binding.reader()
    git(enrolled.checkout, "worktree", "remove", "--force", str(worktree))
    assert _refused(binding.reader)
    assert _refused(binding.full_check)


def test_quick_check_never_runs_git(enrolled, monkeypatch) -> None:
    binding = open_binding(enrolled.checkout, enrolled.resolve)
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    binding.reader()
    binding.check()
    assert calls == []


def test_a_store_with_an_unrepaired_recency_order_is_refused_not_misordered(enrolled) -> None:
    enrolled.record("A second memory", "k-two", kind="observation")
    with sqlite3.connect(enrolled.database) as db:
        db.execute("DROP TABLE IF EXISTS data_repairs")
        db.execute("UPDATE head_index SET durable_seq=0")
    with pytest.raises(NexusError, match="pending upgrade"):
        open_binding(enrolled.checkout, enrolled.resolve)
    enrolled.writer()                                   # a normal open performs the repair
    assert open_binding(enrolled.checkout, enrolled.resolve).reader().status(enrolled.scope)
