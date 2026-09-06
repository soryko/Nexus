from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from nexus_memory.domain.errors import (
    IdempotencyConflict,
    InvalidInput,
    MemoryNotFound,
    RevisionConflict,
    StorageIntegrityError,
    UnsupportedSchema,
    UnsupportedRuntime,
)
from nexus_memory.domain.models import MemoryInput, Scope, WriteReceipt
from nexus_memory.memory.service import MemoryService
from nexus_memory.storage.sqlite import SQLiteRepository


def service(path: Path, namespace: str = "team", actor: str = "local") -> MemoryService:
    return MemoryService(SQLiteRepository(path), Scope(namespace, actor))


def test_validation_normalizes_metadata_without_mutating_caller_values() -> None:
    tags = [" Beta ", "alpha", "ALPHA"]
    item = MemoryInput(" exact \n", tags=tags)
    tags.append("later")
    assert item.content == " exact \n"
    assert item.tags == ("alpha", "beta")
    with pytest.raises(InvalidInput):
        MemoryInput("")
    with pytest.raises(InvalidInput):
        MemoryInput("x" * 65537)
    with pytest.raises(InvalidInput):
        MemoryInput("\ud800")
    with pytest.raises(InvalidInput):
        MemoryInput("x", tags=("bad tag",))
    with pytest.raises(InvalidInput):
        MemoryInput("x", source_uri="\ud800")
    with pytest.raises(InvalidInput):
        Scope(3, "actor")  # type: ignore[arg-type]


def test_public_identifiers_reject_non_strings(tmp_path: Path) -> None:
    svc = service(tmp_path / "memory.db")
    with pytest.raises(InvalidInput):
        svc.get(object())  # type: ignore[arg-type]
    with pytest.raises(InvalidInput):
        svc.revise("id", object(), MemoryInput("x"), "key")  # type: ignore[arg-type]


def test_lifecycle_history_idempotency_and_logical_deletion(tmp_path: Path) -> None:
    svc = service(tmp_path / "memory.db")
    first = svc.record(MemoryInput("one", tags=("A",)), "create")
    assert svc.record(MemoryInput("one", tags=("a",)), "create") == first
    with pytest.raises(IdempotencyConflict):
        svc.record(MemoryInput("different"), "create")

    second = svc.revise(first.memory_id, first.revision_id, MemoryInput("two"), "revise")
    assert svc.get(first.memory_id).content == "two"
    old = svc.get(first.memory_id, first.revision_id)
    assert old.content == "one"
    assert old.current_revision_id == second.revision_id
    assert svc.revise(first.memory_id, first.revision_id, MemoryInput("two"), "revise") == second
    with pytest.raises(RevisionConflict):
        svc.revise(first.memory_id, first.revision_id, MemoryInput("three"), "stale")

    forgotten = svc.forget(first.memory_id, second.revision_id, "delete")
    assert svc.record(MemoryInput("one", tags=("a",)), "create") == first
    assert svc.revise(first.memory_id, first.revision_id, MemoryInput("two"), "revise") == second
    assert svc.forget(first.memory_id, second.revision_id, "delete") == forgotten
    for revision_id in (None, first.revision_id):
        with pytest.raises(MemoryNotFound):
            svc.get(first.memory_id, revision_id)
    with pytest.raises(MemoryNotFound):
        svc.revise(first.memory_id, second.revision_id, MemoryInput("back"), "resurrect")
    with pytest.raises(IdempotencyConflict):
        svc.record(MemoryInput("new"), "delete")


