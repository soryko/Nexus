#!/usr/bin/env python3
"""Start Nexus in an enrolled checkout. Enrollment is an explicit owner command."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))  # support isolated (-I) entry
import json

from project_scope import (ScopeError, approve_folder, enroll, host_project, inspect_scope, recover,
                           resolve, revoke_folder, set_denied)



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
OWNER_COMMANDS = ('enroll', 'recover', 'approve-folder', 'revoke-folder', 'disable', 'enable', 'status')


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
        if args.command in ('disable', 'enable'):
            print(set_denied(args.checkout, args.command == 'disable'))
            return 0
        if args.command == 'status':
            if not args.checkout.is_absolute():
                raise ScopeError('checkout path must be absolute')
            d = inspect_scope(args.checkout)
            print(json.dumps({'state': d.state, 'project_id': d.project_id, 'message': d.message,
                              'checkout': d.checkout and str(d.checkout)}, sort_keys=True))
            return 0 if d.state == 'bound' else 3
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
        try:
            project = host_project('claude' if hooks else args.command)
            if args.expected_project is not None and resolve(args.expected_project).id != project.id:
                raise ScopeError('host entry is bound to a different enrolled project')
        except (ScopeError, OSError):      # OSError: no registry yet, the first-run case
            if not hooks:
                raise
            # Unenrolled or ambiguous. Stay connected with only no-op hook tools (no store, no memory
            # tools) so the plugin's hooks say one line per session instead of failing on every prompt.
            # The reason goes to stderr so misconfiguration is not mistaken for "not enrolled".
            print('Nexus hooks: project not verified; serving no-op hooks only.', file=sys.stderr)
            os.chdir(Path.home())
            os.execv(args.runtime, [str(args.runtime), '--namespace', 'unenrolled', '--hook-unenrolled'])
        options = ['--db', str(project.database), '--namespace', project.id, '--actor', 'local']
        if v2:
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
