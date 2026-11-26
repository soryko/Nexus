"""Atlas read surface over explicitly supplied, independently bound brokers."""
import json
from dataclasses import asdict
from time import monotonic

from nexus_memory.domain.errors import GrantRevoked, OperationFailure, SpaceNotMounted, StalePack
from nexus_memory.domain.graph import AssertionRevisionRef


class ScopeLost(PermissionError):
    """The bound grant no longer permits reading this scope."""


class RequestUnavailable(Exception):
    """Read did not complete; this says nothing about continuing authority."""


class GraphAtlasQueries:
    def __init__(self, brokers):
        self.mounts = {b.binding.space_id:b for b in brokers}

    def _read(self, sid, reader):
        b = self.mounts[sid]
        try:
            env = b.fence.publish(b.grant, b.queue.reserve(1 << 20, monotonic()+2),
                                  source_space_ids=(sid,), destination_space_id=None,
                                  validate_sources=b._validate,
                                  commit=lambda admission: json.dumps(reader(b, admission)).encode())
            return json.loads(b.drain(env))
        except (GrantRevoked, SpaceNotMounted) as exc:
            raise ScopeLost from exc

    def spaces(self):
        def descriptor(b, admission):
            # AuthorityFence holds the catalog writer lock throughout this read.
            from nexus_memory.spaces.catalog import _reader
            with _reader(b.catalog.path) as db:
                mode, = db.execute('SELECT mode FROM sessions WHERE grant_id=?',(b.grant.grant_id,)).fetchone()
            return {'id':b.binding.space_id, 'label':admission.space['title'], 'mode':mode,
                    'default':mode == 'read_write'}
        rows = []
        for sid in self.mounts:
            try:
                rows.append(self._read(sid, descriptor))
            except ScopeLost:
                continue
        return rows

    def pack(self, sid, pack_id):
        b = self.mounts[sid]
        env = b.dispatch('read_pack', {'pack_id':pack_id})
        if isinstance(env, OperationFailure):
            if env.code == 'stale_pack':
                # Handle lookup can reject before broker publication checks the grant.
                self._read(sid, lambda _broker, _admission: None)
                raise StalePack('pack unavailable')
            if env.code in ('grant_revoked', 'space_not_mounted'): raise ScopeLost
            raise RequestUnavailable
        return json.loads(b.drain(env))

    def revision(self, sid, mid, rid):
        """Resolve an immutable provider receipt under the viewer's *current* grant."""
        return self._read(sid, lambda b, _: asdict(b.memory.get(mid, rid)))

    def assertion(self, sid, aid, rid):
        def read(b, _):
            ref = AssertionRevisionRef(aid, rid)
            with b.graph.open_snapshot(b.binding) as snap:
                row = snap.assertion(ref)
                if row is None: raise KeyError(rid)
                from dataclasses import fields
                from nexus_memory.domain.graph import _wire
                return {'assertion':{f.name:_wire(getattr(row,f.name)) for f in fields(row)}, 'support_groups':snap.support_groups(ref),
                        'sources':[dict(source=asdict(e.source),span=asdict(e.span),id=e.evidence_id)
                                   for e in snap.evidence(ref)]}
        return self._read(sid, read)
