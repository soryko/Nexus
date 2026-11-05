#!/usr/bin/env python3
"""Start Nexus in an enrolled checkout. Enrollment is an explicit owner command."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))  # support isolated (-I) entry
from project_scope import ScopeError, enroll, host_project, resolve


def run_atlas(checkout, open_browser):
    """Serve the viewer. Needs the installed Nexus interpreter; never enrolls or writes."""
    if not checkout.is_absolute():
        print('Atlas needs an absolute checkout path.', file=sys.stderr)
        return 1
    try:
        from nexus_memory.atlas.binding import open_binding
        from nexus_memory.atlas.server import serve
        from nexus_memory.domain.errors import NexusError
    except ImportError:
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
    atlas = commands.add_parser('atlas', help='open the read-only Memory Atlas viewer for an enrolled checkout')
    atlas.add_argument('checkout', type=Path)
    atlas.add_argument('--no-browser', action='store_true')
    for name in ('codex', 'claude', 'antigravity'):
        host = commands.add_parser(name)
        host.add_argument('runtime', type=Path)
        host.add_argument('--expected-project', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'enroll':
            print(enroll(args.checkout))
            return 0
        if args.command == 'atlas':
            return run_atlas(args.checkout, not args.no_browser)
        if not args.runtime.is_absolute():
            raise ScopeError('runtime path must be absolute')
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
        project = host_project(args.command)
        if args.expected_project is not None and resolve(args.expected_project).id != project.id:
            raise ScopeError('host entry is bound to a different enrolled project')
        options = ['--db', str(project.database), '--namespace', project.id, '--actor', 'local']
        if args.command == 'codex':
            options.extend(['--codex-project-root', str(project.common.parent)])
        else:
            options.extend(['--repo', str(project.checkout)])
        os.chdir(project.checkout)
        os.environ['PWD'] = str(project.checkout)
        os.execv(args.runtime, [str(args.runtime), *options])
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        print('Nexus unavailable: project scope could not be verified.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
