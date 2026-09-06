"""Acceptance scenarios 3, 5, 6, 7, 8, 9, 10 and 11 against real temporary repositories.

Each scenario is a plain function so that the positive tests and the mutation controls
run exactly the same code: a positive test calls it and expects it to return; a mutation
control applies one mutation and expects the designated scenario to raise.
"""
from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from reference_fakes import FakeVerifier, entry, register
from test_repository_identity import git, init_repo

from nexus_memory.domain.errors import (
    CommitNotFound,
    InvalidReference,
    PathNotInCommit,
    UnsupportedReferenceType,
)
from nexus_memory.domain.models import MemoryInput, ReferenceInput, RepositoryBinding, Scope
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

SCOPE = Scope("verify", "local")
Ref = ReferenceInput


class Spy:
    """Records every git invocation the adapter makes, with its exit status and output."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], int, bytes]] = []
        self._original = GitCli._run

    def install(self) -> "Spy":
        spy = self

        def run(cli, cwd, *arguments):
            result = spy._original(cli, cwd, *arguments)
            spy.calls.append((arguments, result.returncode, result.stdout))
            return result

        GitCli._run = run  # type: ignore[method-assign]
        return self

    def remove(self) -> None:
        GitCli._run = self._original  # type: ignore[method-assign]

    def resolutions(self) -> int:
        return sum(1 for arguments, _, _ in self.calls if arguments[:2] == ("rev-parse", "--verify"))

    def clear(self) -> None:
        self.calls.clear()


def bound(db: Path, checkout: Path, scope: Scope = SCOPE) -> MemoryService:
    store = SQLiteRepository(db)
    binding, verifier = bind_repository(store, scope, checkout, None, GitCli())
    return MemoryService(store, scope, binding, verifier)


def hash_object(checkout: Path, path: str) -> str:
    return git(checkout, "hash-object", "--", path)


def references(svc: MemoryService, receipt) -> list[tuple[str, str, str]]:
    return [(r.commit_oid, r.path, r.object_oid) for r in svc.get(receipt.memory_id).references]


# --- 3: changing refs, resolution per distinct spec --------------------------------------


def scenario_changing_refs(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v1", "-m", "annotated")
    git(repo, "checkout", "-q", "-b", "topic")
    (repo / "file.txt").write_text("topic\n")
    git(repo, "commit", "-qam", "topic")
    topic = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-")  # back to the first branch, HEAD == head
    assert git(repo, "rev-parse", "HEAD") == head != topic
    svc = bound(tmp_path / "memory.sqlite3", repo)
    spy = Spy().install()
    try:
        one = svc.record(MemoryInput("one spec", references=(
            Ref("sub/file.txt"), Ref("file.txt", "HEAD"), Ref("sub/file.txt", "HEAD"),
        )), "one")
        assert spy.resolutions() == 1, spy.calls
        assert references(svc, one) == [
            (head, "file.txt", hash_object(repo, "file.txt")), (head, "sub/file.txt", hash_object(repo, "sub/file.txt")),
        ]

        spy.clear()
        three = svc.record(MemoryInput("three specs", references=(
            Ref("file.txt", "topic"), Ref("sub/file.txt", "v1"), Ref("file.txt", "HEAD"), Ref("sub/file.txt", "topic"),
        )), "three")
        assert spy.resolutions() == 3, spy.calls
        recorded = references(svc, three)
        assert {oid for oid, _, _ in recorded} == {head, topic}      # the annotated tag dereferenced to head
        assert sum(1 for oid, _, _ in recorded if oid == topic) == 2  # references sharing a spec share its OID
        topic_file = next(blob for oid, path, blob in recorded if oid == topic and path == "file.txt")
        assert topic_file == git(repo, "rev-parse", f"{topic}:file.txt")

        git(repo, "branch", "-f", "topic", head)                        # the branch moves
        assert references(svc, three) == recorded                       # stored evidence does not
        pinned = svc.record(MemoryInput("pinned", references=(Ref("file.txt", topic),)), "pinned")
        assert references(svc, pinned) == [(topic, "file.txt", topic_file)]
    finally:
        spy.remove()


# --- 5: object format end to end ---------------------------------------------------------


def scenario_object_format(tmp_path: Path) -> None:
    sha256 = init_repo(tmp_path / "sha256", object_format="sha256")
    sha1 = init_repo(tmp_path / "sha1")
    wide = bound(tmp_path / "wide.sqlite3", sha256)
    narrow = bound(tmp_path / "narrow.sqlite3", sha1)

    receipt = wide.record(MemoryInput("sha256", references=(Ref("sub/file.txt"),)), "k")
    [(commit_oid, _, object_oid)] = references(wide, receipt)
    assert len(commit_oid) == 64 and commit_oid == git(sha256, "rev-parse", "HEAD")
    assert len(object_oid) == 64 and object_oid == hash_object(sha256, "sub/file.txt")
    pinned = wide.record(MemoryInput("pinned", references=(Ref("sub/file.txt", commit_oid),)), "k2")
    assert references(wide, pinned) == [(commit_oid, "sub/file.txt", object_oid)]
    with pytest.raises(InvalidReference):
        wide.record(MemoryInput("x", references=(Ref("sub/file.txt", "a" * 40),)), "k3")

    head = git(sha1, "rev-parse", "HEAD")
    receipt = narrow.record(MemoryInput("sha1", references=(Ref("sub/file.txt", head),)), "k")
    assert references(narrow, receipt) == [(head, "sub/file.txt", hash_object(sha1, "sub/file.txt"))]
    with pytest.raises(InvalidReference):
        narrow.record(MemoryInput("x", references=(Ref("sub/file.txt", "a" * 64),)), "k3")


# --- 6: dirty working tree ---------------------------------------------------------------


def scenario_dirty_tree(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    svc = bound(tmp_path / "memory.sqlite3", repo)
    item = MemoryInput("clean", references=(Ref("sub/file.txt"), Ref("file.txt")))
    before = references(svc, svc.record(item, "before"))

    (repo / "sub" / "file.txt").write_text("modified on disk\n")
    (repo / "file.txt").unlink()
    (repo / "new.txt").write_text("untracked\n")

    assert references(svc, svc.record(item, "after")) == before
    with pytest.raises(PathNotInCommit):
        svc.record(MemoryInput("x", references=(Ref("new.txt"),)), "untracked")


# --- 7: missing objects ------------------------------------------------------------------


def scenario_missing_objects(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    svc = bound(tmp_path / "memory.sqlite3", repo)
    spy = Spy().install()
    try:
        with pytest.raises(CommitNotFound):
            svc.record(MemoryInput("x", references=(Ref("sub/file.txt", "0" * 40),)), "unknown-commit")
        spy.clear()
        with pytest.raises(PathNotInCommit):
            svc.record(MemoryInput("x", references=(Ref("absent.txt"),)), "absent-path")
        lookups = [(rc, out) for arguments, rc, out in spy.calls if arguments[0] == "ls-tree"]
        assert lookups == [(0, b"")], lookups  # absence is empty output at exit 0, never a status
    finally:
        spy.remove()
    assert svc.status().active_memories == 0


# --- 8: unsafe paths never reach git -----------------------------------------------------


UNSAFE = ["/etc/passwd", "../etc/passwd", "sub/../file.txt", "sub/./file.txt", "/sub/file.txt",
          "sub/file.txt/", "sub//file.txt", "", "a\x00b", "..", "."]


def scenario_unsafe_paths(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    svc = bound(tmp_path / "memory.sqlite3", repo)
    spy = Spy().install()
    try:
        for path in UNSAFE:
            with pytest.raises(InvalidReference) as failure:
                svc.record(MemoryInput("x", references=(Ref(path),)), f"k-{path!r}")
            message = str(failure.value)
            assert str(tmp_path) not in message and "git" not in message.lower() and "/" not in message
            assert not any(path in arguments for arguments, _, _ in spy.calls), (path, spy.calls)
    finally:
        spy.remove()
    assert svc.status().active_memories == 0


# --- 9: unsupported entry types ----------------------------------------------------------


def scenario_unsupported_types(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "link").symlink_to("sub/file.txt")
    git(repo, "add", "link")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{git(repo, 'rev-parse', 'HEAD')},vendor")
    git(repo, "commit", "-qm", "kinds")
    listed = git(repo, "ls-tree", "HEAD")
    assert "120000 blob" in listed and "160000 commit" in listed and "040000 tree" in listed
    svc = bound(tmp_path / "memory.sqlite3", repo)
    for path in ("link", "vendor", "sub"):
        with pytest.raises(UnsupportedReferenceType):
            svc.record(MemoryInput("x", references=(Ref(path),)), f"k-{path}")
    with pytest.raises(PathNotInCommit):  # distinguishable from not found
        svc.record(MemoryInput("x", references=(Ref("nothing"),)), "k-nothing")
    executable = svc.record(MemoryInput("x", references=(Ref("sub/file.txt"),)), "k-regular")
    assert svc.get(executable.memory_id).references[0].mode == "100644"


# --- 10: one literal entry ----------------------------------------------------------------


def scenario_one_literal_entry(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "star*.txt").write_text("star\n")
    (repo / "star1.txt").write_text("one\n")
    (repo / ":(top)weird.txt").write_text("weird\n")
    subprocess.run(["git", "--literal-pathspecs", "-C", str(repo), "add", "--", "star*.txt", "star1.txt", ":(top)weird.txt"],
                   check=True, capture_output=True)
    git(repo, "commit", "-qm", "literal names")
    c1 = git(repo, "rev-parse", "HEAD")
    true_blob = hash_object(repo, "sub/file.txt")
    svc = bound(tmp_path / "memory.sqlite3", repo)

    # A directory whose prefix matches a file is not "found": empty output for a partial
    # name, and a tree record - byte-equal but not a blob - for the directory itself.
    with pytest.raises(PathNotInCommit):
        svc.record(MemoryInput("x", references=(Ref("su"),)), "prefix")
    with pytest.raises(UnsupportedReferenceType):
        svc.record(MemoryInput("x", references=(Ref("sub"),)), "directory")

    # Byte-equality itself, on a record whose name differs from the request. Measured: real
    # git never returns such a record without -r, so the rule is exercised on a scripted one.
    scripted = FakeVerifier({"HEAD": c1}, {(c1, "file.txt"): (entry("sub/file.txt", true_blob),)})
    register(tmp_path / "memory.sqlite3", SCOPE, "scripted")
    with pytest.raises(PathNotInCommit):
        MemoryService(SQLiteRepository(tmp_path / "memory.sqlite3"), SCOPE, RepositoryBinding("scripted", "sha1", None), scripted) \
            .record(MemoryInput("x", references=(Ref("file.txt"),)), "renamed")

    # Wildcard and magic characters name only a file of exactly that name.
    literal = svc.record(MemoryInput("x", references=(Ref("star*.txt"), Ref(":(top)weird.txt"))), "literal")
    assert references(svc, literal) == [
        (c1, ":(top)weird.txt", hash_object(repo, ":(top)weird.txt")), (c1, "star*.txt", hash_object(repo, "star*.txt")),
    ]
    with pytest.raises(PathNotInCommit):
        svc.record(MemoryInput("x", references=(Ref("star?.txt"),)), "glob")

    # A replacement ref substitutes silently unless every invocation refuses it.
    (repo / "sub" / "file.txt").write_text("tampered\n")
    git(repo, "commit", "-qam", "tampered")
    c2 = git(repo, "rev-parse", "HEAD")
    git(repo, "replace", c1, c2)
    tampered = hash_object(repo, "sub/file.txt")
    assert git(repo, "ls-tree", c1, "--", "sub/file.txt").split()[2] == tampered  # the measured premise
    pinned = svc.record(MemoryInput("x", references=(Ref("sub/file.txt", c1),)), "replaced")
    assert references(svc, pinned) == [(c1, "sub/file.txt", true_blob)]

    # --full-tree: bound at a subdirectory, a path is still repository-relative.
    git(repo, "replace", "-d", c1)
    inner = bound(tmp_path / "inner.sqlite3", repo / "sub")
    receipt = inner.record(MemoryInput("x", references=(Ref("file.txt"), Ref("sub/file.txt"))), "subdir")
    assert references(inner, receipt) == [
        (c2, "file.txt", hash_object(repo, "file.txt")), (c2, "sub/file.txt", tampered),
    ]


# --- 11: the lock boundary ----------------------------------------------------------------


def scenario_lock_boundary(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = FakeVerifier({"HEAD": "1" * 40}, {("1" * 40, "a"): (entry("a"),)})
    verifier.pause = threading.Event()
    store = SQLiteRepository(db)
    register(db, SCOPE, "scripted")
    svc = MemoryService(store, SCOPE, RepositoryBinding("scripted", "sha1", None), verifier)
    item = MemoryInput("paused", references=(Ref("a"),))
    with ThreadPoolExecutor(max_workers=1) as pool:
        paused = pool.submit(svc.record, item, "paused")
        try:
            assert verifier.paused.wait(timeout=10)
            started = time.perf_counter()
            MemoryService(SQLiteRepository(db), SCOPE).record(MemoryInput("independent"), "independent")
            elapsed = time.perf_counter() - started
            assert elapsed < 2.0, f"independent write took {elapsed:.2f}s: a lock was held during verification"
        finally:
            verifier.pause.set()
        paused.result(timeout=20)


SCENARIOS = {
    "changing_refs": scenario_changing_refs,
    "object_format": scenario_object_format,
    "dirty_tree": scenario_dirty_tree,
    "missing_objects": scenario_missing_objects,
    "unsafe_paths": scenario_unsafe_paths,
    "unsupported_types": scenario_unsupported_types,
    "one_literal_entry": scenario_one_literal_entry,
    "lock_boundary": scenario_lock_boundary,
}
