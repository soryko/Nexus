#!/usr/bin/env python3
"""Bind a portable Antigravity plugin stdio connection to its verified single root."""
from __future__ import annotations

import argparse
import fcntl
import importlib.metadata
import json
import os
import shlex
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import anyio
from mcp import types

from nexus_memory.activity import ActivityJournal
from nexus_memory.domain.models import Scope
from nexus_memory.git.cli import GitCli
from nexus_memory.git.identity import bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_server import _AUTONOMOUS, _EXPLICIT, create_server

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from project_scope import (  # noqa: E402
    ScopeDiagnosis,
    ScopeError,
    inspect_scope,
    private_base,
    private_file,
    resolve,
)

LEASE_TTL_SECONDS = 10.0
# Antigravity puts the calling conversation's id in every tools/call request's _meta and hands the
# same id to hooks as payload conversationId. Observed on Antigravity App 2.35.0 (2026-11-24,
# benchmarks/agy_correlation/FINDINGS-2026-11-24.md): conversation_id equals the hook's
# conversationId for top-level conversations and subagents alike; root_conversation_id names the
# top-level conversation, so it differs inside a subagent and is only a fallback for hosts that
# send nothing else (CLI v1.4.4 documents it). A request consumes only a lease from its own
# conversation, so a call whose hook never ran cannot take a lease written for another
# conversation, which may be another project.
CONVERSATION_META = "antigravity.google/conversation_id"
ROOT_CONVERSATION_META = "antigravity.google/root_conversation_id"
# The read-only tools the Atlas MCP App (tools/mcp_apps_ui/src/atlas.js) calls itself. Those calls
# come from the app, not the model, so no PreToolUse hook writes a host lease for them. A leased
# open_atlas grants them lease-free for its project only, for a fixed window that use does not
# extend; reopening Atlas renews it. Writes and every other tool still need a lease or roots.
ATLAS_UI_TOOLS = frozenset({"get", "history", "search", "compare_at_head"})
ATLAS_UI_GRANT_SECONDS = 300.0
# An inline memory card's "Verify at HEAD" calls compare_at_head itself. A leased get or
# compare_at_head grants that one check for the same memory and project, for the same window.
CARD_GRANT_SOURCES = frozenset({"get", "compare_at_head"})
CARD_GRANT_LIMIT = 64


def _configured_policy():
    if os.environ.get("NEXUS_CAPTURE_POLICY", "").strip().lower() == "explicit":
        return _EXPLICIT
    return _AUTONOMOUS


def _service_version() -> str:
    try:
        return importlib.metadata.version("nexus-memory")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def root_path(roots: list[Any]) -> str:
    if len(roots) != 1:
        raise ScopeError("one workspace root is required")
    uri = urlsplit(str(roots[0].uri))
    if uri.scheme != "file" or uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
        raise ScopeError("workspace must be a local file URI")
    path = unquote(uri.path, errors="strict")
    if not Path(path).is_absolute() or path.startswith("//"):
        raise ScopeError("workspace must be an absolute local directory")
    return path


