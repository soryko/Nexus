"""Regressions for the checkout a bound process follows, from the audit of 0b0a217.

Three defects, all in the gap between the binding made once at launch and the pathname
followed on every later call: evidence stamped with the identity of a repository that had
left the path, a startup a broken git could take down, and a degraded launch that quietly
answered differently from the same arguments with git present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from reference_fakes import OID1, FakeVerifier, binding, entry, register
from test_repository_identity import GIT, SCOPE, bind, common_dir, git, init_repo, rows

from nexus_memory.domain.errors import (
    RepositoryMismatch,
    RepositoryRegistrationFailed,
    RepositoryUnbound,
    VerificationUnavailable,
)
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.git.identity import locate_without_git, token_path
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


def wrong_format_git(directory: Path) -> GitCli:
    """Executable, but not something the kernel will load: no shebang, not a binary.

    This one raises a bare ``OSError`` (ENOEXEC) out of the spawn rather than one of the
    named subclasses, so it used to escape the adapter entirely and surface from the CLI as
    a database that could not be opened.
    """
    return stub_git(directory, "this is not a program\n")


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


def test_the_identity_check_brackets_a_verifying_write_and_never_runs_before_a_replay(tmp_path: Path) -> None:
    """Twice per verifying write, not once: the second is what makes the first worth taking.

    Every git command resolves the bound pathname afresh, so one check up front proves only
    what was there when it ran. It was once one check, and a checkout replaced immediately
    after it passed had the replacement's commits stamped with the departed repository's
    identity. Writes that resolve nothing still ask nothing.
    """
    db = tmp_path / "memory.sqlite3"
    verifier = FakeVerifier({"HEAD": OID1}, {(OID1, "a"): (entry("a"),)})
    store = SQLiteRepository(db)          # migrates, so the identity row below has a table
    register(db, SCOPE, "repo-a")
    svc = MemoryService(store, SCOPE, binding("repo-a"), verifier)
    item = MemoryInput("one", references=(Ref("a"),))

    receipt = svc.record(item, "k")
    assert verifier.identity_checks == 2          # once before resolving, once before writing
    assert svc.record(item, "k") == receipt
    assert verifier.identity_checks == 2          # the replay never asks the checkout
    svc.record(MemoryInput("no references"), "plain")
    assert verifier.identity_checks == 2          # neither does a write that carries none

    verifier.identity_error = RepositoryMismatch("the bound checkout now resolves to a different repository")
    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("two", references=(Ref("a"),)), "k2")
    assert verifier.resolutions == ["HEAD"]       # refused before any further resolution
    assert svc.record(item, "k") == receipt


# --- 2: a git that is present but cannot answer --------------------------------------------


@pytest.mark.parametrize(
    "make_git", [failing_git, unusable_git, wrong_format_git],
    ids=["exits-nonzero", "not-executable", "wrong-binary-format"],
)
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


def test_a_broken_git_does_not_swallow_a_wrong_path_a_conflict_or_a_bad_token(tmp_path: Path) -> None:
    """Degrading must not turn every launch into a success.

    The fallback applies where the checkout is discoverable without git; where it is not,
    the path may really not be a checkout and the operator hears the original error.
    """
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    identity = bind(db, repo).repository_id
    broken = failing_git(tmp_path / "broken")
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(RepositoryUnbound):
        bind_repository(SQLiteRepository(db), SCOPE, plain, None, broken)
    with pytest.raises(RepositoryUnbound):
        bind_repository(SQLiteRepository(db), SCOPE, tmp_path / "missing", None, broken)
    with pytest.raises(RepositoryMismatch):
        bind_repository(SQLiteRepository(db), SCOPE, repo, "another-identity", broken)
    assert bind_repository(SQLiteRepository(db), SCOPE, repo, identity, broken)[0].repository_id == identity

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


# --- 3: what a degraded launch means ---------------------------------------------------------


def test_without_git_a_subdirectory_binds_the_same_identity_as_with_git(tmp_path: Path) -> None:
    """`--repo repo/sub` is one argument, and it must not name two different bindings.

    Reproduced at 0b0a217: a restart without git lost the registered identity for a
    checkout named by a subdirectory, because discovery looked only at `sub/.git` and
    never at its ancestors. Stored evidence stayed readable; the active binding `status`
    reported did not.
    """
    db = tmp_path / "memory.sqlite3"
    main = init_repo(tmp_path / "main")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", str(linked), "-b", "feature")
    identity = bind(db, main).repository_id

    for checkout in (main, main / "sub", linked, linked / "sub"):
        assert bind(db, checkout).repository_id == identity, checkout
        found, verifier = bind_repository(SQLiteRepository(db), SCOPE, checkout, None, None)
        assert found is not None and found.repository_id == identity, checkout
        assert verifier is None
        assert MemoryService(SQLiteRepository(db), SCOPE, found, verifier).status().repository_id == identity

    outside = tmp_path / "outside"
    outside.mkdir()
    assert bind_repository(SQLiteRepository(db), SCOPE, outside, None, None) == (None, None)
    assert bind_repository(SQLiteRepository(db), SCOPE, main / "file.txt", None, None) == (None, None)


def test_without_git_an_explicit_repository_id_is_still_checked(tmp_path: Path) -> None:
    """`--repo-id` means the same thing in both modes.

    Reproduced at 0b0a217: a conflicting `--repo-id` was silently ignored when git was
    missing, while the same arguments failed with git present. A launch that names a
    conflicting identity would have looked bound, and the disagreement would have surfaced
    only in what later evidence claimed.
    """
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    identity = bind(db, repo).repository_id

    found, verifier = bind_repository(SQLiteRepository(db), SCOPE, repo, identity, None)
    assert found is not None and found.repository_id == identity and verifier is None
    for checkout in (repo, repo / "sub"):
        with pytest.raises(RepositoryMismatch):
            bind_repository(SQLiteRepository(db), SCOPE, checkout, "another-identity", None)
        with pytest.raises(RepositoryMismatch):
            bind_repository(SQLiteRepository(db), SCOPE, checkout, "another-identity", GIT)

    unregistered = init_repo(tmp_path / "unregistered")
    with pytest.raises(RepositoryRegistrationFailed):
        bind_repository(SQLiteRepository(db), SCOPE, unregistered, identity, None)


def test_scopes_do_not_share_a_degraded_binding(tmp_path: Path) -> None:
    """Discovery is not authority: the token still has to map to a row in this scope."""
    db = tmp_path / "memory.sqlite3"
    repo = init_repo(tmp_path / "repo")
    bind(db, repo)
    elsewhere = Scope("another-namespace", "local")
    assert bind_repository(SQLiteRepository(db), elsewhere, repo / "sub", None, None) == (None, None)


# --- 4: from the audit of f74f07c ------------------------------------------------------------


def test_a_checkout_replaced_inside_the_verification_window_writes_nothing(tmp_path: Path) -> None:
    """The window between the identity check and the write, not the check itself.

    The check passed and every git command after it resolved the bound pathname again, so a
    checkout swapped in between had the replacement's commit and blob written under the
    departed repository's id — a row that is wrong in exactly the way the check exists to
    prevent, and wrong silently. Confirming again before the write refuses any replacement
    still in place. A swap undone inside the window stays invisible; the binding assumes the
    checkout is not relocated concurrently with a verifying write.
    """
    db = tmp_path / "memory.sqlite3"
    other = init_repo(tmp_path / "other")
    live = init_repo(tmp_path / "live")
    svc = service(db, live)
    assert svc.verifier is not None

    passed_once = svc.verifier.check_identity

    def swap_after_passing() -> None:
        passed_once()                                     # still the repository we bound
        shutil.rmtree(live)
        shutil.copytree(other, live, symlinks=True)       # ...and now it is not
        svc.verifier.check_identity = passed_once         # the swap happens once

    svc.verifier.check_identity = swap_after_passing
    with pytest.raises(RepositoryMismatch):
        svc.record(MemoryInput("across the swap", references=(Ref("file.txt"),)), "race")
    assert rows(db, "revision_references") == []


def test_a_dot_git_without_repository_metadata_is_not_a_discovered_checkout(tmp_path: Path) -> None:
    """An empty ``.git`` makes git exit 128; discovery must not disagree with it.

    Being discoverable without git is the one thing that licenses a launch to suppress git's
    own rejection, so accepting a directory that only *looks* like a Git directory turned an
    invalid ``--repo`` into a silent unbound launch — with a working git, not merely a broken
    one. git wants an object store, a ref store and a HEAD before it accepts a directory.
    """
    db = tmp_path / "memory.sqlite3"
    hollow = tmp_path / "hollow"
    (hollow / ".git").mkdir(parents=True)

    assert locate_without_git(hollow) is None
    for git_cli in (GIT, failing_git(tmp_path / "broken")):
        with pytest.raises(RepositoryUnbound):
            bind_repository(SQLiteRepository(db), SCOPE, hollow, None, git_cli)

    partial = tmp_path / "partial"                        # objects and refs, no HEAD
    (partial / ".git" / "objects").mkdir(parents=True)
    (partial / ".git" / "refs").mkdir()
    assert locate_without_git(partial) is None


def test_a_repository_that_moved_with_its_git_directory_still_verifies(tmp_path: Path) -> None:
    """The token is the registration; the common directory is only where it is kept.

    Comparing the locator refused a repository that had merely been relocated — the one case
    the check documents itself as not refusing — while rebinding recovered the same id, so
    the same repository was and was not itself depending on which door it came through. What
    must still be refused is a *different* repository at the bound path, and the token, not
    the locator, is what refuses it.
    """
    db = tmp_path / "memory.sqlite3"
    original = init_repo(tmp_path / "original")
    link = tmp_path / "checkout"
    link.symlink_to(original)
    svc = service(db, link)
    assert svc.binding is not None and svc.verifier is not None

    moved = tmp_path / "elsewhere" / "moved"
    moved.parent.mkdir()
    shutil.move(str(original), str(moved))
    link.unlink()
    link.symlink_to(moved)

    svc.verifier.check_identity()                          # relocation is not a mismatch
    assert svc.verifier.common_dir == common_dir(moved)     # the locator follows the token
    assert svc.record(MemoryInput("after the move", references=(Ref("file.txt"),)), "moved").operation == "record"

    link.unlink()
    link.symlink_to(init_repo(tmp_path / "impostor"))       # a different repository, same path
    with pytest.raises(RepositoryMismatch):
        svc.verifier.check_identity()


# --- 5: from the audit of 6e2e0d6 ------------------------------------------------------------


def git_accepts(checkout: Path) -> bool:
    """What the installed git actually decides, so the table below cannot drift from it."""
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "--git-common-dir"],
                          capture_output=True).returncode == 0


@pytest.mark.parametrize(("head", "accepted"), [
    (b"ref: refs/heads/main\n", True),                # born branch
    (b"ref: refs/heads/nothing-yet\n", True),         # unborn: well-formed before the ref exists
    (b"ref: refs/heads/main", True),                  # no trailing newline
    (b"ref:   refs/heads/main\n", True),              # whitespace after the prefix
    (b"9" * 39 + b"a\n", True),                       # detached
    (b"9" * 39 + b"A\n", True),                       # detached, uppercase
    (b"b" * 64 + b"\n", True),                        # detached, sha256 width
    (b"a" * 40, True),                                # detached, with no trailing byte at all
    (b"a" * 40 + b" junk\n", True),                   # git reads the id and permits the rest
    (b"this is not a ref\n", False),
    (b"", False),
    (b"ref: heads/main\n", False),                    # symbolic, but not into refs/
    (b"abc123\n", False),                             # hex, but far short of any id width
    (b"abc123", False),                               # ...and the newline had been hiding that
    (b"a" * 39 + b"\xff", False),                     # a width of bytes, not a width of hex
])
def test_discovery_accepts_the_head_forms_git_accepts_and_no_others(tmp_path: Path, head: bytes, accepted: bool) -> None:
    """Existence was never the question git asks about HEAD, and neither is plausibility.

    A HEAD holding arbitrary text is rejected by git with exit 128 while discovery accepted
    it, so a checkout git refuses was still reported discoverable — the same disagreement the
    empty ``.git`` had, one field further in. The cases are bytes rather than text because
    two of them are not text: a value one byte short of an id width, which a trailing newline
    had been concealing, and a width of bytes that is not a width of hex. Each case asserts
    against the installed git as well as the expectation, so this cannot drift from what it
    mirrors.
    """
    repo = init_repo(tmp_path / "repo")
    (repo / ".git" / "HEAD").write_bytes(head)
    assert git_accepts(repo) is accepted           # the expectation still matches git itself
    assert (locate_without_git(repo) is not None) is accepted


def test_discovery_accepts_a_symlinked_head(tmp_path: Path) -> None:
    """The historical form: HEAD as a symlink into refs/, which git still validates."""
    repo = init_repo(tmp_path / "repo")
    head = repo / ".git" / "HEAD"
    head.unlink()
    head.symlink_to("refs/heads/main")
    assert git_accepts(repo)
    assert locate_without_git(repo) == common_dir(repo)


def test_a_git_directory_whose_head_is_unparsable_is_not_a_degraded_launch(tmp_path: Path) -> None:
    """The store and the ref store are present; only HEAD is wrong, and git still refuses.

    Reproduced through the CLI: git exited 128 on this directory while the server started
    unbound with exit 0. Correcting HEAD alone, to an unborn branch, makes both accept it.
    """
    db = tmp_path / "memory.sqlite3"
    hollow = tmp_path / "hollow"
    (hollow / ".git" / "objects").mkdir(parents=True)
    (hollow / ".git" / "refs").mkdir()
    (hollow / ".git" / "HEAD").write_text("not a ref\n")

    assert not git_accepts(hollow)
    assert locate_without_git(hollow) is None
    for git_cli in (GIT, failing_git(tmp_path / "broken")):
        with pytest.raises(RepositoryUnbound):
            bind_repository(SQLiteRepository(db), SCOPE, hollow, None, git_cli)

    (hollow / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert git_accepts(hollow)
    assert locate_without_git(hollow) is not None   # only HEAD changed
