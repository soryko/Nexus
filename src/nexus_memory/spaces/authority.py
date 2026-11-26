"""Short, cooperating Stage A publication transactions (not a distributed commit)."""
from contextlib import closing, contextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
import sqlite3
from time import monotonic

from nexus_memory.domain.errors import (AuthorityBusy, FlowDenied, GrantRevoked, InvalidInput,
    PublicationBusy, PublicationUnknown, SpaceNotMounted, SpaceReadOnly, StorageIntegrityError)
from nexus_memory.domain.spaces import GrantContext, canonical_command_digest, _canonical as canonical_json
from nexus_memory.memory import MemoryService
from nexus_memory.storage.repository import EXACT_COMMAND_VERSION
from .catalog import _identity


def _remaining(deadline, busy):
    ms = int((deadline-monotonic())*1000)
    if ms <= 0:
        raise busy('admission deadline reached')
    return ms


def _timeout(db, deadline, busy):
    db.execute(f'PRAGMA busy_timeout={_remaining(deadline, busy)}')


@contextmanager
def _writer(path, deadline, busy):
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, isolation_level=None,
                                     timeout=_remaining(deadline, busy)/1000)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            _timeout(db, deadline, busy)
            db.execute('BEGIN IMMEDIATE')
            yield db  # close rolls back unless the caller explicitly commits
    except sqlite3.Error as exc:
        if getattr(exc, 'sqlite_errorcode', 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise busy('authority or publication unavailable') from None
        raise StorageIntegrityError('publication storage unavailable') from None


class _Admission:
    """Trusted callback capability valid only during commit. At most one destination transaction."""
    def __init__(self, deadline, context, space, destination):
        self.deadline, self.context = deadline, context
        self.space, self.destination = space, destination
        self.active = self.used = self.effect = False

    def mutate(self, apply, lookup):
        """Prepared SQL callback; lookup reads its digest-bound durable receipt on ambiguity."""
        if not self.active or self.used or self.destination is None:
            raise FlowDenied('no writable destination')
        self.used = True
        path = Path(self.space['locator'])
        ambiguous = False
        try:
            with _writer(path, self.deadline, PublicationBusy) as db:
                if (_identity(path) != (self.space['dev'], self.space['ino']) or not db.execute(
                    'SELECT 1 FROM store_identity JOIN space_partitions USING(store_id) WHERE id=1 '
                    'AND store_id=? AND space_id=? AND namespace=? AND actor=?',
                    (self.space['store_id'], self.destination, self.space['namespace'], self.space['actor'])).fetchone()):
                    raise StorageIntegrityError('destination identity changed')
                old = lookup(db)
                if old is not None:
                    self.effect = True
                    return old
                result = apply(db)
                _timeout(db, self.deadline, PublicationBusy)
                try:
                    db.commit()
                except sqlite3.Error:
                    ambiguous = not db.in_transaction
                    raise
                self.effect = True
                return result
        except (StorageIntegrityError, PublicationBusy):
            if not ambiguous:
                raise
            # Writer closed. Only the authoritative receipt can resolve the outcome.
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0)) as check:
                    check.row_factory = sqlite3.Row
                    result = lookup(check)
            except Exception:
                raise PublicationUnknown('publication outcome unknown') from None
            if result is None:
                raise
            self.effect = True
            return result

    def exact(self, repository, prepared):
        ctx = prepared.context
        if (ctx is None or ctx.space_id != self.destination or ctx.session_id != self.context.session_id or
            ctx.principal_id != self.context.principal_id or ctx.operation.writer_stream_id != self.context.writer_stream_id):
            raise FlowDenied('operation outside grant')
        if ((prepared.scope.namespace, prepared.scope.actor) != (self.space['namespace'], self.space['actor']) or
                ctx.operation.operation_key != prepared.key):
            raise InvalidInput('operation partition or key changed')
        args = prepared.operation, prepared.target, prepared.expected, prepared.item
        if prepared.item and prepared.item.references:
            digest, version = MemoryService._digest_v2(*args), MemoryService.REFERENCE_DIGEST_VERSION
        else:
            digest, version = MemoryService._digest(*args), MemoryService.DIGEST_VERSION
        refs = ctx.publication_sources
        sources = None if refs is None else {'version': 1, 'sources': [asdict(ref) for ref in refs]}
        wrapper = canonical_command_digest(EXACT_COMMAND_VERSION, 'exact', {
            'space_id': ctx.space_id, 'legacy_digest': digest, 'legacy_digest_version': version,
            'compat_metadata': ctx.compat_metadata, 'publication_sources': sources, 'visibility_role': ctx.visibility_role,
        })
        if ((digest, version, wrapper) != (prepared.digest, prepared.digest_version, prepared.command_digest) or
            prepared.compat_json != (None if ctx.compat_metadata is None else canonical_json(ctx.compat_metadata)) or
            prepared.sources_json != (None if sources is None else canonical_json(sources))):
            raise InvalidInput('prepared command changed')

        if any(ref.space_id != self.destination for ref in refs or ()):
            raise FlowDenied('source flow denied')

        def lookup(db):
            # A receipt describes an existing effect. Only _apply_prepared (a new
            # effect) needs current source heads, on the same writer transaction.
            return repository._operation_retry(db, prepared.scope, ctx.operation, wrapper)

        return self.mutate(lambda db: repository._apply_prepared(prepared, nullcontext(db)), lookup)


