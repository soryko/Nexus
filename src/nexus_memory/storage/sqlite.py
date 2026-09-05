from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
import re
from typing import Iterator

from nexus_memory.domain.errors import (
    CursorExpired,
    IdempotencyConflict,
    InvalidQuery,
    MemoryNotFound,
    NexusError,
    RevisionConflict,
    StorageIntegrityError,
    UnsupportedRuntime,
    UnsupportedSchema,
)
from nexus_memory.domain.models import (
    DIGEST_VERSION,
    HistoryPage,
    MemoryInput,
    MemoryView,
    RevisionEntry,
    Scope,
    SearchHit,
    SearchPage,
    SearchQuery,
    StoreStatus,
    WriteReceipt,
)


_TOKEN = re.compile(r"\w+", re.UNICODE)


class SQLiteRepository:
    SCHEMA_VERSION = 2
    MINIMUM_SQLITE = (3, 51, 3)
    EXCERPT_LIMIT = 240

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

    @staticmethod
    def _script(name: str) -> str:
        return files("nexus_memory.storage.migrations").joinpath(name).read_text()

    def _probe_fts5(self) -> None:
        """Establish FTS5 by executing it. Compile options are diagnostic, not capability."""
        probe = None
        try:
            probe = sqlite3.connect(":memory:")
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
            probe.execute("INSERT INTO probe(body) VALUES('nexus capability probe')")
            matched = probe.execute("SELECT count(*) FROM probe WHERE probe MATCH 'capability'").fetchone()[0]
            if matched != 1:
                raise UnsupportedRuntime("SQLite FTS5 support is required")
        except sqlite3.Error as error:
            raise UnsupportedRuntime("SQLite FTS5 support is required") from error
        finally:
            if probe is not None:
                probe.close()

    def _apply_migration_002(self, connection: sqlite3.Connection) -> None:
        for statement in self._script("002_search.sql").split(";"):
            if statement.strip():
                connection.execute(statement)
        self._backfill_index(connection)

    def _backfill_index(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT m.namespace,m.actor,m.memory_id,r.revision_id,r.blob_id,r.kind,r.tags_json,"
            "r.created_at,r.parent_revision_id,CAST(b.body AS TEXT)"
            " FROM memories m"
            " JOIN revisions r ON r.namespace=m.namespace AND r.actor=m.actor"
            "   AND r.memory_id=m.memory_id AND r.revision_id=m.current_revision_id"
            " JOIN blobs b ON b.id=r.blob_id"
            " WHERE m.tombstoned=0"
        ).fetchall()
        for row in rows:
            namespace, actor, memory_id, revision_id, blob_id, kind, tags_json, created_at, parent, body = row
            seq = connection.execute(
                "INSERT INTO head_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,"
                "created_at,durable_seq,has_parent) VALUES(?,?,?,?,?,?,?,?,0,?)",
                (namespace, actor, memory_id, revision_id, blob_id, kind, tags_json, created_at, 1 if parent else 0),
            ).lastrowid
            self._index_tags(connection, seq, tuple(json.loads(tags_json)))
            connection.execute("INSERT INTO head_fts(rowid,body) VALUES(?,?)", (seq, body))

    def _migrate(self) -> None:
        connection = None
        try:
            self._probe_fts5()
            connection = self._connect()
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if journal_mode.lower() != "wal":
                raise StorageIntegrityError("storage initialization failed")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise UnsupportedSchema("database schema is newer than this application")
            if version == 0:
                for statement in self._script("001_initial.sql").split(";"):
                    if statement.strip():
                        connection.execute(statement)
                version = 1
            if version == 1:
                self._apply_migration_002(connection)
                version = 2
            connection.execute(f"PRAGMA user_version = {version}")
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

    def _index_tags(self, db: sqlite3.Connection, seq: int, tags: tuple[str, ...]) -> None:
        for tag in tags:
            db.execute("INSERT OR IGNORE INTO tags(tag) VALUES(?)", (tag,))
            tag_id = db.execute("SELECT id FROM tags WHERE tag=?", (tag,)).fetchone()[0]
            db.execute("INSERT OR IGNORE INTO head_tags(seq,tag_id) VALUES(?,?)", (seq, tag_id))

    def _index_add(self, db: sqlite3.Connection, scope: Scope, memory_id: str, revision_id: str, blob_id: int,
                   item: MemoryInput, created_at: str, durable_seq: int, has_parent: bool) -> None:
        seq = db.execute(
            "INSERT INTO head_index(namespace,actor,memory_id,revision_id,blob_id,kind,tags_json,"
            "created_at,durable_seq,has_parent) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (scope.namespace, scope.actor, memory_id, revision_id, blob_id, item.kind,
             json.dumps(item.tags), created_at, durable_seq, 1 if has_parent else 0),
        ).lastrowid
        self._index_tags(db, seq, item.tags)
        db.execute("INSERT INTO head_fts(rowid,body) VALUES(?,?)", (seq, item.content))

    def _index_remove(self, db: sqlite3.Connection, scope: Scope, memory_id: str) -> None:
        """External content does not self-synchronise: the prior body must be supplied to delete."""
        row = db.execute(
            "SELECT h.seq,CAST(b.body AS TEXT) FROM head_index h JOIN blobs b ON b.id=h.blob_id"
            " WHERE h.namespace=? AND h.actor=? AND h.memory_id=?",
            (scope.namespace, scope.actor, memory_id),
        ).fetchone()
        if row is None:
            return
        seq, body = row
        db.execute("INSERT INTO head_fts(head_fts,rowid,body) VALUES('delete',?,?)", (seq, body))
        db.execute("DELETE FROM head_tags WHERE seq=?", (seq,))
        db.execute("DELETE FROM head_index WHERE seq=?", (seq,))

    def _bump_generation(self, db: sqlite3.Connection) -> None:
        db.execute("UPDATE index_state SET generation = generation + 1 WHERE id = 1")

    @staticmethod
    def _generation(db: sqlite3.Connection) -> int:
        return db.execute("SELECT generation FROM index_state WHERE id = 1").fetchone()[0]

    def _event_and_receipt(self, db: sqlite3.Connection, scope: Scope, key: str, digest: str, memory_id: str, revision_id: str, operation: str) -> WriteReceipt:
        operation_id = str(uuid.uuid4())
        payload = json.dumps({"memory_id": memory_id, "revision_id": revision_id, "operation_id": operation_id, "operation": operation}, separators=(",", ":"))
        cursor = db.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES(?,?,?,?)", (scope.namespace, scope.actor, operation_id, payload))
        receipt = WriteReceipt(memory_id, revision_id, operation_id, cursor.lastrowid, operation)
        db.execute(
            "INSERT INTO receipts(namespace,actor,idempotency_key,request_digest,memory_id,revision_id,"
            "operation_id,durable_seq,operation,digest_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (scope.namespace, scope.actor, key, digest, memory_id, revision_id, operation_id,
             receipt.durable_seq, operation, DIGEST_VERSION),
        )
        return receipt

    def record(self, scope: Scope, item: MemoryInput, key: str, digest: str) -> WriteReceipt:
        body = item.content.encode("utf-8")
        body_digest = hashlib.sha256(body).digest()
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry
            memory_id, revision_id = self._ids()
            blob_id = self._blob(db, scope, body, body_digest)
            created_at = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO memories VALUES(?,?,?,?,0)", (scope.namespace, scope.actor, memory_id, revision_id))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, memory_id, revision_id, None, blob_id, item.kind, json.dumps(item.tags), item.source_uri, item.snapshot, created_at))
            receipt = self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "record")
            self._index_add(db, scope, memory_id, revision_id, blob_id, item, created_at, receipt.durable_seq, False)
            self._bump_generation(db)
            return receipt

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
            created_at = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, memory_id, revision_id, head, blob_id, item.kind, json.dumps(item.tags), item.source_uri, item.snapshot, created_at))
            db.execute("UPDATE memories SET current_revision_id=? WHERE namespace=? AND actor=? AND memory_id=?", (revision_id, scope.namespace, scope.actor, memory_id))
            receipt = self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "revise")
            self._index_remove(db, scope, memory_id)
            self._index_add(db, scope, memory_id, revision_id, blob_id, item, created_at, receipt.durable_seq, True)
            self._bump_generation(db)
            return receipt

    def forget(self, scope: Scope, memory_id: str, expected_revision_id: str, key: str, digest: str) -> WriteReceipt:
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry
            head, _ = self._head(db, scope, memory_id)
            if head != expected_revision_id:
                raise RevisionConflict("current revision does not match expected revision")
            db.execute("UPDATE memories SET tombstoned=1 WHERE namespace=? AND actor=? AND memory_id=?", (scope.namespace, scope.actor, memory_id))
            receipt = self._event_and_receipt(db, scope, key, digest, memory_id, head, "forget")
            self._index_remove(db, scope, memory_id)
            self._bump_generation(db)
            return receipt

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

    @staticmethod
    def _fingerprint(scope: Scope, query: SearchQuery) -> str:
        payload = {
            "namespace": scope.namespace, "actor": scope.actor, "query": query.query,
            "advanced": query.advanced, "tags_all": list(query.tags_all),
            "tags_any": list(query.tags_any), "kinds": list(query.kinds),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _encode_cursor(payload: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

    @staticmethod
    def _decode_cursor(token: str) -> dict:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(token.encode()))
        except (ValueError, TypeError, binascii.Error) as error:
            raise CursorExpired("cursor is not valid for this search") from error
        if not isinstance(decoded, dict):
            raise CursorExpired("cursor is not valid for this search")
        return decoded

    def _filters(self, query: SearchQuery) -> tuple[str, list]:
        clauses: list[str] = []
        parameters: list = []
        if query.kinds:
            clauses.append(f"AND h.kind IN ({','.join('?' * len(query.kinds))})")
            parameters.extend(query.kinds)
        if query.tags_all:
            placeholders = ",".join("?" * len(query.tags_all))
            clauses.append(
                "AND (SELECT count(DISTINCT t.tag) FROM head_tags ht JOIN tags t ON t.id=ht.tag_id"
                f" WHERE ht.seq=h.seq AND t.tag IN ({placeholders})) = ?"
            )
            parameters.extend(query.tags_all)
            parameters.append(len(query.tags_all))
        if query.tags_any:
            placeholders = ",".join("?" * len(query.tags_any))
            clauses.append(
                "AND EXISTS(SELECT 1 FROM head_tags ht JOIN tags t ON t.id=ht.tag_id"
                f" WHERE ht.seq=h.seq AND t.tag IN ({placeholders}))"
            )
            parameters.extend(query.tags_any)
        return " ".join(clauses), parameters

    @staticmethod
    def _match_expression(query: SearchQuery) -> str | None:
        """Literal mode means FTS operators are inert, not that the text must appear verbatim.

        Quoting the whole query as one phrase made every multi-word natural-language
        query a contiguous-phrase search: "append only ledger" could not match "the
        ledger is append-only". Each token is quoted individually instead and combined
        disjunctively, leaving BM25 to rank; a caller who wants a phrase asks for one
        with advanced mode. Returns None when the text contains no indexable token.
        """
        if query.advanced:
            return query.query or ""
        tokens = _TOKEN.findall(query.query or "")
        if not tokens:
            return None
        return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)

    @staticmethod
    def _validate_match_expression(expression: str) -> None:
        """Establish query validity by executing it in isolation.

        FTS5 reports malformed queries as OperationalError with several different message
        shapes ('fts5: syntax error near', 'unterminated string'), so matching on message
        text is version-fragile. Running the expression against an empty in-memory index
        separates a caller's bad query from a genuine storage failure without guessing.
        """
        probe = None
        try:
            probe = sqlite3.connect(":memory:")
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
            probe.execute("SELECT rowid FROM probe WHERE probe MATCH ?", (expression,)).fetchall()
        except sqlite3.OperationalError as error:
            raise InvalidQuery("search query is not valid") from error
        finally:
            if probe is not None:
                probe.close()

    @staticmethod
    def _reasons(query: SearchQuery) -> tuple[str, ...]:
        reasons: list[str] = []
        if query.query:
            reasons.append("body_terms")
        if query.tags_all:
            reasons.append("tags_all")
        if query.tags_any:
            reasons.append("tags_any")
        if query.kinds:
            reasons.append("kind")
        return tuple(reasons) or ("recent",)

    def search(self, scope: Scope, query: SearchQuery) -> SearchPage:
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")  # one read snapshot for generation, index and authority
                generation = self._generation(db)
                fingerprint = self._fingerprint(scope, query)
                after: tuple[float, int] | None = None
                if query.cursor is not None:
                    payload = self._decode_cursor(query.cursor)
                    if payload.get("fingerprint") != fingerprint or payload.get("generation") != generation:
                        raise CursorExpired("cursor is not valid for this search")
                    after = (payload["rank"], payload["seq"])

                filters, filter_parameters = self._filters(query)
                # The authoritative join is mandatory: external content does not self-synchronise,
                # and every eligibility filter is applied before the limit, never after.
                authority = (
                    " JOIN memories m ON m.namespace=h.namespace AND m.actor=h.actor AND m.memory_id=h.memory_id"
                    " WHERE h.namespace=? AND h.actor=? AND m.tombstoned=0 AND m.current_revision_id=h.revision_id "
                )
                if query.query:
                    expression = self._match_expression(query)
                    if expression is None:  # no indexable token: match nothing, never everything
                        db.commit()
                        return SearchPage(hits=(), cursor=None, generation=generation)
                    self._validate_match_expression(expression)
                    inner = (
                        "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,"
                        "h.has_parent,bm25(head_fts) AS rank,"
                        "snippet(head_fts,0,'','','…',16) AS excerpt"
                        " FROM head_fts JOIN head_index h ON h.seq=head_fts.rowid"
                        + authority + "AND head_fts MATCH ? " + filters
                    )
                    parameters = [scope.namespace, scope.actor, expression, *filter_parameters]
                else:
                    inner = (
                        "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,"
                        "h.has_parent,-h.durable_seq AS rank,"
                        f"substr(CAST(b.body AS TEXT),1,{self.EXCERPT_LIMIT}) AS excerpt"
                        " FROM head_index h JOIN blobs b ON b.id=h.blob_id"
                        + authority + filters
                    )
                    parameters = [scope.namespace, scope.actor, *filter_parameters]

                statement = f"SELECT * FROM ({inner})"
                if after is not None:
                    statement += " WHERE (rank > ?) OR (rank = ? AND seq > ?)"
                    parameters.extend([after[0], after[0], after[1]])
                statement += " ORDER BY rank, seq LIMIT ?"
                parameters.append(query.limit + 1)

                rows = db.execute(statement, parameters).fetchall()

                more = len(rows) > query.limit
                rows = rows[: query.limit]
                reasons = self._reasons(query)
                hits = tuple(
                    SearchHit(
                        memory_id=row[1], revision_id=row[2], kind=row[3],
                        tags=tuple(json.loads(row[4])), created_at=row[5],
                        excerpt=row[8], lexical_rank=row[7] if query.query else None,
                        match_reasons=reasons, has_earlier_revisions=bool(row[6]),
                    )
                    for row in rows
                )
                cursor = None
                if more and rows:
                    cursor = self._encode_cursor({
                        "fingerprint": fingerprint, "generation": generation,
                        "rank": rows[-1][7], "seq": rows[-1][0],
                    })
                db.commit()
                return SearchPage(hits=hits, cursor=cursor, generation=generation)
        except NexusError:
            raise
        except (sqlite3.Error, UnicodeError, ValueError) as error:
            raise StorageIntegrityError("storage operation failed") from error

    def history(self, scope: Scope, memory_id: str, limit: int, cursor: str | None = None) -> HistoryPage:
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                head, _ = self._head(db, scope, memory_id)  # excludes forgotten memories
                fingerprint = hashlib.sha256(
                    json.dumps([scope.namespace, scope.actor, memory_id], separators=(",", ":")).encode()
                ).hexdigest()
                after = None
                if cursor is not None:
                    payload = self._decode_cursor(cursor)
                    if payload.get("fingerprint") != fingerprint:
                        raise CursorExpired("cursor is not valid for this history")
                    after = (payload["created_at"], payload["revision_id"])

                statement = (
                    "SELECT revision_id,parent_revision_id,kind,created_at FROM revisions"
                    " WHERE namespace=? AND actor=? AND memory_id=?"
                )
                parameters: list = [scope.namespace, scope.actor, memory_id]
                if after is not None:
                    statement += " AND ((created_at < ?) OR (created_at = ? AND revision_id < ?))"
                    parameters.extend([after[0], after[0], after[1]])
                statement += " ORDER BY created_at DESC, revision_id DESC LIMIT ?"
                parameters.append(limit + 1)

                rows = db.execute(statement, parameters).fetchall()
                more = len(rows) > limit
                rows = rows[:limit]
                entries = tuple(
                    RevisionEntry(revision_id=row[0], parent_revision_id=row[1], kind=row[2],
                                  created_at=row[3], is_current=row[0] == head)
                    for row in rows
                )
                next_cursor = None
                if more and rows:
                    next_cursor = self._encode_cursor(
                        {"fingerprint": fingerprint, "created_at": rows[-1][3], "revision_id": rows[-1][0]}
                    )
                db.commit()
                return HistoryPage(memory_id=memory_id, entries=entries, cursor=next_cursor)
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
                    "sqlite-wal-full", "available", "available", "unavailable", self.SCHEMA_VERSION,
                    sqlite3.sqlite_version, active, forgotten, revisions, pending, latest,
                )
                db.commit()
                return result
        except sqlite3.Error as error:
            raise StorageIntegrityError("storage operation failed") from error
