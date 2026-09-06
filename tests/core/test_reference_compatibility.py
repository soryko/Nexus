"""B2a compatibility obligations and the service-level acceptance tests that need no git.

Acceptance tests 12, 13 and 14 in full; the resolution counts of test 3, the lock
boundary and concurrent double resolution of test 11, the evidence labelling and scope
honesty of test 1, and the application-side path validation of test 8, all against a
scripted verifier. The same scenarios run against real git in the adapter tests.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from reference_fakes import BLOB, OID1, OID2, OID3, FakeVerifier, RaisingVerifier, binding, entry, register
from test_migration_b1 import _v1_database

from nexus_memory.domain.errors import (
    IdempotencyConflict,
    InvalidReference,
    MemoryNotFound,
    VerificationUnavailable,
)
from nexus_memory.domain.models import MemoryInput, ReferenceInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository

SCOPE = Scope("compat", "local")

# Version-1 digests computed by the frozen function at d97f09d, before any B2a change.
PINNED_V1 = {
    ("record", None, None, ("payments retry policy", "decision", ("payments",), None, None)):
        "a29cd383f1d4ad2c03a7f6f37fc60400eb1dca7670f230205d0e7440272b477b",
    ("record", None, None, ("plain body", "observation", ("a", "b"), "file:///x", "s")):
        "5134fefb30594b4a0593c5d2faff3111fd97c4f989b950ea3d4ad99c1cd10836",
    ("revise", "m-1", "r-1", ("two", "observation", (), None, None)):
        "ceaf196c291ae3884c873d0f105079c5bf173c251fa72bf1ad7c332d5eb5ed4c",
    ("forget", "m-1", "r-1", None):
        "76d33b200deff8420ee4994ce2eb32e4c34cea197f6f0fab7a1dd90b784ae3c0",
}


def verifier_with_head(oid: str = OID1, path: str = "sub/file.txt") -> FakeVerifier:
    return FakeVerifier({"HEAD": oid}, {(oid, path): (entry(path),)})


def service(db: Path, verifier=None, bound=None, scope: Scope = SCOPE) -> MemoryService:
    repository = SQLiteRepository(db)
    if bound is not None:
        register(db, scope, bound.repository_id, object_format=bound.object_format)
    return MemoryService(repository, scope, bound, verifier)


# --- acceptance test 12: migration and retry --------------------------------------------


def test_reference_free_requests_keep_the_pinned_version_1_digest_byte_for_byte() -> None:
    for (operation, target, expected, fields), digest in PINNED_V1.items():
        item = None if fields is None else MemoryInput(fields[0], fields[1], fields[2], fields[3], fields[4])
        assert MemoryService._digest(operation, target, expected, item) == digest
        if item is not None:
            assert MemoryService(None, SCOPE)._request_digest(operation, target, expected, item) == (digest, 1)  # type: ignore[arg-type]


def test_a_reference_carrying_request_uses_version_2_over_caller_input_only() -> None:
    svc = MemoryService(None, SCOPE)  # type: ignore[arg-type]
    plain = MemoryInput("body")
    with_reference = MemoryInput("body", references=(ReferenceInput("sub/file.txt", "HEAD"),))
    plain_digest, plain_version = svc._request_digest("record", None, None, plain)
    reference_digest, reference_version = svc._request_digest("record", None, None, with_reference)
    assert (plain_version, reference_version) == (1, 2)
    assert plain_digest != reference_digest
    # The same caller input hashes identically whatever the repository resolves it to.
    assert svc._request_digest("record", None, None, MemoryInput("body", references=(ReferenceInput("sub/file.txt", "HEAD"),))) == (reference_digest, 2)
    # Omitted and literal HEAD are one spec at verification, but distinct caller input.
    omitted = svc._request_digest("record", None, None, MemoryInput("body", references=(ReferenceInput("sub/file.txt"),)))
    assert omitted[0] != reference_digest


def test_receipt_written_before_migration_004_still_replays(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    _, _, key = _v1_database(db)
    svc = service(db, RaisingVerifier(), binding(), Scope("a1", "local"))
    replayed = svc.record(MemoryInput("payments retry policy", kind="decision", tags=["payments"]), key)
    assert (replayed.memory_id, replayed.revision_id, replayed.operation_id) == ("m-1", "r-1", "op-1")
    assert svc.get("m-1").references == ()


def test_same_key_with_a_new_reference_conflicts_before_git_is_reached(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    service(db).record(MemoryInput("body"), "key")
    with pytest.raises(IdempotencyConflict):
        service(db, RaisingVerifier(), binding()).record(
            MemoryInput("body", references=(ReferenceInput("sub/file.txt"),)), "key"
        )


def test_retry_returns_original_receipt_and_evidence_with_a_verifier_that_must_not_run(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = verifier_with_head(OID1)
    item = MemoryInput("body", references=(ReferenceInput("sub/file.txt", "HEAD"),))
    original = service(db, verifier, binding()).record(item, "key")
    recorded = service(db).get(original.memory_id).references
    assert [(r.commit_oid, r.object_oid, r.repository_id) for r in recorded] == [(OID1, BLOB, "repo-a")]

    # HEAD has moved, the object is unavailable, and the verifier raises if reached.
    for detached in (
        service(db, RaisingVerifier(), binding()),
        service(db, RaisingVerifier(), None),
        service(db, None, None),
    ):
        assert detached.record(item, "key") == original
        assert detached.get(original.memory_id).references == recorded

    revised = service(db, verifier_with_head(OID2), binding()).revise(original.memory_id, original.revision_id, item, "key-2")
    assert service(db, RaisingVerifier(), None).revise(original.memory_id, original.revision_id, item, "key-2") == revised
    assert [r.commit_oid for r in service(db).get(original.memory_id).references] == [OID2]
    assert [r.commit_oid for r in service(db).get(original.memory_id, original.revision_id).references] == [OID1]


# --- acceptance test 13: verification unavailable ----------------------------------------


def test_verification_unavailable_refuses_only_reference_carrying_writes(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    for svc in (service(db, None, None), service(db, None, binding("registered-but-no-git"))):
        status = svc.status()
        assert status.verification == "unavailable"
        plain = svc.record(MemoryInput("plain"), f"plain-{status.repository_id}")
        assert svc.get(plain.memory_id).content == "plain"
        with pytest.raises(VerificationUnavailable):
            svc.record(MemoryInput("with reference", references=(ReferenceInput("sub/file.txt"),)), "ref")
        with pytest.raises(VerificationUnavailable):
            svc.revise(plain.memory_id, plain.revision_id, MemoryInput("x", references=(ReferenceInput("a"),)), "ref-2")
    assert service(db, None, None).status().repository_id is None
    assert service(db, None, binding("registered-but-no-git")).status().repository_id == "registered-but-no-git"
    assert service(db, FakeVerifier(), binding()).status().verification == "available"


# --- acceptance test 14: restart without git ---------------------------------------------


def test_restart_without_git_keeps_bindings_evidence_and_replay_readable(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    item = MemoryInput("append-only ledger", tags=("ledger",), references=(ReferenceInput("sub/file.txt"),))
    receipt = service(db, verifier_with_head(OID1), binding("repo-a")).record(item, "key")

    restarted = service(db, None, None)  # git gone: no binding, no verifier
    assert restarted.status().verification == "unavailable"
    view = restarted.get(receipt.memory_id)
    assert [(r.repository_id, r.commit_oid, r.path, r.object_oid, r.entry_type, r.mode) for r in view.references] == [
        ("repo-a", OID1, "sub/file.txt", BLOB, "blob", "100644")
    ]
    assert view.references[0].evidence == ("commit_resolved", "path_resolved", "content_unverified")
    hits = restarted.search(SearchQuery(query="ledger")).hits
    assert [hit.references for hit in hits] == [view.references]
    assert restarted.history(receipt.memory_id).entries[0].revision_id == receipt.revision_id
    assert restarted.record(item, "key") == receipt

    forgotten = restarted.forget(receipt.memory_id, receipt.revision_id, "forget")
    assert restarted.record(item, "key") == receipt           # the receipt still replays
    assert restarted.forget(receipt.memory_id, receipt.revision_id, "forget") == forgotten
    with pytest.raises(MemoryNotFound):                        # while get refuses, tombstone rules unchanged
        restarted.get(receipt.memory_id)
    assert restarted.search(SearchQuery(query="ledger")).hits == ()


# --- acceptance test 3: resolution per distinct spec -------------------------------------


def test_one_spec_resolves_once_and_three_specs_resolve_three_times(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = FakeVerifier(
        {"HEAD": OID1, "main": OID1, "v1": OID2, OID3: OID3},
        {(OID1, "a"): (entry("a"),), (OID1, "b"): (entry("b"),), (OID1, "c"): (entry("c"),),
         (OID2, "b"): (entry("b", "e" * 40),), (OID3, "c"): (entry("c"),)},
    )
    svc = service(db, verifier, binding())

    one = svc.record(MemoryInput("one spec", references=(
        ReferenceInput("a"), ReferenceInput("b", "HEAD"), ReferenceInput("c"),
    )), "one")
    assert verifier.resolutions == ["HEAD"]
    assert {r.commit_oid for r in svc.get(one.memory_id).references} == {OID1}
    assert [r.path for r in svc.get(one.memory_id).references] == ["a", "b", "c"]

    verifier.resolutions.clear()
    three = svc.record(MemoryInput("three specs", references=(
        ReferenceInput("a", "main"), ReferenceInput("b", "v1"), ReferenceInput("c", OID3), ReferenceInput("b", "main"),
    )), "three")
    assert sorted(verifier.resolutions) == sorted(["main", "v1", OID3])
    stored = {(r.commit_oid, r.path): r.object_oid for r in svc.get(three.memory_id).references}
    assert stored == {(OID1, "a"): BLOB, (OID1, "b"): BLOB, (OID2, "b"): "e" * 40, (OID3, "c"): BLOB}


def test_moving_a_branch_changes_no_stored_evidence_and_the_recorded_oid_still_verifies(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = FakeVerifier({"main": OID1}, {(OID1, "a"): (entry("a"),)})
    svc = service(db, verifier, binding())
    receipt = svc.record(MemoryInput("branch", references=(ReferenceInput("a", "main"),)), "k")

    verifier.commits["main"] = OID2  # the branch moved
    assert [r.commit_oid for r in svc.get(receipt.memory_id).references] == [OID1]
    again = svc.record(MemoryInput("pinned", references=(ReferenceInput("a", OID1),)), "k2")
    assert [r.commit_oid for r in svc.get(again.memory_id).references] == [OID1]


# --- acceptance test 11: lock boundary and concurrent double resolution ------------------


def test_an_independent_write_completes_while_verification_is_paused(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    verifier = verifier_with_head()
    verifier.pause = threading.Event()
    svc = service(db, verifier, binding())
    item = MemoryInput("paused", references=(ReferenceInput("sub/file.txt"),))

    with ThreadPoolExecutor(max_workers=1) as pool:
        paused_write = pool.submit(svc.record, item, "paused")
        assert verifier.paused.wait(timeout=10)
        started = time.perf_counter()
        independent = service(db).record(MemoryInput("independent"), "independent")
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"independent write took {elapsed:.2f}s: a lock was held during verification"
        assert not paused_write.done()
        verifier.pause.set()
        committed = paused_write.result(timeout=10)

    assert independent.durable_seq < committed.durable_seq
    assert len(service(db).get(committed.memory_id).references) == 1


def test_two_writers_under_one_key_commit_one_revision_and_one_evidence_record(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    SQLiteRepository(db)
    heads = iter([OID1, OID2])
    lock = threading.Lock()

    class MovingHead(FakeVerifier):
        def resolve_commit(self, spec: str) -> str | None:
            with lock:
                oid = next(heads)
            self.resolutions.append(spec)
            return oid

    verifier = MovingHead({}, {(OID1, "a"): (entry("a", "1" * 40),), (OID2, "a"): (entry("a", "2" * 40),)})
    verifier.barrier = threading.Barrier(2)  # both writers pass the pre-check and verify before either commits
    item = MemoryInput("racing", references=(ReferenceInput("a"),))

    def write() -> object:
        return service(db, verifier, binding()).record(item, "same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = [future.result(timeout=20) for future in [pool.submit(write), pool.submit(write)]]

    assert receipts[0] == receipts[1]
    assert sorted(verifier.resolutions) == ["HEAD", "HEAD"]
    reader = service(db)
    references = reader.get(receipts[0].memory_id).references
    assert len(references) == 1
    assert reader.status().revisions == 1
    winner_oid = references[0].commit_oid
    assert references[0].object_oid == winner_oid  # evidence describes the winner's own observation
    import sqlite3
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT count(*) FROM revision_references").fetchone()[0] == 1
        assert raw.execute("SELECT digest_version FROM receipts").fetchall() == [(2,)]


# --- acceptance test 1: evidence labelled with the identity that recorded it -------------


def test_evidence_is_labelled_by_binding_and_scope_remains_the_only_boundary(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    alpha = service(db, verifier_with_head(OID1), binding("repo-alpha"))
    beta = service(db, verifier_with_head(OID2), binding("repo-beta"))
    item = MemoryInput("shared scope", references=(ReferenceInput("sub/file.txt"),))

    a = alpha.record(item, "a")
    b = beta.record(item, "b")
    assert alpha.get(a.memory_id).references[0].repository_id == "repo-alpha"
    assert beta.get(b.memory_id).references[0].repository_id == "repo-beta"
    # Within one scope the repository is not an isolation boundary.
    assert beta.get(a.memory_id).references[0].repository_id == "repo-alpha"
    assert alpha.get(b.memory_id).references[0].repository_id == "repo-beta"
    assert beta.record(item, "a") == a  # identical input under one key replays across bindings
    # Invisibility is asserted with a different scope, never with a different binding.
    elsewhere = service(db, verifier_with_head(OID1), binding("repo-alpha"), Scope("compat", "other"))
    with pytest.raises(MemoryNotFound):
        elsewhere.get(a.memory_id)
    assert elsewhere.record(item, "a").memory_id != a.memory_id


# --- acceptance test 8: unsafe paths never reach the verifier ----------------------------


@pytest.mark.parametrize("path", [
    "/etc/passwd", "../etc/passwd", "sub/../file.txt", "sub/./file.txt", "/sub/file.txt",
    "sub/file.txt/", "sub//file.txt", "", "a\x00b", "..", ".", "x" * 1025,
])
def test_unsafe_paths_are_refused_in_the_application(tmp_path: Path, path: str) -> None:
    db = tmp_path / "memory.sqlite3"
    svc = service(db, RaisingVerifier(), binding())
    with pytest.raises(InvalidReference) as failure:
        svc.record(MemoryInput("x", references=(ReferenceInput(path),)), "k")
    message = str(failure.value)
    assert str(tmp_path) not in message and "git" not in message.lower() and "/" not in message
    assert svc.status().active_memories == 0


@pytest.mark.parametrize("spec", ["", "-rf", "a b", "a\nb", "x" * 257, "\x01"])
def test_unsafe_commit_specs_are_refused_in_the_application(spec: str) -> None:
    with pytest.raises(InvalidReference):
        ReferenceInput("a", spec)


def test_reference_sets_are_normalised_and_capped() -> None:
    item = MemoryInput("x", references=(
        ReferenceInput("b"), ReferenceInput("a", "main"), ReferenceInput("a"), ReferenceInput("a"),
    ))
    assert item.references == (ReferenceInput("a"), ReferenceInput("b"), ReferenceInput("a", "main"))
    with pytest.raises(InvalidReference):
        MemoryInput("x", references=tuple(ReferenceInput(f"p{n}") for n in range(9)))
    with pytest.raises(InvalidReference):
        MemoryInput("x", references=("not a reference",))  # type: ignore[arg-type]
