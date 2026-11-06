"""Resolve authorized local Git worktrees against an owner-managed registry."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from uuid import UUID, uuid4


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    checkout: Path
    common: Path
    id: str
    database: Path


def private_base(*, create: bool = False) -> Path:
    home = Path.home().resolve(strict=True)
    base = home / '.local/share/nexus-memory/project-scopes-v1'
    current = home
    for part in base.relative_to(home).parts:
        current /= part
        if create:
            current.mkdir(mode=0o700, exist_ok=True)
        metadata = current.lstat()
        forbidden = 0o077 if current == base else 0o022
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & forbidden):
            raise ScopeError('unsafe project storage directory')
    return base


def private_file(path: Path) -> None:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
        raise ScopeError('unsafe project storage file')


def checkout_dirs(workspace: Path) -> tuple[Path, Path]:
    if not workspace.is_absolute():
        raise ScopeError('workspace must be absolute')
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ScopeError('workspace is not a directory')
    result = subprocess.run(
        ['/usr/bin/git', '-C', workspace, 'rev-parse', '--path-format=absolute',
         '--show-toplevel', '--absolute-git-dir', '--git-common-dir'],
        capture_output=True, timeout=3, env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
                                             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
    lines = result.stdout.decode(errors='surrogateescape').splitlines()
    if result.returncode or len(lines) != 3:
        raise ScopeError('workspace is not a supported Git checkout')
    checkout, admin, common = (Path(line).resolve(strict=True) for line in lines)
    if not workspace.is_relative_to(checkout) or workspace.is_relative_to(common):
        raise ScopeError('workspace outside checkout')
    # Refuse a nested or forged marker that Git might bypass while walking to a parent.
    nearest = next((p / '.git' for p in (workspace, *workspace.parents) if os.path.lexists(p / '.git')), None)
    marker = checkout / '.git'
    if nearest != marker or common.name != '.git':
        raise ScopeError('unverified checkout marker')
    metadata = common.stat()
    if (os.path.lexists(common / 'commondir') or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022):
        raise ScopeError('unverified Git common directory')
    if stat.S_ISDIR(marker.lstat().st_mode) and marker == admin == common:
        return checkout, common
    if not stat.S_ISREG(marker.lstat().st_mode) or admin.parent != common / 'worktrees':
        raise ScopeError('unverified linked worktree')
    # The trusted common directory must independently point back to this exact worktree.
    backlink = admin / 'gitdir'
    if not stat.S_ISREG(backlink.lstat().st_mode):
        raise ScopeError('invalid linked worktree backlink')
    target = Path(os.fsdecode(backlink.read_bytes().rstrip(b'\r\n')))
    if (admin / target).resolve(strict=True) != marker:
        raise ScopeError('linked worktree backlink mismatch')
    return checkout, common


def identity(common: Path) -> dict:
    st = common.stat()
    return {'device': st.st_dev, 'inode': st.st_ino, 'birth_time': getattr(st, 'st_birthtime', None)}


def read_registry(base: Path) -> dict:
    path = base / 'registry.json'
    private_file(path)
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('projects'), dict):
        raise ScopeError('invalid project registry')
    seen = set()
    for name, entry in data['projects'].items():
        if not Path(name).is_absolute() or not isinstance(entry, dict):
            raise ScopeError('invalid project mapping')
        value = entry.get('id')
        if (not isinstance(value, str) or str(UUID(value)) != value or value in seen
                or not {'device', 'inode', 'birth_time'} <= entry.keys()):
            raise ScopeError('invalid or duplicate project identity')
        seen.add(value)
    return data


def enroll(workspace: Path) -> str:
    checkout, common = checkout_dirs(workspace)
    if workspace.resolve() != common.parent or checkout != common.parent:
        raise ScopeError('enroll the main checkout explicitly')
    base = private_base(create=True)
    lock = base / '.enroll.lock'
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+') as handle:
        private_file(lock)
        fcntl.flock(handle, fcntl.LOCK_EX)
        path = base / 'registry.json'
        registry = read_registry(base) if os.path.lexists(path) else {'version': 1, 'projects': {}}
        entries = registry['projects']
        key = str(common)
        observed = identity(common)
        if key in entries:
            if any(entries[key].get(k) != v for k, v in observed.items()):
                raise ScopeError('checkout replaced; owner recovery is required')
            return entries[key]['id']
        project_id = str(uuid4())
        entries[key] = {'id': project_id, **observed}
        with tempfile.NamedTemporaryFile(dir=base, mode='w', delete=False) as out:
            try:
                json.dump(registry, out, indent=2)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
                os.replace(out.name, path)
            finally:
                Path(out.name).unlink(missing_ok=True)
        return project_id


def resolve(workspace: Path) -> Project:
    checkout, common = checkout_dirs(workspace)
    base = private_base()
    entry = read_registry(base)['projects'].get(str(common))
    if entry is None or any(entry.get(k) != v for k, v in identity(common).items()):
        raise ScopeError('project is not enrolled or its checkout has changed')
    database = base / (entry['id'] + '.sqlite3')
    for suffix in ('', '-wal', '-shm', '-journal'):
        path = Path(str(database) + suffix)
        if os.path.lexists(path):
            private_file(path)
    return Project(checkout, common, entry['id'], database)


def host_project(client: str, workspace_paths: list[str] | None = None) -> Project:
    cwd = Path.cwd()
    if any(os.environ.get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR')):
        raise ScopeError('Git workspace override is ambiguous')
    signals: list[str] = []
    if workspace_paths is not None:
        if len(workspace_paths) != 1 or not isinstance(workspace_paths[0], str):
            raise ScopeError('a single host workspace is required')
        signals.append(workspace_paths[0])
    if client == 'antigravity':
        signals.extend(os.environ[k] for k in ('WORKSPACE_DIR', 'AGY_WORKSPACE', 'PROJECT_DIR') if k in os.environ)
    elif client == 'claude' and 'CLAUDE_PROJECT_DIR' in os.environ:
        signals.append(os.environ['CLAUDE_PROJECT_DIR'])

    plugin = Path.home().resolve(strict=True) / '.gemini/config/plugins/nexus-memory'
    # Hook commands run in the plugin directory, with the workspace in the event.
    # An MCP startup there has no event and needs an explicit host signal instead.
    if client == 'antigravity' and cwd == plugin:
        if not signals:
            raise ScopeError('missing host workspace')
        workspace = resolve(Path(signals[0]))
    else:
        workspace = resolve(cwd)
    # PWD can corroborate a known workspace but cannot select one on its own.
    pwd = os.environ.get('PWD')
    # A GUI process commonly inherits PWD=/ even after the host changes cwd.
    if pwd not in (None, '/') and (not pwd or Path(pwd).resolve(strict=True) != cwd):
        signals.append(pwd)
    for signal in set(signals):
        if not signal or resolve(Path(signal)).checkout != workspace.checkout:
            raise ScopeError('host workspace disagrees with active checkout')
    return workspace
