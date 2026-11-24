"""The grading boundary is exercised against real pytest and synthetic candidate code only."""
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

import build_fixture
import score_compliance


@pytest.mark.parametrize("redirect", ["leaf", "parent", "absolute", "traversal"])
def test_hidden_check_cannot_follow_candidate_paths(tmp_path, redirect):
    tree, held, outside = [tmp_path / part for part in ("tree", "held", "outside")]
    for folder in (tree, held, outside):
        folder.mkdir()
    (held / "tests").mkdir()
    (held / "tests/test_hidden.py").write_text("def test_hidden(): pass\n")
    marker = outside / "test_hidden.py"
    marker.write_text("unchanged")
    check = "tests/test_hidden.py"
    if redirect == "leaf":
        (tree / "tests").mkdir()
        (tree / check).symlink_to(marker)
    elif redirect == "parent":
        (tree / "tests").symlink_to(outside)
    else:
        check = str(marker) if redirect == "absolute" else "../outside/test_hidden.py"
    result = build_fixture.score(tree, held, [check], sys.executable, tmp_path / "score")
    assert marker.read_text() == "unchanged"
    assert result["passed"] is None and result["instrument_error"]


@pytest.fixture
def listening_port():
    # A connection refusal on an unoccupied port would prove nothing.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        address = listener.getsockname()
        with socket.create_connection(address, timeout=2):
            client, _ = listener.accept()
            client.close()
        yield address[1]


@pytest.mark.parametrize("stage", ["functional", "compliance"])
def test_candidate_runs_without_host_capabilities(tmp_path, stage, listening_port, monkeypatch):
    tree, held = tmp_path / "tree", tmp_path / "held"
    tree.mkdir()
    held.mkdir()
    monkeypatch.setenv("NEXUS_TEST_HOST_SECRET", "private")
    secret = tmp_path / "sibling-secret"
    secret.write_text("local fixture")
    outside = tmp_path / "sibling-output"
    # The positive control (a real pytest PASS) rules out a backend which simply never runs.
    payload = f"""
import os, pathlib, socket, subprocess, sys
def test_permissions():
    import pytest
    (pathlib.Path.cwd() / "candidate-output").write_text("ok")
    (pathlib.Path(os.environ["TMPDIR"]) / "scratch-output").write_text("ok")
    if sys.platform == "linux":
        # The private root and /dev are separate tmpfs mounts. Neither is an output area.
        for mount in ("/", "/dev"):
            assert os.statvfs(mount).f_flag & os.ST_RDONLY
    assert "NEXUS_TEST_HOST_SECRET" not in os.environ
    # Darwin prohibits introducing a new subprocess. Linux children inherit the private
    # namespace. Neither may write a host sibling or signal a process in the host session.
    try:
        subprocess.run([sys.executable, "-c", "from pathlib import Path; Path(" +
                        {repr(str(outside))!r} + ").touch()"], capture_output=True, timeout=3)
    except PermissionError:
        pass
    with pytest.raises(OSError):
        pathlib.Path({str(secret)!r}).read_text()
    with pytest.raises(OSError):
        pathlib.Path({str(outside)!r}).write_text("escaped")
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", {listening_port}), timeout=0.2)
    with pytest.raises((PermissionError, ProcessLookupError)):
        os.kill({os.getpid()}, 0)
"""
    # Execute candidate-controlled code also during collection.
    (tree / "conftest.py").write_text(f"from pathlib import Path\ntry: Path({str(outside)!r}).write_text('escaped')\nexcept OSError: pass\n")
    (tree / "test_candidate.py").write_text(payload)
    (held / "test_candidate.py").write_text(payload)
    if stage == "functional":
        result = build_fixture.score(tree, held, ["test_candidate.py"], sys.executable, tmp_path / "score")
        assert result["passed"] is True, result
    else:
        result = score_compliance._pytest(sys.executable, tree, ["test_candidate.py"])
        assert result["verdict"] == "passed", result
    assert not outside.exists()


def test_unavailable_backend_never_executes_candidate(tmp_path, monkeypatch):
    tree = tmp_path / "candidate"
    tree.mkdir()
    output = tmp_path / "executed"
    (tree / "conftest.py").write_text(f"from pathlib import Path\nPath({str(output)!r}).touch()")
    (tree / "test_ok.py").write_text("def test_ok(): pass\n")
    monkeypatch.setattr(sys, "platform", "unsupported")
    r = score_compliance._pytest(sys.executable, tree, ["test_ok.py"])
    assert not output.exists()
    assert r["instrument_error"]


def test_candidate_git_filter_cannot_touch_host(tmp_path):
    import scorer_sandbox
    tree = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    marker = tmp_path / "outside"
    (tree / ".gitattributes").write_text("hello filter=untrusted\n")
    subprocess.run(["git", "-C", str(tree), "config", "filter.untrusted.clean",
                    f"touch '{marker}'; cat"], check=True)
    (tree / "hello").write_text("hello\n")
    try:
        patch = scorer_sandbox.candidate_patch(tree)
    except subprocess.CalledProcessError:
        pass  # explicitly rejecting a forbidden filter is safe too
    else:
        assert "+hello" in patch
    assert not marker.exists()

def test_legitimate_candidate_diff_and_regression_patch_work(tmp_path):
    import scorer_sandbox
    tree = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    (tree / "hello").write_text("hello\n")
    patch = scorer_sandbox.candidate_patch(tree)
    assert "+hello" in patch
    (tree / "hello").unlink()
    applied = score_compliance._apply(patch, tree)
    assert applied.returncode == 0, applied.stderr
    assert (tree / "hello").read_text() == "hello\n"


