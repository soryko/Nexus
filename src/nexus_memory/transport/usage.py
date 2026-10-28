"""Opt-in usage log: one JSON line per tool call, so use can be measured without reading transcripts.

It records which tool ran, how it ended, how long it took, which client asked, and the opaque
identifiers a *successful* call returned. It never records arguments, memory bodies, queries,
tags, references or paths, and it never reads the database. Each server process gets a random
instance identifier; that identifies a server lifetime, not a conversation, because one client
process may serve several conversations and one conversation may restart its server.

A pilot reads "no use" from this file, so the file has to show its own gaps. Every line carries a
per-instance ``seq`` that counts write *attempts*: a missing number is a lost line. An instance
opens with a ``start`` event and, when it shuts down cleanly, closes with a ``stop`` event that
carries its counters. A log moved or deleted while a server runs is re-created and a
``reopened`` event marks the join.

The destination is checked before the store is opened and refused if it could damage the store.
After that the log fails open: a write that fails is counted, never raised, so the tool call it
describes still succeeds, and ``status`` reports the failure.
"""
from __future__ import annotations

import json
import os
import stat
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import anyio.lowlevel
from mcp.server.context import ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from nexus_memory.domain.errors import NexusError

try:  # POSIX only; on Windows a FIFO cannot be named as a file path anyway.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

try:  # Newer protocol versions carry client info per request, in params._meta, not only at initialize.
    from mcp_types import CLIENT_INFO_META_KEY
except ImportError:  # pragma: no cover - older SDKs have no per-request envelope
    CLIENT_INFO_META_KEY = None

FORMAT_VERSION = 1
#: The tools this build serves. Any other name is logged as "unknown", so a caller cannot write
#: arbitrary text into the log through the tool name.
KNOWN_TOOLS = frozenset({"record", "get", "revise", "forget", "search", "history", "status"})
#: Search can return 100 hits; logging every identifier would make one line several kilobytes.
MAX_HIT_IDS = 20
_CLIENT_FIELD_LIMIT = 64
_DATABASE_SUFFIXES = ("", "-wal", "-shm", "-journal")
_WRITE_TOOLS = frozenset({"record", "revise", "forget"})


class UnsafeUsageLog(Exception):
    """The usage-log destination could damage the store or is not a plain private file."""


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    printable = "".join(character for character in value if character.isprintable())
    return printable[:_CLIENT_FIELD_LIMIT] or None


def _fold(path: str) -> str:
    # A case-insensitive filesystem treats "MEMORY.sqlite3-WAL" as the live WAL, which SQLite may
    # only create later, so no inode comparison can catch it at startup. Compare folded spellings.
    return unicodedata.normalize("NFC", path).casefold()


def _identity(path: str) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _make_private_parents(directory: str) -> None:
    missing: list[str] = []
    while directory and not os.path.exists(directory):
        missing.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for path in reversed(missing):
        os.makedirs(path, mode=0o700, exist_ok=True)


