"""Exercise the actual A3 model-process profile without launching a model or a server."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))
import isolation


@pytest.mark.skipif(sys.platform != "darwin", reason="the A3 arm uses macOS Seatbelt")
@pytest.mark.parametrize("version", ["a3-v1", "a3-v1-REHEARSAL"])
def test_actual_arm_profile_protects_operator_evidence(tmp_path, monkeypatch, version):
    # Supply the paths the runner validates when it loads. The fixture has no model or
    # source clone dependency; write_arm_profile is the same routine invoke uses.
    root = tmp_path.resolve()
    config = json.loads((BENCH / "a3-config-45.json").read_text())
    clone = root / "clone"
    (clone / ".git").mkdir(parents=True)
    config.update(config_version=version, source_clone=str(clone), pytest_python=sys.executable,
                  venv_python=sys.executable, nexus_server=sys.executable, repo=str(BENCH.parents[1]),
                  bench=str(BENCH), store_master="")
    cfg_file = root / "config.json"
    cfg_file.write_text(json.dumps(config))
    monkeypatch.setenv("A1_CONFIG", str(cfg_file))
    monkeypatch.setattr(sys, "argv", ["run_arms_isolated.py", str(root), "k1", "1"])
    spec = importlib.util.spec_from_file_location("fixture_arm_runner", BENCH / "run_arms_isolated.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    runner.OUT = root / "run-k1" / "attempt1" / "policy-B"
    arm = runner.OUT / "arms" / "nexus"
    cwd = arm / "repo"
    tmp = arm / "tmp"
    store = runner.arm_store("nexus")
    for directory in (cwd, tmp, store.parent):
        directory.mkdir(parents=True)
    # Isolate the runtime scratch probe from the user's actual Claude scratch.
    runtime_root = root / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(isolation, "RUNNER_SCRATCH_ENTRIES", (runtime_root,))
    runtime = isolation.runner_scratch_for(cwd)
    profile = runner.write_arm_profile("nexus", cwd)
    existing = arm / "a3-launch.json"
    existing.write_text("original")
    operator = root / ".a3-operator"
    receipt = operator / "receipts" / "cell.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("operator-owned")
    other = root / "run-k2" / "attempt2" / "policy-A" / "arms" / "nexus"
    other.mkdir(parents=True)
    # A symlink inside the writable checkout must not turn it into a grant on the ledger.
    (cwd / "ledger-link").symlink_to(arm, target_is_directory=True)
    protected = [receipt, existing, arm / "record.json", other / "record.json",
                 cwd / "ledger-link" / "trace.jsonl", receipt.parent / "new.json"]
    probe = """
import errno, json, os, sqlite3, sys
from pathlib import Path
runtime, store, temp, operator, *protected = map(Path, sys.argv[1:])
runtime.mkdir(exist_ok=True)
for allowed in [Path("can-write"), temp/"can-write", runtime/"can-write"]:
    allowed.write_text("ok")
with sqlite3.connect(store) as db:
    assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.execute("CREATE TABLE fixture(value)")
    db.execute("INSERT INTO fixture VALUES (42)")
failed = []
for path in protected:
    try:
        path.write_text("forged")
    except PermissionError:
        pass
    else:
        failed.append("write " + str(path))
try:
    protected[0].unlink()
except PermissionError:
    pass
else:
    failed.append("unlink")
try:
    os.link(protected[0], "linked-claim")
    Path("linked-claim").write_text("forged")
except OSError as exc:
    if exc.errno not in (errno.EPERM, errno.EACCES, errno.ENOENT):
        raise
else:
    failed.append("hard-link write")
try:
    operator.rename("moved-ledger")
except PermissionError:
    pass
else:
    failed.append("rename ledger")
print(json.dumps(failed))
"""
    # Intercept the model command at the last possible boundary. Use its actual sandbox
    # prefix, cwd and child environment for our offline adversarial Python process.
    real_run = subprocess.run
    monkeypatch.setattr(runner, "ENV_GATE", False)
    monkeypatch.setattr(runner, "run_identity", lambda: {})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "invalid-offline-test-key")

    def only_local_probe(argv, **kwargs):
        assert argv[:4] == ["sandbox-exec", "-f", str(profile), "claude"]
        assert kwargs["env"]["TMPDIR"] == str(tmp)
        done = real_run([*argv[:3], sys.executable, "-I", "-c", probe, runtime, store,
                         tmp, operator, *protected], cwd=kwargs["cwd"], env=kwargs["env"],
                        text=True, capture_output=True, timeout=30)
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == []
        shell = real_run([*argv[:3], "/bin/sh", "-c",
                          'printf ok >shell-file && ! (printf forged >"$1")', "probe", receipt],
                         cwd=kwargs["cwd"], env=kwargs["env"], capture_output=True, timeout=30)
        assert shell.returncode == 0, shell.stderr
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", only_local_probe)
    runner.invoke("nexus", cwd)
    assert existing.read_text() == "original"
    assert receipt.read_text() == "operator-owned" and receipt.stat().st_nlink == 1
    assert not (arm / "record.json").exists() and not (other / "record.json").exists()
    assert (cwd / "can-write").read_text() == (tmp / "can-write").read_text() == "ok"
