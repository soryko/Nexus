"""Content-free per-scope activity and health journal (<store>.activity.sqlite3).

This module keeps a separate sidecar SQLite journal alongside an enrolled Nexus
memory store so operational telemetry (session starts, recalls offered, read/write
outcomes, and scope/runtime errors) is observable without touching the main
memory store schema, receipts, or durable sequences.

Privacy invariant: prompt text, memory bodies, search queries, tags, and
filesystem paths are NEVER accepted or stored here. Only allowlisted event kinds,
bounded opaque identifiers, counts, and error codes pass sanitization.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mcp.server.context import ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from nexus_memory.domain.errors import NexusError

MAX_EVENTS = 5000
MAX_LIST_LIMIT = 200
_SIDECARS = ("", "-wal", "-shm", "-journal")
_ID_LIMIT = 64
_SAFE_OUTCOME_RE = re.compile(r"^[A-Za-z0-9_.: -]{1,64}$")
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_SAFE_OP_ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,64}$")

ALLOWED_KINDS = frozenset({
    "session_start",
    "recall",
    "stop_capture",
    "tool_status",
    "tool_search",
    "tool_get",
    "tool_history",
    "tool_compare_at_head",
    "tool_record",
    "tool_revise",
    "tool_forget",
    "tool_open_atlas",
    "scope_error",
    "runtime_error",
})

ALLOWED_DETAIL_KEYS = frozenset({
    "candidate_ids",
    "candidate_count",
    "suppressed_count",
    "memory_id",
    "revision_id",
    "operation",
    "durable_seq",
    "hit_count",
    "error_code",
    "outcome_detail",
    "duration_ms",
    "recorded_device",
    "observed_device",
})

_NON_ERROR_OUTCOMES = frozenset({
    "ok",
    "offered",
    "suppressed",
    "empty",
    "bound",
    "committed",
    "replayed",
    "nudged",
    "skipped",
    "Matches at HEAD",
    "Changed at HEAD",
    "Missing at HEAD",
    "Cannot check",
})

_INSTRUMENTED_TOOLS = frozenset({
    "status",
    "search",
    "get",
    "history",
    "compare_at_head",
    "record",
    "revise",
    "forget",
    "open_atlas",
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_seq ON events(kind, seq DESC);
"""


class UnsafeActivityJournal(NexusError):
    code = "unsafe_activity_journal"


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    seq: int
    ts: str
    session_id: str | None
    kind: str
    category: str
    outcome: str
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActivityHealth:
    total_events: int
    events_today: int
    errors_24h: int
    last_event_ts: str | None
    last_error_ts: str | None
    last_error_code: str | None
    counts_by_category: dict[str, int]


def category_for_kind(kind: str, outcome: str = "ok") -> str:
    if kind in ("scope_error", "runtime_error") or outcome not in _NON_ERROR_OUTCOMES:
        return "error"
    if kind == "recall":
        return "recall"
    if kind in ("tool_record", "tool_revise", "tool_forget", "stop_capture"):
        return "write"
    return "read"


def _check_private_regular(path: Path) -> None:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UnsafeActivityJournal("activity journal must be a single regular private file")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise UnsafeActivityJournal("activity journal belongs to another user")
    if os.name != "nt" and (info.st_mode & 0o077):
        raise UnsafeActivityJournal("activity journal permissions must be owner-only (0600)")


def _check_existing_sidecars(base: Path) -> None:
    for suffix in _SIDECARS:
        candidate = Path(str(base) + suffix)
        if os.path.lexists(candidate):
            _check_private_regular(candidate)


def _ensure_private_parent(parent: Path) -> None:
    if os.path.lexists(parent):
        if os.path.islink(parent):
            raise UnsafeActivityJournal("activity journal parent directory cannot be a symlink")
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeActivityJournal("activity journal parent is not a directory")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise UnsafeActivityJournal("activity journal parent belongs to another user")
        if os.name != "nt" and (info.st_mode & 0o002):
            raise UnsafeActivityJournal("activity journal parent directory is world-writable")
        return
    missing: list[Path] = []
    cur = parent
    while cur and not os.path.lexists(cur):
        missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    if cur and os.path.lexists(cur):
        _ensure_private_parent(cur)
    for directory in reversed(missing):
        os.makedirs(directory, mode=0o700, exist_ok=True)


