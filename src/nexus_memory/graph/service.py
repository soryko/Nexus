"""Atomic graph ledger commands. The binding comes from trusted catalog resolution."""
from dataclasses import replace
from datetime import UTC, datetime
import json
from uuid import uuid4

from nexus_memory.domain.errors import (IdempotencyConflict, InvalidEvidence, InvalidInput,
                                        InvalidRelation, ResolutionConflict, RevisionConflict)
from nexus_memory.domain.graph import (AssertionRevisionRef, CurationBatch, EntityRef,
                                       CodeSource, EvidenceRecord, GraphWriteReceipt, MemorySource)
from nexus_memory.domain.spaces import canonical_command_digest
from . import resolution
from .evidence import capture_git_span


def uid():
    return str(uuid4())


class GraphService:
    def __init__(self, repository, binding, *, git_binding=None):
        self.git_binding = git_binding
        self.repository, self.binding = repository, binding

    def _write(self, kind, args, operation, apply):
        space = self.binding.space_id
        digest = canonical_command_digest(1,kind,args)
        key = (space,operation.writer_stream_id,operation.operation_key)
        with self.repository.transaction(self.binding,write=True) as db:
            prior = db.execute("SELECT digest,receipt FROM graph_operations WHERE space_id=? AND family='graph-v1' AND writer=? AND key=?",key).fetchone()
            if prior:
                if prior[0] != digest:
                    raise IdempotencyConflict('graph operation changed')
                return GraphWriteReceipt.from_json(prior[1])
            seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM graph_events WHERE space_id=?',(space,)).fetchone()[0]
            op_id = uid()
            recorded_at = datetime.now(UTC).isoformat().replace('+00:00','Z')
            db.execute('INSERT INTO graph_events VALUES(?,?,?,?,?,?)',(space,seq,op_id,key[1],kind,json.dumps({'recorded_at':recorded_at})))
            db.execute('INSERT OR IGNORE INTO graph_generations VALUES(?,1,0)',(space,))
            mappings,refs = apply(db,seq)
            gen = db.execute('SELECT active FROM graph_generations WHERE space_id=?',(space,)).fetchone()[0]
            receipt = GraphWriteReceipt(op_id,mappings,refs,seq,gen)
            db.execute('UPDATE graph_events SET payload=? WHERE space_id=? AND seq=?',
                       (json.dumps({'recorded_at':recorded_at,'command':args,'receipt':json.loads(receipt.to_json())},sort_keys=True,separators=(',',':')),space,seq))
            db.execute("INSERT INTO graph_operations VALUES(?,'graph-v1',?,?,?,?,?)",(*key,digest,receipt.to_json(),seq))
            db.execute("INSERT INTO graph_projection_state VALUES(?,?,?,'ready') ON CONFLICT(space_id,generation) DO UPDATE SET watermark=excluded.watermark",(space,gen,seq))
            return receipt

    def curate(self, batch, *, operation, derived_sources=None):
        args = json.loads(batch.to_json())
        dependencies = {}
        for name, required in (derived_sources or {}).items():
            if (name not in {e.temp_id for e in batch.evidence} or not isinstance(required,(tuple,list))
                    or not 1 <= len(required) <= 128 or not all(isinstance(k,str) for k in required)):
                raise InvalidEvidence('invalid original dependencies')
            dependencies[name] = list(required)
        if dependencies:
            args = {'batch':args,'original_dependencies':dependencies}
            if len(json.dumps(args).encode()) > 1 << 20:
                raise InvalidInput('oversized graph batch')
        captured = {}
        code = [item for item in batch.evidence if isinstance(item.source, CodeSource)]
        if code:
            # A replay returns its durable receipt even if Git is now offline.
            with self.repository.transaction(self.binding,write=False) as db:
                row = db.execute("SELECT digest,receipt FROM graph_operations WHERE space_id=? AND family='graph-v1' AND writer=? AND key=?",
                    (self.binding.space_id,operation.writer_stream_id,operation.operation_key)).fetchone()
                if row:
                    if row[0] != canonical_command_digest(1,'curate',args):
                        raise IdempotencyConflict('graph operation changed')
                    return GraphWriteReceipt.from_json(row[1])
            if not self.git_binding or not any(r.repository_id == self.git_binding[0].repository_id for r in self.binding.repository_bindings):
                raise InvalidEvidence('repository unavailable')
            for item in code:
                src = item.source
                capture = capture_git_span(self.git_binding,src.commit_oid,src.path,item.span)
                if capture.source != src:
                    raise InvalidEvidence('source mismatch')
                captured[item] = capture.captured_bytes
        def apply(db, seq):
            if code:
                self.git_binding[1].check_identity()
            writer = _Batch(db,self.binding,seq,captured)
            result = writer.apply(batch)
            for name, required in dependencies.items():
                key = writer.ids[name]
                writer.query('INSERT INTO graph_derived_sources VALUES(?,?)',key)
                for dependency in set(required):
                    dep = writer.ids.get(dependency, dependency)
                    writer.require_evidence(dep)
                    writer.query('INSERT INTO graph_source_dependencies VALUES(?,?,?)',key,dep)
            for name in dependencies:
                writer.reject_evidence_cycle(writer.ids[name])
            return result
        return self._write('curate',args,operation,apply)

    def retain(self, source, attestation, *, operation):
        """Owner curation of scoped continued support; never changes the source head."""
        is_revision = isinstance(source,AssertionRevisionRef)
        kind = 'retain_assertion' if is_revision else 'retain_evidence'
        args = {'source':json.loads(source.to_json()) if is_revision else source,
                'attestation':json.loads(attestation.to_json())}
        def apply(db,seq):
            writer = _Batch(db,self.binding,seq)
            if not attestation.evidence or len(attestation.evidence)>128:
                raise InvalidEvidence('retention requires exact evidence')
            for key in attestation.evidence:
                writer.require_evidence(key)
            if is_revision:
                writer.endpoint(source)
            else:
                writer.require_evidence(source)
                writer.query('INSERT INTO graph_retained_support VALUES(?,?,?,?)',source,seq,attestation.to_json())
                writer.reject_evidence_cycle(source)
            return {},()
        return self._write(kind,args,operation,apply)

    def correct(self, assertion_id, expected_revision_id, replacement, *, operation, expected_resolution_generation=None):
        ref = AssertionRevisionRef(assertion_id,expected_revision_id)
        args = {'expected':json.loads(ref.to_json()),'replacement':json.loads(replacement.to_json())}
        if expected_resolution_generation is not None:
            if type(expected_resolution_generation) is not int or expected_resolution_generation < 0:
                raise InvalidInput('invalid resolution generation')
            args['expected_resolution_generation'] = expected_resolution_generation

        def apply(db, seq):
            writer = _Batch(db,self.binding,seq)
            row = writer.query('''SELECT r.seq, g.resolution FROM graph_assertions a
                JOIN graph_assertion_revisions r ON (r.space_id,r.id,r.rev)=(a.space_id,a.id,a.head)
                JOIN graph_generations g USING(space_id)
                WHERE a.space_id=? AND a.id=? AND a.head=?''', assertion_id,expected_revision_id).fetchone()
            if not row:
                raise RevisionConflict('graph head changed')
            expected = expected_resolution_generation
            if expected is None:
                # Legacy call binds to the generation at the exact observed revision.
                expected = writer.query('SELECT coalesce(max(generation),0) FROM graph_resolution_events WHERE space_id=? AND seq<=?',row[0]).fetchone()[0]
            if row[1] != expected:
                raise ResolutionConflict('resolution generation changed')
            return writer.apply(CurationBatch(assertions=(replacement,)),correct=ref)
        return self._write('correct',args,operation,apply)

    def supersede(self, change, *, operation):
        def apply(db, seq):
            # Require an explicit temporary revision endpoint for the replacement.
            name = change.replacement.temp_id
            if not name or change.control.subject != AssertionRevisionRef(name,name) or not change.control.support_groups:
                raise InvalidRelation('supersession needs an exact replacement and independent support')
            return _Batch(db,self.binding,seq).apply(CurationBatch(assertions=(change.replacement,change.control)))
        return self._write('supersede',json.loads(change.to_json()),operation,apply)

    def resolve(self, change, *, operation):
        def apply(db, seq):
            resolution.apply(db,self.binding.space_id,seq,change)
            return {},()
        return self._write('resolve',json.loads(change.to_json()),operation,apply)


