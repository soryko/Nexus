"""Launch authority and lifetime checks for the Atlas viewer.

The project-scope launcher decides which store belongs to a checkout. This module pins
that answer at launch and refuses to serve once any part of it changes. There are two
tiers. The full check re-runs the injected resolver, which runs Git. The quick check
only re-stats what the full check pinned, so it can run at the start and end of every
request without a subprocess.

Retirement writes a marker on the original inode, so no stat can see it. The read-only
repository checks the schema version on every connection for that reason.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from pathlib import Path
from typing import Callable

from nexus_memory.domain.errors import NexusError
from nexus_memory.domain.models import Scope
from nexus_memory.storage import SQLiteRepository

_SIDECARS = ("-wal", "-shm", "-journal")
_RESOLVER_FAILURES = (ValueError, OSError, subprocess.SubprocessError, RuntimeError)


class BindingUnavailable(NexusError):
    code = "binding_unavailable"


def _unavailable() -> BindingUnavailable:
    return BindingUnavailable("the project binding could not be verified")


def _kind(info: os.stat_result) -> int:
    return stat.S_IFMT(info.st_mode)


def _identity(path: Path) -> tuple:
    info = os.lstat(path)
    return (_kind(info), info.st_dev, info.st_ino)


def _stamped(path: Path) -> tuple:
    info = os.lstat(path)
    return (_kind(info), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(path: Path, expected: tuple) -> None:
    info = os.lstat(path)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or (_kind(info), info.st_dev, info.st_ino) != expected):
        raise _unavailable()


def _linked_admin(marker: Path) -> Path | None:
    """The administrative directory a linked worktree's ``.git`` file points at."""
    if not stat.S_ISREG(os.lstat(marker).st_mode):
        return None
    line = marker.read_text(errors="surrogateescape").strip()
    if not line.startswith("gitdir:"):
        raise _unavailable()
    return Path(line.removeprefix("gitdir:").strip()).resolve(strict=True)


class AtlasBinding:
    def __init__(self, checkout: Path, resolve: Callable) -> None:
        self._checkout = checkout
        self._resolve = resolve
        self._lock = threading.Lock()
        self._project = self._resolved()
        self._pin()

    @property
    def project_id(self) -> str:
        return self._project.id

    @property
    def scope(self) -> Scope:
        return Scope(self._project.id, "local")

    @property
    def database(self) -> Path:
        return self._project.database

    def _resolved(self):
        try:
            return self._resolve(self._checkout)
        except NexusError:
            raise
        except _RESOLVER_FAILURES as error:
            raise _unavailable() from error

    def _pin(self) -> None:
        project = self._project
        try:
            marker = project.checkout / ".git"
            admin = _linked_admin(marker)
            self._common = os.stat(project.common)
            self._common_id = (self._common.st_dev, self._common.st_ino,
                               getattr(self._common, "st_birthtime", None))
            self._marker = _stamped(marker) if admin else _identity(marker)
            self._admin = (_identity(admin), _stamped(admin / "gitdir")) if admin else None
            self._admin_path = admin
            self._registry_path = project.database.parent / "registry.json"
            self._registry = _stamped(self._registry_path)
            self._store = _identity(project.database)
            self._sidecars = {s: _identity(Path(str(project.database) + s))
                              for s in _SIDECARS if os.path.lexists(str(project.database) + s)}
            _private(project.database, self._store)
        except OSError as error:
            raise _unavailable() from error

    def full_check(self) -> None:
        """Re-resolve the checkout with Git and compare it with what was pinned."""
        with self._lock:
            fresh = self._resolved()
            if (fresh.id, fresh.database, fresh.common, fresh.checkout) != (
                    self._project.id, self._project.database, self._project.common, self._project.checkout):
                raise _unavailable()
            self._project = fresh
            self._pin_matching()
            SQLiteRepository.open_readonly(fresh.database)  # schema version and retirement

    def _pin_matching(self) -> None:
        before = (self._common_id, self._marker, self._admin, self._store)
        self._pin()
        if (self._common_id, self._marker, self._admin, self._store) != before:
            raise _unavailable()

    def check(self) -> None:
        """Quick check: no subprocess, only the filesystem facts pinned at launch."""
        project = self._project
        try:
            common = os.stat(project.common)
            if (common.st_dev, common.st_ino, getattr(common, "st_birthtime", None)) != self._common_id:
                raise _unavailable()
            marker = project.checkout / ".git"
            admin = self._admin_path
            if (_stamped(marker) if admin else _identity(marker)) != self._marker:
                raise _unavailable()
            if admin and (_linked_admin(marker) != admin
                          or (_identity(admin), _stamped(admin / "gitdir")) != self._admin):
                raise _unavailable()
            _private(project.database, self._store)
            for suffix in _SIDECARS:
                path = Path(str(project.database) + suffix)
                if os.path.lexists(path):
                    info = os.lstat(path)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != os.getuid() or info.st_mode & 0o077):
                        raise _unavailable()
            if _stamped(self._registry_path) != self._registry:
                # Another project enrolling rewrites the registry. That is not a reason to
                # stop serving this one; only a changed answer for this project is.
                self.full_check()
        except NexusError:
            raise
        except OSError as error:
            raise _unavailable() from error

    def reader(self) -> SQLiteRepository:
        """A read-only repository, opened between two quick checks."""
        self.check()
        repository = SQLiteRepository.open_readonly(self._project.database)
        self.check()
        return repository


def open_binding(checkout: Path, resolve_project: Callable) -> AtlasBinding:
    """Bind one enrolled checkout to its existing store, or refuse."""
    checkout = Path(checkout)
    if not checkout.is_absolute():
        raise _unavailable()
    binding = AtlasBinding(checkout, resolve_project)
    binding.reader()  # absent, unsupported or retired stores fail here, not on first use
    return binding