def test_parent_symlink_cannot_substitute_a_host_tree(tmp_path):
    import scorer_sandbox
    real = tmp_path / "host" / "repo"
    real.mkdir(parents=True)
    (real / "private-file").write_text("must not be staged")
    (tmp_path / "arm").symlink_to(real.parent)
    destination = tmp_path / "private-copy"
    with pytest.raises(OSError):
        scorer_sandbox.copy_candidate(tmp_path / "arm" / "repo", destination)
    assert not (destination / "private-file").exists()


def test_hidden_check_source_cannot_be_a_redirected_directory(tmp_path):
    tree, host, redirected = (tmp_path / p for p in ("tree", "host", "redirected"))
    tree.mkdir()
    host.mkdir()
    redirected.symlink_to(host)
    (host / "test_secret.py").write_text("def test_host_file(): pass\n")
    outcome = build_fixture.score(tree, redirected, ["test_secret.py"], sys.executable, tmp_path / "score")
    assert "instrument_error" in outcome
    assert "redirected" in outcome["instrument_error"]


def test_artifact_writer_and_reader_reject_link_tricks(tmp_path):
    import artifact_io as io
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    folder = tmp_path / "arm"
    folder.mkdir()
    for name, create in (("symbolic", lambda p: p.symlink_to(outside)),
                         ("hard", lambda p: os.link(outside, p)),
                         ("fifo", os.mkfifo)):
        target = folder / name
        create(target)
        with pytest.raises(OSError):
            io.write_text(target, "replacement")
        with pytest.raises(OSError):
            io.read_text(target)
        assert outside.read_text() == "untouched"
    redirect = folder / "redirect"
    redirect.symlink_to(tmp_path)
    with pytest.raises(OSError):
        io.create_text(redirect / "new-target", "bad", parents=True)
    assert not (tmp_path / "new-target").exists()
    receipt = folder / "new" / "receipt"
    io.create_text(receipt, "first", parents=True)
    with pytest.raises(FileExistsError):
        io.create_text(receipt, "replayed")
    assert io.read_text(receipt) == "first"
    io.write_text(receipt, "replacement")
    assert io.read_text(receipt) == "replacement"


def test_packaged_traces_stay_readable_and_cannot_redirect(tmp_path):
    import gzip
    import trace_parse
    name = tmp_path / "a.jsonl"
    archived = name.with_suffix(".jsonl.gz")
    archived.write_bytes(gzip.compress(b'{"type":"result","result":"ok"}\n'))
    assert trace_parse.parse(name)["result"]["result"] == "ok"
    name.symlink_to(archived)
    with pytest.raises(OSError):
        trace_parse.parse(name)


def test_broken_pytest_startup_is_not_a_failed_candidate(monkeypatch, tmp_path):
    import scorer_sandbox
    monkeypatch.setattr(scorer_sandbox, "run", lambda *a, **k:
                        subprocess.CompletedProcess(["pytest"], 1, "", "python: startup failed"))
    tree = tmp_path / "candidate"
    tree.mkdir()
    result = build_fixture.score(tree, tmp_path, [], sys.executable, tmp_path / "unused")
    assert result["passed"] is None and result["instrument_error"]
    assert score_compliance._pytest(sys.executable, tree, [])["instrument_error"]


@pytest.fixture
def source_revisions(tmp_path):
    clone = tmp_path / "source"
    clone.mkdir()
    git = ["git", "-C", str(clone), "-c", "user.name=Test", "-c", "user.email=test@localhost"]
    subprocess.run([*git, "init", "-q"], check=True)
    (clone / "tests").mkdir()
    test = clone / "tests/test_example.py"
    test.write_text("def test_existing(): pass\n")
    subprocess.run([*git, "add", "tests"], check=True)
    subprocess.run([*git, "commit", "-qm", "pre-fix"], check=True)
    parent = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    test.write_text("def test_hidden(): pass\n")
    subprocess.run([*git, "commit", "-qam", "fix"], check=True)
    tip = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    return clone, {"task": "example", "fix": tip, "pre_fix": parent}


def test_fixture_and_oracle_never_delete_an_existing_host_tree(tmp_path, source_revisions):
    clone, task = source_revisions
    host = tmp_path / "host"
    marker = host / "example" / "protected"
    marker.parent.mkdir(parents=True)
    marker.touch()
    redirect = tmp_path / "fixture"
    redirect.symlink_to(host)
    with pytest.raises(OSError):
        build_fixture.build(clone, task, redirect / "example")
    assert marker.exists()
    build_fixture.fix_oracle(clone, task, ["tests/test_example.py"], sys.executable,
                             redirect / "example")
    assert marker.exists()


def test_fresh_fixture_is_published_with_working_oracle(tmp_path, source_revisions):
    clone, task = source_revisions
    dest = tmp_path / "fixture" / task["task"]
    fixture = build_fixture.build(clone, task, dest)
    assert (dest / "tests/test_example.py").read_text().startswith("def test_existing")
    assert (Path(fixture["held_checks"]) / "tests/test_example.py").read_text().startswith("def test_hidden")
    assert build_fixture.fix_oracle(clone, task, fixture["checks"], sys.executable,
                                    tmp_path / "unused")["passed"]
    with pytest.raises(FileExistsError):
        build_fixture.build(clone, task, dest)
