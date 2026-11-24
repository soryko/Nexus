import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "check_claude_code_setup.py"
LAUNCHER = ROOT / "integrations" / "project_scope" / "launcher.py"


def run(runtime: Path, launcher: Path = LAUNCHER, *args):
    return subprocess.run([sys.executable, CHECK, "--runtime", runtime, "--launcher", launcher, *args],
                          capture_output=True, text=True)


def runtime_script(tmp_path: Path, help_text: str, code: int = 0) -> Path:
    path = tmp_path / "runtime"
    path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{help_text}\nEOF\nexit {code}\n")
    path.chmod(0o755)
    return path


def test_a_ready_machine_passes_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    checkout = tmp_path / "work tree"
    subprocess.run(["git", "init", "-q", checkout], check=True)
    launcher = tmp_path / "bin" / "launcher.py"
    launcher.parent.mkdir()
    shutil.copy(LAUNCHER, launcher)
    shutil.copy(LAUNCHER.with_name("project_scope.py"), launcher.parent)
    enrolled = subprocess.run([sys.executable, "-I", "-B", launcher, "enroll", checkout],
                              check=True, capture_output=True, text=True).stdout.strip()
    ready = runtime_script(tmp_path, "usage: --hook-project-root --hook-unenrolled")

    def snapshot():
        return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}

    before = snapshot()
    result = run(ready, launcher, "--checkout", checkout, "--expected-project-id", enrolled)
    assert result.returncode == 0, result.stdout
    assert "PASS: installed resolver" in result.stdout and enrolled in result.stdout
    wrong = run(ready, launcher, "--checkout", checkout, "--expected-project-id", "00000000-0000-0000-0000-000000000000")
    assert wrong.returncode == 1 and "different project ID" in wrong.stdout
    assert snapshot() == before


def test_untested_binding_cannot_pass(tmp_path):
    result = run(runtime_script(tmp_path, "--hook-project-root --hook-unenrolled"))
    assert result.returncode == 1 and "FAIL: project binding not tested" in result.stdout


def test_missing_sibling_resolver_does_not_fall_back(tmp_path, monkeypatch):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    expected = "00000000-0000-0000-0000-000000000000"
    (decoy / "project_scope.py").write_text(
        f"from types import SimpleNamespace\ndef resolve(_): return SimpleNamespace(id={expected!r})\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(decoy))
    launcher = tmp_path / "launcher.py"
    launcher.write_text("print('claude-hooks')")
    result = run(runtime_script(tmp_path, "--hook-project-root --hook-unenrolled"), launcher,
                 "--checkout", tmp_path, "--expected-project-id", expected)
    assert result.returncode == 1 and "FAIL: installed resolver could not verify" in result.stdout


def test_an_older_runtime_that_rejects_the_flags_fails(tmp_path):
    older = runtime_script(tmp_path, "error: unrecognized arguments: --hook-project-root", code=2)
    result = run(older)
    assert result.returncode == 1 and "FAIL: runtime" in result.stdout


def test_a_launcher_without_the_command_fails(tmp_path):
    old_launcher = tmp_path / "launcher.py"
    old_launcher.write_text("import sys; print('usage: enroll codex claude antigravity')")
    result = run(runtime_script(tmp_path, "--hook-project-root --hook-unenrolled"), old_launcher)
    assert result.returncode == 1 and "FAIL: launcher" in result.stdout
