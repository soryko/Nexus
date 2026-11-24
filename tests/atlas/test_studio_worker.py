"""Tests for the read-only StudioWorker protocol and privacy/immutability invariants."""
from __future__ import annotations

import hashlib
import json
import shlex
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_memory.activity import ActivityJournal
from nexus_memory.atlas.studio_worker import StudioWorker
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
SCOPE_DIR = REPO_ROOT / "integrations" / "project_scope"


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return res.stdout.strip()


def _digest_files(*paths: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        if p.exists() and p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(SCOPE_DIR))
    import project_scope as scope_mod

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))

    repo = tmp_path / "repo"
    repo.mkdir()
    repo = repo.resolve()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "app.py").write_text("print('v1')\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "initial commit")

    observed = scope_mod.identity(repo / ".git")
    if observed["birth_time"] is None:
        observed["birth_time"] = 123456.25
        monkeypatch.setattr(scope_mod, "identity", lambda _: observed.copy())

    unenrolled = tmp_path / "unenrolled"
    unenrolled.mkdir()
    unenrolled = unenrolled.resolve()
    _git(unenrolled, "init", "-q")

    worker = StudioWorker(scope_mod)
    return type("Env", (), {
        "home": home,
        "repo": repo,
        "unenrolled": unenrolled,
        "scope_mod": scope_mod,
        "worker": worker,
    })


def test_status_diagnosis_across_unenrolled_empty_drift_and_ambiguous(studio_env, tmp_path: Path) -> None:
    w = studio_env.worker
    scope_mod = studio_env.scope_mod

    # 1. Empty / ambiguous / non-file URI
    assert w.handle({"id": 1, "action": "status", "workspace": []})["data"]["state"] == "empty"
    ambig = w.handle({"id": 2, "action": "status", "workspace": [studio_env.repo.as_uri(), studio_env.unenrolled.as_uri()]})
    assert ambig["ok"] and ambig["data"]["state"] == "ambiguous"
    assert w.handle({"id": 3, "action": "status", "workspace": "https://example.com/repo"})["data"]["state"] == "invalid"

    # 2. Unenrolled workspace creates zero registry/DB files
    un = w.handle({"id": 4, "action": "status", "workspace": studio_env.unenrolled.as_uri()})
    assert un["ok"] and un["data"]["state"] == "unenrolled"
    assert un["data"]["project_id"] is None
    assert not (studio_env.home / ".local/share/nexus-memory").exists()

    # 3. Enrolled but uninitialized store (no .sqlite3 yet)
    pid = scope_mod.enroll(studio_env.repo)
    empty_bound = w.handle({"id": 5, "action": "status", "workspace": str(studio_env.repo)})
    assert empty_bound["ok"]
    assert empty_bound["data"]["state"] == "bound"
    assert empty_bound["data"]["project_id"] == pid
    assert empty_bound["data"]["store_initialized"] is False
    assert empty_bound["data"]["overview"] == {"total": 0, "counts": [], "generation": 0}
    db_path = scope_mod.private_base() / f"{pid}.sqlite3"
    assert not db_path.exists()
    assert w.handle({"id": 6, "action": "activity", "workspace": str(studio_env.repo)})["data"]["events"] == []
    assert not ActivityJournal.path_for_store(db_path).exists()

    # 4. APFS device drift surfaces project_id + recover_command and stays fail-closed
    registry_path = scope_mod.private_base() / "registry.json"
    reg = json.loads(registry_path.read_bytes())
    key = str(studio_env.repo / ".git")
    reg["projects"][key]["device"] += 3
    registry_path.write_text(json.dumps(reg))

    drift = w.handle({"id": 7, "action": "status", "workspace": studio_env.repo.as_uri()})
    assert drift["ok"]
    assert drift["data"]["state"] == "device_drift"
    assert drift["data"]["project_id"] == pid
    assert drift["data"]["recorded_device"] == reg["projects"][key]["device"]
    assert drift["data"]["observed_device"] == reg["projects"][key]["device"] - 3
    assert "recover" in drift["data"]["recover_command"]
    assert pid in drift["data"]["recover_command"]

    # A linked worktree's recover command must name the main checkout: recover() goes through
    # main_common(), which refuses any other path, so naming the worktree could never succeed.
    linked = studio_env.repo.parent / "linked"
    _git(studio_env.repo, "worktree", "add", "-q", str(linked))
    linked = linked.resolve()
    linked_drift = w.handle({"id": 70, "action": "status", "workspace": str(linked)})
    assert linked_drift["data"]["state"] == "device_drift"
    argv = shlex.split(linked_drift["data"]["recover_command"])
    assert argv[3] == "recover" and Path(argv[4]) == studio_env.repo
    assert scope_mod.main_common(Path(argv[4])) == studio_env.repo / ".git"
    assert w.handle({"id": 8, "action": "overview", "workspace": str(studio_env.repo)})["ok"] is False


def test_atlas_activity_and_compare_at_head_never_mutate_git_or_sqlite(studio_env) -> None:
    os.umask(0o077)
    scope_mod = studio_env.scope_mod
    pid = scope_mod.enroll(studio_env.repo)
    db_path = scope_mod.private_base() / f"{pid}.sqlite3"
    scope = Scope(pid, "local")

    # Populate store WITHOUT publishing a git checkout-token yet to test zero-token compare_at_head
    store = SQLiteRepository(db_path)
    svc_unpinned = MemoryService(store, scope)
    r1 = svc_unpinned.record(
        MemoryInput("First decision", kind="decision", tags=("runtime", "init"), source_uri="app.py"),
        "seed-1",
    )
    token_file = studio_env.repo / ".git" / "nexus" / "checkout-token"
    assert not token_file.exists()

    w = studio_env.worker
    # Status and compare_at_head without token: returns Cannot check and never writes checkout-token
    no_tok = w.handle({
        "id": 10,
        "action": "compare_at_head",
        "workspace": str(studio_env.repo),
        "params": {"memory_id": r1.memory_id, "revision_id": r1.revision_id, "reference_ordinal": 0},
    })
    assert no_tok["ok"]
    assert no_tok["data"]["outcome"] == "Cannot check"
    assert not token_file.exists()

    # Now bind repository properly with a verified Git reference + activity journal entries
    binding, verifier = bind_repository(store, scope, studio_env.repo, None, GitCli())
    svc = MemoryService(store, scope, binding, verifier)
    r2 = svc.record(
        MemoryInput(
            "Verify app entrypoint",
            kind="constraint",
            tags=("runtime", "cli"),
            references=(ReferenceInput("app.py"),),
        ),
        "seed-2",
    )
    r2_rev2 = svc.revise(
        r2.memory_id,
        r2.revision_id,
        MemoryInput(
            "Verify app entrypoint v2",
            kind="constraint",
            tags=("runtime", "cli"),
            references=(ReferenceInput("app.py"),),
        ),
        "seed-3",
    )
    journal = ActivityJournal(ActivityJournal.path_for_store(db_path))
    journal.record_event("session_start", "bound", session_id="s1")
    journal.record_event("recall", "offered", session_id="s1", detail={"candidate_ids": [r2.memory_id], "candidate_count": 1})

    immutable_files = [
        db_path,
        journal.path,
        token_file,
    ]
    before_digests = _digest_files(*immutable_files)

    # Exercise all read-only actions
    st = w.handle({"id": 11, "action": "status", "workspace": studio_env.repo.as_uri()})
    assert st["ok"] and st["data"]["overview"]["total"] == 2
    assert st["data"]["activity_health"]["total_events"] == 2

    act = w.handle({"id": 12, "action": "activity", "workspace": str(studio_env.repo), "params": {"category": "recall"}})
    assert act["ok"] and len(act["data"]["events"]) == 1
    assert act["data"]["events"][0]["detail"]["candidate_ids"] == [r2.memory_id]

    srch = w.handle({"id": 13, "action": "search", "workspace": str(studio_env.repo), "params": {"query": "entrypoint"}})
    assert srch["ok"] and srch["data"]["matched"] == 1

    insp = w.handle({"id": 14, "action": "inspect", "workspace": str(studio_env.repo), "params": {"memory_id": r2.memory_id}})
    assert insp["ok"] and insp["data"]["content"] == "Verify app entrypoint v2"

    hist = w.handle({"id": 15, "action": "history", "workspace": str(studio_env.repo), "params": {"memory_id": r2.memory_id}})
    assert hist["ok"] and len(hist["data"]["entries"]) == 2

    cmp_rev = w.handle({
        "id": 16,
        "action": "compare_revision",
        "workspace": str(studio_env.repo),
        "params": {"memory_id": r2.memory_id, "revision_id": r2_rev2.revision_id},
    })
    assert cmp_rev["ok"] and cmp_rev["data"]["state"] == "ok"

    nbr = w.handle({"id": 17, "action": "neighborhood", "workspace": str(studio_env.repo), "params": {"memory_id": r2.memory_id}})
    assert nbr["ok"] and len(nbr["data"]["relations"]) == 2

    rel = w.handle({"id": 18, "action": "related", "workspace": str(studio_env.repo), "params": {"memory_id": r2.memory_id, "tag": "runtime"}})
    assert rel["ok"] and rel["data"]["page"]["matched"] == 1

    head_ok = w.handle({
        "id": 19,
        "action": "compare_at_head",
        "workspace": str(studio_env.repo),
        "params": {"memory_id": r2.memory_id, "revision_id": r2_rev2.revision_id, "reference_ordinal": 0},
    })
    assert head_ok["ok"] and head_ok["data"]["outcome"] == "Matches at HEAD"

    # Mutate app.py in a new commit and verify Changed at HEAD
    (studio_env.repo / "app.py").write_text("print('v2')\n")
    _git(studio_env.repo, "add", "app.py")
    _git(studio_env.repo, "commit", "-qm", "update app")
    head_changed = w.handle({
        "id": 20,
        "action": "compare_at_head",
        "workspace": str(studio_env.repo),
        "params": {"memory_id": r2.memory_id, "revision_id": r2_rev2.revision_id, "reference_ordinal": 0},
    })
    assert head_changed["ok"] and head_changed["data"]["outcome"] == "Changed at HEAD"

    assert _digest_files(*immutable_files) == before_digests
    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists():
        assert wal_path.stat().st_size == 0


def test_studio_worker_cli_ndjson_roundtrip(studio_env) -> None:
    req = json.dumps({"id": "ping-1", "action": "status", "workspace": str(studio_env.unenrolled)}) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_memory.atlas.studio_worker"],
        input=req,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(studio_env.home), "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
    )
    resp = json.loads(proc.stdout.strip())
    assert resp["id"] == "ping-1"
    assert resp["ok"] is True
    assert resp["data"]["state"] == "unenrolled"


