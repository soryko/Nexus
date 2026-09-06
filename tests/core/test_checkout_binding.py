"""Regressions for the checkout a bound process follows, from the audit of 0b0a217.

The binding is made once at launch and the pathname is followed on every later call. What
lives at that path can change, so what the process believes about it has to be re-read.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from reference_fakes import OID1, FakeVerifier, binding, entry, register
from test_repository_identity import GIT, SCOPE, bind, common_dir, git, init_repo

from nexus_memory.domain.errors import (
    RepositoryMismatch,
    RepositoryRegistrationFailed,
    RepositoryUnbound,
    VerificationUnavailable,
)
from nexus_memory.domain.models import MemoryInput, ReferenceInput
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.git.identity import token_path
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

PROJECT_ROOT = Path(__file__).parents[2]
Ref = ReferenceInput


def stub_git(directory: Path, script: str, executable: bool = True) -> GitCli:
    """A `git` that is present and runnable but cannot answer, or cannot be run at all."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "git"
    path.write_text(script)
    path.chmod(0o755 if executable else 0o644)
    return GitCli(str(path))


def failing_git(directory: Path) -> GitCli:
    return stub_git(directory, "#!/bin/sh\necho 'fatal: cannot answer' >&2\nexit 128\n")


def unusable_git(directory: Path) -> GitCli:
    return stub_git(directory, "#!/bin/sh\nexit 0\n", executable=False)


def service(db: Path, checkout: Path, git_cli: GitCli | None = GIT) -> MemoryService:
    store = SQLiteRepository(db)
    bound, verifier = bind_repository(store, SCOPE, checkout, None, git_cli)
    return MemoryService(store, SCOPE, bound, verifier)


# --- 1: a checkout replaced under a bound process ------------------------------------------


def test_a_replaced_checkout_is_refused_and_the_original_receipt_still_replays(tmp_path: Path) -> None:
    """The identity a reference records must be true when the reference is taken.

    The common directory is a locator, reused by whatever is put at that path next, so a
    repository that moves away and is replaced would otherwise have the replacement's
    commits resolved through the surviving pathname and stamped with its own identity.
    Reproduced at 0b0a217: a commit that existed only in the newcomer was stored under the
    departed repository's `repository_id`.
    """
    db = tmp_path / "memory.sqlite3"
    path = tmp_path / "checkout"
    init_repo(path)
    svc = service(db, path)
    identity = svc.binding.repository_id
    item = MemoryInput("recorded against the first repository", references=(Ref("file.txt"),))
    receipt = svc.record(item, "first")
    evidence = svc.get(receipt.memory_id).references
    assert [reference.repository_id for reference in evidence] == [identity]

    # A second repository, registered in its own right, takes the first one's place.
    shutil.move(str(path), str(tmp_path / "departed"))
    other = init_repo(tmp_path / "other")
    (other / "only-here.txt").write_text("absent from the first repository\n")
    git(other, "add", "only-here.txt")
    git(other, "commit", "-qm", "only here")
    newcomer = bind(db, other).repository_id
    assert newcomer != identity
    shutil.move(str(other), str(path))

    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("x", references=(Ref("only-here.txt"),)), "newcomer-only")
    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("x", references=(Ref("file.txt"),)), "shared-path")
    assert svc.status().active_memories == 1
    assert svc.get(receipt.memory_id).references == evidence

    # The replay is ahead of the check, which is what a retry after any repository change
    # depends on: it reads a committed receipt and never asks the checkout anything.
    assert svc.record(item, "first") == receipt

    # And the check refuses a changed binding, not a changed path: restore the repository
    # and the same process verifies again, under the identity it has had all along.
    shutil.move(str(path), str(tmp_path / "newcomer"))
    shutil.move(str(tmp_path / "departed"), str(path))
    restored = svc.record(MemoryInput("restored", references=(Ref("file.txt"),)), "restored")
    assert [r.repository_id for r in svc.get(restored.memory_id).references] == [identity]


