"""Exact keys and partition-local current lexical discovery on the pinned connection."""
from nexus_memory.domain.graph import EntityRef, MemorySource, decode_endpoint
from nexus_memory.domain.spaces import MemoryRef
from nexus_memory.domain.retrieval import AnchorCandidate, TraverseQuery


class AnchorProvider:
    def __init__(self, evidence_visible=None):
        self.limitations = set()
        self.evidence_visible = evidence_visible

    def search(self, query, snapshot, limit):
        self.limitations.clear()
        found = {}
        generation, = snapshot.db.execute('SELECT generation FROM index_state WHERE id=1').fetchone()

        def add(endpoint, exact):
            if endpoint not in found:
                found[endpoint] = AnchorCandidate(endpoint, 'exact-fts-v1', generation, exact)

        keys = query.anchors if isinstance(query, TraverseQuery) else (*query.anchors, query.text)
        for key in keys:
            if not key:
                continue
            canonical = snapshot.canonical(key)
            rows = snapshot.query('SELECT id FROM graph_entities WHERE space_id=? AND (id=? OR qualified_key=?) AND seq<=? ORDER BY id LIMIT ?',canonical,key,snapshot.graph_event_seq,limit+1).fetchall()
            if len(rows)>1:
                self.limitations.add('ambiguous_anchor')
            for row in rows:
                add(EntityRef(snapshot.canonical(row[0])), True)
            if len(found)>limit:
                break
        if query.text and query.mode=='historical':
            self.limitations.add('historical_index_unavailable')
        elif query.text and not isinstance(query, TraverseQuery) and len(found)<=limit:
            expr=' '.join('"'+w.replace('"','""')+'"' for w in query.text.split())
            hits=snapshot.db.execute('''SELECT h.memory_id,h.revision_id FROM head_fts JOIN head_index h ON h.seq=head_fts.rowid
                JOIN memories m USING(namespace,actor,memory_id) WHERE head_fts MATCH ? AND h.namespace=? AND h.actor=?
                AND m.tombstoned=0 AND m.current_revision_id=h.revision_id ORDER BY rank,h.memory_id LIMIT ?''',(expr,*snapshot.scope_key,limit+1)).fetchall() if expr else []
            if len(hits)>limit:
                self.limitations.add('candidate_pool')
            remaining=limit+1
            for mid,rid in hits[:limit]:
                source=MemorySource(MemoryRef(snapshot.space,mid,rid))
                rows=snapshot.query('''SELECT r.subject,e.id FROM graph_evidence e JOIN graph_evidence_state s ON s.space_id=e.space_id AND s.evidence=e.id
                    JOIN graph_assertion_evidence a ON a.space_id=e.space_id AND a.evidence=e.id JOIN graph_assertion_revisions r ON (r.space_id,r.id,r.rev)=(a.space_id,a.id,a.rev)
                    JOIN graph_assertions h ON (h.space_id,h.id,h.head)=(r.space_id,r.id,r.rev)
                    WHERE e.space_id=? AND e.source=? AND s.withdrawn=0 AND a.role='support'
                    ORDER BY r.id LIMIT ?''',source.to_json(),remaining).fetchall()
                remaining-=len(rows)
                visible=self.evidence_visible or snapshot._originals_visible
                for endpoint,evidence in rows:
                    if visible(evidence): add(decode_endpoint(endpoint),False)
                if not remaining:
                    self.limitations.add('candidate_pool'); break
        if len(found)>limit: self.limitations.add('candidate_pool')
        return tuple(found.values())[:limit]
