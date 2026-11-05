#!/usr/bin/env python3
"""Build a disposable world for the interactive checks in docs/hook-engine-phase1-interactive-checks.md.

Synthetic data, throwaway HOME, patched COPY of the PR #17 launcher. Writes launch scripts into WORKDIR/bin.
Never touches ~/.claude*, ~/.codex, the installed launcher or any real store.

usage: interactive_setup.py WORKDIR [--nexus-memory-repo PATH]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_matrix as cm  # noqa: E402


def script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -e\n" + body)
    path.chmod(0o755)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--nexus-memory-repo", type=Path, default=Path("/Users/soko/Cerebros/nexus-memory"))
    a = ap.parse_args()
    work = a.workdir.resolve()
    work.mkdir(parents=True)
    w = cm.World(work, a.nexus_memory_repo)
    w.enroll("A")
    anyio.run(w.seed, "A")  # three synthetic memories about "quasarflux"
    bin_dir = work / "bin"
    bin_dir.mkdir()
    A, C = w.repos["A"], w.repos["C"]
    cmd = w.launcher_cmd()

    def mcp_config(name: str, command: list[str]) -> Path:
        p = work / name
        p.write_text(json.dumps({"mcpServers": {"nexus-memory": {
            "command": command[0], "args": command[1:], "env": {"HOME": str(w.home)}}}}))
        return p

    normal = mcp_config("mcp-normal.json", cmd)
    slow_rt = work / "runtime-slow.sh"
    slow_rt.write_text(f"#!/bin/sh\nsleep 6\nexec {w.runtime} \"$@\"\n")
    slow_rt.chmod(0o755)
    slow = mcp_config("mcp-slow.json", [cmd[0], *cmd[1:-1], str(slow_rt)])
    empty = work / "mcp-empty.json"
    empty.write_text('{"mcpServers":{}}')

    # Plugin copy with its hooks removed, to see the status command's "hooks not trusted" line.
    nohooks = work / "plugin-nohooks"
    shutil.copytree(cm.PLUGIN, nohooks)
    (nohooks / "hooks" / "hooks.json").write_text('{"hooks": {}}')

    # A local marketplace in a throwaway config dir, for the plugin-trust checks.
    market = work / "marketplace"
    (market / ".claude-plugin").mkdir(parents=True)
    shutil.copytree(cm.PLUGIN, market / "plugins" / "nexus")
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "nexus-local", "owner": {"name": "local"},
        "plugins": [{"name": "nexus", "source": "./plugins/nexus", "description": "Nexus hooks (disposable copy)"}]}))

    env = (f'export NEXUS_SCOPE_BIN="{w.bin}" NEXUS_SCOPE_HOME="{w.home}"\n'
           f'export WORK="{work}"\n')

    def claude(name: str, cwd: Path, mcp: Path, plugin: Path | None, extra: str = "", config_dir: bool = False) -> None:
        plug = f'--plugin-dir "{plugin}"' if plugin else ""
        cfg = f'export CLAUDE_CONFIG_DIR="{work}/cfg"\n' if config_dir else ""
        script(bin_dir / name, env + cfg + f'cd "{cwd}"\nexport PWD="{cwd}"\n'
               f'exec claude --model haiku --strict-mcp-config --mcp-config "{mcp}" {plug} {extra} '
               f'--debug hooks,mcp --debug-file "{work}/debug-{name}.log" "$@"\n')

    claude("claude-A.sh", A, normal, cm.PLUGIN)                      # enrolled project, normal start
    claude("claude-slow-A.sh", A, slow, cm.PLUGIN)                   # server takes ~6 s to connect
    claude("claude-C.sh", C, normal, cm.PLUGIN)                      # unenrolled project
    claude("claude-noserver-A.sh", A, empty, cm.PLUGIN)              # plugin hooks, no nexus-memory server at all
    claude("claude-nohooks-A.sh", A, normal, nohooks)                # enrolled, but the plugin's hooks are empty
    claude("claude-throwaway-A.sh", A, normal, None, config_dir=True)  # throwaway CLAUDE_CONFIG_DIR, no --plugin-dir
    script(bin_dir / "kill-server.sh", f'''# Stops only the disposable nexus servers of this WORKDIR (matched by its own store path).
PIDS=$(pgrep -f -- "{w.home}/.local/share/nexus-memory" || true)
echo "disposable server pids: ${{PIDS:-none}}"
[ -n "$PIDS" ] && kill $PIDS && echo killed
''')
    script(bin_dir / "grep-hooks.sh", f'''# usage: grep-hooks.sh <debug-name>   e.g. claude-A.sh
grep -E "mcp_tool|Hook (SessionStart|UserPromptSubmit|PreToolUse)|waiting up to|not connected|timed out|SessionStart:(clear|startup|compact)" "{work}/debug-$1.log" | cut -c1-240
''')

    # Codex: throwaway CODEX_HOME and a project with hook definitions (old names, then edited names).
    codex_home, proj = work / "codex-home", work / "codex-proj"
    shutil.copytree(A, proj, symlinks=True)
    (proj / ".codex").mkdir()
    hooks_toml = lambda tool_recall, tool_start: f'''
[mcp_servers.nexus_memory]
command = "{w.runtime}"
args = ["--namespace", "codex-disposable", "--actor", "local", "--db", "{work}/codex.sqlite3", "--hook-project-root", "{proj}"]
startup_timeout_sec = 10
tool_timeout_sec = 10
required = false

[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"
[[hooks.SessionStart.hooks]]
type = "mcp_tool"
server = "nexus_memory"
tool = "{tool_start}"
input = {{ cwd = "${{cwd}}", session_id = "${{session_id}}" }}
timeout = 2

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "mcp_tool"
server = "nexus_memory"
tool = "{tool_recall}"
input = {{ cwd = "${{cwd}}", session_id = "${{session_id}}", prompt = "${{prompt}}" }}
timeout = 2
'''
    (work / "codex-config-v1.toml").write_text(hooks_toml("codex_recall", "codex_session_start"))
    (work / "codex-config-v2.toml").write_text(hooks_toml("nexus_recall", "nexus_session_start"))
    shutil.copy(work / "codex-config-v1.toml", proj / ".codex" / "config.toml")
    script(bin_dir / "codex-run.sh", f'export CODEX_HOME="{codex_home}"\nmkdir -p "$CODEX_HOME"\ncd "{proj}"\nexec codex "$@"\n')
    script(bin_dir / "codex-edit-hook.sh", f'cp "{work}/codex-config-v2.toml" "{proj}/.codex/config.toml"\necho "hook definition changed: codex_recall -> nexus_recall"\n')
    script(bin_dir / "codex-restore-hook.sh", f'cp "{work}/codex-config-v1.toml" "{proj}/.codex/config.toml"\necho restored v1\n')

    (work / "README.txt").write_text(f"WORKDIR={work}\nscripts: {bin_dir}\nmarketplace: {market}\nproject A (enrolled): {A}\n"
                                     f"project C (unenrolled): {C}\ncodex project: {proj}\n")
    print((work / "README.txt").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
