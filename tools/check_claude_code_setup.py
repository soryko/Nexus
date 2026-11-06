#!/usr/bin/env python3
"""Read-only check that a machine is ready for the Nexus Claude Code plugin. It changes nothing.

    python3 tools/check_claude_code_setup.py --runtime ~/.local/share/nexus-memory/venvs/0.1.0a3/bin/nexus-memory \
        --launcher ~/.local/share/nexus-memory/project-scopes-v1/bin/launcher.py

Each line is PASS, FAIL or INFO. Exit status is 1 if anything failed. It runs the runtime with --help and
the launcher with --help only; it never enrolls, starts a store, or reads a client configuration.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    return done.returncode, done.stdout + done.stderr


def check(runtime: Path, launcher: Path) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    def add(ok: bool, text: str) -> None:
        results.append(("PASS" if ok else "FAIL", text))

    code, text = _run([str(runtime), "--help"])
    add(code == 0 and "--hook-project-root" in text and "--hook-unenrolled" in text,
        f"runtime {runtime} understands --hook-project-root and --hook-unenrolled"
        + ("" if code == 0 else " (it did not start; an older build rejects these flags)"))
    code, text = _run([sys.executable, "-I", str(launcher), "--help"])
    add(code == 0 and "claude-hooks" in text, f"launcher {launcher} offers the claude-hooks command")
    plugin = ROOT / "integrations" / "claude-plugin"
    market = ROOT / ".claude-plugin" / "marketplace.json"
    try:
        listing = json.loads(market.read_text())
        entry = listing["plugins"][0]
        ok = entry["name"] == "nexus-memory" and (market.parent.parent / entry["source"] / ".claude-plugin" / "plugin.json").is_file()
    except (OSError, ValueError, KeyError, IndexError):
        ok = False
    add(ok, f"marketplace {market} lists the plugin and its source exists")
    hooks = plugin / "hooks" / "hooks.json"
    try:
        events = set(json.loads(hooks.read_text())["hooks"])
    except (OSError, ValueError, KeyError):
        events = set()
    add(events == {"SessionStart", "UserPromptSubmit", "PreToolUse"}, f"plugin hooks cover {sorted(events)}")
    results.append(("INFO", "Claude trust for the plugin's hooks cannot be read from here: run /nexus-memory:status in Claude Code."))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    args = parser.parse_args()
    results = check(args.runtime, args.launcher)
    for status, text in results:
        print(f"{status}: {text}")
    return 1 if any(status == "FAIL" for status, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
