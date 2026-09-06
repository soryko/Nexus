"""B2a acceptance tests 1 (identity), 2, 4 and 5 (format at bind): registered identity.

Every repository here is a real temporary Git repository, and every binding goes through
the same code path the server uses at launch. History is never consulted for identity,
so the cases that broke the withdrawn discriminator - an orphan branch with a commit, an
orphan branch with an unborn HEAD, a deepened shallow clone - must all bind normally.
"""
from __future__ import annotations

import multiprocessing
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from nexus_memory.domain.errors import (
    RepositoryMismatch,
    RepositoryRegistrationFailed,
    RepositoryUnbound,
)
from nexus_memory.domain.models import Scope
from nexus_memory.git import GitCli, bind_repository, publish_token, read_token
from nexus_memory.git.identity import token_path
from nexus_memory.storage import SQLiteRepository

SCOPE = Scope("identity", "local")
GIT = GitCli()


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *arguments], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def init_repo(path: Path, object_format: str | None = None, commits: int = 1) -> Path:
    arguments = ["git", "init", "-q"]
    if object_format is not None:
        arguments.append(f"--object-format={object_format}")
    subprocess.run([*arguments, str(path)], check=True, capture_output=True)
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    (path / "sub").mkdir()
    (path / "sub" / "file.txt").write_text("a\n")
    (path / "file.txt").write_text("b\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "one")
    for number in range(2, commits + 1):
        (path / "file.txt").write_text(f"{number}\n")
        git(path, "commit", "-qam", str(number))
    return path


def clone(source: Path, target: Path, *extra: str) -> Path:
    subprocess.run(["git", "clone", "-q", *extra, f"file://{source}", str(target)], check=True, capture_output=True)
    git(target, "config", "user.email", "t@example.invalid")
    git(target, "config", "user.name", "t")
    return target


def bind(db: Path, checkout: Path, repository_id: str | None = None, git_cli: GitCli | None = GIT):
    binding, _ = bind_repository(SQLiteRepository(db), SCOPE, checkout, repository_id, git_cli)
    assert binding is not None
    return binding


def rows(db: Path, table: str) -> list[tuple]:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        connection.close()


def common_dir(checkout: Path) -> Path:
    return GIT.inspect(checkout).common_dir


# --- acceptance test 1, identity part ------------------------------------------------------


def test_two_clones_of_one_source_are_distinct_identities(tmp_path: Path) -> None:
    source = init_repo(tmp_path / "source")
    first = clone(source, tmp_path / "first")
    second = clone(source, tmp_path / "second")
    db = tmp_path / "memory.sqlite3"

    assert git(first, "rev-parse", "HEAD") == git(second, "rev-parse", "HEAD")
    assert git(first, "remote", "get-url", "origin") == git(second, "remote", "get-url", "origin")

    one, two = bind(db, first), bind(db, second)
    assert one.repository_id != two.repository_id
    assert len(rows(db, "repositories")) == 2
    # And binding again changes nothing: a shared remote and shared commits associate nothing.
    assert bind(db, first).repository_id == one.repository_id
    assert bind(db, second).repository_id == two.repository_id


# --- acceptance test 2 --------------------------------------------------------------------


def test_main_and_linked_worktrees_bind_to_one_identity(tmp_path: Path) -> None:
    main = init_repo(tmp_path / "main")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", str(linked), "-b", "feature")
    db = tmp_path / "memory.sqlite3"

    # The measured hazard: the raw form is ".git" from the main worktree and absolute from
    # the linked one, so an implementation that compared it would associate nothing.
    assert git(main, "rev-parse", "--git-common-dir") == ".git"
    assert git(linked, "rev-parse", "--git-common-dir").startswith("/")
    assert git(main, "rev-parse", "--show-toplevel") != git(linked, "rev-parse", "--show-toplevel")

    assert bind(db, main).repository_id == bind(db, linked).repository_id
    assert len(rows(db, "repositories")) == 1
    assert read_token(common_dir(main)) == read_token(common_dir(linked))


# --- acceptance test 4 --------------------------------------------------------------------


def test_orphan_branch_worktrees_with_and_without_a_commit_bind_to_the_identity(tmp_path: Path) -> None:
    main = init_repo(tmp_path / "main")
    db = tmp_path / "memory.sqlite3"
    identity = bind(db, main).repository_id

    committed = tmp_path / "orphan-commit"
    git(main, "worktree", "add", "-q", "--orphan", str(committed))
    git(committed, "config", "user.email", "t@example.invalid")
    git(committed, "config", "user.name", "t")
    (committed / "o.txt").write_text("o\n")
    git(committed, "add", "o.txt")
    git(committed, "commit", "-qm", "orphan")
    # The reviewer's probe: a different root commit under the same common directory.
    assert git(committed, "rev-list", "--max-parents=0", "HEAD") != git(main, "rev-list", "--max-parents=0", "HEAD")
    assert bind(db, committed).repository_id == identity

    unborn = tmp_path / "orphan-unborn"
    git(main, "worktree", "add", "-q", "--orphan", str(unborn))
    # The second probe: no root at all, the history check cannot even run.
    failed = subprocess.run(["git", "-C", str(unborn), "rev-list", "--max-parents=0", "HEAD"], capture_output=True)
    assert failed.returncode != 0
    assert bind(db, unborn).repository_id == identity
    assert len(rows(db, "repositories")) == 1


def test_deepening_a_shallow_clone_changes_nothing(tmp_path: Path) -> None:
    source = init_repo(tmp_path / "source", commits=3)
    shallow = clone(source, tmp_path / "shallow", "--depth", "1")
    db = tmp_path / "memory.sqlite3"
    before = bind(db, shallow)
    root_before = git(shallow, "rev-list", "--max-parents=0", "HEAD")

    git(shallow, "fetch", "-q", "--deepen=1")

    assert git(shallow, "rev-list", "--max-parents=0", "HEAD") != root_before  # the withdrawn discriminator moved
    assert bind(db, shallow) == before


def test_moved_checkout_keeps_its_identity_and_updates_the_locator(tmp_path: Path) -> None:
    original = init_repo(tmp_path / "original")
    db = tmp_path / "memory.sqlite3"
    before = bind(db, original)
    assert before.locator == str(common_dir(original))

    moved = tmp_path / "elsewhere" / "moved"
    moved.parent.mkdir()
    shutil.move(str(original), str(moved))

    after = bind(db, moved)
    assert after.repository_id == before.repository_id
    assert after.locator == str(common_dir(moved)) != before.locator
    assert [row[4] for row in rows(db, "repositories")] == [after.locator]


def test_fresh_clone_is_new_and_a_filesystem_copy_claims_the_original(tmp_path: Path) -> None:
    original = init_repo(tmp_path / "original")
    db = tmp_path / "memory.sqlite3"
    identity = bind(db, original).repository_id

    cloned = clone(original, tmp_path / "cloned")
    assert read_token(common_dir(cloned)) is None          # git clone does not carry the token
    assert bind(db, cloned).repository_id != identity

    copied = tmp_path / "copied"
    shutil.copytree(original, copied, symlinks=True)        # cp -R does carry it
    assert bind(db, copied).repository_id == identity      # the documented limit

    token_path(common_dir(copied)).unlink()                 # and the documented remedy
    re_registered = bind(db, copied).repository_id
    assert re_registered != identity
    assert len(rows(db, "repositories")) == 3


def _register_together(db: str, checkout: str, start, results) -> None:
    start.wait()
    try:
        binding = bind(Path(db), Path(checkout))
        results.put(("ok", binding.repository_id))
    except BaseException as error:  # noqa: BLE001 - report anything a worker hits
        results.put(("error", f"{type(error).__name__}: {error}"))


def test_concurrent_registration_publishes_one_token_and_one_identity(tmp_path: Path) -> None:
    checkout = init_repo(tmp_path / "repo")
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_register_together, args=(str(db), str(checkout), start, results))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    start.set()
    outcomes = [results.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(10)

    assert all(status == "ok" for status, _ in outcomes), outcomes
    assert len({identity for _, identity in outcomes}) == 1, outcomes
    nexus = common_dir(checkout) / "nexus"
    assert sorted(entry.name for entry in nexus.iterdir()) == ["checkout-token"]  # one token, no leftovers
    assert len(rows(db, "repositories")) == 1
    assert len(rows(db, "repository_checkouts")) == 1


def test_interrupted_registration_is_recovered_by_adoption(tmp_path: Path) -> None:
    checkout = init_repo(tmp_path / "repo")
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)

    # Interrupted after publication, before the row: a token with no row.
    published = publish_token(common_dir(checkout))
    assert rows(db, "repository_checkouts") == []
    binding = bind(db, checkout)
    assert [row[2] for row in rows(db, "repository_checkouts")] == [published]
    assert read_token(common_dir(checkout)) == published   # adopted, not replaced

    # Interrupted before publication: a leftover temporary file and no token is harmless.
    fresh = init_repo(tmp_path / "fresh")
    nexus = common_dir(fresh) / "nexus"
    nexus.mkdir()
    (nexus / "checkout-token.leftover.tmp").write_text("0" * 10)
    assert read_token(common_dir(fresh)) is None
    other = bind(db, fresh)
    assert other.repository_id != binding.repository_id
    assert read_token(common_dir(fresh)) is not None