def _sanitize_opaque_id(value: object, *, max_len: int = _ID_LIMIT) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_len or not _SAFE_OP_ID_RE.fullmatch(cleaned):
        return None
    return cleaned


def _sanitize_text(value: object, *, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    single_line = " ".join(ch for ch in value.splitlines()).strip()
    printable = "".join(ch for ch in single_line if ch.isprintable())
    if not printable:
        return None
    return printable[:max_len]


def sanitize_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep strictly allowlisted, content-free fields with bounded scalar/ID values."""
    if not detail:
        return {}
    clean: dict[str, Any] = {}
    for key in ALLOWED_DETAIL_KEYS:
        if key not in detail:
            continue
        value = detail[key]
        if key == "candidate_ids":
            if isinstance(value, (list, tuple)):
                ids: list[str] = []
                for item in value[:10]:
                    safe_id = _sanitize_opaque_id(item)
                    if safe_id is not None:
                        ids.append(safe_id)
                clean[key] = ids
        elif key in ("memory_id", "revision_id", "error_code"):
            safe = _sanitize_opaque_id(value)
            if safe is not None:
                clean[key] = safe
        elif key == "operation":
            safe = _sanitize_opaque_id(value, max_len=32)
            if safe is not None:
                clean[key] = safe
        elif key == "outcome_detail":
            safe = _sanitize_text(value, max_len=128)
            if safe is not None:
                clean[key] = safe
        elif key in ("candidate_count", "suppressed_count", "durable_seq", "hit_count", "recorded_device", "observed_device"):
            if type(value) is int:
                clean[key] = value
        elif key == "duration_ms":
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                clean[key] = round(float(value), 2)
    return clean


def _normalize_ts(ts: str | None) -> str:
    if ts is None:
        return datetime.now(UTC).isoformat()
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _parse_ts(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ActivityJournal:
    """Content-free per-scope activity and health journal."""

    def __init__(self, path: Path, *, max_events: int = MAX_EVENTS, read_only: bool = False) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.path = Path(os.path.abspath(path))
        self.max_events = max_events
        self._read_only = read_only

    @classmethod
    def path_for_store(cls, db_path: str | Path) -> Path:
        base = Path(db_path)
        return Path(str(base) + ".activity.sqlite3")

    @classmethod
    def open_readonly(cls, path: str | Path) -> "ActivityJournal":
        return cls(Path(path), read_only=True)

    def _prepare_writable_file(self) -> None:
        if self._read_only:
            raise UnsafeActivityJournal("cannot write through a read-only activity journal handle")
        _ensure_private_parent(self.path.parent)
        _check_existing_sidecars(self.path)
        if not os.path.lexists(self.path):
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags, 0o600)
            except FileExistsError:
                _check_private_regular(self.path)
            else:
                try:
                    if os.name != "nt" and hasattr(os, "fchmod"):
                        os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)

    def _connect_rw(self) -> sqlite3.Connection:
        self._prepare_writable_file()
        conn = sqlite3.connect(str(self.path), timeout=3, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 3000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(_SCHEMA_SQL)
            # Re-verify file permissions after SQLite may have touched -wal/-shm.
            _check_existing_sidecars(self.path)
        except BaseException:
            conn.close()
            raise
        return conn

    def _connect_ro(self) -> sqlite3.Connection | None:
        if not os.path.lexists(self.path):
            return None
        _check_existing_sidecars(self.path)
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3, isolation_level=None)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA busy_timeout = 3000")
        except BaseException:
            conn.close()
            raise
        return conn

    def record_event(
        self,
        kind: str,
        outcome: str = "ok",
        *,
        session_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
        ts: str | None = None,
    ) -> int:
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported activity kind: {kind}")
        safe_outcome = outcome.strip() if isinstance(outcome, str) else ""
        if not _SAFE_OUTCOME_RE.fullmatch(safe_outcome):
            safe_outcome = "error"
        safe_session: str | None = None
        if isinstance(session_id, str) and session_id.strip():
            candidate = session_id.strip()
            if _SAFE_SESSION_RE.fullmatch(candidate):
                safe_session = candidate
        stamp = _normalize_ts(ts)
        payload = json.dumps(sanitize_detail(detail), separators=(",", ":"), sort_keys=True)

        with closing(self._connect_rw()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "INSERT INTO events(ts, session_id, kind, outcome, detail_json) VALUES(?,?,?,?,?)",
                    (stamp, safe_session, kind, safe_outcome, payload),
                )
                seq = int(cur.lastrowid)
                cutoff = conn.execute(
                    "SELECT seq FROM events ORDER BY seq DESC LIMIT 1 OFFSET ?",
                    (self.max_events - 1,),
                ).fetchone()
                if cutoff is not None:
                    conn.execute("DELETE FROM events WHERE seq < ?", (cutoff[0],))
                conn.commit()
                return seq
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def list_events(
        self,
        *,
        limit: int = 50,
        category: str | None = None,
        since_seq: int | None = None,
    ) -> list[ActivityEvent]:
        clamped = max(1, min(int(limit), MAX_LIST_LIMIT))
        conn = self._connect_ro()
        if conn is None:
            return []
        try:
            with closing(conn):
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                ).fetchone():
                    return []
                sql = "SELECT seq, ts, session_id, kind, outcome, detail_json FROM events"
                params: list[Any] = []
                if since_seq is not None and type(since_seq) is int:
                    sql += " WHERE seq > ?"
                    params.append(since_seq)
                sql += " ORDER BY seq DESC"
                if category is None or category == "all":
                    sql += " LIMIT ?"
                    params.append(clamped)
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as error:
            raise UnsafeActivityJournal("activity journal could not be read") from error

        events: list[ActivityEvent] = []
        for seq, ts, session_id, kind, outcome, raw_detail in rows:
            cat = category_for_kind(kind, outcome)
            if category and category != "all" and cat != category:
                continue
            try:
                parsed = json.loads(raw_detail)
                detail = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                detail = {}
            events.append(ActivityEvent(seq, ts, session_id, kind, cat, outcome, detail))
            if len(events) >= clamped:
                break
        return events

    def health_summary(self, *, now: datetime | None = None) -> ActivityHealth:
        ref = (now or datetime.now(UTC)).astimezone(UTC)
        today_date = ref.date()
        window_start = ref - timedelta(hours=24)
        counts = {"recall": 0, "read": 0, "write": 0, "error": 0}
        conn = self._connect_ro()
        if conn is None:
            return ActivityHealth(0, 0, 0, None, None, None, counts)
        try:
            with closing(conn):
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                ).fetchone():
                    return ActivityHealth(0, 0, 0, None, None, None, counts)
                rows = conn.execute(
                    "SELECT seq, ts, kind, outcome, detail_json FROM events ORDER BY seq DESC"
                ).fetchall()
        except sqlite3.OperationalError as error:
            raise UnsafeActivityJournal("activity journal could not be read") from error

        total = len(rows)
        if total == 0:
            return ActivityHealth(0, 0, 0, None, None, None, counts)

        events_today = 0
        errors_24h = 0
        last_event_ts = rows[0][1]
        last_error_ts: str | None = None
        last_error_code: str | None = None

        for _, ts, kind, outcome, raw_detail in rows:
            cat = category_for_kind(kind, outcome)
            counts[cat] = counts.get(cat, 0) + 1
            parsed_ts = _parse_ts(ts)
            if parsed_ts is not None:
                if parsed_ts.date() == today_date:
                    events_today += 1
                if cat == "error" and parsed_ts >= window_start:
                    errors_24h += 1
            elif cat == "error":
                errors_24h += 1
            if cat == "error" and last_error_ts is None:
                last_error_ts = ts
                try:
                    detail = json.loads(raw_detail)
                except ValueError:
                    detail = {}
                last_error_code = detail.get("error_code") if isinstance(detail, dict) else None
                if not isinstance(last_error_code, str) or not last_error_code:
                    last_error_code = outcome

        return ActivityHealth(
            total_events=total,
            events_today=events_today,
            errors_24h=errors_24h,
            last_event_ts=last_event_ts,
            last_error_ts=last_error_ts,
            last_error_code=last_error_code,
            counts_by_category=counts,
        )


def _error_code_from_text(text: str) -> str:
    prefix, sep, _ = text.partition(":")
    candidate = prefix.strip() if sep else ""
    if _SAFE_OP_ID_RE.fullmatch(candidate):
        return candidate
    return "internal_error"


def _structured_dict(result: Any) -> dict[str, Any] | None:
    if isinstance(result, CallToolResult):
        return dict(result.structured_content) if isinstance(result.structured_content, Mapping) else None
    if isinstance(result, BaseModel):
        return result.model_dump()
    if isinstance(result, Mapping):
        structured = result.get("structuredContent")
        if isinstance(structured, Mapping):
            return dict(structured)
        return dict(result)
    return None


def _is_error_result(result: Any) -> tuple[bool, str | None]:
    if isinstance(result, CallToolResult) and result.is_error:
        texts = [item.text for item in result.content if isinstance(item, TextContent)]
        return True, _error_code_from_text(texts[0]) if texts else "internal_error"
    if isinstance(result, Mapping) and result.get("isError"):
        items = result.get("content", ())
        first = items[0].get("text", "") if items and isinstance(items[0], Mapping) else ""
        return True, _error_code_from_text(str(first))
    return False, None


class ActivityMiddleware:
    """MCP middleware recording content-free tool outcomes to an ActivityJournal."""

    def __init__(self, journal: ActivityJournal | Callable[[], ActivityJournal | None]) -> None:
        self.journal = journal

    def _resolve_journal(self) -> ActivityJournal | None:
        if callable(self.journal):
            try:
                return self.journal()
            except Exception:
                return None
        return self.journal

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[BaseModel | dict[str, Any] | None]],
    ) -> BaseModel | dict[str, Any] | None:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params
        tool_name = params.get("name") if isinstance(params, Mapping) else None
        if not isinstance(tool_name, str) or tool_name not in _INSTRUMENTED_TOOLS:
            return await call_next(ctx)

        kind = f"tool_{tool_name}"
        started = time.monotonic()
        try:
            result = await call_next(ctx)
        except Exception:
            duration_ms = (time.monotonic() - started) * 1000.0
            self._safe_record(kind, "internal_error", {"error_code": "internal_error", "duration_ms": duration_ms})
            raise

        duration_ms = (time.monotonic() - started) * 1000.0
        is_err, error_code = _is_error_result(result)
        if is_err:
            code = error_code or "internal_error"
            self._safe_record(kind, code, {"error_code": code, "duration_ms": duration_ms})
            return result

        structured = _structured_dict(result) or {}
        outcome = "ok"
        detail: dict[str, Any] = {"duration_ms": duration_ms}
        if tool_name in ("record", "revise", "forget"):
            outcome = "committed"
            for field in ("memory_id", "revision_id", "operation", "durable_seq"):
                if field in structured:
                    detail[field] = structured[field]
        elif tool_name == "get":
            for field in ("memory_id", "revision_id"):
                if field in structured:
                    detail[field] = structured[field]
        elif tool_name == "search":
            hits = structured.get("hits")
            if isinstance(hits, list):
                detail["hit_count"] = len(hits)
        elif tool_name == "compare_at_head":
            head_outcome = structured.get("outcome")
            if isinstance(head_outcome, str) and head_outcome in _NON_ERROR_OUTCOMES:
                outcome = head_outcome

        self._safe_record(kind, outcome, detail)
        return result

    def _safe_record(self, kind: str, outcome: str, detail: dict[str, Any]) -> None:
        journal = self._resolve_journal()
        if journal is None:
            return
        try:
            journal.record_event(kind, outcome, detail=detail)
        except Exception:
            pass
