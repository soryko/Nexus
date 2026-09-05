from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Iterator

from nexus_memory.domain.errors import (
    IdempotencyConflict,
    MemoryNotFound,
    NexusError,
    RevisionConflict,
    StorageIntegrityError,
    UnsupportedRuntime,
    UnsupportedSchema,
)
from nexus_memory.domain.models import MemoryInput, MemoryView, Scope, StoreStatus, WriteReceipt


class SQLiteRepository:
    SCHEMA_VERSION = 1
    MINIMUM_SQLITE = (3, 51, 3)

    def __init__(self, path: str | Path) -> None:
        if sqlite3.sqlite_version_info < self.MINIMUM_SQLITE:
            raise UnsupportedRuntime("SQLite 3.51.3 or newer is required")
        self.path = str(path)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    def _migrate(self) -> None:
        connection = None
        try:
            connection = self._connect()
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if journal_mode.lower() != "wal":
                raise StorageIntegrityError("storage initialization failed")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise UnsupportedSchema("database schema is newer than this application")
            if version == 0:
                script = files("nexus_memory.storage.migrations").joinpath("001_initial.sql").read_text()
                for statement in script.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            connection.commit()
        except NexusError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise StorageIntegrityError("storage initialization failed") from error
        finally:
            if connection is not None:
                connection.close()

    def _before_commit(self) -> None:
        pass

    def _after_commit(self) -> None:
        pass

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            self._before_commit()
            connection.commit()
            self._after_commit()
        except NexusError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise StorageIntegrityError("storage operation failed") from error
        except BaseException:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _receipt(row: tuple) -> WriteReceipt:
        return WriteReceipt(row[0], row[1], row[2], row[3], row[4])

    def _retry(self, db: sqlite3.Connection, scope: Scope, key: str, digest: str) -> WriteReceipt | None:
        row = db.execute(
            "SELECT memory_id,revision_id,operation_id,durable_seq,operation,request_digest FROM receipts WHERE namespace=? AND actor=? AND idempotency_key=?",
            (scope.namespace, scope.actor, key),
        ).fetchone()
        if row is None:
            return None
        if row[5] != digest:
            raise IdempotencyConflict("idempotency key was used for a different request")
        return self._receipt(row[:5])

    @staticmethod
    def _ids() -> tuple[str, str]:
        return str(uuid.uuid4()), str(uuid.uuid4())

    def _blob(self, db: sqlite3.Connection, scope: Scope, body: bytes, digest: bytes) -> int:
        row = db.execute("SELECT id,body FROM blobs WHERE namespace=? AND actor=? AND digest=?", (scope.namespace, scope.actor, digest)).fetchone()
        if row is not None:
            if bytes(row[1]) != body:
                raise StorageIntegrityError("storage integrity check failed")
            return row[0]
        cursor = db.execute("INSERT INTO blobs(namespace,actor,digest,body) VALUES(?,?,?,?)", (scope.namespace, scope.actor, digest, body))
        return cursor.lastrowid

    def _event_and_receipt(self, db: sqlite3.Connection, scope: Scope, key: str, digest: str, memory_id: str, revision_id: str, operation: str) -> WriteReceipt:
        operation_id = str(uuid.uuid4())
        payload = json.dumps({"memory_id": memory_id, "revision_id": revision_id, "operation_id": operation_id, "operation": operation}, separators=(",", ":"))
        cursor = db.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES(?,?,?,?)", (scope.namespace, scope.actor, operation_id, payload))
        receipt = WriteReceipt(memory_id, revision_id, operation_id, cursor.lastrowid, operation)
        db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, key, digest, memory_id, revision_id, operation_id, receipt.durable_seq, operation))
        return receipt

    def record(self, scope: Scope, item: MemoryInput, key: str, digest: str) -> WriteReceipt:
        body = item.content.encode("utf-8")
        body_digest = hashlib.sha256(body).digest()
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry
            memory_id, revision_id = self._ids()
            blob_id = self._blob(db, scope, body, body_digest)
            db.execute("INSERT INTO memories VALUES(?,?,?,?,0)", (scope.namespace, scope.actor, memory_id, revision_id))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, memory_id, revision_id, None, blob_id, item.kind, json.dumps(item.tags), item.source_uri, item.snapshot, datetime.now(UTC).isoformat()))
            return self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "record")

    def _head(self, db: sqlite3.Connection, scope: Scope, memory_id: str) -> tuple[str, int]:
        row = db.execute("SELECT current_revision_id,tombstoned FROM memories WHERE namespace=? AND actor=? AND memory_id=?", (scope.namespace, scope.actor, memory_id)).fetchone()
        if row is None or row[1]:
            raise MemoryNotFound("memory not found")
        return row

    def revise(self, scope: Scope, memory_id: str, expected_revision_id: str, item: MemoryInput, key: str, digest: str) -> WriteReceipt:
        body = item.content.encode("utf-8")
        body_digest = hashlib.sha256(body).digest()
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry
            head, _ = self._head(db, scope, memory_id)
            if head != expected_revision_id:
                raise RevisionConflict("current revision does not match expected revision")
            revision_id = str(uuid.uuid4())
            blob_id = self._blob(db, scope, body, body_digest)
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, memory_id, revision_id, head, blob_id, item.kind, json.dumps(item.tags), item.source_uri, item.snapshot, datetime.now(UTC).isoformat()))
            db.execute("UPDATE memories SET current_revision_id=? WHERE namespace=? AND actor=? AND memory_id=?", (revision_id, scope.namespace, scope.actor, memory_id))
            return self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "revise")

    def forget(self, scope: Scope, memory_id: str, expected_revision_id: str, key: str, digest: str) -> WriteReceipt:
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry
            head, _ = self._head(db, scope, memory_id)
            if head != expected_revision_id:
                raise RevisionConflict("current revision does not match expected revision")
            db.execute("UPDATE memories SET tombstoned=1 WHERE namespace=? AND actor=? AND memory_id=?", (scope.namespace, scope.actor, memory_id))
            return self._event_and_receipt(db, scope, key, digest, memory_id, head, "forget")

    def get(self, scope: Scope, memory_id: str, revision_id: str | None = None) -> MemoryView:
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                head, _ = self._head(db, scope, memory_id)
                selected = revision_id or head
                row = db.execute("SELECT r.revision_id,r.parent_revision_id,b.body,r.kind,r.tags_json,r.source_uri,r.snapshot,r.created_at FROM revisions r JOIN blobs b ON b.id=r.blob_id WHERE r.namespace=? AND r.actor=? AND r.memory_id=? AND r.revision_id=?", (scope.namespace, scope.actor, memory_id, selected)).fetchone()
                if row is None:
                    raise MemoryNotFound("memory not found")
                result = MemoryView(memory_id, row[0], row[1], bytes(row[2]).decode("utf-8"), row[3], tuple(json.loads(row[4])), row[5], row[6], row[7], head)
                db.commit()
                return result
        except NexusError:
            raise
        except (sqlite3.Error, UnicodeError, ValueError) as error:
            raise StorageIntegrityError("storage operation failed") from error

    def status(self, scope: Scope) -> StoreStatus:
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                active, forgotten = db.execute(
                    "SELECT coalesce(sum(tombstoned=0),0),coalesce(sum(tombstoned=1),0) FROM memories WHERE namespace=? AND actor=?",
                    (scope.namespace, scope.actor),
                ).fetchone()
                revisions = db.execute("SELECT count(*) FROM revisions WHERE namespace=? AND actor=?", (scope.namespace, scope.actor)).fetchone()[0]
                pending, latest = db.execute(
                    "SELECT count(*),coalesce(max(durable_seq),0) FROM outbox WHERE namespace=? AND actor=?",
                    (scope.namespace, scope.actor),
                ).fetchone()
                result = StoreStatus(
                    "sqlite-wal-full", "available", "unavailable", self.SCHEMA_VERSION,
                    sqlite3.sqlite_version, active, forgotten, revisions, pending, latest,
                )
                db.commit()
                return result
        except sqlite3.Error as error:
            raise StorageIntegrityError("storage operation failed") from error
