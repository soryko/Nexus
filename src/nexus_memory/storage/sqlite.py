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
    RepositoryMismatch,
    RevisionConflict,
    StorageIntegrityError,
    UnsupportedRuntime,
    UnsupportedSchema,
)
from nexus_memory.domain.models import (
    REPOSITORY_BOUND,
    DIGEST_VERSION,
    HistoryPage,
    RevisionHit,
    MemoryInput,
    MemoryView,
    RepositoryBinding,
    RevisionEntry,
    Scope,
    SearchHit,
    SearchPage,
    SearchQuery,
    StoreStatus,
    VerifiedReference,
    WriteReceipt,
)


_TOKEN = re.compile(r"\w+", re.UNICODE)

_SEPARATORS = ("_", "/", "\\", ".")
_CAMEL = re.compile(r"[a-z0-9][A-Z]")
_ALNUM_MIX = re.compile(r"(?:[A-Za-z][0-9])|(?:[0-9][A-Za-z])")


def _is_code_shaped(chunk: str) -> bool:
    """Whether a whitespace-delimited chunk looks like an identifier, path or symbol.

    Used only by the ``split`` search profile, to keep a stemmer away from text where
    stemming is wrong. The test is deliberately syntactic and local: a chunk qualifies
    if a separator sits between alphanumerics (``transcode_worker.py``, ``/v3/assets``),
    if it is camelCase, or if letters and digits are adjacent (``mux2``, ``h264``).
    Trailing sentence punctuation is stripped first so that an ordinary word ending a
    sentence is not mistaken for a dotted name.
    """
    chunk = chunk.strip("`'\"(),;:!?“”‘’")
    chunk = chunk.rstrip(".")
    if not chunk or not any(character.isalnum() for character in chunk):
        return False
    for separator in _SEPARATORS:
        position = chunk.find(separator)
        while position != -1:
            before = chunk[position - 1] if position > 0 else ""
            after = chunk[position + 1] if position + 1 < len(chunk) else ""
            if before.isalnum() and after.isalnum():
                return True
            position = chunk.find(separator, position + 1)
    return bool(_CAMEL.search(chunk) or _ALNUM_MIX.search(chunk))


def _prose_text(text: str) -> str:
    """The subset of a body that the ``split`` profile allows a stemmer to see."""
    return " ".join(chunk for chunk in text.split() if not _is_code_shaped(chunk))