def test_scope_isolation_includes_actor_and_namespace(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    alice = service(path, "n", "alice")
    receipt = alice.record(MemoryInput("secret"), "same-key")
    for other in (service(path, "n", "bob"), service(path, "other", "alice")):
        with pytest.raises(MemoryNotFound):
            other.get(receipt.memory_id)
        independent = other.record(MemoryInput("other"), "same-key")
        assert independent.memory_id != receipt.memory_id


def test_exact_bytes_are_deduplicated_only_within_scope(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    svc = service(path)
    svc.record(MemoryInput("é\n"), "a")
    svc.record(MemoryInput("é\n"), "b")
    svc.record(MemoryInput("é\n"), "c")
    service(path, "elsewhere").record(MemoryInput("é\n"), "d")
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT body FROM blobs ORDER BY id").fetchall()
    assert [row[0] for row in rows].count("é\n".encode()) == 2
    assert "é\n".encode() in [row[0] for row in rows]


def test_blob_digest_collision_fails_closed_without_indexing_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    class Digest:
        def digest(self) -> bytes:
            return b"x" * 32

    monkeypatch.setattr("nexus_memory.storage.sqlite.hashlib", SimpleNamespace(sha256=lambda _: Digest()))
    path = tmp_path / "memory.db"
    svc = service(path)
    first = svc.record(MemoryInput("first"), "one")
    with pytest.raises(StorageIntegrityError):
        svc.record(MemoryInput("second"), "two")
    assert svc.get(first.memory_id).content == "first"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM blobs").fetchone()[0] == 1


def test_transactional_outbox_has_ordered_content_free_events(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    svc = service(path)
    one = svc.record(MemoryInput("do not copy me"), "a")
    two = svc.revise(one.memory_id, one.revision_id, MemoryInput("nor me"), "b")
    svc.forget(one.memory_id, two.revision_id, "c")
    with sqlite3.connect(path) as db:
        events = db.execute("SELECT durable_seq, payload FROM outbox ORDER BY durable_seq").fetchall()
    assert [seq for seq, _ in events] == [1, 2, 3]
    assert [json.loads(payload)["operation"] for _, payload in events] == ["record", "revise", "forget"]
    assert all("content" not in json.loads(payload) for _, payload in events)
    status = svc.status()
    assert (status.active_memories, status.forgotten_memories, status.revisions) == (0, 1, 2)
    assert (status.pending_outbox, status.latest_durable_seq) == (3, 3)


def test_independent_connections_enforce_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    initial = service(path).record(MemoryInput("zero"), "initial")

    def revise(number: int):
        return service(path).revise(
            initial.memory_id, initial.revision_id, MemoryInput(str(number)), f"revision-{number}"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revise, number) for number in (1, 2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except RevisionConflict as error:
            outcomes.append(error)
    assert sum(not isinstance(value, Exception) for value in outcomes) == 1
    assert sum(isinstance(value, RevisionConflict) for value in outcomes) == 1


def test_independent_connections_same_key_create_one_operation(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    SQLiteRepository(path)

    def record():
        return service(path).record(MemoryInput("same"), "same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: record(), range(2)))
    assert receipts[0] == receipts[1]
    with sqlite3.connect(path) as db:
        assert tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("blobs", "memories", "revisions", "receipts", "outbox")) == (1, 1, 1, 1, 1)


def test_unknown_future_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    SQLiteRepository(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 999")
    with pytest.raises(UnsupportedSchema):
        SQLiteRepository(path)


def test_unsafe_sqlite_runtime_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    with pytest.raises(UnsupportedRuntime):
        SQLiteRepository(tmp_path / "memory.db")


def test_repository_refuses_storage_that_cannot_enable_wal() -> None:
    with pytest.raises(StorageIntegrityError, match="^storage initialization failed$"):
        SQLiteRepository(":memory:")


def _crash_writer(path: str, phase: str) -> None:
    class CrashRepository(SQLiteRepository):
        def _before_commit(self) -> None:
            if phase == "before":
                os._exit(91)

        def _after_commit(self) -> None:
            if phase == "after":
                os._exit(92)

    MemoryService(CrashRepository(path), Scope("team", "local")).record(MemoryInput("payload"), "crash")


@pytest.mark.parametrize(("phase", "exitcode", "visible"), [("before", 91, False), ("after", 92, True)])
def test_subprocess_exit_at_commit_boundaries_recovers_consistently(
    tmp_path: Path, phase: str, exitcode: int, visible: bool
) -> None:
    path = tmp_path / "memory.db"
    SQLiteRepository(path)
    process = multiprocessing.get_context("spawn").Process(target=_crash_writer, args=(str(path), phase))
    process.start()
    process.join(10)
    assert process.exitcode == exitcode
    svc = service(path)
    with sqlite3.connect(path) as db:
        counts = tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("blobs", "memories", "revisions", "receipts", "outbox"))
        stored = db.execute("SELECT memory_id,revision_id,operation_id,durable_seq,operation FROM receipts").fetchone()
    assert counts == ((1, 1, 1, 1, 1) if visible else (0, 0, 0, 0, 0))
    if visible:
        assert svc.record(MemoryInput("payload"), "crash") == WriteReceipt(*stored)


def test_database_failures_are_sanitized_and_rollback(tmp_path: Path) -> None:
    class FailingRepository(SQLiteRepository):
        def _before_commit(self) -> None:
            raise sqlite3.OperationalError("sensitive SQL and /secret/path")

    path = tmp_path / "memory.db"
    svc = MemoryService(FailingRepository(path), Scope("team", "local"))
    with pytest.raises(StorageIntegrityError) as caught:
        svc.record(MemoryInput("private body"), "key")
    assert str(caught.value) == "storage operation failed"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_connection_failure_is_sanitized(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "memory.db")
    repository._connect = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("secret path"))  # type: ignore[method-assign]
    svc = MemoryService(repository, Scope("team", "local"))
    with pytest.raises(StorageIntegrityError, match="^storage operation failed$"):
        svc.record(MemoryInput("private"), "key")


def test_status_reads_one_committed_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    writer = service(path)
    fired = False

    class InterleavingRepository(SQLiteRepository):
        def _connect(self):
            nonlocal fired
            connection = super()._connect()
            def interleave(sql: str) -> None:
                nonlocal fired
                if not fired and "SELECT count(*) FROM revisions" in sql:
                    fired = True
                    writer.record(MemoryInput("new"), "interleaved")
            connection.set_trace_callback(interleave)
            return connection

    status = MemoryService(InterleavingRepository(path), Scope("team", "local")).status()
    assert (status.active_memories, status.revisions, status.pending_outbox) in {(0, 0, 0), (1, 1, 1)}


def _chain(error: BaseException) -> list[dict]:
    """The exception and everything it was raised from or during."""
    links, seen, current = [], set(), error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        links.append({
            "type": type(current).__name__,
            "message": str(current),
            "sqlite_errorname": getattr(current, "sqlite_errorname", None),
            "errno": getattr(current, "errno", None),
        })
        current = current.__cause__ or current.__context__
    return links


def _initialize_together(path: str, start, results) -> None:
    """One worker racing the others to initialize the same database.

    This used to report `type(error).__name__` and nothing else, which is why repeated
    runs have never explained the initialization flake: `OperationalError` alone names
    neither the statement, nor the chained cause, nor the worker it happened in. Blind
    repetition cannot identify a cause it does not record, so the worker now returns the
    traceback, the exception chain and its own pid, and the next ordinary failure carries
    the evidence with it.

    The reporting path itself has been exercised on a forced failure (an unopenable path,
    which surfaced the chained `SQLITE_CANTOPEN` the old code flattened away). That
    establishes diagnostic coverage and nothing more: an injected error says the reporting
    works, not what the original flake was. **The original cause remains unknown.**

    It need not stay unknown until an ordinary failure happens to recur, though — a
    controlled reproduction that provokes the same signature would establish a cause just
    as well. Neither is worth building on speculation, so the instrumentation stays in
    place, and further work waits for an observed failure or a specific testable
    hypothesis about the cause. Blind repetition is neither of those.
    """
    import traceback

    start.wait()
    try:
        SQLiteRepository(path)
        results.put({"pid": os.getpid(), "outcome": "ok"})
    except BaseException as error:  # noqa: BLE001 - a worker must report anything it hits
        results.put({
            "pid": os.getpid(),
            "outcome": "error",
            "type": type(error).__name__,
            "message": str(error),
            "chain": _chain(error),
            "traceback": traceback.format_exc(),
        })


def test_simultaneous_process_initialization_is_safe(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [context.Process(target=_initialize_together, args=(str(tmp_path / "new.db"), start, results)) for _ in range(8)]
    for process in processes:
        process.start()
    start.set()

    # Join every process before asserting anything, so one worker's exit code cannot hide
    # the state of the other seven, and record *why* each one is being reported.
    workers = []
    for process in processes:
        process.join(10)
        workers.append({
            "pid": process.pid,
            "exitcode": process.exitcode,
            "still_alive_after_join": process.is_alive(),
            "join_timed_out": process.exitcode is None,
        })
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join(5)

    # Drain what the workers actually reported. A missing message is itself evidence —
    # a worker that died before `put` leaves the queue short — so the shortfall is
    # recorded rather than raised as a bare Empty from inside the assertion.
    reports, queue_failures = [], []
    for _ in processes:
        try:
            reports.append(results.get(timeout=5))
        except queue.Empty:
            queue_failures.append("no message within 5s: a worker died before reporting")
            break

    diagnosis = {
        "database": str(tmp_path / "new.db"),
        "workers": workers,
        "reports": reports,
        "queue_failures": queue_failures,
        "messages_expected": len(processes),
        "messages_received": len(reports),
    }
    healthy = (all(w["exitcode"] == 0 for w in workers)
               and not queue_failures
               and [r["outcome"] for r in reports] == ["ok"] * len(processes))
    if not healthy:
        # Preserve the evidence: pytest keeps this directory for the last three runs, so a
        # flake that fires once in fifty is readable afterwards instead of being gone.
        log = tmp_path / "initialization-diagnosis.json"
        log.write_text(json.dumps(diagnosis, indent=2, default=str))
        pytest.fail(f"simultaneous initialization failed; diagnosis written to {log}\n"
                    f"{json.dumps(diagnosis, indent=2, default=str)}")


class LifecycleMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        import tempfile

        self.directory = tempfile.TemporaryDirectory()
        self.svc = service(Path(self.directory.name) / "memory.db")
        self.receipt = self.svc.record(MemoryInput("0"), "seed")
        self.expected = "0"
        self.deleted = False
        self.counter = 0

    @rule()
    def revise_or_observe_deleted(self) -> None:
        self.counter += 1
        if self.deleted:
            with pytest.raises(MemoryNotFound):
                self.svc.get(self.receipt.memory_id)
            return
        old = self.receipt
        self.expected = str(self.counter)
        self.receipt = self.svc.revise(old.memory_id, old.revision_id, MemoryInput(self.expected), f"k-{self.counter}")
        assert self.svc.get(old.memory_id, old.revision_id).content == str(self.counter - 1)

    @rule()
    def forget(self) -> None:
        if not self.deleted:
            self.receipt = self.svc.forget(self.receipt.memory_id, self.receipt.revision_id, "gone")
            self.deleted = True

    @invariant()
    def current_state_matches_model(self) -> None:
        if self.deleted:
            with pytest.raises(MemoryNotFound):
                self.svc.get(self.receipt.memory_id)
        else:
            assert self.svc.get(self.receipt.memory_id).content == self.expected

    def teardown(self) -> None:
        self.directory.cleanup()


TestLifecycle = LifecycleMachine.TestCase
TestLifecycle.settings = settings(max_examples=12, stateful_step_count=8, suppress_health_check=[HealthCheck.too_slow])