def test_unenrolled_status_offers_a_shell_quoted_enroll_command(studio_env) -> None:
    # The command is built by the worker with shlex.quote, never by the page, so a checkout whose
    # name holds shell metacharacters round-trips through a shell as the literal path.
    hostile = studio_env.repo.parent / "repo$backup $(touch pwned)'q"
    hostile.mkdir()
    hostile = hostile.resolve()
    _git(hostile, "init", "-q")
    status = studio_env.worker.handle({"id": 90, "action": "status", "workspace": str(hostile)})
    assert status["data"]["state"] == "unenrolled"
    argv = shlex.split(status["data"]["enroll_command"])
    assert argv[3] == "enroll" and Path(argv[4]) == hostile
    assert studio_env.scope_mod.main_common(Path(argv[4])) == hostile / ".git"

    bound_or_empty = studio_env.worker.handle({"id": 91, "action": "status", "workspace": []})
    assert bound_or_empty["data"]["enroll_command"] is None



def test_status_reports_the_checkout_root_for_a_subdirectory_workspace(studio_env) -> None:
    # Reference paths are repository-relative, so file links must be built from the checkout
    # root, not from a subdirectory the host opened as its workspace.
    studio_env.scope_mod.enroll(studio_env.repo)
    sub = studio_env.repo / "pkg"
    sub.mkdir()
    status = studio_env.worker.handle({"id": 95, "action": "status", "workspace": sub.as_uri()})
    assert status["data"]["state"] == "bound"
    assert status["data"]["checkout_uri"] == studio_env.repo.as_uri()
    empty = studio_env.worker.handle({"id": 96, "action": "status", "workspace": []})
    assert empty["data"]["checkout_uri"] is None
