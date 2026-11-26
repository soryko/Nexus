#!/usr/bin/env python3
"""Start Nexus in an enrolled checkout. Enrollment is an explicit owner command."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))  # support isolated (-I) entry
import json

from project_scope import (CATALOG, ScopeError, approve_folder, checked_database, enroll, host_project,
                           inspect_scope, private_base, recover, registry_entry, resolve, revoke_folder, set_denied)

# Owner commands reach the catalog through this interpreter's Nexus build. A host launch uses the
# admin command installed beside the server it starts, so the grant and the server share one build.
OWNER_ADMIN = [sys.executable, '-I', '-c', 'import sys; from nexus_memory.spaces.admin import main; sys.exit(main())']
MODES = {'recall': 'Recall only', 'maintain': 'Recall + maintain', 'off': 'Off'}


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


def owner_catalog() -> Path | None:
    """The owner catalog if one exists. It is created by a repo's first host launch, not here."""
    catalog = checked_database(private_base(), CATALOG)
    return catalog if catalog.exists() else None


def open_session(runtime: Path, project, host: str) -> Path:
    grant = session_dir(project.catalog) / (uuid4().hex + '.grant')
    catalog_admin([str(runtime.with_name('nexus-memory-admin'))], project.catalog, 'open',
                  {'project_id': project.id, 'checkout': str(project.checkout), 'host': host}, out=grant)
    return grant


def set_ceiling(project_id: str, ceiling: str) -> dict:
    catalog = owner_catalog()
    if catalog is None:
        return {'space_id': None}
    return catalog_admin(OWNER_ADMIN, catalog, 'ceiling', {'project_id': project_id, 'ceiling': ceiling})


def disable(checkout: Path) -> int:
    """Stop the catalog first, so open sessions lose their grants before the registry says off.
    If the catalog cannot be reached the registry denial still stops new launches, and the owner is told."""
    entry = registry_entry(checkout) or {}
    failure = None
    if entry.get('store') == 'catalog':
        try:
            set_ceiling(entry['id'], 'off')
        except (ScopeError, OSError, subprocess.SubprocessError) as error:
            failure = error
    print(set_denied(checkout, True))
    if failure:
        print(f'Nexus: new sessions are refused, but open sessions were not stopped ({failure}). '
              'Run disable again.', file=sys.stderr)
        return 1
    return 0


def enable(checkout: Path) -> int:
    """Remove the registry denial, then reopen the space at recall only: a new generation, never maintain."""
    print(set_denied(checkout, False))
    entry = registry_entry(checkout) or {}
    if entry.get('store') == 'catalog':
        try:
            set_ceiling(entry['id'], 'recall')
        except (ScopeError, OSError, subprocess.SubprocessError) as error:
            print(f'Nexus: the repo is still off in the catalog ({error}). Run enable again.', file=sys.stderr)
            return 1
    return 0


def set_mode(checkout: Path, mode: str) -> int:
    d = inspect_scope(checkout)
    if d.state != 'bound' or d.store != 'catalog':
        raise ScopeError('mode applies to a catalog repo that is enrolled and enabled')
    if set_ceiling(d.project_id, mode)['space_id'] is None:
        raise ScopeError('open this repo in a host first; its project space does not exist yet')
    print(MODES[mode])
    return 0


def status(checkout: Path) -> int:
    d = inspect_scope(checkout)
    report = {'state': d.state, 'project_id': d.project_id, 'message': d.message,
              'checkout': d.checkout and str(d.checkout), 'store': d.store, 'mode': None}
    if d.state == 'disabled':
        report['mode'] = MODES['off']
    elif d.state == 'eligible':
        report['mode'] = MODES['recall'] + ' (on first open)'
    elif d.state == 'bound' and d.store == 'legacy':
        report['mode'] = 'Legacy store (no ceiling)'
    elif d.state == 'bound':
        try:
            catalog = owner_catalog()
            space = catalog_admin(OWNER_ADMIN, catalog, 'status', {'project_id': d.project_id}) if catalog else {}
            report['mode'] = MODES[space.get('ceiling') or 'recall']
            report['generation'] = space.get('generation')
        except (ScopeError, OSError, subprocess.SubprocessError) as error:
            report['mode'], report['message'] = 'Unknown', str(error)
    print(json.dumps(report, sort_keys=True))
    return 0 if d.state == 'bound' and report['mode'] in (MODES['recall'], MODES['maintain'], 'Legacy store (no ceiling)') else 3