def test_a_deleted_or_replaced_token_is_a_mismatch_not_a_fresh_identity(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    svc = service(db, repo)
    item = MemoryInput("x", references=(Ref("file.txt"),))
    svc.record(item, "before")

    token_path(common_dir(repo)).unlink()
    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("x", references=(Ref("file.txt"),)), "after-removal")
    assert svc.record(item, "before").operation == "record"  # the replay is unaffected


def test_the_identity_check_runs_once_per_verifying_write_and_never_before_a_replay(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = FakeVerifier({"HEAD": OID1}, {(OID1, "a"): (entry("a"),)})
    store = SQLiteRepository(db)          # migrates, so the identity row below has a table
    register(db, SCOPE, "repo-a")
    svc = MemoryService(store, SCOPE, binding("repo-a"), verifier)
    item = MemoryInput("one", references=(Ref("a"),))

    receipt = svc.record(item, "k")
    assert verifier.identity_checks == 1
    assert svc.record(item, "k") == receipt
    assert verifier.identity_checks == 1          # the replay never asks the checkout
    svc.record(MemoryInput("no references"), "plain")
    assert verifier.identity_checks == 1          # neither does a write that carries none

    verifier.identity_error = RepositoryMismatch("the bound checkout now resolves to a different repository")
    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("two", references=(Ref("a"),)), "k2")
    assert verifier.resolutions == ["HEAD"]       # refused before any further resolution
    assert svc.record(item, "k") == receipt


# --- 2: a git that is present but cannot answer --------------------------------------------


@pytest.mark.parametrize("make_git", [failing_git, unusable_git], ids=["exits-nonzero", "not-executable"])
def test_a_git_that_cannot_answer_degrades_like_a_missing_one(tmp_path: Path, make_git) -> None:
    """Verification is never a startup dependency, and an unusable binary is not an exception.

    Reproduced at 0b0a217 through the CLI: with a registered checkout and existing
    memories, a `git` that exits non-zero gave `startup_error: repository_unbound` and exit
    1, while a `git` that was simply absent started normally. One unusable binary took the
    whole server down, including every path that never shells out.
    """
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    identity = bind(db, repo).repository_id
    service(db, repo).record(MemoryInput("written while git worked"), "existing")

    degraded = service(db, repo, make_git(tmp_path / make_git.__name__))
    assert degraded.binding is not None and degraded.binding.repository_id == identity
    assert degraded.verifier is None
    assert degraded.status().repository_id == identity
    assert degraded.status().verification == "unavailable"
    assert degraded.status().active_memories == 1
    with pytest.raises(VerificationUnavailable):
        degraded.record(MemoryInput("x", references=(Ref("file.txt"),)), "refs")
    assert degraded.record(MemoryInput("plain writes still work"), "plain").operation == "record"


def test_a_broken_git_does_not_swallow_a_wrong_path_or_a_bad_token(tmp_path: Path) -> None:
    """Degrading must not turn every launch into a success.

    The fallback applies where the checkout is discoverable without git; where it is not,
    the path may really not be a checkout and the operator hears the original error.
    """
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    bind(db, repo)
    broken = failing_git(tmp_path / "broken")
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(RepositoryUnbound):
        bind_repository(SQLiteRepository(db), SCOPE, plain, None, broken)
    with pytest.raises(RepositoryUnbound):
        bind_repository(SQLiteRepository(db), SCOPE, tmp_path / "missing", None, broken)

    token_path(common_dir(repo)).write_text("truncated")
    with pytest.raises(RepositoryRegistrationFailed):
        bind_repository(SQLiteRepository(db), SCOPE, repo, None, broken)


def test_the_cli_starts_with_a_registered_checkout_and_a_git_that_cannot_answer(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    bind(db, repo)
    service(db, repo).record(MemoryInput("existing"), "existing")

    shadow = tmp_path / "shadow"
    failing_git(shadow)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    env["PATH"] = os.pathsep.join([str(shadow), env.get("PATH", "")])
    started = subprocess.run(
        [sys.executable, "-m", "nexus_memory", "--namespace", SCOPE.namespace, "--actor", SCOPE.actor,
         "--db", str(db), "--repo", str(repo)],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
        stdin=subprocess.DEVNULL, check=False,
    )
    assert started.returncode == 0, started.stderr
    assert "startup_error" not in started.stderr
