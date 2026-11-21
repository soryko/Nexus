"""Compare recorded evidence against the bound worktree's pinned commit, never its files."""
import pytest

from b2a_scenarios import SCOPE, bound
from test_repository_identity import git, init_repo
from nexus_memory.domain.errors import MemoryNotFound
from nexus_memory.domain.models import HeadComparison, MemoryInput, ReferenceInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService


@pytest.fixture
def target(tmp_path):
    repo = init_repo(tmp_path / "repo")
    svc = bound(tmp_path / "memory.sqlite3", repo)
    item = MemoryInput("original", references=(ReferenceInput("file.txt"),))
    receipt = svc.record(item, "one")
    return repo, svc, receipt


def check(svc, receipt):
    return svc.compare_at_head(receipt.memory_id, receipt.revision_id, 0)


def test_committed_changes_only_and_no_mutation(target):
    repo, svc, receipt = target
    head = git(repo, "rev-parse", "HEAD")
    current = svc.revise(receipt.memory_id, receipt.revision_id, MemoryInput("second"), "two")
    def state():
        return (svc.get(receipt.memory_id, receipt.revision_id), svc.get(receipt.memory_id),
                svc.history(receipt.memory_id), svc.search(SearchQuery("second")), svc.status())
    before = state()
    path = repo / "file.txt"
    path.write_text("staged")
    git(repo, "add", "file.txt")
    path.write_text("unstaged")
    assert check(svc, receipt) == HeadComparison("Matches at HEAD", head, head)
    git(repo, "commit", "-qm", "staged only")
    assert check(svc, receipt) == HeadComparison("Changed at HEAD", head, git(repo, "rev-parse", "HEAD"))
    git(repo, "mv", "file.txt", "renamed")
    git(repo, "commit", "-qm", "rename")
    assert check(svc, receipt) == HeadComparison("Missing at HEAD", head, git(repo, "rev-parse", "HEAD"))
    assert check(svc, current) == HeadComparison("Cannot check")
    assert before == state()
    assert svc.record(MemoryInput("original", references=(ReferenceInput("file.txt"),)), "one") == receipt


def test_mode_and_symlink_with_identical_blob(target):
    repo, svc, receipt = target
    git(repo, "update-index", "--chmod=+x", "file.txt")
    git(repo, "commit", "-qm", "mode")
    assert check(svc, receipt).outcome == "Matches at HEAD"
    path = repo / "file.txt"
    link_text = path.read_text()
    path.unlink()
    path.symlink_to(link_text)
    git(repo, "add", "file.txt")
    git(repo, "commit", "-qm", "symlink")
    assert git(repo, "rev-parse", "HEAD:file.txt") == git(repo, "rev-parse", "HEAD~1:file.txt")
    assert check(svc, receipt).outcome == "Changed at HEAD"


def test_detached_linked_worktree_and_racing_branch_switch(target, tmp_path, monkeypatch):
    repo, svc, receipt = target
    head = git(repo, "rev-parse", "HEAD")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--detach", str(linked), head)
    other = bound(tmp_path / "memory.sqlite3", linked)
    git(linked, "rm", "file.txt")
    git(linked, "commit", "-qm", "remove")
    detached = git(linked, "rev-parse", "HEAD")
    assert check(other, receipt) == HeadComparison("Missing at HEAD", head, detached)
    assert check(svc, receipt) == HeadComparison("Matches at HEAD", head, head)
    original = other.verifier.resolve_commit
    calls = []
    def switch_after_resolution(spec):
        oid = original(spec)
        calls.append(spec)
        git(linked, "checkout", "-qb", "new-branch", head)
        return oid
    monkeypatch.setattr(other.verifier, "resolve_commit", switch_after_resolution)
    assert check(other, receipt) == HeadComparison("Missing at HEAD", head, detached)
    assert calls == ["HEAD"]


@pytest.mark.parametrize("failure", ["unbound", "wrong_repo", "replaced", "unborn", "timeout", "missing_blob", "missing_tree"])
def test_cannot_check_is_not_missing(target, tmp_path, failure):
    repo, svc, receipt = target
    if failure == "unbound":
        svc = MemoryService(svc.repository, SCOPE)
    elif failure == "wrong_repo":
        svc = bound(tmp_path / "memory.sqlite3", init_repo(tmp_path / "other"))
    elif failure == "replaced":
        repo.rename(tmp_path / "moved")
        init_repo(repo)
    elif failure == "unborn":
        git(repo, "checkout", "--orphan", "unborn")
    elif failure == "timeout":
        svc.verifier.git.timeout = 0
    else:
        oid = git(repo, "rev-parse", "HEAD:file.txt" if failure == "missing_blob" else "HEAD^{tree}")
        (repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    assert check(svc, receipt) == HeadComparison("Cannot check")


def test_authorization_and_membership_precede_git(target, monkeypatch):
    _, svc, receipt = target
    monkeypatch.setattr(svc.verifier, "check_identity", lambda: pytest.fail("unexpected Git access"))
    foreign = MemoryService(svc.repository, Scope("foreign"), svc.binding, svc.verifier)
    with pytest.raises(MemoryNotFound, match="^memory not found$"):
        check(foreign, receipt)
    unverified = svc.record(MemoryInput("file", source_uri="file.txt", snapshot="HEAD"), "source")
    assert check(svc, unverified) == HeadComparison("Cannot check")
    with pytest.raises(MemoryNotFound, match="^memory not found$"):
        svc.compare_at_head(unverified.memory_id, receipt.revision_id, 0)
    svc.forget(receipt.memory_id, receipt.revision_id, "forget")
    with pytest.raises(MemoryNotFound, match="^memory not found$"):
        check(svc, receipt)