def capture_policy():
    """The Claude hooks launch's capture policy: autonomous unless NEXUS_CAPTURE_POLICY=explicit.

    The plugin's command hooks read the same variable, so the cue, the guard and the Stop check
    agree. An unrecognised value falls back to explicit, the more conservative mode, and says so.
    """
    value = os.environ.get('NEXUS_CAPTURE_POLICY', '').strip().lower()
    if value in ('', 'autonomous'):
        return 'autonomous'
    if value != 'explicit':
        print('Nexus: unrecognised NEXUS_CAPTURE_POLICY; using explicit.', file=sys.stderr)
    return 'explicit'


# Owner commands print the reason they refused; host launches never echo scope details.
OWNER_COMMANDS = ('enroll', 'recover', 'approve-folder', 'revoke-folder', 'disable', 'enable', 'mode', 'status')


def run_atlas(checkout, open_browser):
    """Serve the viewer. Needs the installed Nexus interpreter; never enrolls or writes."""
    if not checkout.is_absolute():
        print('Atlas needs an absolute checkout path.', file=sys.stderr)
        return 1
    try:
        from nexus_memory.atlas.binding import open_binding
        from nexus_memory.atlas.server import serve
        from nexus_memory.domain.errors import NexusError
    except ImportError as error:
        if (error.name or '').startswith('nexus_memory.atlas'):
            print('This Nexus runtime does not include Atlas; install a newer Nexus wheel.', file=sys.stderr)
        else:
            print('Atlas needs the installed Nexus interpreter (run this launcher with its python).', file=sys.stderr)
        return 1
    try:
        serve(open_binding(checkout, resolve), open_browser)
    except NexusError as error:
        print(f'Atlas unavailable: {error}', file=sys.stderr)
        return 1
    except OSError:
        print('Atlas could not start its local server.', file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('enroll').add_argument('checkout', type=Path)
    recovery = commands.add_parser('recover', help='confirm a device-only change for an enrolled main checkout')
    recovery.add_argument('checkout', type=Path)
    recovery.add_argument('--expected-project-id', metavar='UUID', required=True)
    commands.add_parser('approve-folder', help='enable recall for repos opened under this folder').add_argument('folder', type=Path)
    commands.add_parser('revoke-folder', help='withdraw a folder approval; records are kept').add_argument('folder', type=Path)
    commands.add_parser('disable', help='turn Nexus off for one repo, overriding folders').add_argument('checkout', type=Path)
    commands.add_parser('enable', help='remove a per-repo disable').add_argument('checkout', type=Path)
    commands.add_parser('status', help='show how Nexus treats a checkout (read-only)').add_argument('checkout', type=Path)
    mode = commands.add_parser('mode', help='recall only, or also let Nexus maintain memory for this repo')
    mode.add_argument('checkout', type=Path)
    mode.add_argument('mode', choices=('recall', 'maintain'))
    atlas = commands.add_parser('atlas', help='open the read-only Memory Atlas viewer for an enrolled checkout')
    atlas.add_argument('checkout', type=Path)
    atlas.add_argument('--no-browser', action='store_true')
    for name in ('codex', 'claude', 'claude-hooks', 'antigravity'):
        host = commands.add_parser(name)
        host.add_argument('runtime', type=Path)
        host.add_argument('--expected-project', type=Path)
        host.add_argument('--catalog', type=Path)
        host.add_argument('--grant-file', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'enroll':
            print(enroll(args.checkout))
            return 0
        if args.command == 'recover':
            print(recover(args.checkout, args.expected_project_id))
            return 0
        if args.command == 'approve-folder':
            print(approve_folder(args.folder))
            return 0
        if args.command == 'revoke-folder':
            print(revoke_folder(args.folder))
            return 0
        if args.command in ('disable', 'enable', 'mode', 'status') and not args.checkout.is_absolute():
            raise ScopeError('checkout path must be absolute')
        if args.command == 'disable':
            return disable(args.checkout)
        if args.command == 'enable':
            return enable(args.checkout)
        if args.command == 'mode':
            return set_mode(args.checkout, args.mode)
        if args.command == 'status':
            return status(args.checkout)
        if args.command == 'atlas':
            return run_atlas(args.checkout, not args.no_browser)
        if not args.runtime.is_absolute():
            raise ScopeError('runtime path must be absolute')
        v2 = args.catalog is not None or args.grant_file is not None
        if v2 and (not args.catalog or not args.grant_file or not args.catalog.is_absolute()
                   or not args.grant_file.is_absolute() or args.command == 'antigravity'):
            raise ScopeError('unsupported or incomplete scoped profile')
        plugin = Path.home().resolve(strict=True) / '.gemini/config/plugins/nexus-memory'
        is_plugin_launch = Path.cwd() == plugin or os.environ.get('PLUGIN_ROOT') == str(plugin)
        if args.command == 'antigravity' and is_plugin_launch:
            # The host starts plugin servers here without an active checkout. Its MCP
            # roots response is the authority; project hints can only corroborate it.
            python = args.runtime.with_name('python')
            gateway = Path(__file__).resolve().with_name('antigravity_mcp.py')
            command = [str(python), '-I', str(gateway)]
            if args.expected_project is not None:
                command.append(str(args.expected_project))
            os.execv(python, command)
        hooks = args.command == 'claude-hooks'
        grant = None
        try:
            # Antigravity has no catalog session grants yet, so a catalog repo is refused there.
            catalog_ok = args.command != 'antigravity' and not v2
            project = host_project('claude' if hooks else args.command, allow_catalog=catalog_ok)
            if (args.expected_project is not None
                    and resolve(args.expected_project, allow_catalog=catalog_ok).id != project.id):
                raise ScopeError('host entry is bound to a different enrolled project')
            if project.catalog is not None:
                grant = open_session(args.runtime, project, args.command)
        except (ScopeError, OSError, subprocess.SubprocessError):  # OSError: no registry yet, the first-run case
            if not hooks:
                raise
            # Unenrolled or ambiguous. Stay connected with only no-op hook tools (no store, no memory
            # tools) so the plugin's hooks say one line per session instead of failing on every prompt.
            # The reason goes to stderr so misconfiguration is not mistaken for "not enrolled".
            print('Nexus hooks: project not verified; serving no-op hooks only.', file=sys.stderr)
            os.chdir(Path.home())
            os.execv(args.runtime, [str(args.runtime), '--namespace', 'unenrolled', '--hook-unenrolled'])
        options = ['--db', str(project.database), '--namespace', project.id, '--actor', 'local']
        if grant is not None:
            v2 = True
            options = ['--catalog', str(project.catalog), '--grant-file', str(grant), '--project-id', project.id]
        elif v2:
            options = ['--catalog', str(args.catalog), '--grant-file', str(args.grant_file), '--project-id',project.id]
        if args.command == 'codex' and not v2:
            options.extend(['--codex-project-root', str(project.common.parent)])
        elif hooks:
            options.extend(['--hook-project-root', str(project.common.parent),
                            '--capture-policy', capture_policy()])
        else:
            options.extend(['--repo', str(project.checkout)])
        os.chdir(project.checkout)
        os.environ['PWD'] = str(project.checkout)
        os.execv(args.runtime, [str(args.runtime), *options])
    except ScopeError as error:
        if args.command in OWNER_COMMANDS:
            print(f'Nexus: {error}', file=sys.stderr)
            return 1
        print('Nexus unavailable: project scope could not be verified.', file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        print('Nexus unavailable: project scope could not be verified.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
