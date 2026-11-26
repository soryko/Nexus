"""Bounded finite pattern search. Proof and conflict allowances span all seeds."""
from collections import defaultdict
from dataclasses import dataclass, replace
from itertools import zip_longest
import json

from nexus_memory.domain.graph import AssertionRevisionRef, CodeSource, MemorySource, Span, Validity, Applicability
from nexus_memory.domain.retrieval import ClaimRevision, PathEdge, RelationStep, Citation, EvidenceBundle
from nexus_memory.graph.eligibility import Proof, YES, MAYBE
from nexus_memory.graph.temporal import time_key


def step(p, inverse=False):
    return RelationStep(p, 'inverse' if inverse else 'forward')


def pattern(q):
    if q.pattern: return tuple((s,) for s in q.pattern)
    if q.intent in ('rationale','compound'):
        base=((step('IMPLEMENTS'),),(step('MOTIVATED_BY'),))
        return base+((step('REGRESSION_FOR',True),),) if q.intent=='compound' else base
    if q.intent=='impact': return (tuple(step(p,True) for p in ('CALLS','IMPORTS','DEPENDS_ON')),)
    return (tuple(step(p) for p in ('IMPLEMENTS','MOTIVATED_BY','REGRESSION_FOR','DEFINED_IN','CALLS','IMPORTS','DEPENDS_ON','MENTIONS')),)


@dataclass(frozen=True)
class State:
    seed: int
    endpoint: object
    visited: frozenset
    path: tuple = ()  # ordered (original record, traversal step, selected group)
    degree: int = 0


