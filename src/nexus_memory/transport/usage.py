"""Opt-in usage log: one JSON line per tool call, so use can be measured without reading transcripts.

It records which tool ran, how it ended, how long it took, which client asked, and the opaque
memory identifiers a *successful* call returned. It never records arguments, memory bodies,
queries, tags, references or paths, and it never reads the database. Each server process gets
a random instance identifier; that identifies a server lifetime, not a conversation, because
one client process may serve several conversations and one conversation may restart its server.

The destination is checked once at startup and refused if it could corrupt the store. After
that the log fails open: a write that fails is counted, never raised, so the tool call it
describes still succeeds. `status` reports the counters, so a log that stopped writing reads
as failing rather than as silence.
"""
from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.context import ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from nexus_memory.domain.errors import NexusError

FORMAT_VERSION = 1
#: The tools this build serves. Any other name is logged as "unknown", so a caller cannot write
#: arbitrary text into the log through the tool name.
KNOWN_TOOLS = frozenset({"record", "get", "revise", "forget", "search", "history", "status"})
#: Search can return 100 hits; logging every identifier would make one line several kilobytes.
MAX_HIT_IDS = 20
_CLIENT_FIELD_LIMIT = 64
_DATABASE_SUFFIXES = ("", "-wal", "-shm", "-journal")


class UnsafeUsageLog(Exception):
    """The usage-log destination could damage the store or is not a plain private file."""


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    printable = "".join(character for character in value if character.isprintable())
    return printable[:_CLIENT_FIELD_LIMIT] or None


def _open_destination(path: Path, database: Path) -> int:
    target = Path(os.path.abspath(path))
    if target.is_symlink():
        raise UnsafeUsageLog("usage log path is a symbolic link")
    resolved = target.resolve()
    database_resolved = Path(os.path.abspath(database)).resolve()
    forbidden = {Path(str(database_resolved) + suffix) for suffix in _DATABASE_SUFFIXES}
    forbidden |= {Path(str(Path(os.path.abspath(database))) + suffix) for suffix in _DATABASE_SUFFIXES}
    if resolved in forbidden or target in forbidden:
        raise UnsafeUsageLog("usage log path is a database file")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as error:
        raise UnsafeUsageLog("usage log path cannot be opened for appending") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise UnsafeUsageLog("usage log path is not a regular file")
    try:
        database_info = os.stat(database_resolved)
    except OSError:
        database_info = None
    if database_info is not None and (info.st_dev, info.st_ino) == (database_info.st_dev, database_info.st_ino):
        os.close(descriptor)
        raise UnsafeUsageLog("usage log path is a database file")
    return descriptor


class UsageLog:
    def __init__(self, path: Path, database: Path) -> None:
        self._descriptor = _open_destination(path, database)
        self.instance = uuid.uuid4().hex
        self.events_written = 0
        self.write_failures = 0

    def write(self, event: dict[str, Any]) -> None:
        line = json.dumps(
            {"v": FORMAT_VERSION, "ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "instance": self.instance, **event},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        try:
            # One write per line: with O_APPEND each call lands at the end of the file as a unit,
            # so concurrent servers sharing a log do not interleave inside a line.
            data = (line + "\n").encode("ascii")
            if os.write(self._descriptor, data) != len(data):
                raise OSError("short write")
        except OSError:
            self.write_failures += 1
        else:
            self.events_written += 1

    def close(self) -> None:
        try:
            os.close(self._descriptor)
        except OSError:
            pass


def _structured(result: object) -> Mapping[str, Any] | None:
    if isinstance(result, CallToolResult):
        content = result.structured_content
    elif isinstance(result, Mapping):
        content = result.get("structuredContent", result.get("structured_content"))
    elif isinstance(result, BaseModel):
        content = getattr(result, "structured_content", None)
    else:
        content = None
    return content if isinstance(content, Mapping) else None


def _is_error(result: object) -> bool:
    if isinstance(result, CallToolResult):
        return bool(result.is_error)
    if isinstance(result, Mapping):
        return bool(result.get("isError") or result.get("is_error"))
    return bool(getattr(result, "is_error", False))


def _error_text(result: object) -> str:
    if isinstance(result, CallToolResult):
        texts = [item.text for item in result.content if isinstance(item, TextContent)]
    elif isinstance(result, Mapping):
        texts = [str(item.get("text", "")) for item in result.get("content", []) if isinstance(item, Mapping)]
    else:
        texts = []
    return texts[0] if texts else ""


def _known_codes() -> frozenset[str]:
    codes, pending = set(), [NexusError]
    while pending:
        error_type = pending.pop()
        codes.add(error_type.code)
        pending.extend(error_type.__subclasses__())
    return frozenset(codes | {"internal_error", "invalid_input"})


def _outcome(result: object) -> str:
    if not _is_error(result):
        return "ok"
    text = _error_text(result)
    codes = _known_codes()
    code = text.split(":", 1)[0]
    # Only a known code is logged; anything else would be text this log promised not to keep.
    return code if code in codes else "error"


def _identifiers(tool: str, content: Mapping[str, Any]) -> dict[str, Any]:
    if tool == "search":
        hits = content.get("hits")
        if not isinstance(hits, list):
            return {}
        ids = [hit.get("memory_id") for hit in hits if isinstance(hit, Mapping) and isinstance(hit.get("memory_id"), str)]
        return {"hits": len(hits), "hit_ids": ids[:MAX_HIT_IDS], "hit_ids_truncated": len(ids) > MAX_HIT_IDS}
    if tool in {"record", "get", "revise", "forget", "history"}:
        memory_id = content.get("memory_id")
        return {"memory_id": memory_id} if isinstance(memory_id, str) else {}
    return {}


def _client(ctx: ServerRequestContext[Any, Any]) -> dict[str, str | None] | None:
    try:
        params = ctx.session.client_params
        info = getattr(params, "client_info", None)
    except Exception:
        return None
    if info is None:
        return None
    return {"name": _clean(getattr(info, "name", None)), "version": _clean(getattr(info, "version", None))}


class UsageMiddleware:
    """Outermost middleware: it sees the result after error text has already been made safe."""

    def __init__(self, log: UsageLog) -> None:
        self.log = log

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[Any]],
    ) -> Any:
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params
        name = params.get("name") if isinstance(params, Mapping) else None
        tool = name if isinstance(name, str) and name in KNOWN_TOOLS else "unknown"
        started = time.monotonic()
        try:
            result = await call_next(ctx)
        except BaseException:
            # Cancellation and anything the inner middleware did not convert: the call did not
            # complete, which is its own outcome, and the exception continues unchanged.
            self._call(ctx, tool, "cancelled", started, {})
            raise
        outcome = _outcome(result)
        content = _structured(result) if outcome == "ok" else None
        self._call(ctx, tool, outcome, started, _identifiers(tool, content) if content is not None else {})
        return result

    def _call(self, ctx: ServerRequestContext[Any, Any], tool: str, outcome: str, started: float, ids: dict[str, Any]) -> None:
        self.log.write({
            "event": "call",
            "tool": tool,
            "outcome": outcome,
            "ms": round((time.monotonic() - started) * 1000),
            "client": _client(ctx),
            **ids,
        })