def _refuse_git_env_overrides() -> None:
    if any(os.environ.get(key) for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")):
        raise ScopeError("Git workspace override is ambiguous")


def _verify_env_signals_agree(checkout: Path) -> None:
    for key in ("WORKSPACE_DIR", "AGY_WORKSPACE", "PROJECT_DIR"):
        val = os.environ.get(key)
        if val:
            if resolve(Path(val)).checkout != checkout:
                raise ScopeError("host workspace disagrees with active checkout")


def _launcher_cmd(kind: str, checkout: Path | None, project_id: str | None) -> str | None:
    launcher = SCRIPTS_DIR / "launcher.py"
    if kind == "device_drift" and checkout is not None and project_id is not None:
        return (
            f"{shlex.quote(sys.executable)} -I {shlex.quote(str(launcher))} "
            f"recover {shlex.quote(str(checkout))} "
            f"--expected-project-id {shlex.quote(project_id)}"
        )
    if kind == "unenrolled" and checkout is not None:
        return (
            f"{shlex.quote(sys.executable)} -I {shlex.quote(str(launcher))} "
            f"enroll {shlex.quote(str(checkout))}"
        )
    return None


def _diagnostic_status_result(
    *,
    scope_state: str,
    message: str,
    diag: ScopeDiagnosis | None = None,
) -> types.CallToolResult:
    project_id = diag.project_id if diag else None
    recorded_device = diag.recorded_device if diag else None
    observed_device = diag.observed_device if diag else None
    # enroll and recover accept only the main checkout (main_common), which is the common Git
    # directory's parent; diag.checkout is the active, possibly linked, worktree.
    main_checkout = diag.common.parent if (diag and diag.common is not None) else None
    recover_command = _launcher_cmd("device_drift", main_checkout, project_id) if scope_state == "device_drift" else None
    enroll_command = _launcher_cmd("unenrolled", main_checkout, None) if scope_state == "unenrolled" else None

    payload = {
        "scope_state": scope_state,
        "durable_storage": "unavailable",
        "message": message,
        "project_id": project_id,
        "recorded_device": recorded_device,
        "observed_device": observed_device,
        "recover_command": recover_command,
        "enroll_command": enroll_command,
        "sqlite_version": sqlite3.sqlite_version,
        "service_version": _service_version(),
        "capture_policy": _configured_policy().name,
    }
    return types.CallToolResult(
        is_error=False,
        structured_content=payload,
        content=[types.TextContent(type="text", text=json.dumps(payload))],
    )


def denied(code: str = "project_scope_unavailable", message: str | None = None) -> types.CallToolResult:
    error = {
        "code": code,
        "message": message or "A single verified project root is required; reconnect after changing workspaces.",
    }
    return types.CallToolResult(
        is_error=True,
        structured_content=error,
        content=[types.TextContent(type="text", text=f"{code}: {error['message']}")],
    )


class ConcurrentRootsChange(ScopeError):
    """The roots changed while a call was resolving them: refuse this call, retry on the next."""


def _lease_file_path() -> Path | None:
    override = os.environ.get("NEXUS_AGY_LEASE_FILE")
    if override:
        path = Path(override)
        return path if path.is_absolute() else None
    try:
        return private_base(create=False) / ".agy_host_leases.json"
    except (FileNotFoundError, ScopeError, OSError):
        return None


def _consume_host_lease(
    conversation: str | None, lease_ttl: float = LEASE_TTL_SECONDS
) -> tuple[str, str | None, str | None]:
    if conversation is None:
        return ("missing", None, None)
    lease_path = _lease_file_path()
    if lease_path is None or not os.path.lexists(lease_path):
        return ("missing", None, None)
    with os.fdopen(os.open(lease_path, os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as file:
        private_file(lease_path)
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            raw = json.load(file)
        except ValueError:
            raw = []
        entries = raw if isinstance(raw, list) else []
        now = time.time()
        live = [
            item
            for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("ts"), (int, float))
            and now - float(item["ts"]) <= lease_ttl
        ]
        # Entries of other conversations are kept for their own requests but never considered here.
        others = [item for item in live if item.get("conversation") != conversation]
        mine = [item for item in live if item.get("conversation") == conversation]
        unattested = [item for item in mine if item.get("attested") is False]
        active = [
            item
            for item in mine
            if item.get("attested") is not False
            and isinstance(item.get("workspace"), str)
            and item["workspace"].strip()
        ]
        if unattested:
            # A Nexus call is in flight whose hook could not name one workspace. This request may
            # be that call, so no pending lease can be taken as evidence for it: fail closed.
            file.seek(0)
            json.dump(others + active + unattested, file)
            file.truncate()
            return (
                "ambiguous",
                None,
                "A Nexus tool call without a single attested workspace is in flight; retry in a single workspace.",
            )
        if not active:
            file.seek(0)
            json.dump(others, file)
            file.truncate()
            return ("missing", None, None)

        distinct = {os.path.realpath(item["workspace"]) for item in active}
        if len(distinct) > 1:
            file.seek(0)
            json.dump(others + active, file)
            file.truncate()
            return (
                "ambiguous",
                None,
                "Multiple workspaces have concurrent MCP tool calls in flight; retry in a single workspace.",
            )

        popped = active.pop(0)
        file.seek(0)
        json.dump(others + active, file)
        file.truncate()
        return ("ok", popped["workspace"], None)


class GuardedService:
    """Keep the original tool handlers; admit every call against fresh client roots or host leases."""

    def __init__(self, expected: Path | None, *, pinned_workspace: Path | None = None) -> None:
        self.expected = expected
        self.pinned_workspace = pinned_workspace
        self.project = None
        self.service: MemoryService | None = None
        self.journal: ActivityJournal | None = None
        self.database_path: Path | None = None
        self._services_by_project: dict[str, tuple[Any, MemoryService, ActivityJournal, Path]] = {}
        self.changed = False
        self.generation = 0
        self.lock = anyio.Lock()
        self._clock = time.monotonic
        self._atlas_grant: tuple[str, float] | None = None  # (project id, expiry on _clock)
        self._card_grants: dict[str, tuple[str, float]] = {}  # memory id -> (project id, expiry)

    def _atlas_grant_admits(self, tool_name: str | None) -> bool:
        if tool_name not in ATLAS_UI_TOOLS or self.project is None or self._atlas_grant is None:
            return False
        project_id, expires = self._atlas_grant
        return project_id == self.project.id and self._clock() < expires

    def _card_grant_admits(self, tool_name: str | None, memory_id: str | None) -> bool:
        if tool_name != "compare_at_head" or self.project is None or memory_id is None:
            return False
        grant = self._card_grants.get(memory_id)
        return grant is not None and grant[0] == self.project.id and self._clock() < grant[1]

    def _grant_card(self, project_id: str, memory_id: str) -> None:
        now = self._clock()
        self._card_grants = {k: v for k, v in self._card_grants.items() if v[1] > now}
        self._card_grants.pop(memory_id, None)
        self._card_grants[memory_id] = (project_id, now + ATLAS_UI_GRANT_SECONDS)
        while len(self._card_grants) > CARD_GRANT_LIMIT:
            del self._card_grants[next(iter(self._card_grants))]

    def __getattr__(self, name: str) -> Any:
        if self.service is None:
            raise ScopeError("project scope is unbound")
        return getattr(self.service, name)

    def _bind_project(self, project: Any) -> None:
        cached = self._services_by_project.get(project.id)
        if cached is None or cached[0] != project:
            store = SQLiteRepository(project.database)
            scope = Scope(project.id, "local")
            git = GitCli() if GitCli.available() else None
            binding, verifier = bind_repository(store, scope, project.checkout, None, git)
            service = MemoryService(store, scope, binding, verifier)
            journal = ActivityJournal(ActivityJournal.path_for_store(project.database))
            cached = (project, service, journal, project.database)
            self._services_by_project[project.id] = cached
        self.project, self.service, self.journal, self.database_path = cached

    def _tool_name(self, ctx: Any) -> str | None:
        params = getattr(ctx, "params", None)
        if isinstance(params, dict):
            name = params.get("name")
        else:
            name = getattr(params, "name", None)
        return name if isinstance(name, str) else None

    def _request_conversation(self, ctx: Any) -> str | None:
        """The request's conversation id from _meta, or None (then no lease can be matched)."""
        params = getattr(ctx, "params", None)
        meta = params.get("_meta") if isinstance(params, dict) else getattr(params, "meta", None)
        if not isinstance(meta, dict):
            meta = getattr(ctx, "meta", None)
        if not isinstance(meta, dict):
            return None
        for key in (CONVERSATION_META, ROOT_CONVERSATION_META):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _memory_id_argument(self, ctx: Any) -> str | None:
        params = getattr(ctx, "params", None)
        arguments = params.get("arguments") if isinstance(params, dict) else getattr(params, "arguments", None)
        value = arguments.get("memory_id") if isinstance(arguments, dict) else None
        return value if isinstance(value, str) and value else None

    async def authorize(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method == "notifications/roots/list_changed":
            self.generation += 1
        if ctx.method != "tools/call":
            return await call_next(ctx)

        tool_name = self._tool_name(ctx)
        async with self.lock:
            if self.changed:
                return denied()
            host_lease_mode = False
            try:
                epoch = self.generation
                _refuse_git_env_overrides()
                has_roots_cap = ctx.session.check_client_capability(
                    types.ClientCapabilities(roots=types.RootsCapability())
                )
                if has_roots_cap:
                    with anyio.fail_after(3):
                        roots = await ctx.session.list_roots()

                    root_list = getattr(roots, "roots", []) or []
                    if len(root_list) == 0:
                        if self.project is None and tool_name == "status":
                            return _diagnostic_status_result(
                                scope_state="empty",
                                message="No active workspace root advertised by client.",
                            )
                        raise ScopeError("one workspace root is required")
                    if len(root_list) > 1:
                        if self.project is None and tool_name == "status":
                            return _diagnostic_status_result(
                                scope_state="ambiguous",
                                message="Multiple workspace roots are open; select a single enrolled checkout.",
                            )
                        raise ScopeError("one workspace root is required")
                    raw_path = root_path(root_list)
                elif self.pinned_workspace is not None:
                    if not self.pinned_workspace.is_absolute():
                        raise ScopeError("workspace must be an absolute local directory")
                    raw_path = str(self.pinned_workspace)
                else:
                    host_lease_mode = True
                    lease_state, leased_path, lease_msg = _consume_host_lease(self._request_conversation(ctx))
                    if lease_state == "ambiguous":
                        if tool_name == "status":
                            return _diagnostic_status_result(
                                scope_state="ambiguous",
                                message=lease_msg or "Multiple workspace roots are active.",
                            )
                        return denied("project_scope_ambiguous", lease_msg)
                    if lease_state == "missing":
                        if self._atlas_grant_admits(tool_name) or self._card_grant_admits(
                            tool_name, self._memory_id_argument(ctx)
                        ):
                            diag = inspect_scope(self.project.checkout)
                            if diag.state != "bound":
                                raise ScopeError(diag.message or "project scope unavailable")
                            _verify_env_signals_agree(self.project.checkout)
                            return await call_next(ctx)
                        if tool_name == "status":
                            return _diagnostic_status_result(
                                scope_state="host_missing_roots",
                                message=(
                                    "MCP client did not advertise roots capability; global plugin MCP stays "
                                    "fail-closed without attested roots or an active host tool lease "
                                    "(Nexus Studio and hooks still receive workspace URIs natively). For hosts "
                                    "without MCP roots or hooks, add a workspace-scoped .agents/mcp_config.json "
                                    "launching `launch.mjs mcp --workspace /abs/path/to/repo`."
                                ),
                            )
                        raise ScopeError("client did not advertise roots")
                    if leased_path is None:
                        raise ScopeError("host tool lease unavailable")
                    raw_path = leased_path

                diag = inspect_scope(Path(raw_path))
                if diag.state != "bound":
                    if self.project is not None and not host_lease_mode:
                        self.changed = True
                        raise ScopeError(diag.message or "project scope changed")
                    if tool_name == "status":
                        return _diagnostic_status_result(
                            scope_state=diag.state,
                            message=diag.message or f"Workspace state is {diag.state}.",
                            diag=diag,
                        )
                    if diag.state == "device_drift":
                        return denied(
                            "scope_device_drift",
                            diag.message or "APFS device drift detected; run owner recover command.",
                        )
                    if diag.state == "unenrolled":
                        return denied(
                            "project_not_enrolled",
                            "Workspace is not enrolled in Nexus project scope.",
                        )
                    raise ScopeError(diag.message or "project scope unavailable")

                project = resolve(Path(raw_path))
                _verify_env_signals_agree(project.checkout)

                if has_roots_cap and self.pinned_workspace is not None:
                    if resolve(self.pinned_workspace).id != project.id:
                        raise ScopeError("pinned workspace disagrees with client root")
                if self.project is not None and self.project != project and not host_lease_mode:
                    self.changed = True
                    raise ScopeError("project changed")
                if epoch != self.generation:
                    raise ConcurrentRootsChange("concurrent roots change")
                if self.expected is not None and resolve(self.expected).id != project.id:
                    raise ScopeError("workspace differs from configured project")

                self._bind_project(project)
                if tool_name == "open_atlas":
                    self._atlas_grant = (project.id, self._clock() + ATLAS_UI_GRANT_SECONDS)
                if host_lease_mode and tool_name in CARD_GRANT_SOURCES:
                    memory_id = self._memory_id_argument(ctx)
                    if memory_id is not None:
                        self._grant_card(project.id, memory_id)
            except Exception as error:
                if self.project is not None and not host_lease_mode:
                    if not isinstance(error, ConcurrentRootsChange):
                        self.changed = True
                    return denied()
                if tool_name == "status" and isinstance(error, ScopeError):
                    return _diagnostic_status_result(scope_state="invalid", message=str(error))
                return denied()
            return await call_next(ctx)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Antigravity MCP gateway for Nexus Memory.")
    parser.add_argument("expected_project", nargs="?", type=Path, default=None)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Owner-pinned absolute checkout path for workspace-local configs on hosts without MCP roots.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = _parse_args(argv)
    guard = GuardedService(args.expected_project, pinned_workspace=args.workspace)
    server = create_server(
        guard,
        capture_policy=_configured_policy(),
        database=lambda: guard.database_path,
        activity_journal=lambda: guard.journal,
        enable_mcp_apps=True,
    )
    server.middleware.insert(0, guard.authorize)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