class AuthorityFence:
    def __init__(self, catalog, queue):
        if queue.catalog.path != catalog.path:
            raise InvalidInput('queue catalog mismatch')
        self.catalog, self.queue = catalog, queue

    def publish(self, context, slot, *, source_space_ids, destination_space_id, validate_sources, commit):
        if not isinstance(context, GrantContext):
            raise GrantRevoked('grant unavailable')
        sources = frozenset(source_space_ids)
        self.queue._claim(slot, context)
        admission = env = None
        checking_commit = False
        try:
            with _writer(self.catalog.path, slot.deadline, AuthorityBusy) as db:
                row = db.execute('SELECT * FROM sessions WHERE grant_id=?', (context.grant_id,)).fetchone()
                if not row or self.catalog._grant(db, row) != context:
                    raise GrantRevoked('grant revoked')
                sid = row['space_id']
                space = db.execute('SELECT * FROM spaces JOIN stores USING(store_id) WHERE space_id=? '
                                   "AND owner=? AND state='active'", (sid, context.principal_id)).fetchone()
                if not space or sources - {sid} or destination_space_id not in (None, sid):
                    raise SpaceNotMounted('space not mounted')
                # The ceiling is read under this lock, the one set_ceiling takes to lower it.
                self.catalog._within_ceiling(space, row, 'read_write' if destination_space_id else 'read_only')
                if destination_space_id is not None:
                    if row['mode'] != 'read_write':
                        raise SpaceReadOnly('space read only')
                    # Stage A policy generation 1: same space (hence same audience) only.
                    if space['policy_version'] != 1 or context.exposed_space_ids - {sid}:
                        raise FlowDenied('source flow denied')
                admission = _Admission(slot.deadline, context, space, destination_space_id)
                validate_sources(admission)
                _remaining(slot.deadline, PublicationBusy)
                admission.active = True
                try:
                    payload = commit(admission)
                finally:
                    admission.active = False
                _timeout(db, slot.deadline, PublicationBusy)
                env = self.queue.stage(slot, payload, read=destination_space_id is None)
                db.execute('INSERT INTO publications VALUES(?,?,?,?,?,?)', (env.publication_id, context.session_id,
                           env.grant_id, env.epoch, env.response_digest, env.enqueued_at))
                db.executemany('INSERT OR IGNORE INTO exposure VALUES(?,?)', ((context.session_id, s) for s in sources))
                _timeout(db, slot.deadline, PublicationBusy)
                checking_commit = True
                db.commit()
                return env
        except Exception:
            if checking_commit:
                try:
                    if self.queue._matches(env):
                        return env
                except sqlite3.Error:
                    self.queue._discard(slot)
                    raise PublicationUnknown('publication outcome unknown') from None
            self.queue._discard(slot)
            if admission and admission.effect:
                raise PublicationUnknown('publication outcome unknown') from None
            raise

    def revoke(self, grant_id, *, expected_epoch, operation):
        deadline = monotonic()+2
        with _writer(self.catalog.path, deadline, AuthorityBusy) as db:
            op = self.catalog._operation(db, operation, 'revoke', grant_id, expected_epoch)
            if op['receipt']:
                return int(op['receipt'])
            changed = db.execute('UPDATE sessions SET epoch=epoch+1,grant_id=NULL,space_id=NULL,alias=NULL,mode=NULL '
                                 'WHERE grant_id=? AND epoch=?', (grant_id, expected_epoch))
            if changed.rowcount != 1:
                raise GrantRevoked('grant revoked')
            epoch = expected_epoch+1
            self.catalog._finish(db, op, str(epoch))
            _timeout(db, deadline, AuthorityBusy)
            db.commit()
            return epoch
