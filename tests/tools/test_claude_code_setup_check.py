import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "check_claude_code_setup.py"
LAUNCHER = ROOT / "integrations" / "project_scope" / "launcher.py"


def run(runtime: Path, launcher: Path = LAUNCHER):
    return subprocess.run([sys.executable, CHECK, "--runtime", runtime, "--launcher", launcher],
                          capture_output=True, text=True)


def runtime_script(tmp_path: Path, help_text: str, code: int = 0) -> Path:
    path = tmp_path / "runtime"
    path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{help_text}\nEOF\nexit {code}\n")
    path.chmod(0o755)
    return path


def test_a_ready_machine_passes_and_nothing_is_written(tmp_path):
    ready = runtime_script(tmp_path, "usage: --hook-project-root --hook-unenrolled")
    before = sorted(p.name for p in tmp_path.iterdir())
    result = run(ready)
    assert result.returncode == 0, result.stdout
    assert result.stdout.count("PASS:") == 4 and "FAIL" not in result.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_an_older_runtime_that_rejects_the_flags_fails(tmp_path):
    older = runtime_script(tmp_path, "error: unrecognized arguments: --hook-project-root", code=2)
    result = run(older)
    assert result.returncode == 1 and "FAIL: runtime" in result.stdout


def test_a_launcher_without_the_command_fails(tmp_path):
    old_launcher = tmp_path / "launcher.py"
    old_launcher.write_text("import sys; print('usage: enroll codex claude antigravity')")
    result = run(runtime_script(tmp_path, "--hook-project-root --hook-unenrolled"), old_launcher)
    assert result.returncode == 1 and "FAIL: launcher" in result.stdout
