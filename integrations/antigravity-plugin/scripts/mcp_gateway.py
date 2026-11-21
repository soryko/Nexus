#!/usr/bin/env python3
"""Bind a portable Antigravity plugin stdio connection to its verified single root."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shlex
import sqlite3
import sys
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
from nexus_memory.transport.mcp_server import create_server

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from project_scope import ScopeDiagnosis, ScopeError, resolve, inspect_scope  # noqa: E402


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
    recover_command = _launcher_cmd("device_drift", diag.checkout, project_id) if (diag and scope_state == "device_drift") else None
    enroll_command = _launcher_cmd("unenrolled", diag.checkout, None) if (diag and scope_state == "unenrolled") else None

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
        "capture_policy": "explicit",
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


class GuardedService:
    """Keep the original tool handlers; admit every call against fresh client roots."""

    def __init__(self, expected: Path | None, *, pinned_workspace: Path | None = None) -> None:
        self.expected = expected
        self.pinned_workspace = pinned_workspace
        self.project = None
        self.service: MemoryService | None = None
        self.journal: ActivityJournal | None = None
        self.database_path: Path | None = None
        self.changed = False
        self.generation = 0
        self.lock = anyio.Lock()

    def __getattr__(self, name: str) -> Any:
        if self.service is None:
            raise ScopeError("project scope is unbound")
        return getattr(self.service, name)

    def _tool_name(self, ctx: Any) -> str | None:
        params = getattr(ctx, "params", None)
        if isinstance(params, dict):
            name = params.get("name")
        else:
            name = getattr(params, "name", None)
        return name if isinstance(name, str) else None

    async def authorize(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method == "notifications/roots/list_changed":
            self.generation += 1
        if ctx.method != "tools/call":
            return await call_next(ctx)

        tool_name = self._tool_name(ctx)
        async with self.lock:
            if self.changed:
                return denied()
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
                    if self.project is None and tool_name == "status":
                        return _diagnostic_status_result(
                            scope_state="host_missing_roots",
                            message=(
                                "MCP client did not advertise roots capability; global plugin MCP stays "
                                "fail-closed without attested roots (Nexus Studio and hooks still receive "
                                "workspace URIs natively). For hosts without MCP roots, add a workspace-scoped "
                                ".agents/mcp_config.json launching `launch.mjs mcp --workspace /abs/path/to/repo`."
                            ),
                        )
                    raise ScopeError("client did not advertise roots")

                diag = inspect_scope(Path(raw_path))
                if diag.state != "bound":
                    if self.project is not None:
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
                if self.project is not None and self.project != project:
                    self.changed = True
                    raise ScopeError("project changed")
                if epoch != self.generation:
                    raise ConcurrentRootsChange("concurrent roots change")
                if self.expected is not None and resolve(self.expected).id != project.id:
                    raise ScopeError("workspace differs from configured project")

                if self.service is None:
                    store = SQLiteRepository(project.database)
                    scope = Scope(project.id, "local")
                    git = GitCli() if GitCli.available() else None
                    binding, verifier = bind_repository(store, scope, project.checkout, None, git)
                    self.service = MemoryService(store, scope, binding, verifier)
                    self.journal = ActivityJournal(ActivityJournal.path_for_store(project.database))
                    self.database_path = project.database
                    self.project = project
            except Exception as error:
                if self.project is not None:
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
        database=lambda: guard.database_path,
        activity_journal=lambda: guard.journal,
        enable_mcp_apps=True,
    )
    server.middleware.insert(0, guard.authorize)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
