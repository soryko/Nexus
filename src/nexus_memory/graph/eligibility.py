"""Authoritative proof evaluation; projection lag is not an input."""
import json
from nexus_memory.domain.graph import Applicability, AssertionRevisionRef, EligibilityResult, MemorySource
from .temporal import valid_at

YES, NO, MAYBE = 'eligible', 'ineligible', 'unknown'


def conjunction(values):
    unknown = False
    for value in values:
        if value == NO:
            return NO
        unknown |= value == MAYBE
    return MAYBE if unknown else YES


class Proof:
    def __init__(self, snapshot, context, budget):
        self.snap, self.ctx = snapshot, context
        self.remaining = budget.proof_premise_rows
        self.controls = budget.conflict_rows
        self.active = set()
        self.groups = {}
        self.premises = {}
        self.originals = {}
        self.retained_by = {}
        self.reasons = set()

    def fail(self, reason, value=NO):
        self.reasons.add(reason)
        return value

    def visit(self, key, check):
        if key in self.active:
            return self.fail('cyclic_dependency')
        if self.remaining <= 0 or len(self.active) >= 64:
            return self.fail('proof_budget', MAYBE)
        self.remaining -= 1
        self.active.add(key)
        try:
            return check()
        finally:
            self.active.remove(key)

    def applicability(self, app):
        if app.kind == 'unknown':
            return self.fail('unknown_applicability', MAYBE)
        if self.ctx.repository_id != app.repository_id or (app.kind=='commit_set' and self.ctx.resolved_commit not in app.commits):
            return self.fail('not_applicable')
        return conjunction(self.evidence(e) for e in app.evidence)

    def retention(self, source):
        if isinstance(source, AssertionRevisionRef):
            row = self.snap.query("""SELECT json_extract(payload,'$.command.attestation') FROM graph_events
                WHERE space_id=? AND seq<=? AND kind='retain_assertion' AND json_extract(payload,'$.command.source.revision_id')=? ORDER BY seq DESC LIMIT 1""",self.snap.graph_event_seq,source.revision_id).fetchone()
        else:
            row = self.snap.query('SELECT attestation FROM graph_retained_support WHERE space_id=? AND evidence=? AND seq<=? ORDER BY seq DESC LIMIT 1',source,self.snap.graph_event_seq).fetchone()
        if not row:
            return self.fail('source_superseded')
        app = Applicability.from_json(row[0])
        value = self.applicability(app)
        if value == YES:
            self.retained_by[source] = app.evidence
        return value

    def bounded_rows(self, rows):
        """Reserve before consuming a premise or probe, including rejected ones."""
        while self.remaining:
            row = rows.fetchone()
            if row is None:
                return
            self.remaining -= 1
            yield row
        self.fail('proof_budget', MAYBE)
        yield None

    def evidence(self, key):
        def check():
            row = self.snap.query('''SELECT e.source,s.head,s.withdrawn FROM graph_evidence e JOIN graph_evidence_state s
                ON (s.space_id,s.evidence)=(e.space_id,e.id) WHERE e.space_id=? AND e.id=?''',key).fetchone()
            if not row or row[2] or row[1]>self.snap.graph_event_seq:
                return self.fail('evidence_unavailable')
            source = json.loads(row[0]); state = YES
            if source['kind']=='memory':
                ref = MemorySource.from_json(source).ref
                head = self.snap.memory_head(ref)
                if not head:
                    return self.fail('source_unavailable')
                if head != ref.revision_id:
                    state = self.retention(key)
            deps = self.snap.query('SELECT required FROM graph_source_dependencies WHERE space_id=? AND derived=? ORDER BY required',key)
            originals = []
            for r in self.bounded_rows(deps):
                if r is None: return MAYBE
                originals.append(r[0])
                state = conjunction((state, self.evidence(r[0])))
                if state == NO: return NO
            self.originals[key] = tuple(originals)
            return state
        return self.visit(('e',key),check)

    def revision(self, ref, *, premise=False, suppress=True):
        def check():
            record = self.snap.assertion(ref)
            if not record or record.review_state != 'accepted':
                return self.fail('not_accepted')
            guard = YES
            if self.snap.head(ref.assertion_id) != ref:
                guard = self.retention(ref) if premise else self.fail('not_selected_head')
            valid = valid_at(record.validity,self.snap.valid_as_of or self.ctx.host_time)
            if valid is False:
                return self.fail('outside_validity')
            if valid is None:
                guard = conjunction((guard,self.fail('unknown_validity',MAYBE)))
            guard = conjunction((guard,self.applicability(record.applicability)))
            if guard == NO:
                return NO
            complete = []; unknown = False
            if ref not in self.premises:
                rows = self.snap.query('SELECT ordinal, coalesce(evidence,dependency) FROM graph_support_premises WHERE space_id=? AND rev=? ORDER BY ordinal,position',ref.revision_id)
                groups = {}
                for row in self.bounded_rows(rows):
                    if row is None: return MAYBE
                    groups.setdefault(row[0], []).append(row[1])
                self.premises[ref] = tuple(tuple(g) for g in groups.values())
            for i,group in enumerate(self.premises[ref]):
                value = conjunction(self.premise(p,suppress) for p in group)
                if value == YES:
                    complete.append(str(i))
                unknown |= value == MAYBE
            self.groups[ref] = tuple(complete)
            support = YES if complete else MAYBE if unknown else self.fail('no_complete_group')
            base = conjunction((guard,support))
            if suppress and base != NO:
                return conjunction((base,self.not_suppressed(ref)))
            return base
        return self.visit(('r',ref,suppress),check)

    def premise(self, key, suppress):
        row = self.snap.query('SELECT id FROM graph_assertion_revisions WHERE space_id=? AND rev=?',key).fetchone()
        if row:
            return self.revision(AssertionRevisionRef(row[0],key),premise=True,suppress=suppress)
        return self.evidence(key)

    def not_suppressed(self, ref):
        # Read immutable control endpoints, not cached active adjacency. Test replacement
        # BASE support: suppressing B by C must not resurrect A in C -> B -> A.
        # Nested proof shares this allowance. Do not fetch even a limit probe
        # after another control/replacement consumed the remaining rows.
        rows = self.snap.query("SELECT id,rev,subject FROM graph_assertion_revisions WHERE space_id=? AND predicate='SUPERSEDES' AND object=? AND seq<=? ORDER BY seq LIMIT ?",ref.to_json(),self.snap.graph_event_seq,self.controls)
        unknown = False
        while self.controls:
            row = rows.fetchone()
            if row is None:
                return MAYBE if unknown else YES
            self.controls -= 1
            control = AssertionRevisionRef(row[0],row[1]); replacement = AssertionRevisionRef.from_json(row[2])
            # A control may cite this predecessor as a base premise. Base proof
            # has a separate cycle domain from scoped suppression.
            state = conjunction((self.revision(control,suppress=False),self.revision(replacement,premise=True,suppress=False)))
            if state == YES:
                return self.fail('superseded')
            unknown |= state == MAYBE
        return self.fail('control_budget', MAYBE)


def evaluate(ref, snapshot, context, budget):
    if not any((s.store_id,s.memory_sequence,s.graph_sequence)==(snapshot.store_id,snapshot.memory_event_seq,snapshot.graph_event_seq) for s in context.snapshots):
        return EligibilityResult('unknown',('snapshot_mismatch',),())
    proof = Proof(snapshot,context,budget)
    status = proof.revision(ref)
    return EligibilityResult(status,tuple(sorted(proof.reasons)),proof.groups.get(ref,()) if status==YES else ())
