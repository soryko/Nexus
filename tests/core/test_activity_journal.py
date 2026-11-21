"""Tests for the content-free per-scope ActivityJournal and ActivityMiddleware."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from mcp import Client

from nexus_memory.activity import (
    ActivityJournal,
    ActivityMiddleware,
    UnsafeActivityJournal,
    category_for_kind,
    sanitize_detail,
)
from nexus_memory.domain.models import Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_server import create_server


def test_path_for_store_stays_beside_database(tmp_path: Path) -> None:
    db = tmp_path / "0a3f790e.sqlite3"
    assert ActivityJournal.path_for_store(db) == tmp_path / "0a3f790e.sqlite3.activity.sqlite3"


def test_readonly_missing_journal_never_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite3.activity.sqlite3"
    journal = ActivityJournal.open_readonly(path)
    assert journal.list_events(limit=20) == []
    health = journal.health_summary()
    assert health.total_events == 0
    assert health.events_today == 0
    assert health.errors_24h == 0
    assert health.last_event_ts is None
    assert not path.exists()


def test_private_permissions_and_symlink_or_unsafe_mode_refusal(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "store.activity.sqlite3"
    journal = ActivityJournal(path)
    seq = journal.record_event("session_start", "bound", session_id="sess-1")
    assert seq == 1
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            assert (sidecar.stat().st_mode & 0o077) == 0

    # Loosening permissions on the file must fail closed on both read and write.
    path.chmod(0o644)
    with pytest.raises(UnsafeActivityJournal):
        journal.record_event("tool_status", "ok")
    with pytest.raises(UnsafeActivityJournal):
        ActivityJournal.open_readonly(path).list_events()
    path.chmod(0o600)

    # Symlink sidecar must be refused.
    wal = Path(str(path) + "-wal")
    if not wal.exists():
        wal.symlink_to(path)
        with pytest.raises(UnsafeActivityJournal):
            journal.record_event("tool_status", "ok")
        with pytest.raises(UnsafeActivityJournal):
            ActivityJournal.open_readonly(path).health_summary()
        wal.unlink()

    # Symlink main journal file must be refused.
    link = tmp_path / "link.activity.sqlite3"
    link.symlink_to(path)
    with pytest.raises(UnsafeActivityJournal):
        ActivityJournal(link).record_event("tool_status", "ok")
    with pytest.raises(UnsafeActivityJournal):
        ActivityJournal.open_readonly(link).list_events()


def test_allowlist_sanitization_drops_prompts_bodies_queries_and_paths(tmp_path: Path) -> None:
    dirty = {
        "prompt": "secret user prompt text",
        "content": "secret memory body",
        "query": "secret search terms",
        "path": "/Users/soko/secret/file.py",
        "tags": ["secret"],
        "candidate_ids": ["mem-1", "mem-2", "invalid id with spaces", "x" * 100],
        "candidate_count": 2,
        "suppressed_count": 1,
        "memory_id": "mem-1",
        "revision_id": "rev-1",
        "operation": "record",
        "durable_seq": 7,
        "hit_count": 3,
        "error_code": "project_scope_unavailable",
        "outcome_detail": "line1\nline2",
        "duration_ms": 12.3456,
        "recorded_device": 16777232,
        "observed_device": 16777230,
        "bool_as_int": True,
    }
    clean = sanitize_detail(dirty)
    assert set(clean) <= {
        "candidate_ids", "candidate_count", "suppressed_count", "memory_id", "revision_id",
        "operation", "durable_seq", "hit_count", "error_code", "outcome_detail", "duration_ms",
        "recorded_device", "observed_device",
    }
    assert clean["candidate_ids"] == ["mem-1", "mem-2"]
    assert clean["duration_ms"] == 12.35
    assert clean["outcome_detail"] == "line1 line2"
    assert "prompt" not in clean and "content" not in clean and "path" not in clean

    path = tmp_path / "sanitized.activity.sqlite3"
    journal = ActivityJournal(path)
    journal.record_event("recall", "offered", session_id="conv-1", detail=dirty)
    raw = path.read_bytes()
    for forbidden in (b"secret user prompt", b"secret memory body", b"secret search terms", b"/Users/soko"):
        assert forbidden not in raw


def test_ring_retention_category_filters_and_health_summary(tmp_path: Path) -> None:
    path = tmp_path / "ring.activity.sqlite3"
    journal = ActivityJournal(path, max_events=5)
    now = datetime(2026, 11, 21, 14, 0, 0, tzinfo=UTC)
    old_ts = (now - timedelta(hours=30)).isoformat()
    recent_ts = (now - timedelta(hours=1)).isoformat()

    journal.record_event("scope_error", "project_scope_unavailable", detail={"error_code": "project_scope_unavailable"}, ts=old_ts)
    journal.record_event("session_start", "bound", ts=old_ts)
    journal.record_event("recall", "offered", detail={"candidate_count": 2}, ts=recent_ts)
    journal.record_event("tool_get", "ok", detail={"memory_id": "m1"}, ts=recent_ts)
    journal.record_event("tool_record", "committed", detail={"memory_id": "m2", "durable_seq": 1}, ts=recent_ts)
    journal.record_event("runtime_error", "recall_unavailable", detail={"error_code": "recall_unavailable"}, ts=recent_ts)
    journal.record_event("tool_compare_at_head", "Matches at HEAD", ts=recent_ts)

    ro = ActivityJournal.open_readonly(path)
    all_events = ro.list_events(limit=50)
    assert len(all_events) == 5
    assert [e.kind for e in all_events] == [
        "tool_compare_at_head",
        "runtime_error",
        "tool_record",
        "tool_get",
        "recall",
    ]
    assert [e.kind for e in ro.list_events(category="recall")] == ["recall"]
    assert [e.kind for e in ro.list_events(category="write")] == ["tool_record"]
    assert [e.kind for e in ro.list_events(category="error")] == ["runtime_error"]
    assert [e.kind for e in ro.list_events(category="read")] == ["tool_compare_at_head", "tool_get"]
    assert len(ro.list_events(since_seq=all_events[2].seq)) == 2

    health = ro.health_summary(now=now)
    assert health.total_events == 5
    assert health.events_today == 5
    assert health.errors_24h == 1
    assert health.last_error_code == "recall_unavailable"
    assert health.counts_by_category == {"recall": 1, "read": 2, "write": 1, "error": 1}
    assert category_for_kind("tool_get", "not_found") == "error"


def test_wal_concurrent_read_while_writer_holds_transaction(tmp_path: Path) -> None:
    path = tmp_path / "wal.activity.sqlite3"
    journal = ActivityJournal(path)
    journal.record_event("session_start", "bound")

    with closing(sqlite3.connect(path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        events = ActivityJournal.open_readonly(path).list_events()
        assert len(events) == 1
        assert events[0].kind == "session_start"


def test_mcp_server_activity_middleware_logs_opaque_outcomes_only(tmp_path: Path) -> None:
    db_path = tmp_path / "store.sqlite3"
    journal = ActivityJournal(ActivityJournal.path_for_store(db_path))
    service = MemoryService(SQLiteRepository(db_path), Scope("ns-1", "local"))
    server = create_server(service, activity_journal=journal)

    async def scenario() -> None:
        async with Client(server) as client:
            rec = await client.call_tool("record", {
                "content": "sensitive architectural note xyz",
                "idempotency_key": "act-1",
                "kind": "decision",
            })
            assert not rec.is_error
            mid = rec.structured_content["memory_id"]
            got = await client.call_tool("get", {"memory_id": mid})
            assert not got.is_error
            srch = await client.call_tool("search", {"query": "architectural"})
            assert not srch.is_error
            err = await client.call_tool("get", {"memory_id": "missing-id"})
            assert err.is_error

    anyio.run(scenario)

    events = ActivityJournal.open_readonly(journal.path).list_events(limit=20)
    assert [e.kind for e in events] == ["tool_get", "tool_search", "tool_get", "tool_record"]
    assert events[0].category == "error"
    assert events[0].outcome == "not_found"
    assert events[1].detail["hit_count"] == 1
    assert events[3].category == "write"
    assert events[3].outcome == "committed"
    raw = journal.path.read_bytes()
    assert b"sensitive architectural note xyz" not in raw
    assert b"architectural" not in raw