@pytest.mark.parametrize("content", [b"abc\n", b"0" * 63 + b"\n", b"A" * 64 + b"\n", b"\xff\xfe" * 32, b""])
def test_invalid_or_truncated_token_is_a_clear_error_that_mints_nothing(tmp_path: Path, content: bytes) -> None:
    checkout = init_repo(tmp_path / "repo")
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    token = token_path(common_dir(checkout))
    token.parent.mkdir()
    token.write_bytes(content)

    with pytest.raises(RepositoryRegistrationFailed) as failure:
        bind(db, checkout)

    message = str(failure.value)
    assert "token" in message
    assert str(tmp_path) not in message and "/" not in message
    assert token.read_bytes() == content          # left in place, not replaced
    assert rows(db, "repositories") == []         # nothing minted


@pytest.mark.skipif(os.geteuid() == 0, reason="permission bits do not bind root")
def test_read_only_git_directory_binds_an_existing_token_and_refuses_registration(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    registered = init_repo(tmp_path / "registered")
    identity = bind(db, registered).repository_id
    unregistered = init_repo(tmp_path / "unregistered")
    read_only = stat.S_IRUSR | stat.S_IXUSR
    locked: list[Path] = []
    try:
        for directory in (common_dir(registered) / "nexus", common_dir(registered), common_dir(unregistered)):
            directory.chmod(read_only)
            locked.append(directory)

        assert bind(db, registered).repository_id == identity   # no write is required to bind

        with pytest.raises(RepositoryRegistrationFailed) as failure:
            bind(db, unregistered)
        assert str(tmp_path) not in str(failure.value) and "/" not in str(failure.value)
        assert len(rows(db, "repositories")) == 1
    finally:
        for directory in locked:
            directory.chmod(stat.S_IRWXU)


def test_repo_id_associates_deliberately_and_refuses_a_token_that_maps_elsewhere(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    original = init_repo(tmp_path / "original")
    unrelated = init_repo(tmp_path / "unrelated")
    identity = bind(db, original).repository_id
    other = bind(db, unrelated).repository_id

    cloned = clone(original, tmp_path / "cloned")
    assert bind(db, cloned, repository_id=identity).repository_id == identity
    assert bind(db, cloned).repository_id == identity           # the association persists
    # Cardinality: two tokens map to one identity; no token maps to two.
    mapping = {row[2]: row[3] for row in rows(db, "repository_checkouts")}
    assert sorted(mapping.values()).count(identity) == 2
    assert len(mapping) == 3

    with pytest.raises(RepositoryMismatch):
        bind(db, cloned, repository_id=other)
    with pytest.raises(RepositoryMismatch):
        bind(db, original, repository_id=other)
    with pytest.raises(RepositoryMismatch):
        bind(db, clone(original, tmp_path / "another"), repository_id="no-such-repository")
    assert len(rows(db, "repositories")) == 2


# --- acceptance test 5, format at bind -----------------------------------------------------


def test_object_format_is_recorded_at_bind(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    sha256 = init_repo(tmp_path / "sha256", object_format="sha256")
    sha1 = init_repo(tmp_path / "sha1")
    assert len(git(sha256, "rev-parse", "HEAD")) == 64
    assert bind(db, sha256).object_format == "sha256"
    assert bind(db, sha1).object_format == "sha1"
    assert sorted(row[3] for row in rows(db, "repositories")) == ["sha1", "sha256"]


# --- binding without git, and non-repositories ---------------------------------------------


def test_without_git_an_existing_registration_still_binds_and_nothing_registers(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    main = init_repo(tmp_path / "main")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", str(linked), "-b", "feature")
    identity = bind(db, main).repository_id

    binding, verifier = bind_repository(SQLiteRepository(db), SCOPE, main, None, None)
    assert binding is not None and binding.repository_id == identity and verifier is None
    binding, verifier = bind_repository(SQLiteRepository(db), SCOPE, linked, None, None)
    assert binding is not None and binding.repository_id == identity and verifier is None

    fresh = init_repo(tmp_path / "fresh")
    assert bind_repository(SQLiteRepository(db), SCOPE, fresh, None, None) == (None, None)
    assert read_token(common_dir(fresh)) is None
    with pytest.raises(RepositoryRegistrationFailed):
        bind_repository(SQLiteRepository(db), SCOPE, fresh, identity, None)


def test_a_path_that_is_not_a_checkout_is_repository_unbound(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RepositoryUnbound):
        bind(db, plain)
    with pytest.raises(RepositoryUnbound):
        bind(db, tmp_path / "missing")
    assert rows(db, "repositories") == []
