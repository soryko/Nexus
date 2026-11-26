"""Resolve authorized local Git worktrees against an owner-managed registry."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import grp
import json
import math
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    checkout: Path
    common: Path
    id: str
    database: Path | None
    # Set for a repo whose memory lives in the owner catalog (registry version 2). Such a
    # repo has no per-project database; only a caller that asks for the catalog gets one.
    catalog: Path | None = None


@dataclass(frozen=True)
class ScopeDiagnosis:
    """Strictly read-only inspection result for a workspace path."""

    # "bound" | "device_drift" | "unenrolled" | "eligible" | "disabled" | "moved" | "identity_changed" | "invalid"
    state: str
    checkout: Path | None = None
    common: Path | None = None
    project_id: str | None = None
    database: Path | None = None
    recorded_device: int | None = None
    observed_device: int | None = None
    message: str = ""
    store: str | None = None          # "catalog" | "legacy", once an entry exists


def private_group(metadata: os.stat_result) -> bool:
    """True for a Linux user-private group: the owner's primary group, named for the owner, with
    no other member and no other account using it as its primary group. Checked on every call."""
    if not sys.platform.startswith('linux') or metadata.st_uid != os.getuid():
        return False
    try:
        user = pwd.getpwuid(os.getuid())
        group = grp.getgrgid(metadata.st_gid)
    except KeyError:
        return False
    if metadata.st_gid != user.pw_gid or group.gr_name != user.pw_name or set(group.gr_mem) - {user.pw_name}:
        return False
    return all(other.pw_gid != metadata.st_gid or other.pw_uid == user.pw_uid for other in pwd.getpwall())


def writable_by_others(metadata: os.stat_result) -> bool:
    """World write always counts; group write counts unless the group is the owner's private one."""
    return bool(metadata.st_mode & 0o002) or (bool(metadata.st_mode & 0o020) and not private_group(metadata))


def canonical(path: Path) -> Path:
    """Resolve symlinks and, on macOS, the stored letter case of every component."""
    path = path.resolve(strict=True)
    if sys.platform == 'darwin':
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if path.is_dir() else 0))
        try:
            path = Path(fcntl.fcntl(fd, fcntl.F_GETPATH, b'\0' * 1024).rstrip(b'\0').decode(errors='surrogateescape'))
        finally:
            os.close(fd)
    return path