class _Batch:
    def __init__(self, db, binding, seq, captured=None):
        self.captured = captured or {}
        self.db,self.binding,self.seq = db,binding,seq
        self.ids,self.revisions = {},{}

    def query(self, sql, *args):
        return self.db.execute(sql,(self.binding.space_id,*args))

    def require_evidence(self, key):
        if not self.query('SELECT 1 FROM graph_evidence_state WHERE space_id=? AND evidence=? AND withdrawn=0',key).fetchone():
            raise InvalidEvidence('evidence unavailable')

    def reject_evidence_cycle(self, start):
        # Include retained-support attestation inputs as well as immutable originals.
        cyclic = self.query('''WITH RECURSIVE edges(a,b) AS (
            SELECT derived,required FROM graph_source_dependencies WHERE space_id=?1 UNION
            SELECT r.evidence,j.value FROM graph_retained_support r,json_each(r.attestation,'$.evidence') j
            WHERE r.space_id=?1 AND r.seq=(SELECT max(t.seq) FROM graph_retained_support t
               WHERE t.space_id=r.space_id AND t.evidence=r.evidence)), walk(node) AS (
            SELECT b FROM edges WHERE a=?2 UNION SELECT b FROM edges JOIN walk ON a=node)
            SELECT 1 FROM walk WHERE node=?2 LIMIT 1''',start).fetchone()
        if cyclic:
            raise InvalidRelation('cyclic evidence dependency')

    def map(self, name, value):
        if name is not None:
            if name in self.ids:
                raise InvalidInput('duplicate temporary name')
            self.ids[name] = value

    def endpoint(self, endpoint):
        if isinstance(endpoint, EntityRef):
            key = self.ids.get(endpoint.entity_id,endpoint.entity_id)
            if self.query('SELECT 1 FROM graph_entities WHERE space_id=? AND id=?',key).fetchone():
                return EntityRef(key)
        elif isinstance(endpoint, AssertionRevisionRef):
            if endpoint.assertion_id == endpoint.revision_id and endpoint.assertion_id in self.revisions:
                return self.revisions[endpoint.assertion_id]
            if self.query('SELECT 1 FROM graph_assertion_revisions WHERE space_id=? AND id=? AND rev=?',endpoint.assertion_id,endpoint.revision_id).fetchone():
                return endpoint
        raise InvalidRelation('unknown endpoint')

    def apply(self, batch, correct=None):
        for entity in batch.entities:
            row = self.query('SELECT id FROM graph_entities WHERE space_id=? AND kind=? AND qualified_key=?',entity.kind,entity.qualified_key).fetchone()
            key = row[0] if row else uid()
            if not row:
                self.query('INSERT INTO graph_entities VALUES(?,?,?,?,?)',key,entity.kind,entity.qualified_key,self.seq)
            self.map(entity.temp_id,key)
        refs = []
        for draft in batch.assertions:
            ref = AssertionRevisionRef(correct.assertion_id if correct else uid(),uid())
            refs.append(ref)
            self.map(draft.temp_id,ref.assertion_id)
            if draft.temp_id:
                self.revisions[draft.temp_id] = ref
            if correct:
                changed = self.query('UPDATE graph_assertions SET head=?2 WHERE space_id=?1 AND id=?3 AND head=?4',ref.revision_id,ref.assertion_id,correct.revision_id)
                if not changed.rowcount:
                    raise RevisionConflict('graph head changed')
                self.query('UPDATE graph_adjacency SET active=0 WHERE space_id=? AND id=? AND rev=?',correct.assertion_id,correct.revision_id)
            else:
                self.query('INSERT INTO graph_assertions VALUES(?,?,?)',ref.assertion_id,ref.revision_id)
        evidence = [(item, uid()) for item in batch.evidence]
        for item, key in evidence:
            self.map(item.temp_id, key)
        resolved = []
        for ref,draft in zip(refs,batch.assertions):
            applicability = replace(draft.applicability,evidence=tuple(self.ids.get(e,e) for e in draft.applicability.evidence))
            d = replace(draft,subject=self.endpoint(draft.subject),object=self.endpoint(draft.object),applicability=applicability)
            resolved.append(d)
            cols = (ref.assertion_id,ref.revision_id,d.subject.to_json(),d.predicate,d.object.to_json())
            now = self.query("SELECT json_extract(payload,'$.recorded_at') FROM graph_events WHERE space_id=? AND seq=?",self.seq).fetchone()[0]
            self.query('INSERT INTO graph_assertion_revisions VALUES(?,?,?,?,?,?,?,?,?)',*cols,d.to_json(),now,self.seq)
            self.query('INSERT INTO graph_adjacency VALUES(?,?,?,?,?,?,?)',*cols,int(d.review_state!='retracted'))
        for item, key in evidence:
            self.capture(item, key)
        for ref,d in zip(refs,resolved):
            for e in d.applicability.evidence:
                if not self.query('SELECT 1 FROM graph_evidence_state WHERE space_id=? AND evidence=? AND withdrawn=0',e).fetchone():
                    raise InvalidEvidence('unknown attestation evidence')
            self.query('INSERT INTO graph_applicability VALUES(?,?,?)',ref.revision_id,d.applicability.to_json())
            self.groups(ref,d)
            if d.predicate=='SUPERSEDES':
                self.reject_cycle(d.subject.to_json(), 'supersession')
        for w in batch.withdrawals:
            if not self.query('UPDATE graph_evidence_state SET withdrawn=1, head=?2 WHERE space_id=?1 AND evidence=?3 AND head=?4 AND withdrawn=0',self.seq,w.evidence_id,w.expected_head_event_seq).rowcount:
                raise RevisionConflict('evidence head changed')
        return self.ids,tuple(refs)

    def capture(self,item,key):
        src = item.source
        target = self.revisions.get(item.target)
        if target is None:
            found = self.query('SELECT id,rev FROM graph_assertion_revisions WHERE space_id=? AND rev=?',item.target).fetchone()
            target = AssertionRevisionRef(*found) if found else None
        if not target:
            raise InvalidEvidence('source or target unavailable')
        if isinstance(src, CodeSource):
            data = self.captured[item]
            origin = src.to_json()
        else:
            if not isinstance(src,MemorySource) or src.ref.space_id != self.binding.space_id:
                raise InvalidEvidence('source unavailable')
            scope = self.binding.scope
            row = self.db.execute('SELECT b.body FROM memories m JOIN revisions r USING(namespace,actor,memory_id) JOIN blobs b ON (b.namespace,b.actor,b.id)=(r.namespace,r.actor,r.blob_id) WHERE m.namespace=? AND m.actor=? AND m.memory_id=? AND m.current_revision_id=r.revision_id AND r.revision_id=? AND m.tombstoned=0',
                                  (scope.namespace,scope.actor,src.ref.memory_id,src.ref.revision_id)).fetchone()
            if not row:
                raise InvalidEvidence('source unavailable')
            data = row[0][item.span.start:item.span.end]
            origin = src.ref.memory_id
        try:
            record = EvidenceRecord(key,target,item.role,src,item.span,data,origin)
        except InvalidInput:
            raise InvalidEvidence('invalid source span') from None
        self.query('INSERT INTO graph_evidence VALUES(?,?,?,?,?,?)',key,src.to_json(),item.span.to_json(),data,record.origin_identity)
        self.query('INSERT INTO graph_assertion_evidence VALUES(?,?,?,?,?)',target.assertion_id,target.revision_id,key,item.role)
        self.query('INSERT INTO graph_evidence_state VALUES(?,?,?,0)',key,self.seq)

    def groups(self,ref,draft):
        for index,group in enumerate(draft.support_groups):
            self.query('INSERT INTO graph_support_groups VALUES(?,?,?)',ref.revision_id,index)
            for pos,name in enumerate(group):
                e = self.ids.get(name,name)
                if self.query('SELECT 1 FROM graph_evidence_state WHERE space_id=? AND evidence=? AND withdrawn=0',e).fetchone():
                    dep = None
                else:
                    dep = self.revisions[name].revision_id if name in self.revisions else name
                    if not self.query('SELECT 1 FROM graph_assertion_revisions WHERE space_id=? AND rev=?',dep).fetchone():
                        raise InvalidRelation('unknown premise')
                    e = None
                self.query('INSERT INTO graph_support_premises VALUES(?,?,?,?,?,?)',ref.revision_id,index,pos,e,dep)
            self.reject_cycle(ref.revision_id,'premise')

    def reject_cycle(self, start, kind):
        if kind=='premise':
            edges = 'SELECT rev,dependency FROM graph_support_premises WHERE space_id=?1 AND dependency IS NOT NULL'
        else:
            edges = "SELECT subject,object FROM graph_adjacency WHERE space_id=?1 AND predicate='SUPERSEDES'"
        sql = f'''WITH RECURSIVE edge(a,b) AS ({edges}), walk(node) AS (
                  SELECT b FROM edge WHERE a=?2 UNION SELECT b FROM edge JOIN walk ON a=node)
                  SELECT 1 FROM walk WHERE node=?2 LIMIT 1'''
        if self.query(sql,start).fetchone():
            raise InvalidRelation('cyclic graph dependency')
