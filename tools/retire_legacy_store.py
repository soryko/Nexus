"""Archive a private Nexus v4 database and permanently retire its old reader endpoint."""
import argparse
import hashlib
import os
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4


# All legacy servers refuse to initialize a store with a newer schema. Already-running
# servers open SQLite per operation, so their query tables must also be absent.
RETIRED_VERSION = 2147483647


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _check_integrity(connection):
    if (connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
        raise ValueError("legacy store failed SQLite integrity verification")


def _copy_archive(source, recovery, archive_id):
    # Caller holds an EXCLUSIVE lock in DELETE mode, with all WAL bytes checkpointed,
    # no hot journal and no writes in its transaction. Copy the committed main file.
    archive = recovery / (archive_id + ".sqlite3")
    pending = archive.with_suffix(".partial")
    descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as dest:
            digest = hashlib.sha256()
            while chunk := source.read(1 << 20):
                digest.update(chunk)
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        if _digest(pending) != digest.hexdigest():
            raise ValueError("recovery copy checksum mismatch")
        with closing(sqlite3.connect(pending.as_uri() + "?mode=ro&immutable=1", uri=True)) as verify:
            _check_integrity(verify)
        # Expose only complete snapshots and never overwrite another recovery copy.
        os.link(pending, archive)
        _sync_directory(recovery)
    finally:
        pending.unlink(missing_ok=True)
    return digest.hexdigest()


def _existing_archive(db, recovery):
    archive_id, checksum = db.execute("SELECT archive_id,sha256 FROM nexus_retired").fetchone()
    archive = recovery / (str(UUID(archive_id)) + ".sqlite3")
    info = archive.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or _digest(archive) != checksum):
        raise ValueError("retirement marker does not match the private recovery copy")
    return archive


def retire(database, recovery):
    database = database.absolute()
    recovery = recovery.absolute()
    info = database.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ValueError("expected an owner-controlled, single-linked SQLite database")
    recovery.mkdir(mode=0o700, exist_ok=True)
    owner = recovery.lstat()
    if not stat.S_ISDIR(owner.st_mode) or owner.st_uid != os.getuid() or owner.st_mode & 0o077:
        raise ValueError("recovery directory must be private to the current user")
    _sync_directory(recovery.parent)

    # POSIX fcntl locks are process-wide: closing *any* descriptor for this inode
    # during the transaction would release SQLite's locks and allow a lost write.
    # Open the snapshot descriptor first and close it only after SQLite closes.
    with database.open("rb") as source, closing(sqlite3.connect(
        database.as_uri() + "?mode=rw", uri=True, timeout=3, isolation_level=None
    )) as db:
        if not os.path.samestat(os.fstat(source.fileno()), info):
            raise ValueError("legacy database was replaced before retirement")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == RETIRED_VERSION:
            return _existing_archive(db, recovery)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if version != 4 or not {
            "memories", "revisions", "receipts", "head_fts", "repository_checkouts"
        }.issubset(tables):
            raise ValueError("unrecognized legacy Nexus schema")
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        if db.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise ValueError("could not checkpoint and leave WAL")
        db.execute("BEGIN EXCLUSIVE")
        if db.execute("PRAGMA user_version").fetchone()[0] == RETIRED_VERSION:
            return _existing_archive(db, recovery)
        if (db.execute("PRAGMA journal_mode").fetchone() != ("delete",)
                or not os.path.samestat(database.lstat(), info)
                or any(Path(str(database) + ext).exists() for ext in ("-wal", "-shm", "-journal"))):
            raise ValueError("database identity or journal changed during retirement")
        archive_id = str(uuid4())
        checksum = _copy_archive(source, recovery, archive_id)
        # Fence the original inode transactionally. An old client may already have
        # opened its file descriptor even though it has not acquired a SQLite read lock.
        objects = db.execute("SELECT name,type FROM pragma_table_list WHERE schema='main'").fetchall()
        for kind in ("virtual", "view", "table"):
            for name, object_kind in objects:
                if object_kind == kind and not name.startswith("sqlite_"):
                    quoted = '"' + name.replace('"', '""') + '"'
                    db.execute(f"DROP {'VIEW' if kind == 'view' else 'TABLE'} {quoted}")
        db.execute("CREATE TABLE nexus_retired(archive_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
        db.execute("INSERT INTO nexus_retired VALUES (?,?)", (archive_id, checksum))
        db.execute(f"PRAGMA user_version={RETIRED_VERSION}")
        db.commit()
        return _existing_archive(db, recovery)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--archive-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(retire(args.database, args.archive_dir))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Legacy store retirement failed: {error}\n")