class SQLiteRepository:
    SCHEMA_VERSION = 4
    MINIMUM_SQLITE = (3, 51, 3)
    EXCERPT_LIMIT = 240
    SEARCH_PROFILES = ("exact", "stem", "dual", "split")
    HISTORY_PROFILES = ("none", "all", "window3")
    HISTORY_WINDOW = 3          # superseded revisions kept per memory under ``window3``
    REFERENCE_COLUMNS = "repository_id,commit_oid,path,object_oid,entry_type,mode,checked_at"

    def __init__(self, path: str | Path, index_profile: str = "exact",
                 history_profile: str = "none") -> None:
        """``index_profile`` selects how bodies are tokenised for search.

        ``exact`` is the shipped behaviour and the default; the other three exist so a
        stemming change can be measured against it on development data before anything
        is promoted. The profile is a property of the database, not of a call: it is
        recorded in ``search_profile`` and the auxiliary indexes are rebuilt when the
        requested profile differs from the stored one.

        ``history_profile`` selects whether superseded revisions generate candidates at
        all, and how many of them are indexed. ``none`` is the shipped behaviour and the
        default: no historical index exists, nothing maintains one, and it costs nothing.
        ``all`` indexes every superseded revision; ``window3`` keeps only the three most
        recent per memory. Like the search profile it is a property of the database, and
        a change to it drops and rebuilds the structure rather than altering it.
        """
        if sqlite3.sqlite_version_info < self.MINIMUM_SQLITE:
            raise UnsupportedRuntime("SQLite 3.51.3 or newer is required")
        if index_profile not in self.SEARCH_PROFILES:
            raise UnsupportedRuntime(f"unknown search index profile: {index_profile}")
        if history_profile not in self.HISTORY_PROFILES:
            raise UnsupportedRuntime(f"unknown history index profile: {history_profile}")
        self.path = str(path)
        self.index_profile = index_profile
        self.history_profile = history_profile
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

    _STEM_TOKENIZER = "porter unicode61"

    def _stored_profile(self, connection: sqlite3.Connection) -> str:
        return connection.execute("SELECT profile FROM search_profile WHERE id=1").fetchone()[0]

    def _apply_profile(self, connection: sqlite3.Connection) -> None:
        """Bring the auxiliary search structures into line with the requested profile.

        Rebuilding is unconditional when the profile changes, because an FTS5 table's
        tokenizer is fixed at creation: a profile switch is a drop and a rebuild, not an
        ALTER. Doing it here keeps the profile a property of the database, so a caller
        cannot half-open one database under two tokenisers.
        """
        stored = self._stored_profile(connection)
        if stored == self.index_profile:
            return
        connection.execute("DROP TABLE IF EXISTS head_fts_stem")
        connection.execute("DROP TABLE IF EXISTS head_fts_prose")
        if stored == "stem" or self.index_profile == "stem":
            connection.execute("DROP TABLE IF EXISTS head_fts")
            tokenizer = f",tokenize='{self._STEM_TOKENIZER}'" if self.index_profile == "stem" else ""
            connection.execute(
                "CREATE VIRTUAL TABLE head_fts USING fts5(body,content='head_body',content_rowid='rowid'" + tokenizer + ")"
            )
            connection.execute("INSERT INTO head_fts(head_fts) VALUES('rebuild')")
        if self.index_profile == "dual":
            connection.execute(
                "CREATE VIRTUAL TABLE head_fts_stem USING fts5(body,content='head_body',content_rowid='rowid'"
                f",tokenize='{self._STEM_TOKENIZER}')"
            )
            connection.execute("INSERT INTO head_fts_stem(head_fts_stem) VALUES('rebuild')")
        if self.index_profile == "split":
            # Not external content: the prose index holds a filtered copy of the body, so the
            # stemmer never sees an identifier. That copy is a real storage cost, and it is
            # meant to be visible to the storage gate rather than hidden behind a view.
            connection.execute(
                f"CREATE VIRTUAL TABLE head_fts_prose USING fts5(body,tokenize='{self._STEM_TOKENIZER}')"
            )
            for seq, body in connection.execute("SELECT rowid,body FROM head_body").fetchall():
                connection.execute(
                    "INSERT INTO head_fts_prose(rowid,body) VALUES(?,?)", (seq, _prose_text(body))
                )
        connection.execute("UPDATE search_profile SET profile=? WHERE id=1", (self.index_profile,))

    def _history_tokenizer(self) -> str:
        """The historical index is tokenised as the head index for the profile in force.

        Matching the head tokenizer is the point: a query that reaches a current head
        through the porter stemmer must reach a superseded body the same way, or the two
        channels would disagree about what a term is and the comparison between them
        would be a comparison of tokenisers.
        """
        return "" if self.index_profile == "exact" else f",tokenize='{self._STEM_TOKENIZER}'"

    def _stored_history_profile(self, connection: sqlite3.Connection) -> str:
        """The history profile this database was last opened under.

        The table is created only when a history profile is actually asked for. A
        database that never leaves the shipped default carries no trace of this feature —
        not the index, not the state row, not the page that would hold it.
        """
        exists = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='history_profile'"
        ).fetchone()[0]
        if not exists:
            if self.history_profile == "none":
                return "none"
            connection.execute(
                "CREATE TABLE history_profile("
                "id INTEGER PRIMARY KEY CHECK(id = 1), profile TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO history_profile(id,profile) VALUES(1,'none')")
            return "none"
        return connection.execute("SELECT profile FROM history_profile WHERE id=1").fetchone()[0]

    def _apply_history_profile(self, connection: sqlite3.Connection) -> None:
        """Bring the historical index into line with the requested profile.

        Unconditional drop and rebuild on any change, for the same reason the search
        profile rebuilds: an FTS5 tokenizer is fixed at creation, and a window profile
        holds a different row set than a full one. A database opened under ``none``
        carries no historical structure at all, so the shipped default pays nothing.
        """
        stored = self._stored_history_profile(connection)
        if stored == self.history_profile and stored == "none":
            return
        if stored == self.history_profile and self._history_tokenizer_matches(connection):
            return
        connection.execute("DROP TABLE IF EXISTS revision_fts")
        connection.execute("DROP VIEW IF EXISTS revision_body")
        connection.execute("DROP TABLE IF EXISTS revision_index")
        if self.history_profile != "none":
            connection.execute(
                "CREATE TABLE revision_index ("
                "seq INTEGER PRIMARY KEY, namespace TEXT NOT NULL, actor TEXT NOT NULL,"
                "memory_id TEXT NOT NULL, revision_id TEXT NOT NULL,"
                "blob_id INTEGER NOT NULL REFERENCES blobs(id), kind TEXT NOT NULL,"
                "tags_json TEXT NOT NULL, created_at TEXT NOT NULL,"
                "UNIQUE(namespace, actor, memory_id, revision_id))"
            )
            connection.execute(
                "CREATE INDEX revision_index_memory ON revision_index(namespace, actor, memory_id, seq)"
            )
            connection.execute(
                "CREATE VIEW revision_body AS SELECT x.seq AS rowid, CAST(b.body AS TEXT) AS body"
                " FROM revision_index x JOIN blobs b ON b.id = x.blob_id"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE revision_fts USING fts5(body,content='revision_body',"
                "content_rowid='rowid'" + self._history_tokenizer() + ")"
            )
            self._backfill_history(connection)
        connection.execute("UPDATE history_profile SET profile=? WHERE id=1", (self.history_profile,))

    def _history_tokenizer_matches(self, connection: sqlite3.Connection) -> bool:
        """Whether the existing historical index was built under the current tokenizer.

        The search profile can change under a database whose history profile did not, and
        that changes what the historical index should hold. Rebuilding on that is not
        optional: an index built with one tokenizer answers a different question.
        """
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='revision_fts'"
        ).fetchone()
        if row is None:
            return self.history_profile == "none"
        wanted = self._history_tokenizer().replace(",tokenize=", "").strip("'")
        return (wanted in row[0]) if wanted else ("tokenize" not in row[0])

    def _backfill_history(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT r.namespace,r.actor,r.memory_id,r.revision_id,r.blob_id,r.kind,r.tags_json,"
            "r.created_at,CAST(b.body AS TEXT)"
            " FROM revisions r"
            " JOIN memories m ON m.namespace=r.namespace AND m.actor=r.actor AND m.memory_id=r.memory_id"
            " JOIN blobs b ON b.id=r.blob_id"
            " WHERE m.tombstoned=0 AND m.current_revision_id<>r.revision_id"
            " ORDER BY r.rowid"
        ).fetchall()
        for row in rows:
            self._history_insert(connection, *row)
        if self.history_profile == "window3":
            for (namespace, actor, memory_id) in connection.execute(
                "SELECT DISTINCT namespace,actor,memory_id FROM revision_index"
            ).fetchall():
                self._history_prune(connection, namespace, actor, memory_id)

    def _history_insert(self, db: sqlite3.Connection, namespace: str, actor: str, memory_id: str,
                        revision_id: str, blob_id: int, kind: str, tags_json: str,
                        created_at: str, body: str) -> None:
        cursor = db.execute(
            "INSERT OR IGNORE INTO revision_index(namespace,actor,memory_id,revision_id,blob_id,"
            "kind,tags_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (namespace, actor, memory_id, revision_id, blob_id, kind, tags_json, created_at),
        )
        if cursor.rowcount:  # an ignored duplicate must not write a second FTS row
            db.execute("INSERT INTO revision_fts(rowid,body) VALUES(?,?)", (cursor.lastrowid, body))

    def _history_prune(self, db: sqlite3.Connection, namespace: str, actor: str, memory_id: str) -> None:
        """Keep only the most recent window of superseded revisions for one memory.

        Insertion order is chronological, so ``seq`` orders history without consulting a
        timestamp that a caller could have supplied.
        """
        stale = db.execute(
            "SELECT x.seq,CAST(b.body AS TEXT) FROM revision_index x JOIN blobs b ON b.id=x.blob_id"
            " WHERE x.namespace=? AND x.actor=? AND x.memory_id=? AND x.seq NOT IN ("
            "SELECT seq FROM revision_index WHERE namespace=? AND actor=? AND memory_id=?"
            " ORDER BY seq DESC LIMIT ?)",
            (namespace, actor, memory_id, namespace, actor, memory_id, self.HISTORY_WINDOW),
        ).fetchall()
        for seq, body in stale:
            db.execute("INSERT INTO revision_fts(revision_fts,rowid,body) VALUES('delete',?,?)", (seq, body))
            db.execute("DELETE FROM revision_index WHERE seq=?", (seq,))

    def _history_add(self, db: sqlite3.Connection, scope: Scope, memory_id: str, revision_id: str) -> None:
        """Record the revision that has just stopped being the head."""
        if self.history_profile == "none":
            return
        row = db.execute(
            "SELECT r.blob_id,r.kind,r.tags_json,r.created_at,CAST(b.body AS TEXT) FROM revisions r"
            " JOIN blobs b ON b.id=r.blob_id"
            " WHERE r.namespace=? AND r.actor=? AND r.memory_id=? AND r.revision_id=?",
            (scope.namespace, scope.actor, memory_id, revision_id),
        ).fetchone()
        if row is None:
            return
        self._history_insert(db, scope.namespace, scope.actor, memory_id, revision_id, *row)
        if self.history_profile == "window3":
            self._history_prune(db, scope.namespace, scope.actor, memory_id)

    def _history_remove_memory(self, db: sqlite3.Connection, scope: Scope, memory_id: str) -> None:
        """A forgotten memory leaves no historical rows behind.

        The eligibility join in ``search_history`` would filter them anyway. Deleting
        them as well is the same belt-and-braces the head index already uses: two
        independent reasons why forgotten content cannot surface, not one.
        """
        if self.history_profile == "none":
            return
        rows = db.execute(
            "SELECT x.seq,CAST(b.body AS TEXT) FROM revision_index x JOIN blobs b ON b.id=x.blob_id"
            " WHERE x.namespace=? AND x.actor=? AND x.memory_id=?",
            (scope.namespace, scope.actor, memory_id),
        ).fetchall()
        for seq, body in rows:
            db.execute("INSERT INTO revision_fts(revision_fts,rowid,body) VALUES('delete',?,?)", (seq, body))
            db.execute("DELETE FROM revision_index WHERE seq=?", (seq,))

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
            if version == 2:
                for statement in self._script("003_search_profile.sql").split(";"):
                    if statement.strip():
                        connection.execute(statement)
                version = 3
            if version == 3:
                # Additive: three tables, no backfill, no row rewritten. A failure rolls the
                # database back to version 3 with the transaction.
                for statement in self._script("004_repositories.sql").split(";"):
                    if statement.strip():
                        connection.execute(statement)
                version = 4
            connection.execute(f"PRAGMA user_version = {version}")
            self._apply_profile(connection)
            self._apply_history_profile(connection)
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
        if self.index_profile == "dual":
            db.execute("INSERT INTO head_fts_stem(rowid,body) VALUES(?,?)", (seq, item.content))
        elif self.index_profile == "split":
            db.execute("INSERT INTO head_fts_prose(rowid,body) VALUES(?,?)", (seq, _prose_text(item.content)))

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
        if self.index_profile == "dual":
            db.execute("INSERT INTO head_fts_stem(head_fts_stem,rowid,body) VALUES('delete',?,?)", (seq, body))
        elif self.index_profile == "split":
            db.execute("DELETE FROM head_fts_prose WHERE rowid=?", (seq,))
        db.execute("DELETE FROM head_tags WHERE seq=?", (seq,))
        db.execute("DELETE FROM head_index WHERE seq=?", (seq,))

    def _bump_generation(self, db: sqlite3.Connection) -> None:
        db.execute("UPDATE index_state SET generation = generation + 1 WHERE id = 1")

    @staticmethod
    def _generation(db: sqlite3.Connection) -> int:
        return db.execute("SELECT generation FROM index_state WHERE id = 1").fetchone()[0]

    def receipt(self, scope: Scope, key: str, digest: str) -> WriteReceipt | None:
        """The pre-check a reference-carrying write runs before verification.

        It decides nothing: it can only short-circuit to a receipt that is already
        committed, and the authoritative check inside the write transaction is unchanged.
        A key reused for a different payload conflicts here exactly as it would inside
        the transaction, so a retry that would have conflicted also never invokes git.
        """
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                found = self._retry(db, scope, key, digest)
                db.commit()
                return found
        except NexusError:
            raise
        except sqlite3.Error as error:
            raise StorageIntegrityError("storage operation failed") from error

    def _store_references(self, db: sqlite3.Connection, scope: Scope, memory_id: str, revision_id: str,
                          references: tuple[VerifiedReference, ...]) -> None:
        for reference in references:
            db.execute(
                "INSERT INTO revision_references(namespace,actor,memory_id,revision_id,repository_id,"
                "commit_oid,path,object_oid,entry_type,mode,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (scope.namespace, scope.actor, memory_id, revision_id, reference.repository_id,
                 reference.commit_oid, reference.path, reference.object_oid, reference.entry_type,
                 reference.mode, reference.checked_at),
            )

    def _references(self, db: sqlite3.Connection, scope: Scope, memory_id: str,
                    revision_ids: tuple[str, ...]) -> dict[str, tuple[VerifiedReference, ...]]:
        if not revision_ids:
            return {}
        placeholders = ",".join("?" * len(revision_ids))
        rows = db.execute(
            "SELECT revision_id,repository_id,commit_oid,path,object_oid,entry_type,mode,checked_at"
            " FROM revision_references WHERE namespace=? AND actor=? AND memory_id=?"
            f" AND revision_id IN ({placeholders}) ORDER BY revision_id,commit_oid,path",
            (scope.namespace, scope.actor, memory_id, *revision_ids),
        ).fetchall()
        grouped: dict[str, list[VerifiedReference]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(VerifiedReference(*row[1:]))
        return {revision_id: tuple(items) for revision_id, items in grouped.items()}

    def _hit_references(self, db: sqlite3.Connection, scope: Scope,
                        pairs: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[VerifiedReference, ...]]:
        """Load the reference evidence for one page of hits.

        The work this does is bounded by the page, not by the scope. The form shipped
        through ``cdf51e7`` was not: it is retained outside production as
        ``tests/core/evidence_control.py``, which substitutes itself for
        ``_evidence_sql`` to serve as the control arm of the evidence-loading
        experiment and as a differential-testing oracle. There is one production form.
        """
        if not pairs:
            return {}
        # Distinct pairs only. A page carries one row per memory so this changes nothing
        # today, but the shipped form joins against these rows and would multiply a
        # duplicate where the retained control's OR-list would absorb it. The two must not
        # be able to disagree for a reason as incidental as that.
        unique = list(dict.fromkeys(pairs))
        statement, parameters = self._evidence_sql(scope, unique)
        rows = db.execute(statement, parameters).fetchall()
        grouped: dict[tuple[str, str], list[VerifiedReference]] = {}
        for row in rows:
            grouped.setdefault((row[0], row[1]), []).append(VerifiedReference(*row[2:]))
        return {pair: tuple(items) for pair, items in grouped.items()}

    def _evidence_sql(self, scope: Scope,
                      pairs: list[tuple[str, str]]) -> tuple[str, list]:
        """The page's pairs drive the lookup, one seek each on the primary key's autoindex.

        ``CROSS JOIN`` is a plan constraint, not a semantic one: in SQLite it is an inner
        join that the planner may not reorder. It is what makes the cost bounded. Written
        as a plain join, the planner is free to lead with ``revision_references`` and scan
        the whole ``(namespace, actor)`` partition instead -- which is what it does, and it
        was measured at roughly 4,800x the work of this form on a 20,000-memory scope.

        This is the seam the retained control substitutes itself at. It is a method for
        that reason and for no other; production selects nothing here.
        """
        values = ",".join("(?,?)" for _ in pairs)
        parameters: list = []
        for memory_id, revision_id in pairs:
            parameters.extend([memory_id, revision_id])
        parameters.extend([scope.namespace, scope.actor])
        columns = ",".join("r." + column for column in
                           ("memory_id,revision_id," + self.REFERENCE_COLUMNS).split(","))
        return (
            f"WITH pairs(memory_id,revision_id) AS (VALUES {values})"
            f" SELECT {columns} FROM pairs p CROSS JOIN revision_references r"
            " ON r.namespace=? AND r.actor=? AND r.memory_id=p.memory_id"
            " AND r.revision_id=p.revision_id"
            " ORDER BY r.memory_id,r.revision_id,r.commit_oid,r.path",
            parameters,
        )

    def bind_checkout(self, scope: Scope, token: str, locator: str | None, object_format: str,
                      repository_id: str | None = None) -> RepositoryBinding:
        """Bind a published checkout token to a repository identity, in one immediate transaction.

        ``BEGIN IMMEDIATE`` serialises registrations, so of two processes that published
        the same token the second one reads the row the first one wrote and returns the
        same mapping rather than minting a second identity. A token with no row is adopted
        under a new identity here, or under ``repository_id`` when the operator asks for a
        deliberate association; a token that already maps to a different identity than the
        one asked for is a mismatch.
        """
        if object_format not in ("sha1", "sha256"):
            raise RepositoryMismatch("unsupported repository object format")
        with self._transaction() as db:
            now = datetime.now(UTC).isoformat()
            row = db.execute(
                "SELECT c.repository_id,r.object_format,r.locator FROM repository_checkouts c"
                " JOIN repositories r ON r.namespace=c.namespace AND r.actor=c.actor AND r.repository_id=c.repository_id"
                " WHERE c.namespace=? AND c.actor=? AND c.token=?",
                (scope.namespace, scope.actor, token),
            ).fetchone()
            if row is not None:
                bound_id, stored_format, stored_locator = row
                if repository_id is not None and repository_id != bound_id:
                    raise RepositoryMismatch("checkout is registered to a different repository")
                if stored_format != object_format:
                    raise RepositoryMismatch("repository object format differs from the registered one")
                if locator is not None and locator != stored_locator:
                    db.execute(
                        "UPDATE repositories SET locator=? WHERE namespace=? AND actor=? AND repository_id=?",
                        (locator, scope.namespace, scope.actor, bound_id),
                    )
                return RepositoryBinding(bound_id, object_format, locator if locator is not None else stored_locator)
            if repository_id is not None:
                existing = db.execute(
                    "SELECT object_format FROM repositories WHERE namespace=? AND actor=? AND repository_id=?",
                    (scope.namespace, scope.actor, repository_id),
                ).fetchone()
                if existing is None:
                    raise RepositoryMismatch("no such repository is registered in this scope")
                if existing[0] != object_format:
                    raise RepositoryMismatch("repository object format differs from the registered one")
                bound_id = repository_id
                if locator is not None:
                    db.execute(
                        "UPDATE repositories SET locator=? WHERE namespace=? AND actor=? AND repository_id=?",
                        (locator, scope.namespace, scope.actor, bound_id),
                    )
            else:
                bound_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO repositories(namespace,actor,repository_id,object_format,locator,registered_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (scope.namespace, scope.actor, bound_id, object_format, locator, now),
                )
            db.execute(
                "INSERT INTO repository_checkouts(namespace,actor,token,repository_id,registered_at) VALUES(?,?,?,?,?)",
                (scope.namespace, scope.actor, token, bound_id, now),
            )
            return RepositoryBinding(bound_id, object_format, locator)

    def checkout_binding(self, scope: Scope, token: str) -> RepositoryBinding | None:
        """Read an existing binding without registering anything. Used when git is absent."""
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                row = db.execute(
                    "SELECT c.repository_id,r.object_format,r.locator FROM repository_checkouts c"
                    " JOIN repositories r ON r.namespace=c.namespace AND r.actor=c.actor AND r.repository_id=c.repository_id"
                    " WHERE c.namespace=? AND c.actor=? AND c.token=?",
                    (scope.namespace, scope.actor, token),
                ).fetchone()
                db.commit()
                return None if row is None else RepositoryBinding(row[0], row[1], row[2])
        except sqlite3.Error as error:
            raise StorageIntegrityError("storage operation failed") from error

    def _event_and_receipt(self, db: sqlite3.Connection, scope: Scope, key: str, digest: str, memory_id: str, revision_id: str, operation: str, digest_version: int = DIGEST_VERSION) -> WriteReceipt:
        operation_id = str(uuid.uuid4())
        payload = json.dumps({"memory_id": memory_id, "revision_id": revision_id, "operation_id": operation_id, "operation": operation}, separators=(",", ":"))
        cursor = db.execute("INSERT INTO outbox(namespace,actor,operation_id,payload) VALUES(?,?,?,?)", (scope.namespace, scope.actor, operation_id, payload))
        receipt = WriteReceipt(memory_id, revision_id, operation_id, cursor.lastrowid, operation)
        db.execute(
            "INSERT INTO receipts(namespace,actor,idempotency_key,request_digest,memory_id,revision_id,"
            "operation_id,durable_seq,operation,digest_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (scope.namespace, scope.actor, key, digest, memory_id, revision_id, operation_id,
             receipt.durable_seq, operation, digest_version),
        )
        return receipt

    def record(self, scope: Scope, item: MemoryInput, key: str, digest: str,
               references: tuple[VerifiedReference, ...] = (), digest_version: int = DIGEST_VERSION) -> WriteReceipt:
        body = item.content.encode("utf-8")
        body_digest = hashlib.sha256(body).digest()
        with self._transaction() as db:
            if retry := self._retry(db, scope, key, digest):
                return retry  # the loser's verification result is discarded, never merged
            memory_id, revision_id = self._ids()
            blob_id = self._blob(db, scope, body, body_digest)
            created_at = datetime.now(UTC).isoformat()
            db.execute("INSERT INTO memories VALUES(?,?,?,?,0)", (scope.namespace, scope.actor, memory_id, revision_id))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scope.namespace, scope.actor, memory_id, revision_id, None, blob_id, item.kind, json.dumps(item.tags), item.source_uri, item.snapshot, created_at))
            self._store_references(db, scope, memory_id, revision_id, references)
            receipt = self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "record", digest_version)
            self._index_add(db, scope, memory_id, revision_id, blob_id, item, created_at, receipt.durable_seq, False)
            self._bump_generation(db)
            return receipt

    def _head(self, db: sqlite3.Connection, scope: Scope, memory_id: str) -> tuple[str, int]:
        row = db.execute("SELECT current_revision_id,tombstoned FROM memories WHERE namespace=? AND actor=? AND memory_id=?", (scope.namespace, scope.actor, memory_id)).fetchone()
        if row is None or row[1]:
            raise MemoryNotFound("memory not found")
        return row

    def revise(self, scope: Scope, memory_id: str, expected_revision_id: str, item: MemoryInput, key: str, digest: str,
               references: tuple[VerifiedReference, ...] = (), digest_version: int = DIGEST_VERSION) -> WriteReceipt:
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
            self._store_references(db, scope, memory_id, revision_id, references)
            receipt = self._event_and_receipt(db, scope, key, digest, memory_id, revision_id, "revise", digest_version)
            self._index_remove(db, scope, memory_id)
            self._index_add(db, scope, memory_id, revision_id, blob_id, item, created_at, receipt.durable_seq, True)
            self._history_add(db, scope, memory_id, head)   # the outgoing head
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
            self._history_remove_memory(db, scope, memory_id)
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
                references = self._references(db, scope, memory_id, (selected,)).get(selected, ())
                result = MemoryView(memory_id, row[0], row[1], bytes(row[2]).decode("utf-8"), row[3], tuple(json.loads(row[4])), row[5], row[6], row[7], head, references)
                db.commit()
                return result
        except NexusError:
            raise
        except (sqlite3.Error, UnicodeError, ValueError) as error:
            raise StorageIntegrityError("storage operation failed") from error

    @staticmethod
    def _fingerprint(scope: Scope, query: SearchQuery, repository_id: str | None = None) -> str:
        """Every eligibility argument enters the fingerprint, or page two answers a different question.

        ``repository`` enters **resolved**, not literal: two processes in one scope bound to
        different repositories both send ``"bound"``, and a literal fingerprint would let each
        accept the other's cursor and page across a different repository's evidence with a
        well-formed ``(rank, seq)``. Under ``"any"`` — and under absence, which is identical to
        it — the result set does not depend on the binding, so neither does this payload, and
        the two continue to fingerprint alike. The identity is only ever hashed, never carried.
        """
        payload = {
            "namespace": scope.namespace, "actor": scope.actor, "query": query.query,
            "advanced": query.advanced, "tags_all": list(query.tags_all),
            "tags_any": list(query.tags_any), "kinds": list(query.kinds),
            "repository": ["bound", repository_id] if query.repository == REPOSITORY_BOUND else None,
            "reference_paths": list(query.reference_paths or ()),
            "reference_path_prefix": query.reference_path_prefix,
            "reference_commits": list(query.reference_commits or ()),
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

    # The correlation that ties an evidence row to the head revision already selected.
    # ``revision_id`` is part of it, not decoration: without it the predicate would match a
    # reference any revision of the memory ever carried, and B2b §4 is head revisions only.
    REFERENCE_CORRELATION = (
        "r.namespace=h.namespace AND r.actor=h.actor"
        " AND r.memory_id=h.memory_id AND r.revision_id=h.revision_id"
    )

    @staticmethod
    def _path_prefix_clause(prefix: str) -> tuple[str, list]:
        """A segment-aligned prefix as a byte range, never as a pattern.

        ``LIKE`` would fold ASCII case and read ``%`` and ``_`` as wildcards; ``GLOB`` would
        read ``*``, ``?`` and ``[...]``. A BINARY comparison against ``[prefix + "/", prefix +
        "0")`` is segment-aligned by construction — ``/`` is 0x2F and ``0`` is 0x30, so exactly
        the paths whose next byte is a separator fall inside it — and it is sargable, which a
        leading-wildcard pattern is not. The exact path is the separate equality.
        """
        return "r.path=? OR (r.path>=? AND r.path<?)", [prefix, prefix + "/", prefix + "0"]

    @staticmethod
    def _reference_conditions(query: SearchQuery, repository_id: str | None) -> tuple[list[str], list]:
        """The conditions one reference row must satisfy, in the order their parameters bind."""
        conditions: list[str] = []
        parameters: list = []
        if query.repository == REPOSITORY_BOUND:
            conditions.append("AND r.repository_id=?")
            parameters.append(repository_id)
        paths = query.reference_paths or ()
        prefix = query.reference_path_prefix
        if paths or prefix is not None:
            # Both ask the same question of the same column, so they are alternatives to each
            # other while every other argument conjoins. B2b §4 calls this out as the one
            # deliberate exception, because it is a surprise otherwise.
            alternatives: list[str] = []
            if paths:
                alternatives.append(f"r.path IN ({','.join('?' * len(paths))})")
                parameters.extend(paths)
            if prefix is not None:
                clause, prefix_parameters = SQLiteRepository._path_prefix_clause(prefix)
                alternatives.append(clause)
                parameters.extend(prefix_parameters)
            conditions.append("AND (" + " OR ".join(alternatives) + ")")
        commits = query.reference_commits or ()
        if commits:
            conditions.append(f"AND r.commit_oid IN ({','.join('?' * len(commits))})")
            parameters.extend(commits)
        return conditions, parameters

    @staticmethod
    def _reference_filter(query: SearchQuery, repository_id: str | None) -> tuple[str, list]:
        """One EXISTS over ``revision_references``, correlated to the selected head revision.

        Existence, not a join: a memory whose three references all match appears once, and the
        ``(rank, seq)`` keyset stays a keyset. Every condition sits inside a **single**
        subquery over a single row, which is what makes "a.py at commit X" mean what it reads
        like rather than "a.py somewhere and X somewhere".
        """
        conditions, parameters = SQLiteRepository._reference_conditions(query, repository_id)
        if not conditions:
            return "", []
        clause = (
            "AND EXISTS(SELECT 1 FROM revision_references r WHERE "
            + SQLiteRepository.REFERENCE_CORRELATION + " " + " ".join(conditions) + ")"
        )
        return clause, parameters

    def _filters(self, query: SearchQuery, repository_id: str | None = None) -> tuple[str, list]:
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
        reference_clause, reference_parameters = self._reference_filter(query, repository_id)
        if reference_clause:
            clauses.append(reference_clause)
            parameters.extend(reference_parameters)
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

    def _match_legs(self, query: SearchQuery, expression: str) -> list[tuple[str, str]]:
        """Which index or indexes this profile matches against, and with what expression.

        ``exact`` and ``stem`` are one leg over ``head_fts``; the profile difference lives
        entirely in that table's tokenizer. ``dual`` adds a stemmed leg over the same
        bodies. ``split`` adds a stemmed leg that only ever sees prose, and sends it only
        the prose-shaped query tokens, so an identifier never reaches the stemmer from
        either side. Advanced queries take the exact leg alone: an FTS5 expression cannot
        be token-filtered without reinterpreting the caller's syntax.
        """
        if self.index_profile in ("exact", "stem"):
            return [("head_fts", expression)]
        if self.index_profile == "dual":
            return [("head_fts", expression), ("head_fts_stem", expression)]
        legs = [("head_fts", expression)]
        if query.advanced:
            return legs
        prose = [chunk for chunk in (query.query or "").split() if not _is_code_shaped(chunk)]
        tokens = _TOKEN.findall(" ".join(prose))
        if tokens:
            legs.append(("head_fts_prose", " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)))
        return legs

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
        if SQLiteRepository._restricts_references(query):
            reasons.append("references")
        return tuple(reasons) or ("recent",)

    @staticmethod
    def _restricts_references(query: SearchQuery) -> bool:
        """Only a *restricting* argument earns a reason, matching _reasons' truthiness test.

        An empty list and an absent argument are the same query, so they must produce the same
        match_reasons as well as the same rows and the same cursor.
        """
        return bool(
            query.repository == REPOSITORY_BOUND
            or query.reference_paths
            or query.reference_path_prefix is not None
            or query.reference_commits
        )

    def _browse_statement(self, inner: str, after: tuple[float, int] | None) -> tuple[str, list]:
        """The recency page, ordered on the indexed column rather than on the negated alias.

        ``head_index_recent`` is ``(namespace, actor, durable_seq DESC)``. The form this
        replaced wrapped the select and ordered by ``rank``, which is ``-h.durable_seq``:
        an expression the index cannot answer, so SQLite sorted every eligible row in the
        scope to return one page of twenty. Ordering on ``h.durable_seq DESC`` directly
        lets the walk stop at the limit. ``h.seq`` stays as the tiebreak and must stay
        **ASC** — within one ``durable_seq`` the index carries rowid ascending, so ``DESC``
        reintroduces the sort. Measured in B3 §1.

        The cursor predicate is two halves doing different jobs. ``durable_seq <= ?`` is the
        half the index can use: it becomes a range constraint and the seek starts at the
        cursor rather than at the top of the scope. The disjunction resolves the tie that
        bound admits, on rows already arrived at. Written as the disjunction alone — the
        direct translation of the wrapper's predicate, and the obvious way to write it — it
        is not a range constraint at all and every deep page pays for the whole prefix
        ahead of it, which page two is too shallow to show. B3 §2.

        ``tests/core/ordering_control.py`` retains the wrapper form and
        ``test_candidate_ordering.py`` differentially tests this against it.
        """
        if after is None:
            return inner + " ORDER BY h.durable_seq DESC, h.seq LIMIT ?", []
        return (
            inner + " AND h.durable_seq <= ? AND (h.durable_seq < ? OR h.seq > ?)"
                    " ORDER BY h.durable_seq DESC, h.seq LIMIT ?",
            [-after[0], -after[0], after[1]],
        )

    def _ranked_statement(self, inner: str, after: tuple[float, int] | None) -> tuple[str, list]:
        """The lexical page, ordered by ``bm25`` through the wrapper.

        ``rank`` here is a score computed per row, not a column any index carries, and the
        multi-leg form is already a ``GROUP BY`` over a union — so the sort is not
        avoidable by ordering differently, and B3 does not touch this path.
        """
        statement = f"SELECT * FROM ({inner})"
        if after is None:
            return statement + " ORDER BY rank, seq LIMIT ?", []
        return (
            statement + " WHERE (rank > ?) OR (rank = ? AND seq > ?) ORDER BY rank, seq LIMIT ?",
            [after[0], after[0], after[1]],
        )

    def search(self, scope: Scope, query: SearchQuery, repository_id: str | None = None) -> SearchPage:
        """``repository_id`` is the identity the *service* resolved for ``repository: "bound"``.

        Storage never reads a binding and never learns one from a tool call: it is handed the
        already-resolved identity, or None, and an unbound process never reaches here because
        the service raises repository_unbound ahead of any cursor check.
        """
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")  # one read snapshot for generation, index and authority
                generation = self._generation(db)
                fingerprint = self._fingerprint(scope, query, repository_id)
                after: tuple[float, int] | None = None
                browse = False  # the recency branch orders on head_index_recent directly
                if query.cursor is not None:
                    payload = self._decode_cursor(query.cursor)
                    if payload.get("fingerprint") != fingerprint or payload.get("generation") != generation:
                        raise CursorExpired("cursor is not valid for this search")
                    after = (payload["rank"], payload["seq"])

                filters, filter_parameters = self._filters(query, repository_id)
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
                    legs = self._match_legs(query, expression)
                    leg_sql = []
                    parameters = []
                    for table, leg_expression in legs:
                        leg_sql.append(
                            "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,"
                            f"h.has_parent,bm25({table}) AS rank,"
                            f"snippet({table},0,'','','…',16) AS excerpt"
                            f" FROM {table} JOIN head_index h ON h.seq={table}.rowid"
                            + authority + f"AND {table} MATCH ? " + filters
                        )
                        parameters.extend([scope.namespace, scope.actor, leg_expression, *filter_parameters])
                    if len(leg_sql) == 1:
                        inner = leg_sql[0]
                    else:
                        # One row per memory, keeping its best leg. BM25 scores from two indexes
                        # are computed over different corpus statistics, so best-of is a stated
                        # heuristic, not a principled combination — it is recorded as such.
                        inner = (
                            "SELECT seq,memory_id,revision_id,kind,tags_json,created_at,has_parent,"
                            "MIN(rank) AS rank,excerpt FROM (" + " UNION ALL ".join(leg_sql) + ") GROUP BY seq"
                        )
                else:
                    browse = True
                    inner = (
                        "SELECT h.seq AS seq,h.memory_id,h.revision_id,h.kind,h.tags_json,h.created_at,"
                        "h.has_parent,-h.durable_seq AS rank,"
                        f"substr(CAST(b.body AS TEXT),1,{self.EXCERPT_LIMIT}) AS excerpt"
                        " FROM head_index h JOIN blobs b ON b.id=h.blob_id"
                        + authority + filters
                    )
                    parameters = [scope.namespace, scope.actor, *filter_parameters]

                order = self._browse_statement if browse else self._ranked_statement
                statement, cursor_parameters = order(inner, after)
                parameters.extend(cursor_parameters)
                parameters.append(query.limit + 1)

                rows = db.execute(statement, parameters).fetchall()

                more = len(rows) > query.limit
                rows = rows[: query.limit]
                reasons = self._reasons(query)
                evidence = self._hit_references(db, scope, [(row[1], row[2]) for row in rows])
                hits = tuple(
                    SearchHit(
                        memory_id=row[1], revision_id=row[2], kind=row[3],
                        tags=tuple(json.loads(row[4])), created_at=row[5],
                        excerpt=row[8], lexical_rank=row[7] if query.query else None,
                        match_reasons=reasons, has_earlier_revisions=bool(row[6]),
                        references=evidence.get((row[1], row[2]), ()),
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

    def search_history(self, scope: Scope, query: SearchQuery) -> tuple[RevisionHit, ...]:
        """Candidate generation over non-head revisions — the T2 second channel.

        Eligibility is enforced inside the statement, never after it: the authoritative
        join to ``memories`` drops tombstoned rows, and ``current_revision_id<>x.revision_id``
        drops anything that is now the head. A row that a caller would have filtered out
        therefore never occupies one of that caller's hit slots, which is what makes a
        limit on this channel mean what it says.

        Returns nothing under the ``none`` history profile, where no such index exists.
        """
        if self.history_profile == "none":
            return ()
        db = None
        try:
            db = self._connect()
            with closing(db):
                db.execute("BEGIN")
                expression = self._match_expression(query)
                if expression is None:  # no indexable token: match nothing, never everything
                    db.commit()
                    return ()
                self._validate_match_expression(expression)
                rows = db.execute(
                    "SELECT x.memory_id,x.revision_id,m.current_revision_id,x.kind,x.tags_json,"
                    "x.created_at,bm25(revision_fts) AS rank,"
                    f"snippet(revision_fts,0,'','','…',16) AS excerpt"
                    " FROM revision_fts JOIN revision_index x ON x.seq=revision_fts.rowid"
                    " JOIN memories m ON m.namespace=x.namespace AND m.actor=x.actor"
                    "   AND m.memory_id=x.memory_id"
                    " WHERE x.namespace=? AND x.actor=? AND m.tombstoned=0"
                    "   AND m.current_revision_id<>x.revision_id AND revision_fts MATCH ?"
                    " ORDER BY rank, x.seq LIMIT ?",
                    (scope.namespace, scope.actor, expression, query.limit),
                ).fetchall()
                hits = tuple(
                    RevisionHit(
                        memory_id=row[0], revision_id=row[1], current_revision_id=row[2],
                        kind=row[3], tags=tuple(json.loads(row[4])), created_at=row[5],
                        excerpt=row[7], lexical_rank=row[6],
                    )
                    for row in rows
                )
                db.commit()
                return hits
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
