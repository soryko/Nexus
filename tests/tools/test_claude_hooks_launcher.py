"""The `claude-hooks` launcher command: hook-enabled Claude Code, and a quiet fallback when unenrolled.

The existing `claude` command is deliberately untouched, so a pilot or client that runs an older runtime
through it keeps working.
"""
import os
from pathlib import Path
import subprocess
import sys

ENTRY = Path(__file__).resolve().parents[2] / 'integrations' / 'project_scope' / 'launcher.py'


def run(cwd, home, *args, **extra):
    env = {'HOME': str(home), 'PATH': os.environ['PATH'], **extra}
    return subprocess.run([sys.executable, '-I', ENTRY, *args], cwd=cwd, env=env, capture_output=True, text=True)


def git(cwd, *args):
    subprocess.run(['git', '-C', cwd, *args], check=True, capture_output=True,
                   env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})


def world(tmp_path, enrol=True):
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@x', 'commit', '-q', '--allow-empty', '-m', 'i')
    repo = repo.resolve()
    fake = tmp_path / 'fake-runtime'
    fake.write_text('#!/bin/sh\necho "cwd=$(pwd -P)"\nfor a in "$@"; do echo "arg=$a"; done\n')
    fake.chmod(0o755)
    if enrol:
        assert run(repo, home, 'enroll', str(repo)).returncode == 0
    return home, repo, fake


def parse(out):
    lines = out.strip().splitlines()
    return lines[0].removeprefix('cwd='), [x.removeprefix('arg=') for x in lines[1:]]


def test_an_enrolled_project_gets_hook_root_and_its_private_store(tmp_path):
    home, repo, fake = world(tmp_path)
    result = run(repo, home, 'claude-hooks', str(fake), PWD=str(repo))
    assert result.returncode == 0, result.stderr
    cwd, args = parse(result.stdout)
    assert cwd == str(repo)
    assert args[0] == '--db' and args[1].startswith(str(home.resolve())) and '--actor' in args
    assert args[args.index('--hook-project-root') + 1] == str(repo / '.git').removesuffix('/.git')
    assert '--repo' not in args and '--hook-unenrolled' not in args


def test_an_unenrolled_project_gets_only_the_no_op_hook_server(tmp_path):
    home, repo, fake = world(tmp_path, enrol=False)
    result = run(repo, home, 'claude-hooks', str(fake), PWD=str(repo))
    assert result.returncode == 0, result.stderr
    cwd, args = parse(result.stdout)
    assert args == ['--namespace', 'unenrolled', '--hook-unenrolled']
    assert cwd == str(home.resolve())
    assert 'not verified' in result.stderr          # misconfiguration stays distinguishable from a quiet no-op


def test_a_mismatched_expected_project_also_falls_back_and_says_why(tmp_path):
    home, repo, fake = world(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    git(other, 'init', '-q')
    git(other, '-c', 'user.name=t', '-c', 'user.email=t@x', 'commit', '-q', '--allow-empty', '-m', 'i')
    assert run(other, home, 'enroll', str(other.resolve())).returncode == 0
    result = run(repo, home, 'claude-hooks', str(fake), '--expected-project', str(other.resolve()), PWD=str(repo))
    assert parse(result.stdout)[1] == ['--namespace', 'unenrolled', '--hook-unenrolled']
    assert 'not verified' in result.stderr


def test_the_existing_claude_command_is_unchanged(tmp_path):
    home, repo, fake = world(tmp_path)
    result = run(repo, home, 'claude', str(fake), PWD=str(repo))
    _, args = parse(result.stdout)
    assert '--repo' in args and '--hook-project-root' not in args and '--hook-unenrolled' not in args


def test_the_existing_claude_command_still_refuses_an_unenrolled_project(tmp_path):
    home, repo, fake = world(tmp_path, enrol=False)
    result = run(repo, home, 'claude', str(fake), PWD=str(repo))
    assert result.returncode == 1 and result.stdout == ''


def test_a_relative_runtime_is_refused(tmp_path):
    home, repo, _ = world(tmp_path)
    result = run(repo, home, 'claude-hooks', 'runtime', PWD=str(repo))
    assert result.returncode == 1 and result.stdout == ''
