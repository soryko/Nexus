"""Provision a disposable Nexus home with folder-approved catalog repos.

Usage: python -I project_fixture.py <directory> [recall] [--repo NAME]
Creates (or reuses) <directory>/nexus-home, approves <directory>/work once, and adds the repo
<directory>/work/NAME (default api) in maintain mode, or recall-only with `recall`. Prints the
writer host's bindings for that repo; each repo has its own journal. Nothing outside the given
directory is read or written: HOME points inside it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / 'integrations/project_scope/launcher.py'
BRIDGE = Path(sys.executable).with_name('nexus-memory-bridge')

args = sys.argv[1:]
name = args[args.index('--repo') + 1] if '--repo' in args else 'api'
base = Path(args[0]).resolve()
home, work = base / 'nexus-home', base / 'work'
base.mkdir(parents=True, exist_ok=True)
fresh = not home.exists()
home.mkdir(mode=0o700, exist_ok=True)
repo = work / name
repo.mkdir(parents=True)
env = {'HOME': str(home), 'PATH': os.environ['PATH']}


def run(*argv, cwd=base, stdin=None):
    result = subprocess.run([sys.executable, '-I', str(LAUNCHER), *map(str, argv)], cwd=cwd, env=env, text=True,
                            capture_output=True, stdin=stdin, check=False)
    if result.returncode:
        raise SystemExit(f'launcher {argv[0]} failed: {result.stderr}')
    return result


for git in (['init', '-q'], ['-c', 'user.name=f', '-c', 'user.email=f@example.invalid', 'commit', '-q',
                              '--allow-empty', '-m', 'f']):
    subprocess.run(['git', '-C', str(repo), *git], check=True, capture_output=True)
if fresh:
    run('approve-folder', work)
run('dsh', BRIDGE, cwd=repo, stdin=subprocess.DEVNULL)      # first open creates the project space
if 'recall' not in args:
    run('mode', repo, 'maintain')
launcher = [sys.executable, '-I', str(LAUNCHER)]
print(json.dumps({'workspace': str(repo.resolve()), 'home': str(home), 'launcher': launcher,
                  'binding': {'executable': sys.executable, 'args': ['-I', str(LAUNCHER), 'dsh', str(BRIDGE)],
                              'home': str(home), 'journalDir': str(base / ('journal-' + name))},
                  'readBinding': {'executable': sys.executable, 'args': ['-I', str(LAUNCHER), 'dsh-worker', str(BRIDGE)],
                                  'home': str(home)}}))
