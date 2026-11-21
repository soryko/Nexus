"""Owner-side inventory and online backup. Neither helper opens a repository or migrates it."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from nexus_memory.domain.errors import StorageIntegrityError
from nexus_memory.domain.models import Scope

_SCOPE_TABLES = ('blobs', 'memories', 'revisions', 'receipts', 'outbox', 'head_index',
                 'repositories', 'repository_checkouts', 'revision_references', 'revision_index',
                 'space_partitions')


def inventory_partitions(connection: sqlite3.Connection, configured_scope: Scope | None = None) -> tuple[Scope, ...]:
    """Discover complete physical tuples, never a Cartesian product of components."""
    tables = {name for name, in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    pairs = {(configured_scope.namespace, configured_scope.actor)} if configured_scope else set()
    for table in _SCOPE_TABLES:
        if table in tables:
            pairs.update(connection.execute(f'SELECT DISTINCT namespace, actor FROM {table}'))
    return tuple(Scope(*pair) for pair in sorted(pairs))


@dataclass(frozen=True)
class BackupManifest:
    schema_version: int
    store_id: str | None
    counts: dict[str, int]
    row_hashes: dict[str, str]
    last_durable_seq: int
    integrity_check: tuple[str, ...]
    foreign_key_check: tuple[tuple, ...]


def store_manifest(connection: sqlite3.Connection) -> BackupManifest:
    """Describe a stable snapshot. Caller owns the read transaction or offline copy."""
    version, = connection.execute('PRAGMA user_version').fetchone()
    identity = connection.execute('SELECT store_id FROM store_identity WHERE id=1').fetchone()[0] if version >= 5 else None
    counts, hashes = {}, {}
    # Hash stored tables (including sqlite_sequence and FTS shadow storage), not views
    # or virtual projections. Column order is schema order; row order is value order.
    for name, in connection.execute("SELECT name FROM pragma_table_list WHERE schema='main' "
                                    "AND type IN ('table','shadow') AND name != 'sqlite_schema'"):
        table = '"' + name.replace('"', '""') + '"'
        columns = connection.execute(f'SELECT * FROM {table} LIMIT 0').description
        order = ','.join(str(i) for i in range(1, len(columns) + 1))
        digest, count = hashlib.sha256(), 0
        for row in connection.execute(f'SELECT * FROM {table} ORDER BY {order}'):
            values = [{'blob': v.hex()} if isinstance(v, bytes) else v for v in row]
            digest.update(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n')
            count += 1
        counts[name], hashes[name] = count, digest.hexdigest()
    last, = connection.execute('SELECT coalesce(max(durable_seq),0) FROM outbox').fetchone()
    return BackupManifest(version, identity, counts, hashes, last,
                          tuple(msg for msg, in connection.execute('PRAGMA integrity_check')),
                          tuple(connection.execute('PRAGMA foreign_key_check')))


def backup_store(path: str | Path, destination: str | Path) -> BackupManifest:
    """Publish a verified, standalone SQLite snapshot at a fresh owner-selected path."""
    destination = Path(destination)
    if any(os.path.lexists(str(destination) + suffix) for suffix in ('', '-wal', '-shm', '-journal')):
        raise FileExistsError(destination)
    uri = Path(path).resolve(strict=True).as_uri() + '?mode=ro'
    with TemporaryDirectory(dir=destination.parent) as temporary:
        snapshot = Path(temporary) / 'backup'
        snapshot.touch(mode=0o600)
        with closing(sqlite3.connect(uri, uri=True)) as source, closing(sqlite3.connect(snapshot)) as copy:
            source.execute('BEGIN')
            original = store_manifest(source)
            source.backup(copy)
            copy.execute('PRAGMA journal_mode=DELETE')
            manifest = store_manifest(copy)
            if manifest != original or manifest.integrity_check != ('ok',) or manifest.foreign_key_check:
                raise StorageIntegrityError('backup verification failed')
        os.link(snapshot, destination)  # Atomic publication; never replace an existing backup.
        return manifest
