"""Volatile, process-bounded response staging. A receipt alone cannot recreate bytes."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
import os
import sqlite3
from threading import Lock
from time import monotonic
from uuid import uuid4

from nexus_memory.domain.errors import PublicationBusy
from nexus_memory.domain.spaces import PublicationEnvelope


@dataclass(frozen=True, slots=True, eq=False)
class PublicationSlot:
    publication_id: str
    max_bytes: int
    deadline: float


@dataclass
class _Entry:
    queue: 'PublicationQueue'
    slot: PublicationSlot
    context: object = None
    envelope: PublicationEnvelope | None = None


_entries = {}
_lock = Lock()


def _reset_child():
    global _entries, _lock
    _entries, _lock = {}, Lock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_reset_child)


class PublicationQueue:
    def __init__(self, catalog):
        self.catalog = catalog

    def reserve(self, max_bytes, deadline):
        now = monotonic()
        if (type(max_bytes) is not int or not 0 <= max_bytes <= 2**20 or
                not isfinite(deadline) or deadline <= now):
            raise PublicationBusy('publication unavailable')
        with _lock:
            for key, entry in list(_entries.items()):
                if entry.context is None and entry.slot.deadline <= now:
                    del _entries[key]
            if len(_entries) >= 64 or sum(e.slot.max_bytes for e in _entries.values()) + max_bytes > 2**20:
                raise PublicationBusy('publication queue full')
            slot = PublicationSlot(str(uuid4()), max_bytes, min(deadline, now+2))
            _entries[slot.publication_id] = _Entry(self, slot)
            return slot

    def _entry(self, slot):
        e = _entries.get(slot.publication_id)
        if not e or e.queue is not self or e.slot is not slot:
            raise PublicationBusy('invalid reservation')
        return e

    def cancel(self, slot):
        with _lock:
            if self._entry(slot).context is not None:
                raise PublicationBusy('reservation in use')
            del _entries[slot.publication_id]

    def _claim(self, slot, context):
        with _lock:
            e = self._entry(slot)
            if e.context is not None:
                raise PublicationBusy('reservation in use')
            e.context = context

    def _discard(self, slot):
        with _lock:
            _entries.pop(slot.publication_id, None)

    def stage(self, slot, payload):
        with _lock:
            e = self._entry(slot)
            if not e.context or e.envelope or type(payload) is not bytes or len(payload) > slot.max_bytes:
                raise PublicationBusy('publication cannot be staged')
            e.envelope = PublicationEnvelope(slot.publication_id, e.context.grant_id, e.context.epoch,
                sha256(payload).hexdigest(), payload, datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
            return e.envelope

    def _matches(self, env):
        with closing(sqlite3.connect(self.catalog.path.as_uri()+'?mode=ro', uri=True, timeout=0)) as db:
            row = db.execute('SELECT grant_id,epoch,digest,enqueued_at FROM publications WHERE publication_id=?',
                             (env.publication_id,)).fetchone()
        return row == (env.grant_id, env.epoch, sha256(env.payload).hexdigest(), env.enqueued_at)

    def drain(self, publication_id, *, expected=None):
        with _lock:
            e = _entries.get(publication_id)
            if e is None or e.queue is not self or e.envelope is None or (expected is not None and expected != e.envelope):
                raise PublicationBusy('publication unavailable')
            try:
                if not self._matches(e.envelope):
                    raise PublicationBusy('publication unavailable')
            except sqlite3.Error:
                raise PublicationBusy('publication unavailable') from None
            del _entries[publication_id]
            return e.envelope.payload
