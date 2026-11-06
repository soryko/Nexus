from __future__ import annotations

import http.client
import os
import re
import signal
import subprocess
import sys
import time

from conftest import INTEGRATION

LAUNCHER = INTEGRATION / "launcher.py"


def _env(home):
    return {"HOME": str(home), "PATH": os.environ["PATH"]}


def test_unenrolled_checkout_is_refused_with_a_message_not_a_traceback(enrolled) -> None:
    other = enrolled.new_checkout("elsewhere")
    result = subprocess.run([sys.executable, "-I", str(LAUNCHER), "atlas", str(other), "--no-browser"],
                            env=_env(enrolled.home), capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert "Atlas unavailable" in result.stderr


def test_relative_checkout_is_refused(enrolled) -> None:
    result = subprocess.run([sys.executable, "-I", str(LAUNCHER), "atlas", "repo", "--no-browser"],
                            env=_env(enrolled.home), capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "absolute" in result.stderr


def test_launch_serves_the_shell_prints_no_secret_and_stops_on_interrupt(enrolled) -> None:
    process = subprocess.Popen([sys.executable, "-I", str(LAUNCHER), "atlas", str(enrolled.checkout), "--no-browser"],
                               env=_env(enrolled.home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        first = process.stdout.readline()
        port = int(re.search(r"http://127\.0\.0\.1:(\d+)/", first).group(1))
        launch_line = process.stdout.readline()
        assert "launch.html" in launch_line
        launch_file = launch_line.rsplit(": ", 1)[1].strip()
        secret = re.search(r"#k=([\w-]+)", open(launch_file).read()).group(1)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        assert connection.getresponse().status == 200
        connection.request("GET", "/api/overview", headers={"Host": f"127.0.0.1:{port}"})
        assert connection.getresponse().status == 401
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0 and "Traceback" not in err
        assert secret not in first + launch_line + out + err
        assert not os.path.exists(launch_file)
    finally:
        if process.poll() is None:
            process.kill()


def test_a_runtime_without_atlas_says_so_instead_of_blaming_the_interpreter(enrolled, tmp_path) -> None:
    import shutil
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("launcher.py", "project_scope.py"):
        shutil.copyfile(INTEGRATION / name, bin_dir / name)
    (bin_dir / "nexus_memory").mkdir()                       # an older runtime: the package exists, Atlas does not
    (bin_dir / "nexus_memory" / "__init__.py").write_text("")
    result = subprocess.run([sys.executable, "-I", str(bin_dir / "launcher.py"), "atlas", str(enrolled.checkout), "--no-browser"],
                            env=_env(enrolled.home), capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert result.returncode == 1 and "does not include Atlas" in result.stderr and "Traceback" not in result.stderr
