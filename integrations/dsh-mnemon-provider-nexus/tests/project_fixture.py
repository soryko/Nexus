"""Provision a disposable Nexus home with one folder-approved catalog repo in maintain mode.

Usage: python -I project_fixture.py <empty directory>. Prints the writer host's bridge binding.
Nothing outside the given directory is read or written: HOME points inside it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / 'integrations/project_scope/launcher.py'
BRIDGE = Path(sys.executable).with_name('nexus-memory-bridge')

base = Path(sys.argv[1]).resolve()
home, work = base / 'nexus-home', base / 'work'
home.mkdir(mode=0o700)
api = work / 'api'
api.mkdir(parents=True)
env = {'HOME': str(home), 'PATH': os.environ['PATH']}


def run(*args, cwd=base, stdin=None):
    result = subprocess.run([sys.executable, '-I', str(LAUNCHER), *map(str, args)], cwd=cwd, env=env, text=True,
                            capture_output=True, stdin=stdin, check=False)
    if result.returncode:
        raise SystemExit(f'launcher {args[0]} failed: {result.stderr}')
    return result


for git in (['init', '-q'], ['-c', 'user.name=f', '-c', 'user.email=f@example.invalid', 'commit', '-q',
                              '--allow-empty', '-m', 'f']):
    subprocess.run(['git', '-C', str(api), *git], check=True, capture_output=True)
run('approve-folder', work)
run('dsh', BRIDGE, cwd=api, stdin=subprocess.DEVNULL)      # first open creates the project space
if len(sys.argv) < 3 or sys.argv[2] != 'recall':
    run('mode', api, 'maintain')
print(json.dumps({'workspace': str(api.resolve()), 'home': str(home),
                  'binding': {'executable': sys.executable, 'args': ['-I', str(LAUNCHER), 'dsh', str(BRIDGE)],
                              'home': str(home), 'journalDir': str(base / 'journal')}}))
