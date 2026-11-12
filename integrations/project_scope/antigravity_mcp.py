#!/usr/bin/env python3
"""Bind a workspace-neutral Antigravity stdio connection to its first attested root."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

import anyio
from mcp import types
from nexus_memory.domain.models import Scope
from nexus_memory.git.cli import GitCli
from nexus_memory.git.identity import bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_server import create_server

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_scope import ScopeError, host_project, resolve


def root_path(roots) -> str:
    if len(roots) != 1:
        raise ScopeError('one workspace root is required')
    uri = urlsplit(str(roots[0].uri))
    if uri.scheme != 'file' or uri.netloc not in ('', 'localhost') or uri.query or uri.fragment:
        raise ScopeError('workspace must be a local file URI')
    path = unquote(uri.path, errors='strict')
    if not Path(path).is_absolute() or path.startswith('//'):
        raise ScopeError('workspace must be an absolute local directory')
    return path


def denied():
    error = {'code': 'project_scope_unavailable',
             'message': 'A single verified project root is required; reconnect after changing workspaces.'}
    return types.CallToolResult(is_error=True, structured_content=error,
                                content=[types.TextContent(type='text', text=json.dumps(error))])


class ConcurrentRootsChange(ScopeError):
    """The roots changed while a call was resolving them: refuse this call, but the next one re-checks."""


class GuardedService:
    """Keep the original tool handlers; admit every call against fresh client roots."""

    def __init__(self, expected):
        self.expected = expected
        self.project = None
        self.service = None
        self.changed = False
        self.generation = 0
        self.lock = anyio.Lock()

    def __getattr__(self, name):
        if self.service is None:
            raise ScopeError('project scope is unbound')
        return getattr(self.service, name)

    async def authorize(self, ctx, call_next):
        if ctx.method == 'notifications/roots/list_changed':
            self.generation += 1
        if ctx.method != 'tools/call':
            return await call_next(ctx)
        async with self.lock:
            if self.changed:
                return denied()
            try:
                epoch = self.generation
                if not ctx.session.check_client_capability(types.ClientCapabilities(roots=types.RootsCapability())):
                    raise ScopeError('client did not advertise roots')
                with anyio.fail_after(3):
                    roots = await ctx.session.list_roots()
                project = host_project('antigravity', [root_path(roots.roots)])
                if self.project is not None and self.project != project:
                    self.changed = True
                    raise ScopeError('project changed')
                if epoch != self.generation:
                    raise ConcurrentRootsChange('concurrent roots change')
                if self.expected is not None and resolve(self.expected).id != project.id:
                    raise ScopeError('workspace differs from configured project')
                if self.service is None:
                    # The registry and all store paths have been checked. No startup/list
                    # request creates a database. Bind once, including Git verification.
                    store = SQLiteRepository(project.database)
                    scope = Scope(project.id, 'local')
                    binding, verifier = bind_repository(store, scope, project.checkout, None, GitCli())
                    self.service = MemoryService(store, scope, binding, verifier)
                    self.project = project
            except Exception as error:
                # Once bound, any failure to confirm the same single root (a different or ambiguous root, a host
                # failure) requires reconnection. Only a race with a roots notification is retried by the next call.
                if self.project is not None and not isinstance(error, ConcurrentRootsChange):
                    self.changed = True
                return denied()
            return await call_next(ctx)


def main():
    os.umask(0o077)
    expected = Path(sys.argv[1]) if len(sys.argv) == 2 else None
    guard = GuardedService(expected)
    # Prepend to the original MCP validation chain. Descriptions, schemas,
    # annotations and operation semantics come from this installed Nexus build.
    server = create_server(guard)
    server.middleware.insert(0, guard.authorize)
    server.run()


if __name__ == '__main__':
    main()
