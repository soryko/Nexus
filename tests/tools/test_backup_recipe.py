"""Execute the documented backup recipe exactly as a user would, on disposable databases."""
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


GUIDE = Path(__file__).resolve().parents[2] / "docs/experimental-release.md"


def backup(source, destination):
    section = GUIDE.read_text().split("### Back up with the online backup API", 1)[1]
    script = section.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    return subprocess.run([sys.executable, "-", str(source), str(destination)],
                          input=script, capture_output=True, text=True, umask=0o022)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_backup_is_private_even_under_permissive_umask(tmp_path):
    source = tmp_path / "original #1.sqlite3"
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE TABLE data AS SELECT 'private text' AS body")
    source.chmod(0o600)
    destination = tmp_path / "new" / "backups" / "snapshot #1.sqlite3"
    run = backup(source, destination)
    assert run.returncode == 0, run.stderr
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute("SELECT body FROM data").fetchone() == ("private text",)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(destination.parent.parent.stat().st_mode) == 0o700


def test_backup_refuses_dangling_link_and_preserves_existing_files(tmp_path):
    source = tmp_path / "db"
    with closing(sqlite3.connect(source)) as db:
        db.execute("CREATE TABLE data(body)")
    victim = tmp_path / "elsewhere"
    destination = tmp_path / "backup"
    destination.symlink_to(victim)
    assert backup(source, destination).returncode != 0
    assert destination.is_symlink() and not victim.exists()
    destination.unlink()
    destination.write_bytes(b"keep")
    assert backup(source, destination).returncode != 0
    assert destination.read_bytes() == b"keep"


def test_failed_backup_never_publishes_incomplete_copy(tmp_path):
    source, destination = tmp_path / "invalid", tmp_path / "backup"
    source.write_bytes(b"invalid database" * 20)
    assert backup(source, destination).returncode != 0
    assert not destination.exists()
