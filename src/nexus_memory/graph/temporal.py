"""Knowledge selection on the same pinned connection as current visibility checks."""
from copy import copy
import json
from nexus_memory.domain.graph import AssertionRevisionRef, _timestamp_key
from nexus_memory.domain.errors import InvalidRelation


def time_key(timestamp):
    whole, fraction = _timestamp_key(timestamp.removesuffix('+00:00')+'Z' if timestamp.endswith('+00:00') else timestamp)
    return whole.isoformat()+'.'+fraction


def valid_at(validity, when):
    if validity.kind == 'unknown':
        return None
    return validity.kind == 'unbounded' or time_key(validity.valid_from) <= time_key(when) < time_key(validity.valid_to)


class TemporalSelection:
    known_as_of = valid_as_of = None

    def historical(self, *, valid_as_of, known_as_of):
        view = copy(self)
        view.valid_as_of, view.known_as_of = valid_as_of, known_as_of
        time_key(valid_as_of)
        cutoff = time_key(known_as_of)
        self.db.create_function('utc_key',1,lambda t: time_key(t) if t else None,deterministic=True)
        # Select a causal prefix: a missing clock (pre-A07 event) or a clock
        # rollback must never reveal a future resolution through its sequence.
        # Each call selects absolute cutoffs from the original pinned transaction,
        # even when the receiver is already a historical selection.
        first_unseen = self.query("SELECT min(seq) FROM graph_events WHERE space_id=? AND seq<=? AND coalesce(utc_key(json_extract(payload,'$.recorded_at')),'~')>?",self._pinned_graph_seq,cutoff).fetchone()[0]
        view.graph_event_seq = first_unseen-1 if first_unseen is not None else self._pinned_graph_seq
        view.memory_event_seq = self.db.execute('''SELECT coalesce(max(durable_seq),0) FROM receipts JOIN revisions USING(namespace,actor,memory_id,revision_id)
            WHERE namespace=? AND actor=? AND operation IN ('record','revise') AND durable_seq<=? AND utc_key(created_at)<=?''',(*self.scope_key,self._pinned_memory_seq,cutoff)).fetchone()[0]
        return view

    def head(self, assertion_id):
        row = self.query('SELECT rev FROM graph_assertion_revisions WHERE space_id=? AND id=? AND seq<=? ORDER BY seq DESC LIMIT 1',assertion_id,self.graph_event_seq).fetchone()
        return AssertionRevisionRef(assertion_id,row[0]) if row else None

    def memory_head(self, ref):
        if ref.space_id != self.space:
            return None
        row = self.db.execute('SELECT current_revision_id FROM memories WHERE namespace=? AND actor=? AND memory_id=? AND tombstoned=0',(*self.scope_key,ref.memory_id)).fetchone()
        if row and self.known_as_of:
            row = self.db.execute('''SELECT revision_id FROM revisions JOIN receipts USING(namespace,actor,memory_id,revision_id)
                WHERE namespace=? AND actor=? AND memory_id=? AND operation IN ('record','revise') AND durable_seq<=? AND utc_key(created_at)<=?
                ORDER BY durable_seq DESC LIMIT 1''',(*self.scope_key,ref.memory_id,self.memory_event_seq,time_key(self.known_as_of))).fetchone()
        return row[0] if row else None

    def historical_canonical(self, member):
        aliases = {}
        for row in self.query('SELECT change FROM graph_resolution_events WHERE space_id=? AND seq<=? ORDER BY seq',self.graph_event_seq):
            change = json.loads(row[0])
            for key in change['memberships']:
                if change['action'] in ('merge','alias'):
                    aliases[key] = change['target']
                else:
                    aliases.pop(key,None)
        seen = set()
        while member in aliases:
            if member in seen:
                raise InvalidRelation('cyclic resolution')
            seen.add(member)
            member = aliases[member]
        return member