def _open_destination(path: Path, database: Path) -> int:
    """Open the log for appending, or refuse. Runs before the store is opened and never touches it."""
    try:
        target = os.path.abspath(path)
        if os.path.islink(target):
            raise UnsafeUsageLog("usage log path is a symbolic link")
        if os.path.lexists(target) and not stat.S_ISREG(os.lstat(target).st_mode):
            raise UnsafeUsageLog("usage log path is not a regular file")
        database_files = [os.path.realpath(database) + suffix for suffix in _DATABASE_SUFFIXES]
        database_files += [os.path.abspath(database) + suffix for suffix in _DATABASE_SUFFIXES]
        folded = {_fold(name) for name in database_files}
        if _fold(os.path.realpath(target)) in folded or _fold(target) in folded:
            raise UnsafeUsageLog("usage log path is a database file")
        _make_private_parents(os.path.dirname(target))
    except UnsafeUsageLog:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeUsageLog("usage log path cannot be inspected") from error

    flags = (os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NONBLOCK", 0))
    created = True
    try:
        try:
            descriptor = os.open(target, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            created = False
            descriptor = os.open(target, flags)
    except OSError as error:
        raise UnsafeUsageLog("usage log path cannot be opened for appending") from error

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeUsageLog("usage log path is not a regular file")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            raise UnsafeUsageLog("usage log file belongs to another user")
        if any(_identity(name) == (info.st_dev, info.st_ino) for name in database_files):
            raise UnsafeUsageLog("usage log path is a database file")
        # POSIX permission bits only. Windows reports writable files as 0o666 whatever their ACL says,
        # and before Python 3.13 it has no os.fchmod at all, so tightening there is meaningless.
        if os.name != "nt" and hasattr(os, "fchmod") and stat.S_IMODE(info.st_mode) & 0o077:
            os.fchmod(descriptor, 0o600)
        if fcntl is not None:
            fcntl.fcntl(descriptor, fcntl.F_SETFL, fcntl.fcntl(descriptor, fcntl.F_GETFL) & ~os.O_NONBLOCK)
    except BaseException as error:
        os.close(descriptor)
        if created:
            try:
                os.unlink(target)
            except OSError:
                pass
        if isinstance(error, UnsafeUsageLog):
            raise
        if isinstance(error, OSError):
            raise UnsafeUsageLog("usage log path cannot be opened for appending") from error
        raise
    return descriptor


class UsageLog:
    def __init__(self, path: Path, database: Path, *, common: Mapping[str, Any] | None = None) -> None:
        self._path = os.path.abspath(path)
        self._database = database
        self._descriptor = _open_destination(path, database)
        self.instance = uuid.uuid4().hex
        #: Fields repeated on every line, so a lost start line cannot orphan an instance's calls.
        self.common = dict(common or {})
        self.attempts = 0
        self.events_written = 0
        self.write_failures = 0
        self.reopens = 0
        #: A failed write left part of a line in the file and it was not terminated yet.
        self._torn = False

    def _still_ours(self) -> bool:
        try:
            info = os.fstat(self._descriptor)
            if info.st_nlink == 0:
                return False
            at_path = os.lstat(self._path)
        except OSError:
            return False
        return (at_path.st_dev, at_path.st_ino) == (info.st_dev, info.st_ino)

    def _reopen(self) -> bool:
        try:
            descriptor = _open_destination(Path(self._path), self._database)
        except UnsafeUsageLog:
            return False
        try:
            os.close(self._descriptor)
        except OSError:
            pass
        self._descriptor = descriptor
        self._torn = False  # any fragment belongs to the file that was moved away
        self.reopens += 1
        self._emit({"event": "reopened", "reopens": self.reopens})
        return True

    def write(self, event: Mapping[str, Any]) -> None:
        # The file at the configured path is the one the reader opens; writing to a descriptor that
        # no longer names it (moved, deleted, replaced) would count as written and be read as silence.
        if not self._still_ours() and not self._reopen():
            self.attempts += 1
            self.write_failures += 1
            return
        self._emit(event)

    def _emit(self, event: Mapping[str, Any]) -> None:
        self.attempts += 1
        line = json.dumps(
            {"v": FORMAT_VERSION, "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
             "instance": self.instance, "seq": self.attempts, **self.common, **event},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        # A fragment left by an earlier failed write is ended first, so this event starts a line of
        # its own instead of being glued to the fragment and lost with it.
        data = memoryview((("\n" if self._torn else "") + line + "\n").encode("ascii"))
        sent = 0
        try:
            # With O_APPEND each write lands at the current end of the file, so concurrent servers
            # sharing a log do not overwrite each other. A short write is finished rather than left
            # as a fragment that would swallow the next line.
            while sent < len(data):
                written = os.write(self._descriptor, data[sent:])
                if written <= 0:
                    raise OSError("no progress")
                sent += written
        except OSError:
            self.write_failures += 1
            if sent:
                # Part of this line is in the file. End it now if the disk allows; otherwise the
                # next successful write ends it. The fragment stays one malformed line, which the
                # reader counts; a line another server appended meanwhile can share its fate.
                self._torn = not self._terminate()
        else:
            self.events_written += 1
            self._torn = False

    def _terminate(self) -> bool:
        try:
            return os.write(self._descriptor, b"\n") == 1
        except OSError:
            return False

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
    code = _error_text(result).split(":", 1)[0]
    # Only a known code is logged; anything else would be text this log promised not to keep.
    return code if code in _known_codes() else "error"


def _opaque(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _identifiers(tool: str, content: Mapping[str, Any]) -> dict[str, Any]:
    if tool == "search":
        hits = content.get("hits")
        if not isinstance(hits, list):
            return {}
        ids = [hit.get("memory_id") for hit in hits if isinstance(hit, Mapping) and isinstance(hit.get("memory_id"), str)]
        return {"hits": len(hits), "hit_ids": ids[:MAX_HIT_IDS], "hit_ids_truncated": len(ids) > MAX_HIT_IDS}
    if tool in _WRITE_TOOLS:
        # A retry with the same idempotency key replays the original receipt, so the same
        # operation_id marks a replay and a new operation_id marks a new write.
        found = {name: _opaque(content.get(name)) for name in ("memory_id", "revision_id", "operation_id")}
        return {name: value for name, value in found.items() if value is not None}
    if tool in {"get", "history"}:
        memory_id = _opaque(content.get("memory_id"))
        return {"memory_id": memory_id} if memory_id is not None else {}
    return {}


def _client(ctx: ServerRequestContext[Any, Any]) -> dict[str, str | None] | None:
    info: Any = None
    try:
        info = getattr(ctx.session.client_params, "client_info", None)
    except Exception:
        info = None
    if info is None and CLIENT_INFO_META_KEY is not None and isinstance(ctx.params, Mapping):
        meta = ctx.params.get("_meta")
        if isinstance(meta, Mapping):
            info = meta.get(CLIENT_INFO_META_KEY)
    if info is None:
        return None
    if isinstance(info, Mapping):
        name, version = info.get("name"), info.get("version")
    else:
        name, version = getattr(info, "name", None), getattr(info, "version", None)
    label = {"name": _clean(name), "version": _clean(version)}
    return label if label["name"] or label["version"] else None


class UsageMiddleware:
    """Outermost middleware: it sees the result after error text has already been made safe."""

    def __init__(self, log: UsageLog) -> None:
        self.log = log
        # A stdio server process serves exactly one client, so a label seen once applies to the
        # requests that omit it (newer protocol versions make per-request client info optional).
        self._last_client: dict[str, str | None] | None = None

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
        except anyio.get_cancelled_exc_class():
            self._call(ctx, tool, "cancelled", started, {})
            raise
        except BaseException:
            # The inner middleware converts ordinary exceptions to results, so this is rare; it is
            # still a call that did not complete, and the exception continues unchanged.
            self._call(ctx, tool, "error", started, {})
            raise
        outcome = _outcome(result)
        content = _structured(result) if outcome == "ok" else None
        ids = _identifiers(tool, content) if content is not None else {}
        try:
            # Synchronous tools run to completion in a worker thread even when the client cancels,
            # and the response is then dropped. Without this check such a call would be logged as
            # delivered when the client never saw it.
            await anyio.lowlevel.checkpoint_if_cancelled()
        except anyio.get_cancelled_exc_class():
            self._call(ctx, tool, "cancelled", started, {"completed": outcome, "delivered": False, **ids})
            raise
        self._call(ctx, tool, outcome, started, ids)
        return result

    def _call(self, ctx: ServerRequestContext[Any, Any], tool: str, outcome: str, started: float, ids: dict[str, Any]) -> None:
        client = _client(ctx)
        if client is not None:
            self._last_client = client
        self.log.write({
            "event": "call",
            "tool": tool,
            "outcome": outcome,
            "ms": round((time.monotonic() - started) * 1000),
            "client": client if client is not None else self._last_client,
            **ids,
        })