def private_base(*, create: bool = False) -> Path:
    home = Path.home().resolve(strict=True)
    base = home / '.local/share/nexus-memory/project-scopes-v1'
    current = home
    for part in base.relative_to(home).parts:
        current /= part
        if create:
            current.mkdir(mode=0o700, exist_ok=True)
        metadata = current.lstat()
        unsafe = metadata.st_mode & 0o077 if current == base else writable_by_others(metadata)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or unsafe:
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
    workspace = canonical(workspace)
    if not workspace.is_dir():
        raise ScopeError('workspace is not a directory')
    result = subprocess.run(
        ['/usr/bin/git', '-C', workspace, 'rev-parse', '--path-format=absolute',
         '--show-toplevel', '--absolute-git-dir', '--git-common-dir'],
        capture_output=True, timeout=3, env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
                                             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
    lines = result.stdout.decode(errors='surrogateescape').splitlines()
    if result.returncode or len(lines) != 3:
        raise ScopeError(unsupported_reason(workspace) or 'workspace is not a supported Git checkout')
    checkout, admin, common = (canonical(Path(line)) for line in lines)
    if not workspace.is_relative_to(checkout) or workspace.is_relative_to(common):
        raise ScopeError('workspace outside checkout')
    # Refuse a nested or forged marker that Git might bypass while walking to a parent.
    nearest = next((p / '.git' for p in (workspace, *workspace.parents) if os.path.lexists(p / '.git')), None)
    marker = checkout / '.git'
    if nearest != marker or common.name != '.git':
        raise ScopeError(unsupported_reason(workspace) or 'unverified checkout marker')
    metadata = common.stat()
    if (os.path.lexists(common / 'commondir') or metadata.st_uid != os.getuid()
            or writable_by_others(metadata)):
        raise ScopeError('unverified Git common directory')
    if stat.S_ISDIR(marker.lstat().st_mode) and marker == admin == common:
        return checkout, common
    if not stat.S_ISREG(marker.lstat().st_mode) or admin.parent != common / 'worktrees':
        raise ScopeError(unsupported_reason(workspace) or 'unverified linked worktree')
    # The trusted common directory must independently point back to this exact worktree.
    backlink = admin / 'gitdir'
    if not stat.S_ISREG(backlink.lstat().st_mode):
        raise ScopeError('invalid linked worktree backlink')
    target = Path(os.fsdecode(backlink.read_bytes().rstrip(b'\r\n')))
    if (admin / target).resolve(strict=True) != marker:
        raise ScopeError('linked worktree backlink mismatch')
    return checkout, common


def unsupported_reason(workspace: Path) -> str | None:
    """Name a layout v1 refuses on purpose, for status. Never used to admit anything."""
    try:
        result = subprocess.run(
            ['/usr/bin/git', '-C', workspace, 'rev-parse', '--is-bare-repository',
             '--show-superproject-working-tree', '--absolute-git-dir'],
            capture_output=True, timeout=3, env={'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
                                                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.decode(errors='surrogateescape').splitlines()
    if result.returncode or not lines:
        return None
    if lines[0] == 'true':
        return 'bare repositories are not supported'
    if len(lines) == 3 and lines[1]:
        return 'submodules are not supported'
    if lines[-1] and Path(lines[-1]).name != '.git' and '/worktrees/' not in lines[-1]:
        return 'repositories with a separate Git directory are not supported'
    return None


def identity(common: Path) -> dict:
    st = common.stat()
    return {'device': st.st_dev, 'inode': st.st_ino, 'birth_time': getattr(st, 'st_birthtime', None)}


REGISTRY_VERSION = 2
ORIGINS = ('explicit', 'folder')
STORES = ('legacy', 'catalog')
CATALOG = 'catalog'   # the owner catalog is <base>/catalog.sqlite3


def read_registry(base: Path) -> dict:
    """Read version 1 or 2. Version 2 adds approved folders, per-repo denials and lazily
    enrolled entries; a version 1 reader refuses it, so an old build fails closed."""
    path = base / 'registry.json'
    private_file(path)
    data = json.loads(path.read_bytes())
    if (not isinstance(data, dict) or data.get('version') not in (1, REGISTRY_VERSION)
            or not isinstance(data.get('projects'), dict)):
        raise ScopeError('invalid project registry')
    if data['version'] == 1 and data.keys() & {'folders', 'denied'}:
        raise ScopeError('invalid project registry')
    for section in ('folders', 'denied'):
        value = data.setdefault(section, {})
        if not isinstance(value, dict) or any(not Path(k).is_absolute() or not isinstance(v, dict)
                                              for k, v in value.items()):
            raise ScopeError('invalid project registry')
    seen = set()
    for name, entry in data['projects'].items():
        if not Path(name).is_absolute() or not isinstance(entry, dict):
            raise ScopeError('invalid project mapping')
        value = entry.get('id')
        if (not isinstance(value, str) or str(UUID(value)) != value or value in seen
                or not {'device', 'inode', 'birth_time'} <= entry.keys()
                or entry.get('origin', 'explicit') not in ORIGINS
                or entry.get('store', 'legacy') not in STORES
                or (data['version'] == 1 and entry.keys() & {'origin', 'store'})):
            raise ScopeError('invalid or duplicate project identity')
        seen.add(value)
    return data


def needed_version(registry: dict) -> int:
    """Version 2 only while version-2 state exists, so a v1-only registry stays readable by old builds."""
    v2 = (registry.get('folders') or registry.get('denied')
          or any(entry.get('origin', 'explicit') != 'explicit' or 'store' in entry
                 for entry in registry['projects'].values()))
    return REGISTRY_VERSION if v2 else 1


def empty_registry() -> dict:
    return {'version': 1, 'projects': {}, 'folders': {}, 'denied': {}}


@contextmanager
def registry_lock(base: Path, *, create: bool = False):
    lock = base / '.enroll.lock'
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    with os.fdopen(os.open(lock, flags, 0o600), 'r+') as handle:
        private_file(lock)
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def write_registry(base: Path, registry: dict) -> None:
    version = needed_version(registry)
    registry = {'version': version, 'projects': registry['projects'],
                **({'folders': registry.get('folders', {}), 'denied': registry.get('denied', {})} if version == 2 else {})}
    if version == 1:
        registry['projects'] = {k: {f: v for f, v in e.items() if f != 'origin'} for k, e in registry['projects'].items()}
    with tempfile.NamedTemporaryFile(dir=base, mode='w', delete=False) as out:
        try:
            json.dump(registry, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
            os.replace(out.name, base / 'registry.json')
        finally:
            Path(out.name).unlink(missing_ok=True)


def main_common(workspace: Path) -> Path:
    checkout, common = checkout_dirs(workspace)
    if canonical(workspace) != common.parent or checkout != common.parent:
        raise ScopeError('specify the main checkout explicitly')
    return common


def load(base: Path) -> dict:
    return read_registry(base) if os.path.lexists(base / 'registry.json') else empty_registry()


def distinct_identity(observed: dict) -> bool:
    """Whether (device, inode, birth time) can tell this Git directory from a later one. Without a
    birth time (Linux: os.stat has none) a reused inode looks identical, so no identity match is trusted."""
    birth = observed.get('birth_time')
    return type(birth) in (float, int) and birth > 0 and math.isfinite(birth)


def moved_from(registry: dict, common: Path, observed: dict) -> str | None:
    """The enrolled path whose Git directory has this exact identity, if it is not this path. Never a
    match without a birth time: an unrelated repo that reuses a deleted one's inode must not inherit it."""
    if not distinct_identity(observed):
        return None
    for path, entry in registry['projects'].items():
        if path != str(common) and all(entry.get(k) == v for k, v in observed.items()):
            return path
    return None


def approved_folder(registry: dict, checkout: Path, common: Path) -> str | None:
    """Both the opened worktree and its main checkout must sit inside one approved folder."""
    for folder in registry.get('folders', {}):
        root = Path(folder)
        if checkout.is_relative_to(root) and common.parent.is_relative_to(root):
            return folder
    return None


def _register(base: Path, registry: dict, common: Path, origin: str) -> str:
    """Caller holds the registry lock and has re-read the registry under it."""
    entries = registry['projects']
    key = str(common)
    if key in registry['denied']:
        raise ScopeError('Nexus is disabled for this repo; run enable first')
    observed = identity(common)
    if key in entries:
        if any(entries[key].get(k) != v for k, v in observed.items()):
            raise ScopeError('checkout replaced; owner recovery is required')
        if origin == 'explicit' and entries[key].get('origin', 'explicit') != 'explicit':
            entries[key]['origin'] = 'explicit'
            write_registry(base, registry)
        return entries[key]['id']
    previous = moved_from(registry, common, observed)
    if previous is not None:
        raise ScopeError(f'moved: this Git directory is enrolled at {previous}; run recover')
    project_id = str(uuid4())
    # A repo first enrolled through a folder starts recall-only, which only the catalog enforces,
    # so it never gets a per-project database. An explicit enroll keeps the version 1 layout.
    entries[key] = {'id': project_id, **observed, 'origin': origin, **({'store': 'catalog'} if origin == 'folder' else {})}
    write_registry(base, registry)
    return project_id


def enroll(workspace: Path) -> str:
    common = main_common(workspace)
    base = private_base(create=True)
    with registry_lock(base, create=True):
        return _register(base, load(base), common, 'explicit')


def approve_folder(folder: Path) -> str:
    """Owner command: repos under this folder get recall on first open. Grants no maintenance."""
    if not folder.is_absolute():
        raise ScopeError('folder must be absolute')
    folder = canonical(folder)
    if not folder.is_dir() or folder == Path(folder.anchor):
        raise ScopeError('approve a specific folder, not a file or the filesystem root')
    base = private_base(create=True)
    with registry_lock(base, create=True):
        registry = load(base)
        registry['folders'].setdefault(str(folder), {})
        write_registry(base, registry)
    return str(folder)


def revoke_folder(folder: Path) -> str:
    """Owner command: repos enrolled only through this folder stop resolving; records are kept."""
    base = private_base()
    key = str(canonical(folder)) if os.path.lexists(folder) else str(folder)
    with registry_lock(base):
        registry = load(base)
        if registry['folders'].pop(key, None) is None:
            raise ScopeError('folder is not approved')
        write_registry(base, registry)
    return key


def uncovered_catalog_projects() -> list[str]:
    """Catalog repos enrolled through a folder that no approved folder covers any more. Their open
    sessions must be cut off; new launches are already refused by resolve()."""
    registry = load(private_base())
    return [entry['id'] for key, entry in registry['projects'].items()
            if entry.get('origin') == 'folder' and entry.get('store') == 'catalog'
            and approved_folder(registry, Path(key).parent, Path(key)) is None]


def set_denied(workspace: Path, denied: bool) -> str:
    """Owner command: a per-repo denial overrides every folder approval. Records are kept."""
    _, common = checkout_dirs(workspace)
    base = private_base(create=denied)
    with registry_lock(base, create=denied):
        registry = load(base)
        if denied:
            registry['denied'][str(common)] = {}
        elif registry['denied'].pop(str(common), None) is None:
            raise ScopeError('Nexus is not disabled for this repo')
        write_registry(base, registry)
    return str(common)


def session_dir(catalog: Path) -> Path:
    sessions = catalog.parent / 'sessions'
    sessions.mkdir(mode=0o700, exist_ok=True)
    info = sessions.lstat()
    if not sessions.is_dir() or sessions.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ScopeError('unsafe session directory')
    return sessions


def catalog_admin(command: list[str], catalog: Path, action: str, request: dict, *, out: Path | None = None) -> dict:
    """Run one `project` admin action. Any refusal or failure is a ScopeError: no memory."""
    name = uuid4().hex
    path = session_dir(catalog) / (name + '.request')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as file:
        json.dump(request, file)
    argv = [*command, 'project', action, '--catalog', str(catalog), '--request', str(path)]
    if action != 'status':
        argv += ['--operation-key', name]
    if out is not None:
        argv += ['--out', str(out)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    finally:
        path.unlink(missing_ok=True)
    try:
        output = json.loads(result.stdout)
    except ValueError:
        output = None
    if result.returncode or not isinstance(output, dict) or 'error' in output:
        reason = output.get('error') if isinstance(output, dict) else 'catalog unavailable'
        raise ScopeError(f'the project catalog refused: {reason}')
    return output


def mint_grant(admin: list[str], project: Project, host: str, *, role: str = 'root') -> Path:
    """A one-time grant file for one session of a catalog repo, minted by the admin command of the
    build that will read it. The file sits in <catalog dir>/sessions; its reader deletes it."""
    grant = session_dir(project.catalog) / (uuid4().hex + '.grant')
    request = {'project_id': project.id, 'checkout': str(project.checkout), 'host': host}
    if role != 'root':
        request['role'] = role
    catalog_admin(admin, project.catalog, 'open', request, out=grant)
    return grant


def registry_entry(workspace: Path) -> dict | None:
    """The registry entry for a checkout's Git directory, enabled or not, without any other check.
    Owner controls use it so a damaged store or catalog never hides which repo they act on."""
    _, common = checkout_dirs(workspace)
    return load(private_base()).get('projects', {}).get(str(common))


def checked_database(base: Path, project_id: str, *, required: bool = False) -> Path:
    database = base / (project_id + '.sqlite3')
    for suffix in ('', '-wal', '-shm', '-journal'):
        path = Path(str(database) + suffix)
        if (required and not suffix) or os.path.lexists(path):
            private_file(path)
    return database


def device_only_drift(entry: dict, observed: dict) -> bool:
    for obj in (entry, observed):
        birth = obj['birth_time']
        if (type(obj['device']) is not int or obj['device'] < 0
                or type(obj['inode']) is not int or obj['inode'] <= 0
                or type(birth) not in (float, int) or not birth > 0
                or (isinstance(birth, float) and not math.isfinite(birth))):
            return False
    return (entry['device'] != observed['device'] and entry['inode'] == observed['inode']
            and entry['birth_time'] == observed['birth_time'])


def recover(workspace: Path, expected_project_id: str) -> str:
    """Confirm a device-only identity change, or a move of the same Git directory to a new path,
    keeping the project ID and store. Never creates a project or a store."""
    base = private_base()
    with registry_lock(base):
        common = main_common(workspace)
        registry = read_registry(base)
        entries = registry['projects']
        entry = entries.get(str(common))
        observed = identity(common)
        if entry is None:
            previous = moved_from(registry, common, observed)
            if previous is None or entries[previous]['id'] != expected_project_id:
                raise ScopeError('expected project does not match enrollment')
            # The old path must no longer hold this Git directory (a move, not a second path to it).
            try:
                still_there = os.path.lexists(previous) and identity(Path(previous)) == observed
            except OSError:
                still_there = False
            if still_there:
                raise ScopeError('the enrolled path still holds this Git directory')
            if entries[previous].get('store') != 'catalog':      # a catalog repo has no per-project database
                checked_database(base, expected_project_id, required=True)
            entries[str(common)] = entries.pop(previous)
            if registry['denied'].pop(previous, None) is not None:
                registry['denied'][str(common)] = {}
            write_registry(base, registry)
            return expected_project_id
        if entry['id'] != expected_project_id:
            raise ScopeError('expected project does not match enrollment')
        if not device_only_drift(entry, observed):
            raise ScopeError('recovery requires a device-only identity change')
        checked_database(base, expected_project_id, required=True)
        entry['device'] = observed['device']
        write_registry(base, registry)
        return expected_project_id


def inspect_scope(workspace: Path) -> ScopeDiagnosis:
    """Classify a workspace's enrollment state without creating files or mutating registry."""
    try:
        checkout, common = checkout_dirs(workspace)
    except (ScopeError, OSError, subprocess.SubprocessError) as error:
        return ScopeDiagnosis(state='invalid', message=str(error) or 'workspace is not a supported Git checkout')

    try:
        base = private_base(create=False)
    except FileNotFoundError:
        return ScopeDiagnosis(
            state='unenrolled',
            checkout=checkout,
            common=common,
            message='workspace is not enrolled in Nexus project scope',
        )
    except (ScopeError, OSError) as error:
        return ScopeDiagnosis(
            state='invalid',
            checkout=checkout,
            common=common,
            message=str(error) or 'project storage is invalid',
        )

    registry_path = base / 'registry.json'
    if not os.path.lexists(registry_path):
        return ScopeDiagnosis(
            state='unenrolled',
            checkout=checkout,
            common=common,
            message='workspace is not enrolled in Nexus project scope',
        )

    try:
        registry = read_registry(base)
    except (ScopeError, OSError, ValueError) as error:
        return ScopeDiagnosis(
            state='invalid',
            checkout=checkout,
            common=common,
            message=str(error) or 'project registry is invalid',
        )

    if str(common) in registry['denied']:
        denied = registry['projects'].get(str(common))
        return ScopeDiagnosis(state='disabled', checkout=checkout, common=common,
                              project_id=denied and denied['id'], store=denied and denied.get('store', 'legacy'),
                              message='Nexus is disabled for this repo')
    entry = registry['projects'].get(str(common))
    folder = approved_folder(registry, checkout, common)
    if entry is None:
        try:
            previous = moved_from(registry, common, identity(common))
        except OSError:
            previous = None
        if previous is not None:
            return ScopeDiagnosis(state='moved', checkout=checkout, common=common,
                                  project_id=registry['projects'][previous]['id'],
                                  message=f'moved: this Git directory is enrolled at {previous}; run recover')
        if folder is not None:
            return ScopeDiagnosis(state='eligible', checkout=checkout, common=common,
                                  message=f'recall will be enabled on first open (approved folder {folder})')
        return ScopeDiagnosis(
            state='unenrolled',
            checkout=checkout,
            common=common,
            message='workspace is not enrolled in Nexus project scope',
        )
    if entry.get('origin', 'explicit') == 'folder' and folder is None:
        return ScopeDiagnosis(state='unenrolled', checkout=checkout, common=common, project_id=entry['id'],
                              store=entry.get('store', 'legacy'),
                              message='no approved folder covers this checkout any more')

    try:
        observed = identity(common)
    except OSError as error:
        return ScopeDiagnosis(
            state='invalid',
            checkout=checkout,
            common=common,
            project_id=entry['id'],
            message=str(error) or 'could not stat Git common directory',
        )

    recorded_dev = entry.get('device') if isinstance(entry.get('device'), int) else None
    observed_dev = observed.get('device') if isinstance(observed.get('device'), int) else None

    store = entry.get('store', 'legacy')
    if not any(entry.get(k) != v for k, v in observed.items()):
        try:
            database = checked_database(base, CATALOG if store == 'catalog' else entry['id'], required=False)
        except (ScopeError, OSError) as error:
            return ScopeDiagnosis(
                state='invalid',
                checkout=checkout,
                common=common,
                project_id=entry['id'],
                recorded_device=recorded_dev,
                observed_device=observed_dev,
                message=str(error) or 'project database is unsafe',
            )
        return ScopeDiagnosis(
            state='bound',
            checkout=checkout,
            common=common,
            project_id=entry['id'],
            database=database,
            recorded_device=recorded_dev,
            observed_device=observed_dev,
            message='project scope is bound',
            store=store,
        )

    if device_only_drift(entry, observed):
        database: Path | None = None
        try:
            database = checked_database(base, entry['id'], required=False)
        except (ScopeError, OSError) as error:
            return ScopeDiagnosis(
                state='invalid',
                checkout=checkout,
                common=common,
                project_id=entry['id'],
                recorded_device=recorded_dev,
                observed_device=observed_dev,
                message=str(error) or 'project database is unsafe',
            )
        return ScopeDiagnosis(
            state='device_drift',
            checkout=checkout,
            common=common,
            project_id=entry['id'],
            database=database,
            recorded_device=recorded_dev,
            observed_device=observed_dev,
            message=f'device-only drift detected ({recorded_dev} -> {observed_dev}); owner recovery required',
        )

    return ScopeDiagnosis(
        state='identity_changed',
        checkout=checkout,
        common=common,
        project_id=entry['id'],
        recorded_device=recorded_dev,
        observed_device=observed_dev,
        message='checkout identity changed (inode or birth time mismatch); owner verification required',
    )


def resolve(workspace: Path, *, lazy: bool = False, allow_catalog: bool = False) -> Project:
    """Bind a workspace to its enrolled project. With lazy=True (the host's own active checkout
    only), a repo under an approved folder is registered on first open, under the registry lock.

    A catalog repo resolves only for a caller that passes allow_catalog: one that will reach its
    memory through the catalog's fence. Any other caller is refused rather than handed a path."""
    checkout, common = checkout_dirs(workspace)
    base = private_base()
    registry = read_registry(base)
    key = str(common)
    if key in registry['denied']:
        raise ScopeError('Nexus is disabled for this repo')
    entry = registry['projects'].get(key)
    if entry is None and lazy and approved_folder(registry, checkout, common):
        with registry_lock(base):
            registry = read_registry(base)   # another host may have registered it meanwhile
            if approved_folder(registry, checkout, common):
                _register(base, registry, common, 'folder')
        registry = read_registry(base)
        entry = registry['projects'].get(key)
    if entry is None or any(entry.get(k) != v for k, v in identity(common).items()):
        raise ScopeError('project is not enrolled or its checkout has changed')
    if entry.get('origin', 'explicit') == 'folder' and not approved_folder(registry, checkout, common):
        raise ScopeError('no approved folder covers this checkout')
    if entry.get('store') == 'catalog':
        if not allow_catalog:
            raise ScopeError('this repo uses the project catalog, which this entry point does not support yet')
        return Project(checkout, common, entry['id'], None, checked_database(base, CATALOG))
    database = checked_database(base, entry['id'])
    return Project(checkout, common, entry['id'], database)


def host_project(client: str, workspace_paths: list[str] | None = None, *, allow_catalog: bool = False) -> Project:
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
        workspace = resolve(Path(signals[0]), lazy=True, allow_catalog=allow_catalog)
    else:
        workspace = resolve(cwd, lazy=True, allow_catalog=allow_catalog)
    # PWD can corroborate a known workspace but cannot select one on its own.
    pwd = os.environ.get('PWD')
    # A GUI process commonly inherits PWD=/ even after the host changes cwd.
    if pwd not in (None, '/') and (not pwd or Path(pwd).resolve(strict=True) != cwd):
        signals.append(pwd)
    for signal in set(signals):
        if not signal or resolve(Path(signal), allow_catalog=allow_catalog).checkout != workspace.checkout:
            raise ScopeError('host workspace disagrees with active checkout')
    return workspace
