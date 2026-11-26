"""Owner/registrar interface. Paths and session creation are never model inputs."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4

from nexus_memory.domain.errors import (GrantRevoked, IdempotencyConflict, InvalidInput,
    SpaceNotMounted, SpaceReadOnly, StorageIntegrityError, UnsupportedSchema)
from nexus_memory.domain.models import RepositoryBinding, Scope
from nexus_memory.domain.spaces import GrantContext, Mount, OperationRef, SpaceBinding
from .migration import backup_store, inventory_partitions, prepare_owner_store

_SCHEMA = '''
CREATE TABLE stores(store_id TEXT PRIMARY KEY, locator TEXT NOT NULL UNIQUE, dev INTEGER, ino INTEGER,
 state TEXT NOT NULL, UNIQUE(dev,ino));
CREATE TABLE spaces(space_id TEXT PRIMARY KEY, store_id TEXT NOT NULL REFERENCES stores,
 namespace TEXT NOT NULL, actor TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, owner TEXT NOT NULL,
 policy_version INTEGER NOT NULL DEFAULT 1, UNIQUE(store_id,namespace,actor));
CREATE TABLE operations(stream TEXT, key TEXT, digest TEXT NOT NULL, allocation TEXT NOT NULL, receipt TEXT,
 PRIMARY KEY(stream,key));
CREATE TABLE sessions(session TEXT PRIMARY KEY, owner TEXT NOT NULL, stream TEXT NOT NULL, epoch INTEGER NOT NULL,
 grant_id TEXT, space_id TEXT REFERENCES spaces, alias TEXT, mode TEXT);
CREATE TABLE exposure(session TEXT REFERENCES sessions, space_id TEXT REFERENCES spaces, PRIMARY KEY(session,space_id));
PRAGMA user_version=1;
'''


# Stage B repo ceiling, highest first. A session grant can never exceed it; see set_ceiling.
CEILINGS = ('maintain', 'recall', 'off')
ROLES = ('root', 'delegated')


def _id():
    return str(uuid4())


def _check(condition, message='store registration mismatch'):
    if not condition:
        raise StorageIntegrityError(message)


def _identity(path):
    try:
        stat = path.stat()
    except OSError:
        raise StorageIntegrityError('store unavailable') from None
    return stat.st_dev, stat.st_ino


@contextmanager
def _reader(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        yield db


def _version(db):
    version, = db.execute('PRAGMA user_version').fetchone()
    if version not in range(1, 7):
        raise UnsupportedSchema('unsupported memory store')
    if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='memories'").fetchone():
        raise UnsupportedSchema('not a memory store')
    return version


def _store_uuid(db):
    identities = db.execute('SELECT store_id FROM store_identity WHERE id=1').fetchall()
    _check(len(identities) == 1)
    return identities[0][0]


def _binding(db, store_id, sid, ns, actor):
    repos = tuple(RepositoryBinding(r, fmt, None) for r, fmt in db.execute(
        'SELECT repository_id,object_format FROM repositories WHERE namespace=? AND actor=? ORDER BY repository_id',
        (ns, actor)))
    return SpaceBinding(sid, store_id, Scope(ns, actor), repos)


def _bindings(path, store_id):
    with _reader(path) as db:
        _check(_version(db) in (5, 6))
        _check(db.execute('SELECT store_id FROM store_identity').fetchall() == [(store_id,)])
        result = []
        for sid, ns, actor, identity in db.execute('SELECT space_id,namespace,actor,store_id FROM space_partitions ORDER BY namespace,actor'):
            _check(identity == store_id)
            result.append(_binding(db, store_id, sid, ns, actor))
        _check({b.scope for b in result} == set(inventory_partitions(db)))
        return tuple(result)


class Catalog:
    def __init__(self, path, *, legacy_owner='local'):
        self.path = Path(path).resolve()
        self.legacy_owner = legacy_owner
        if not self.path.exists():
            self.path.touch(mode=0o600, exist_ok=True)
        with self._transaction() as db:
            version, = db.execute('PRAGMA user_version').fetchone()
            if not version and not db.execute('SELECT 1 FROM sqlite_schema').fetchone():
                for statement in _SCHEMA.strip().rstrip(';').split(';'):
                    db.execute(statement)
            elif version not in (1, 2, 3) or not db.execute("SELECT 1 FROM sqlite_schema WHERE name='operations'").fetchone():
                raise UnsupportedSchema('unsupported catalog')
            if version < 2:
                db.execute('CREATE TABLE publications(publication_id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions, '
                           'grant_id TEXT NOT NULL, epoch INTEGER NOT NULL, digest TEXT NOT NULL, enqueued_at TEXT NOT NULL)')
                db.execute('PRAGMA user_version=2')
            if version < 3:
                # Version 3 (Stage B): a per-space ceiling and its generation, and a session role.
                # Existing spaces keep Stage A behaviour ('maintain'); a v2 runtime refuses version 3.
                db.execute("ALTER TABLE spaces ADD COLUMN ceiling TEXT NOT NULL DEFAULT 'maintain' "
                           "CHECK(ceiling IN ('maintain','recall','off'))")
                db.execute('ALTER TABLE spaces ADD COLUMN ceiling_generation INTEGER NOT NULL DEFAULT 1')
                db.execute("ALTER TABLE sessions ADD COLUMN role TEXT NOT NULL DEFAULT 'root' "
                           "CHECK(role IN ('root','delegated'))")
                db.execute('PRAGMA user_version=3')

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except sqlite3.IntegrityError:
                raise StorageIntegrityError('catalog identity conflict') from None
            except sqlite3.DatabaseError:
                raise StorageIntegrityError('catalog or store unavailable') from None

    @staticmethod
    def _operation(db, operation, *command):
        if not isinstance(operation, OperationRef):
            raise InvalidInput('catalog operation required')
        key = operation.writer_stream_id, operation.operation_key
        digest = hashlib.sha256(json.dumps(command, separators=(',',':')).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO operations VALUES(?,?,?,?,NULL)', (*key,digest,_id()))
        row = db.execute('SELECT * FROM operations WHERE stream=? AND key=?', key).fetchone()
        if row['digest'] != digest:
            raise IdempotencyConflict('catalog command changed')
        return row

    @staticmethod
    def _finish(db, op, receipt):
        db.execute('UPDATE operations SET receipt=? WHERE stream=? AND key=?', (receipt,op['stream'],op['key']))

    @staticmethod
    def _store(db, store_id):
        row = db.execute('SELECT * FROM stores WHERE store_id=?',(store_id,)).fetchone()
        _check(row is not None)
        return row

    @staticmethod
    def _mapped(db, store_id):
        return {tuple(r) for r in db.execute('SELECT space_id,namespace,actor FROM spaces WHERE store_id=?',(store_id,))}

    def _inspect(self, db, row, *, installed=True):
        path = Path(row['locator'])
        _check(_identity(path) == (row['dev'],row['ino']))
        bindings = _bindings(path, row['store_id'])
        if installed:
            _check(self._mapped(db,row['store_id']) == {(b.space_id,b.scope.namespace,b.scope.actor) for b in bindings})
        return bindings

    def _install_bindings(self, db, bindings, title, kind, owner):
        # A project space starts recall-only; maintenance is a separate owner opt-in.
        ceiling = 'recall' if kind == 'project' else 'maintain'
        for b in bindings:
            db.execute('INSERT INTO spaces(space_id,store_id,namespace,actor,title,kind,owner,policy_version,ceiling) '
                       'VALUES(?,?,?,?,?,?,?,1,?)',
                       (b.space_id,b.store_id,b.scope.namespace,b.scope.actor,title or b.scope.namespace,kind,owner,ceiling))

    def register_store(self, path, *, operation, expected_store_id=None) -> tuple[SpaceBinding,...]:
        path = Path(path).resolve()
        command = 'register', str(path), expected_store_id, self.legacy_owner
        # Reserve both identity and locator before any cross-file effect. A different
        # process can resume the same intent but cannot adopt an interrupted import.
        with self._transaction() as db:
            op = self._operation(db,operation,*command)
            dev, ino = _identity(path)
            with _reader(path) as source:
                version = _version(source)
                sid = _store_uuid(source) if version >= 5 else op['allocation']
            _check(expected_store_id in (None,sid))
            row = db.execute('SELECT * FROM stores WHERE store_id=? OR locator=? OR (dev=? AND ino=?)', (sid,str(path),dev,ino)).fetchone()
            if row:
                _check(row['locator'] == str(path), 'duplicate store at another locator')
                _check((row['store_id'],row['dev'],row['ino']) == (sid,dev,ino))
                if row['state'] == 'active':
                    bindings = self._inspect(db,row)
                    self._finish(db,op,sid)
                    return bindings
                _check(row['state'] == op['allocation'])
            else:
                _check(op['receipt'] is None)
                db.execute('INSERT INTO stores VALUES(?,?,?,?,?)',(sid,str(path),dev,ino,op['allocation']))
        with self._transaction() as db:
            row = self._store(db,sid)
            _check((row['locator'],row['dev'],row['ino']) == (str(path),*_identity(path)))
            if row['state'] == 'active':
                return self._inspect(db,row)
            _check(row['state'] == op['allocation'])
            with _reader(path) as source:
                version = _version(source)
            if version < 5:
                # Quiesced owner import. A fresh backup per attempt also covers a
                # process exit between backup publication and migration commit.
                backup_store(path,self.path.parent / ('backup-' + _id() + '.sqlite'))
                prepare_owner_store(path, sid)
            bindings = self._inspect(db,row,installed=False)
            self._install_bindings(db,bindings,None,'legacy',self.legacy_owner)
            db.execute("UPDATE stores SET state='active' WHERE store_id=?",(sid,))
            self._finish(db,op,sid)
            return bindings

    def create_space(self, name, kind, *, principal_id, operation) -> SpaceBinding:
        if not name or not principal_id or kind not in ('personal','project','research'):
            raise InvalidInput('invalid space')
        command = 'create', name, kind, principal_id
        with self._transaction() as db:
            op = self._operation(db,operation,*command)
            path = self.path.parent / ('space-' + op['allocation'] + '.sqlite')
            reserved = db.execute('SELECT * FROM stores WHERE locator=?',(str(path),)).fetchone()
            if not op['receipt'] and not reserved:
                _check(not path.exists())
                db.execute('INSERT INTO stores VALUES(?,?,NULL,NULL,?)',(_id(),str(path),op['allocation']))
        with self._transaction() as db:
            op = self._operation(db,operation,*command)
            space_id = op['allocation']
            if op['receipt']:
                row = self._store(db,op['receipt'])
                _check(row['state'] == 'active')
                binding, = self._inspect(db,row)
                return binding
            reserved = db.execute('SELECT * FROM stores WHERE locator=?',(str(path),)).fetchone()
            _check(reserved['state'] == space_id)
            sid = reserved['store_id']
            scope = Scope('space:' + space_id, 'space')
            if not path.exists():
                with TemporaryDirectory(dir=self.path.parent) as tmp:
                    staging = Path(tmp) / 'store'
                    prepare_owner_store(staging, sid)
                    with closing(sqlite3.connect(staging)) as store, store:
                        store.execute('INSERT INTO space_partitions VALUES(?,?,?,?)',(sid,scope.namespace,scope.actor,space_id))
                    backup_store(staging,path)
            binding, = _bindings(path,sid)
            _check(binding.space_id == space_id and binding.scope == scope)
            db.execute("UPDATE stores SET dev=?,ino=?,state='active' WHERE store_id=?",(*_identity(path),sid))
            self._install_bindings(db,(binding,),name,kind,principal_id)
            self._finish(db,op,sid)
            return binding

    def _deactivate(self, sid, operation, state):
        with self._transaction() as db:
            op = self._operation(db,operation,state,sid)
            if op['receipt']:
                return
            row = self._store(db,sid)
            _check(row['state'] == 'active' or (row['state'],state) == ('retired','restoring'))
            db.execute('UPDATE stores SET state=? WHERE store_id=?',(state,sid))
            db.execute('UPDATE sessions SET epoch=epoch+1,grant_id=NULL,space_id=NULL,alias=NULL,mode=NULL '
                       'WHERE space_id IN (SELECT space_id FROM spaces WHERE store_id=?)',(sid,))
            self._finish(db,op,sid)

    def retire_store(self, store_id, *, operation):
        self._deactivate(store_id,operation,'retired')

    def begin_restore(self, store_id, *, operation):
        self._deactivate(store_id,operation,'restoring')

    def restore_store(self, store_id, path, *, operation):
        path = Path(path).resolve()
        with self._transaction() as db:
            op = self._operation(db,operation,'restore',store_id,str(path))
            row = self._store(db,store_id)
            if op['receipt']:
                _check(row['locator'] == str(path) and row['state'] == 'active')
                return self._inspect(db,row)
            _check(row['state'] == 'restoring')
            with _reader(path) as source:
                _check(source.execute('PRAGMA integrity_check').fetchall() == [('ok',)] and not source.execute('PRAGMA foreign_key_check').fetchall())
            db.execute("UPDATE stores SET state='active',locator=?,dev=?,ino=? WHERE store_id=?",(str(path),*_identity(path),store_id))
            bindings = self._inspect(db,self._store(db,store_id))
            self._finish(db,op,store_id)
            return bindings

    def revoke_space_sessions(self, space_id, *, principal_id, operation):
        """Owner control: end every session on a space without changing its ceiling (a folder approval
        was withdrawn). Uses the same epoch bump as 'off', under the lock the fence holds."""
        with self._transaction() as db:
            op = self._operation(db,operation,'revoke-sessions',space_id,principal_id)
            if op['receipt']:
                return int(op['receipt'])
            if not db.execute('SELECT 1 FROM spaces WHERE space_id=? AND owner=?',(space_id,principal_id)).fetchone():
                raise SpaceNotMounted('space not mounted')
            count = db.execute('UPDATE sessions SET epoch=epoch+1,grant_id=NULL,space_id=NULL,alias=NULL,mode=NULL '
                               'WHERE space_id=?',(space_id,)).rowcount
            self._finish(db,op,str(count))
            return count

    def launch_context(self, principal_id, *, operation, role='root'):
        """Trusted fresh-context launcher only; there is no caller-chosen session ID.

        role='delegated' (a subagent or worker) can only ever hold a read-only grant."""
        if role not in ROLES:
            raise InvalidInput('invalid session role')
        with self._transaction() as db:
            command = ('launch',principal_id) if role == 'root' else ('launch',principal_id,role)
            op = self._operation(db,operation,*command)
            if not op['receipt']:
                db.execute('INSERT INTO sessions(session,owner,stream,epoch,role) VALUES(?,?,?,0,?)',
                           (op['allocation'],principal_id,operation.writer_stream_id,role))
                self._finish(db,op,op['allocation'])
            return op['allocation']

    def set_ceiling(self, space_id, ceiling, *, principal_id, operation):
        """Owner control: change a space's ceiling under the catalog lock the fence holds while it
        publishes. Lowering it revokes every grant above it (all grants, for 'off') by bumping the
        session epoch, so no write admitted under the old ceiling can commit after this returns.
        Returns the new ceiling generation."""
        if ceiling not in CEILINGS:
            raise InvalidInput('invalid ceiling')
        with self._transaction() as db:
            op = self._operation(db,operation,'ceiling',space_id,ceiling,principal_id)
            if op['receipt']:
                return int(op['receipt'])
            row = db.execute('SELECT ceiling,ceiling_generation FROM spaces WHERE space_id=? AND owner=?',
                             (space_id,principal_id)).fetchone()
            if row is None:
                raise SpaceNotMounted('space not mounted')
            generation = row['ceiling_generation']+1
            db.execute('UPDATE spaces SET ceiling=?,ceiling_generation=? WHERE space_id=?',(ceiling,generation,space_id))
            if ceiling == 'off':
                db.execute('UPDATE sessions SET epoch=epoch+1,grant_id=NULL,space_id=NULL,alias=NULL,mode=NULL '
                           'WHERE space_id=?',(space_id,))
            elif ceiling == 'recall':
                # Writers keep their reads: the grant is narrowed, not revoked. The fence refuses any later
                # write on both counts (ceiling and session mode) under this lock, and raising the ceiling
                # again never restores write to these sessions.
                db.execute("UPDATE sessions SET mode='read_only' WHERE space_id=? AND mode='read_write'",(space_id,))
            self._finish(db,op,str(generation))
            return generation

    @staticmethod
    def _within_ceiling(space, session, mode):
        """Raise unless a grant of this mode fits the space's ceiling and the session's role."""
        if space['ceiling'] == 'off':
            raise SpaceNotMounted('space disabled')
        if mode == 'read_write' and (space['ceiling'] != 'maintain' or session['role'] != 'root'):
            raise SpaceReadOnly('space read only')

    @staticmethod
    def _grant(db, session):
        exposed = frozenset(x[0] for x in db.execute('SELECT space_id FROM exposure WHERE session=?',(session['session'],)))
        return GrantContext(session['owner'],session['session'],session['grant_id'],session['epoch'],session['stream'],exposed)

    def bind_session(self, session_id, principal_id, space_id, *, alias, mode, operation):
        return self._mount(session_id,principal_id,space_id,alias,mode,operation,False)

    def switch_session(self, session_id, principal_id, space_id, *, alias, mode, operation):
        return self._mount(session_id,principal_id,space_id,alias,mode,operation,True)

    def _mount(self, session_id, principal_id, space_id, alias, mode, operation, switch):
        mount = Mount(alias,space_id,mode,mode == 'read_write')
        with self._transaction() as db:
            op = self._operation(db,operation,'switch' if switch else 'bind',session_id,principal_id,mount.to_json())
            session = db.execute('SELECT * FROM sessions WHERE session=? AND owner=? AND stream=?',
                                 (session_id,principal_id,operation.writer_stream_id)).fetchone()
            if session is None or (op['receipt'] and session['grant_id'] != op['receipt']):
                raise GrantRevoked('grant revoked')
            space = db.execute('SELECT store_id,ceiling FROM spaces WHERE space_id=? AND owner=?',(space_id,principal_id)).fetchone()
            if not space or (not switch and session['grant_id'] and not op['receipt']):
                raise SpaceNotMounted('space not mounted')
            if not op['receipt']:
                self._within_ceiling(space,session,mode)
            row = self._store(db,space[0])
            _check(row['state'] == 'active')
            self._inspect(db,row)
            if not op['receipt']:
                db.execute('UPDATE sessions SET epoch=epoch+1,grant_id=?,space_id=?,alias=?,mode=? WHERE session=?',
                           (op['allocation'],space_id,alias,mode,session_id))
                db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?)',(session_id,space_id))
                self._finish(db,op,op['allocation'])
                session = db.execute('SELECT * FROM sessions WHERE session=?',(session_id,)).fetchone()
            return self._grant(db,session)

    def _resolve(self, grant, alias, write):
        with self._transaction() as db:
            session = db.execute('SELECT * FROM sessions WHERE grant_id=?',(grant.grant_id,)).fetchone()
            if not session or self._grant(db,session) != grant:
                raise GrantRevoked('grant revoked')
            if alias is not None and alias != session['alias']:
                raise SpaceNotMounted('space not mounted')
            if write and session['mode'] != 'read_write':
                raise SpaceReadOnly('space read only')
            sid = session['space_id']
            row = db.execute('SELECT stores.*,namespace,actor,ceiling FROM stores JOIN spaces USING(store_id) WHERE space_id=?', (sid,)).fetchone()
            self._within_ceiling(row,session,session['mode'] if write else 'read_only')
            path = Path(row['locator'])
            _check(row['state'] == 'active' and _identity(path) == (row['dev'], row['ino']))
            with _reader(path) as store:
                _check(_version(store) in (5, 6) and _store_uuid(store) == row['store_id'])
                scope = row['namespace'], row['actor']
                _check(store.execute('SELECT namespace,actor FROM space_partitions WHERE store_id=? AND space_id=?',
                                     (row['store_id'], sid)).fetchone() == scope)
                return _binding(store, row['store_id'], sid, *scope), row['locator']