class Search:
    def __init__(self,snap,ctx,q,limitations,expired):
        self.snap,self.ctx,self.q,self.b=snap,ctx,q,q.budget
        self.limits,self.expired=limitations,expired
        self.proof=Proof(snap,ctx,self.b)
        self.expanded=self.proof_rows=self.proof_edges=0
        self.validated={}
        self.disputes={}

    def check(self, ref):
        if ref not in self.validated:
            value=self.proof.revision(ref)
            if value==MAYBE: self.limits.add('unknown_eligibility')
            self.validated[ref]=value
        if 'proof_budget' in self.proof.reasons: self.limits.add('proof_premise_rows')
        if 'control_budget' in self.proof.reasons: self.limits.add('conflict_rows')
        return self.validated[ref]==YES

    def expand(self, state, choices, proof=False):
        """Yield one charged quantum even when every alternative is rejected."""
        streams = []
        for step in choices:
            column = 'subject' if step.direction == 'forward' else 'object'
            refs = self.snap.retrieval_refs(f'{column}=? AND predicate=?',
                (state.endpoint.to_json(), step.predicate), self.b.edges_per_endpoint + 1)
            streams.append((step, column, refs))
        degree = 0
        while streams:
            next_round = []
            for step, column, refs in streams:
                if self.expired():
                    self.limits.add('deadline'); return self.b.edges_per_endpoint
                remaining = self.proof.remaining if proof else self.b.expansion_rows - self.expanded
                if remaining <= 0:
                    self.limits.add('proof_premise_rows' if proof else 'expansion_rows'); return self.b.edges_per_endpoint
                ref = next(refs, None)
                if ref is None: continue
                if proof:
                    self.proof.remaining -= 1
                    self.proof_rows += 1
                else:
                    self.expanded += 1
                degree += 1
                if degree > self.b.edges_per_endpoint:
                    self.limits.add('edges_per_endpoint'); return self.b.edges_per_endpoint
                next_round.append((step, column, refs))
                alternatives = []
                r = self.snap.assertion(ref)
                if r and r.predicate == step.predicate and getattr(r, column) == state.endpoint:
                    dest = r.subject if column == 'object' else r.object
                    if dest not in state.visited and self.check(ref):
                        groups = self.proof.premises[ref]
                        for idx in self.proof.groups[ref]:
                            alternatives.append(State(state.seed, dest, state.visited | {dest},
                                state.path + ((r,step,groups[int(idx)]),), state.degree))
                        if proof: self.proof_edges += 1
                yield alternatives
            streams = next_round
        return degree

    def ranked_expansion(self, state, choices):
        # Share one bounded scan among all siblings. Yield control after each row
        # so a hub cannot monopolize the global allowance while we measure it.
        scan = self.expand(state, choices)
        siblings = []
        while True:
            try:
                batch = next(scan)
            except StopIteration as done:
                degree = state.degree + done.value
                break
            siblings.extend(batch)
            yield ()
        yield [replace(child, degree=degree) for child in siblings]

    def paths(self,seeds):
        states=[State(i,a.endpoint,frozenset((a.endpoint,))) for i,a in enumerate(seeds)]
        positions=pattern(self.q)
        if len(positions)>self.b.semantic_depth:
            self.limits.add('semantic_depth'); return []
        endpoints={a.endpoint for a in seeds}
        for choices in positions:
            by_seed=defaultdict(list)
            for st in states:
                by_seed[st.seed].append(self.ranked_expansion(st, choices))
            streams = [stream for batch in zip_longest(*by_seed.values()) for stream in batch if stream]
            states = []
            while streams:
                active = []
                for stream in streams:
                    alternatives = next(stream, None)
                    if alternatives is not None:
                        states.extend(alternatives)
                        active.append(stream)
                streams = active
            states.sort(key=lambda st:(-len(endpoints & st.visited),st.seed,st.degree,tuple(r.ref.revision_id for r,_,_ in st.path),tuple(g for _,_,g in st.path)))
            if not self.q.exact:
                signatures=defaultdict(set); kept=[]
                for st in states:
                    seen = signatures[st.endpoint]
                    signature = tuple((r.ref, s, frozenset(g)) for r,s,g in st.path)
                    if signature in seen: continue
                    if len(seen)>=self.b.alternative_states: self.limits.add('alternative_states'); continue
                    seen.add(signature); kept.append(st)
                if len(kept)>self.b.beam_width: self.limits.add('beam_width')
                states=kept[:self.b.beam_width]
        if len(states)>self.b.finalist_bundles: self.limits.add('finalist_bundles')
        return states[:self.b.finalist_bundles]

    def conflicts(self,ref):
        if ref not in self.disputes:
            found=[]; remaining=self.proof.controls
            rows=self.snap.retrieval_refs("predicate='DISPUTES' AND (subject=? OR object=?)",(ref.to_json(),ref.to_json()),remaining)
            while self.proof.controls:
                control=next(rows,None)
                if control is None: break
                self.proof.controls-=1
                r=self.snap.assertion(control)
                if (r and r.predicate=='DISPUTES' and ref in (r.subject,r.object) and self.check(control)
                        and self.check(r.subject) and self.check(r.object)):
                    found.append(self.claim(control))
            else:
                self.limits.add('conflict_rows')
            self.disputes[ref]=tuple(found)
        return self.disputes[ref]

    def claim(self,ref):
        return ClaimRevision(self.snap.space,ref.assertion_id,ref.revision_id)

    def bundle(self,st):
        groups=[]; citations={}; disputes={}; edges=[]; apps=[]; intervals=[]
        cited = set()
        def cite(key):
            if key in cited: return True
            cited.add(key)
            row=self.snap.query('SELECT source,span,captured FROM graph_evidence WHERE space_id=? AND id=?',key).fetchone()
            if not row:
                dep=self.snap.query('SELECT id FROM graph_assertion_revisions WHERE space_id=? AND rev=?',key).fetchone()
                ref=AssertionRevisionRef(dep[0],key) if dep else None
                selected=self.proof.groups.get(ref)
                if not selected: return False
                record=self.snap.assertion(ref)
                return (all(cite(k) for k in (*record.applicability.evidence,*self.proof.retained_by.get(ref,())))
                        and group(self.proof.premises[ref][int(selected[0])]))
            data=json.loads(row[0]); source=(MemorySource if data['kind']=='memory' else CodeSource).from_json(data)
            span=Span.from_json(row[1]); citations.setdefault((source,span),Citation(key,source,span,bytes(row[2]).decode()))
            return all(cite(k) for k in (*self.proof.originals.get(key,()),*self.proof.retained_by.get(key,())))
        def group(g):
            if g in groups: return True
            groups.append(g)
            return len(groups)<=8 and all(cite(k) for k in g)
        semantic=st.path[-1][0]
        if semantic.predicate=='REGRESSION_FOR' and st.path[-1][1].direction=='inverse':
            attached=next((a for batch in self.expand(st,(step('DEFINED_IN'),),True) for a in batch),None)
            if attached: st=attached
        for r,s,g in st.path:
            if not group(g) or not all(cite(k) for k in r.applicability.evidence):
                self.limits.add('support_manifest'); return None
            disputes.update(dict.fromkeys(self.conflicts(r.ref)))
            edges.append(PathEdge(self.claim(r.ref),s,r.subject,r.object)); apps.append(r.applicability)
            if r.validity.kind=='interval': intervals.append(r.validity)
        validity=Validity('interval',max((v.valid_from for v in intervals),key=time_key),min((v.valid_to for v in intervals),key=time_key)) if intervals else Validity.unbounded()
        sets=[set(a.commits) for a in apps if a.kind=='commit_set']; commits=tuple(sorted(set.intersection(*sets))) if sets else ()
        app=Applicability('commit_set' if sets else 'project_wide',apps[0].repository_id,commits,tuple(dict.fromkeys(e for a in apps for e in a.evidence)))
        return EvidenceBundle(self.claim(semantic.ref),tuple(edges),tuple(groups),tuple(citations.values()),semantic.origin_kind,semantic.review_state,validity,app,tuple(disputes),(),bool(disputes))
