from dataclasses import replace
import sqlite3
import pytest
from fixtures.v2.lease_case import context, lease_case
from graph_case import op
from nexus_memory.domain.graph import CurationBatch, EvidenceWithdrawal
from nexus_memory.domain.errors import RevisionConflict
from nexus_memory.domain.retrieval import RecallBudget


def test_alternative_support_and_whole_summary_invalidation(tmp_path):
    from nexus_memory.graph.eligibility import evaluate
    c=lease_case(tmp_path); ids=c.receipt.id_mappings
    a,b,d,att=(ids[n+'-source'] for n in ('implements','motivation','regression','definition'))
    draft=replace(c.drafts[0],subject=c.drafts[0].subject.__class__(ids['renew_lease']),object=c.drafts[0].object.__class__(ids['D17']),applicability=replace(c.drafts[0].applicability,evidence=(att,)),support_groups=((a,b),(d,)))
    primary,summary=c.service.curate(CurationBatch(assertions=(draft,replace(draft,temp_id='summary',support_groups=((a,b),)))),operation=op('alternatives')).assertion_revisions
    def check(ref):
        with c.repo.open_snapshot(c.binding) as snap:
            return evaluate(ref,snap,context(c,snap,c.R),RecallBudget())
    def withdraw(e,key):
        return c.service.curate(CurationBatch(withdrawals=(EvidenceWithdrawal(e,1),)),operation=op(key))
    assert len(check(primary).eligible_support_groups)==2
    human,=c.service.curate(CurationBatch(assertions=(replace(draft,support_groups=((a,),(b,))),)),operation=op('independent')).assertion_revisions
    withdraw(a,'remove-a')
    assert check(human).status=='eligible'
    with c.repo.open_snapshot(c.binding) as snap:
        assert snap.assertion(human).origin_kind=='human_asserted'
    with pytest.raises(RevisionConflict): withdraw(a,'again')
    with sqlite3.connect(c.repo.path) as db: db.execute('UPDATE graph_adjacency SET active=1')
    assert check(primary).status=='eligible' and check(summary).status=='ineligible'
    assert check(c.refs['regression']).status=='eligible'
    withdraw(d,'remove-c'); assert check(primary).status=='ineligible'
    assert check(c.refs['motivation']).status=='eligible'
    # Withdrawing only applicability invalidates an otherwise complete group.
    edge=c.service.curate(CurationBatch(assertions=(replace(draft,support_groups=((b,),)),)),operation=op('att')).assertion_revisions[0]
    withdraw(att,'remove-att'); assert check(edge).status=='ineligible'


def test_retention_and_derived_dependencies_do_not_survive_removal(tmp_path):
    from nexus_memory.graph.eligibility import evaluate
    from nexus_memory.domain.models import MemoryInput
    from nexus_memory.domain.errors import InvalidRelation
    c=lease_case(tmp_path); ids=c.receipt.id_mappings
    a,b,d=(ids[n+'-source'] for n in ('implements','motivation','definition'))
    ref=c.refs['implements']; app=replace(c.drafts[0].applicability,evidence=(b,))
    def check(view=None):
        with c.repo.open_snapshot(c.binding) as snap:
            if view: snap=snap.historical(valid_as_of='2026-08-15T00:00:00Z',known_as_of=view)
            return evaluate(ref,snap,context(c,snap,c.R),RecallBudget()).status
    receipt=c.writes['implements']
    from fixtures.v2.lease_case import clock
    with clock('nexus_memory.storage.sqlite','2026-10-01T00:00:00Z'):
        revised=c.memory.revise(receipt.memory_id,receipt.revision_id,MemoryInput('corrected lease'), 'correct-source')
    assert check()=='ineligible'
    with pytest.raises(InvalidRelation):
        c.service.retain(a,replace(app,evidence=(a,)),operation=op('self'))
    with clock('nexus_memory.graph.service','2026-11-01T00:00:00Z'):
        c.service.retain(a,app,operation=op('retain'))
    assert check()=='eligible'
    assert check('2026-09-15T00:00:00Z')=='eligible'  # original memory head
    assert check('2026-10-15T00:00:00Z')=='ineligible'  # no future attestation
    with pytest.raises(InvalidRelation):
        c.service.retain(b,replace(app,evidence=(a,)),operation=op('cross-cycle'))
    item=replace(c.evidence[1],target=c.refs['motivation'].revision_id,temp_id='summary')
    derived=c.service.curate(CurationBatch(evidence=(item,)),derived_sources={'summary':(d,)},operation=op('derived'))
    c.service.retain(a,replace(app,evidence=(derived.id_mappings['summary'],)),operation=op('retain-derived'))
    c.service.curate(CurationBatch(withdrawals=(EvidenceWithdrawal(d,1),)),operation=op('drop-required'))
    assert check()=='ineligible'
    c.memory.forget(receipt.memory_id,revised.revision_id,'forget')
    with c.repo.open_snapshot(c.binding) as snap:
        view=snap.historical(valid_as_of='2026-08-15T00:00:00Z',known_as_of=snap.assertion(ref).recorded_at)
        assert evaluate(ref,view,context(c,view,c.R),RecallBudget()).status=='ineligible'
        assert view.evidence(ref)==()


def test_derived_span_hidden_after_original_withdrawal(tmp_path):
    c = lease_case(tmp_path)
    ref = c.refs['motivation']
    original = c.receipt.id_mappings['definition-source']
    item = replace(c.evidence[1], target=ref.revision_id, temp_id='summary')
    receipt = c.service.curate(CurationBatch(evidence=(item,)), derived_sources={'summary': (original,)}, operation=op('summary'))
    summary = receipt.id_mappings['summary']
    def visible(snap):
        return {e.evidence_id for e in snap.evidence(ref)}
    with c.repo.open_snapshot(c.binding) as pinned:
        assert summary in visible(pinned)
        c.service.curate(CurationBatch(withdrawals=(EvidenceWithdrawal(original, 1),)), operation=op('withdraw'))
        assert summary in visible(pinned)
    with c.repo.open_snapshot(c.binding) as fresh:
        assert visible(fresh) == {c.receipt.id_mappings['motivation-source']}


def test_forged_active_projection_and_cyclic_premise_cannot_prove_claim(tmp_path):
    from nexus_memory.graph.eligibility import evaluate
    from nexus_memory.domain.graph import EntityRef
    c=lease_case(tmp_path); ids=c.receipt.id_mappings; old=c.refs['implements']
    evidence=ids['motivation-source']
    replacement=replace(c.drafts[0],subject=EntityRef(ids['renew_lease']),object=EntityRef(ids['D17']),
        support_groups=((evidence,),),applicability=replace(c.drafts[0].applicability,evidence=(evidence,)))
    new,=c.service.correct(old.assertion_id,old.revision_id,replacement,operation=op('correct')).assertion_revisions
    with sqlite3.connect(c.repo.path) as db:
        db.execute('UPDATE graph_adjacency SET active=1 WHERE rev=?',(old.revision_id,))
        db.execute('UPDATE graph_support_premises SET evidence=NULL,dependency=rev WHERE rev=?',(new.revision_id,))
    with c.repo.open_snapshot(c.binding) as snap:
        for ref,reason in ((old,'not_selected_head'),(new,'cyclic_dependency')):
            result=evaluate(ref,snap,context(c,snap,c.R),RecallBudget())
            assert result.status=='ineligible' and reason in result.reasons
