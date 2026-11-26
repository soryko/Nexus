"""Reads on a single pinned SQLite transaction. No writer initialization."""
import hashlib
import json
from itertools import groupby
from nexus_memory.domain.errors import CursorExpired, InvalidInput
from nexus_memory.domain.graph import (Applicability, AssertionRecord, AssertionRevisionRef, EvidenceRecord,
                                       GraphPage, MemorySource, CodeSource, Span, Validity, decode_endpoint)


from .temporal import TemporalSelection


class Snapshot(TemporalSelection):
    def __init__(self, db, binding):
        self.scope_key = (binding.scope.namespace,binding.scope.actor)
        self.store_id = binding.store_id
        self.db, self.space = db, binding.space_id
        self.graph_event_seq = self.query('SELECT coalesce(max(seq),0) FROM graph_events WHERE space_id=?').fetchone()[0]
        self.memory_event_seq = db.execute('SELECT coalesce(max(durable_seq),0) FROM outbox WHERE namespace=? AND actor=?',
                                          (binding.scope.namespace,binding.scope.actor)).fetchone()[0]

        self._pinned_graph_seq = self.graph_event_seq
        self._pinned_memory_seq = self.memory_event_seq

    def query(self, sql, *params):
        return self.db.execute(sql, (self.space,*params))

    def canonical(self, member):
        if self.known_as_of:
            return self.historical_canonical(member)
        from .resolution import canonical
        return canonical(self.db,self.space,member)

    def assertion(self, ref):
        row = self.query('SELECT * FROM graph_assertion_revisions WHERE space_id=? AND id=? AND rev=? AND seq<=?',ref.assertion_id,ref.revision_id,self.graph_event_seq).fetchone()
        if not row:
            return None
        d = json.loads(row['content'])
        return AssertionRecord(ref=ref, subject=decode_endpoint(row['subject']), object=decode_endpoint(row['object']),predicate=row['predicate'],
                               qualifiers=d['qualifiers'], origin_kind=d['origin_kind'], review_state=d['review_state'],producer_version=d['producer_version'],
                               validity=Validity.from_json(d['validity']),applicability=Applicability.from_json(d['applicability']),
                               recorded_at=row['recorded_at'],graph_event_seq=row['seq'])

    def support_groups(self, ref):
        rows = self.query('SELECT p.ordinal,coalesce(p.evidence,p.dependency) FROM graph_assertion_revisions r JOIN graph_support_premises p USING(space_id,rev) WHERE r.space_id=? AND r.id=? AND r.rev=? AND r.seq<=? ORDER BY p.ordinal,p.position',ref.assertion_id,ref.revision_id,self.graph_event_seq)
        return tuple(tuple(r[1] for r in group) for _,group in groupby(rows,lambda r:r[0]))

    def _originals_visible(self, evidence):
        # Evidence bytes are readable only if all transitive originals are still
        # visible. UNION visits a shared original only once, even in corrupt cycles.
        rows = self.query('''WITH RECURSIVE originals(id) AS (
            SELECT required FROM graph_source_dependencies WHERE space_id=?1 AND derived=?2
            UNION SELECT d.required FROM originals o JOIN graph_source_dependencies d
            ON d.space_id=?1 AND d.derived=o.id)
            SELECT e.source,s.withdrawn,s.head FROM originals o
            LEFT JOIN graph_evidence e ON e.space_id=?1 AND e.id=o.id
            LEFT JOIN graph_evidence_state s ON s.space_id=?1 AND s.evidence=o.id''',evidence)
        for source, withdrawn, head in rows:
            if source is None or withdrawn != 0 or head > self.graph_event_seq:
                return False
            data = json.loads(source)
            if data['kind'] == 'memory' and not self.memory_head(MemorySource.from_json(data).ref):
                return False
        return True

    def evidence(self, ref):
        rows = self.query('SELECT e.*,a.role FROM graph_assertion_evidence a JOIN graph_evidence e ON (e.space_id,e.id)=(a.space_id,a.evidence) JOIN graph_evidence_state s ON (s.space_id,s.evidence)=(e.space_id,e.id) WHERE a.space_id=? AND a.id=? AND a.rev=? AND s.withdrawn=0 AND s.head<=? ORDER BY e.id',ref.assertion_id,ref.revision_id,self.graph_event_seq)
        result = []
        for row in rows:
            d = json.loads(row['source'])
            source = (MemorySource if d['kind']=='memory' else CodeSource).from_json(d)
            if isinstance(source,MemorySource) and not self.memory_head(source.ref):
                continue
            if not self._originals_visible(row['id']):
                continue
            result.append(EvidenceRecord(row['id'],ref,row['role'],source,Span.from_json(row['span']),row['captured'],row['origin']))
        return tuple(result)

    def retrieval_refs(self, condition, params, limit):
        """Internal fixed predicates only. Caller reserves and charges its own probe."""
        if self.known_as_of:
            rows = self.query(f'''SELECT id,rev FROM graph_assertion_revisions r WHERE space_id=?1 AND seq<=?2
                AND NOT EXISTS(SELECT 1 FROM graph_assertion_revisions n WHERE n.space_id=r.space_id AND n.id=r.id AND n.seq>r.seq AND n.seq<=?2)
                AND json_extract(content,'$.review_state')!='retracted' AND ({condition}) ORDER BY id,rev LIMIT ?''',
                self.graph_event_seq, *params, limit)
        else:
            rows = self.query(f'SELECT id,rev FROM graph_adjacency WHERE space_id=? AND active=1 AND ({condition}) ORDER BY id,rev LIMIT ?', *params, limit)
        yield from (AssertionRevisionRef(*row) for row in rows)

    def _page(self, condition, params, after, limit):
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise InvalidInput('invalid graph page size')
        stamp = hashlib.sha256(json.dumps([self.space,self.graph_event_seq,self.memory_event_seq,condition,params]).encode()).hexdigest()
        last = ('','')
        if after is not None:
            try:
                token,a,r = json.loads(after)
                if token != stamp or not isinstance(a,str) or not isinstance(r,str):
                    raise ValueError
                last = (a,r)
            except (TypeError,ValueError):
                raise CursorExpired('invalid graph cursor') from None
        if self.known_as_of:
            rows = self.query(f"""SELECT id,rev FROM graph_assertion_revisions r WHERE space_id=?1 AND seq<=?2
                AND NOT EXISTS(SELECT 1 FROM graph_assertion_revisions n WHERE n.space_id=r.space_id AND n.id=r.id AND n.seq>r.seq AND n.seq<=?2)
                AND json_extract(content,'$.review_state')!='retracted' AND ({condition}) AND (id,rev)>(?,?) ORDER BY id,rev LIMIT ?""",self.graph_event_seq,*params,*last,limit+1).fetchall()
        else:
            rows = self.query(f'SELECT id,rev FROM graph_adjacency WHERE space_id=? AND active=1 AND ({condition}) AND (id,rev)>(?,?) ORDER BY id,rev LIMIT ?',*params,*last,limit+1).fetchall()
        refs = tuple(AssertionRevisionRef(*row) for row in rows[:limit])
        return GraphPage(refs,json.dumps([stamp,*rows[limit-1]]) if len(rows)>limit else None)

    def adjacent(self, endpoint, steps, after, limit):
        # Inverse steps prefix the registered predicate with ^.
        parts,params = [],[]
        for step in steps:
            col = 'object' if step.startswith('^') else 'subject'
            parts.append(f'({col}=? AND predicate=?)')
            params.extend((endpoint.to_json(),step.removeprefix('^')))
        return self._page(' OR '.join(parts) or '0',params,after,limit)

    def control_relations(self, ref, after, limit):
        return self._page("predicate IN ('SUPPORTS','DISPUTES','SUPERSEDES') AND (subject=? OR object=?)",[ref.to_json(),ref.to_json()],after,limit)
