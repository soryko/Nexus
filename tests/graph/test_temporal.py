from dataclasses import replace
from fixtures.v2.lease_case import context, lease_case
from graph_case import op
from nexus_memory.domain.graph import EntityRef, EntityResolutionInput, Validity
from nexus_memory.domain.retrieval import RecallBudget


def test_valid_time_and_known_time_are_independent(tmp_path):
    from nexus_memory.graph.eligibility import evaluate
    c=lease_case(tmp_path); ids=c.receipt.id_mappings; ref=c.refs['implements']
    def status(snap,r,commit):
        return evaluate(r,snap,context(c,snap,commit),RecallBudget()).status
    with c.repo.open_snapshot(c.binding) as snap:
        for commit,expected in ((c.R,('eligible','ineligible')),(c.M,('ineligible','eligible')),(c.U,('ineligible','ineligible'))):
            assert (status(snap,ref,commit),status(snap,c.refs['main'],commit))==expected
        before=snap.assertion(ref).recorded_at
    first=c.drafts[0]; valid=Validity('interval','2026-08-01T00:00:00Z','2026-09-01T00:00:00Z')
    new=replace(first,subject=EntityRef(ids['renew_lease']),object=EntityRef(ids['D17']),support_groups=((ids['implements-source'],),),applicability=replace(first.applicability,evidence=(ids['implements-source'],)),validity=valid)
    from fixtures.v2.lease_case import clock
    with clock('nexus_memory.graph.service','2026-11-01T12:00:00Z'):
        second,=c.service.correct(ref.assertion_id,ref.revision_id,new,operation=op('late')).assertion_revisions
    with clock('nexus_memory.graph.service','2026-11-02T00:00:00Z'):
        c.service.resolve(EntityResolutionInput('merge',0,(ids['D17'],),ids['D32']),operation=op('merge'))
    with clock('nexus_memory.graph.service','2026-11-03T00:00:00Z'):
        c.service.resolve(EntityResolutionInput('split',1,(ids['D17'],),ids['D32']),operation=op('split'))
    with c.repo.open_snapshot(c.binding) as snap:
        for known,selected in ((before,ref),(snap.assertion(second).recorded_at,second)):
            view=snap.historical(valid_as_of='2026-08-15T00:00:00Z',known_as_of=known)
            assert view.head(ref.assertion_id)==selected
            assert status(view,selected,c.R)=='eligible'
            assert view.canonical(ids['D17'])==ids['D17']
        end=snap.historical(valid_as_of=valid.valid_to,known_as_of=snap.assertion(second).recorded_at)
        assert status(end,second,c.R)=='ineligible'
        assert ref in view.historical(valid_as_of=valid.valid_from,known_as_of=before).adjacent(new.subject,('IMPLEMENTS',),None,20).refs


def test_scoped_suppression_uses_exact_endpoints_and_base_support(tmp_path):
    from nexus_memory.domain.graph import Applicability, AssertionRevisionRef, CurationBatch, SupersessionInput
    from nexus_memory.graph.eligibility import evaluate
    c=lease_case(tmp_path); ids=c.receipt.id_mappings; old=c.refs['implements']
    evidence=ids['motivation-source']; app=replace(c.drafts[0].applicability,evidence=(evidence,))
    draft=replace(c.drafts[0],subject=EntityRef(ids['renew_lease']),object=EntityRef(ids['D17']),temp_id='next',support_groups=((evidence,),),applicability=app)
    def supersede(predecessor,key):
        control=replace(draft,subject=AssertionRevisionRef('next','next'),predicate='SUPERSEDES',object=predecessor,temp_id='control')
        return c.service.supersede(SupersessionInput(predecessor,draft,control),operation=op(key)).assertion_revisions[0]
    def status(ref):
        with c.repo.open_snapshot(c.binding) as snap:
            return evaluate(ref,snap,context(c,snap,c.R),RecallBudget()).status
    middle=supersede(old,'middle'); last=supersede(middle,'last')
    assert [status(r) for r in (old,middle,last)]==['ineligible','ineligible','eligible']
    changed,=c.service.correct(last.assertion_id,last.revision_id,draft,operation=op('changed')).assertion_revisions
    assert [status(r) for r in (old,middle,last,changed)]==['ineligible','eligible','ineligible','eligible']
    for key,unknown in [('valid',replace(draft,validity=Validity.unknown())),('app',replace(draft,applicability=Applicability()))]:
        r,=c.service.curate(CurationBatch(assertions=(unknown,)),operation=op(key)).assertion_revisions
        assert status(r)=='unknown'


def exact_draft(c):
    ids = c.receipt.id_mappings
    source = ids['motivation-source']
    return replace(c.drafts[0], temp_id='next', subject=EntityRef(ids['renew_lease']),
                   object=EntityRef(ids['D17']), support_groups=((source,),),
                   applicability=replace(c.drafts[0].applicability, evidence=(source,)))


def test_retained_exact_replacement_suppresses_after_correction(tmp_path):
    from nexus_memory.domain.graph import AssertionRevisionRef, SupersessionInput
    from nexus_memory.graph.eligibility import evaluate
    c = lease_case(tmp_path)
    old = c.refs['implements']
    draft = exact_draft(c)
    control = replace(draft, temp_id='control', predicate='SUPERSEDES', object=old,
                      subject=AssertionRevisionRef('next', 'next'))
    replacement, edge = c.service.supersede(SupersessionInput(old, draft, control), operation=op('replace')).assertion_revisions
    c.service.correct(replacement.assertion_id, replacement.revision_id, draft, operation=op('correct'))
    c.service.retain(replacement, draft.applicability, operation=op('retain-exact'))
    with c.repo.open_snapshot(c.binding) as snap:
        assert snap.assertion(edge).subject == replacement
        result = evaluate(old, snap, context(c, snap, c.R), RecallBudget())
        assert result.status == 'ineligible' and 'superseded' in result.reasons
        assert evaluate(replacement, snap, context(c, snap, c.R), RecallBudget()).status == 'ineligible'


def test_support_groups_hide_future_revision(tmp_path):
    c = lease_case(tmp_path)
    with c.repo.open_snapshot(c.binding) as snap:
        ref = c.refs['implements']
        view = snap.historical(valid_as_of='2026-08-15T00:00:00Z', known_as_of='2026-08-31T00:00:00Z')
        assert snap.support_groups(ref) and view.assertion(ref) is None
        assert view.support_groups(ref) == ()


def test_chained_cutoffs_reselect_from_pinned_snapshot(tmp_path):
    from nexus_memory.domain.graph import CurationBatch
    from nexus_memory.domain.models import MemoryInput
    c = lease_case(tmp_path)
    with c.repo.open_snapshot(c.binding) as root:
        def at(view, cutoff):
            return view.historical(valid_as_of='2026-08-15T00:00:00Z', known_as_of=cutoff)
        early = at(root, '2026-08-01T00:00:00Z')
        assert (early.memory_event_seq, early.graph_event_seq) == (0, 0)
        later = '2099-01-01T00:00:00Z'
        c.memory.record(MemoryInput('post-pin'), 'post-pin')
        c.service.curate(CurationBatch(assertions=(exact_draft(c),)), operation=op('post-pin'))
        direct, chained = at(root, later), at(early, later)
        assert (chained.memory_event_seq, chained.graph_event_seq) == (direct.memory_event_seq, direct.graph_event_seq) == (root.memory_event_seq, root.graph_event_seq)
        assert chained.head(c.refs['implements'].assertion_id) == c.refs['implements']


def test_correction_rechecks_resolution_generation(tmp_path):
    import pytest
    from nexus_memory.domain.errors import ResolutionConflict
    c = lease_case(tmp_path)
    ids = c.receipt.id_mappings
    ref = c.refs['implements']
    draft = exact_draft(c)
    c.service.resolve(EntityResolutionInput('merge', 0, (ids['D17'],), ids['D32']), operation=op('merge'))
    def correct(**kwargs):
        return c.service.correct(ref.assertion_id, ref.revision_id, draft, operation=op('edit'), **kwargs)
    with pytest.raises(ResolutionConflict):
        correct()
    with pytest.raises(ResolutionConflict):
        correct(expected_resolution_generation=0)
    receipt = correct(expected_resolution_generation=1)
    c.service.resolve(EntityResolutionInput('split', 1, (ids['D17'],), ids['D32']), operation=op('split'))
    assert correct(expected_resolution_generation=1) == receipt
    with c.repo.open_snapshot(c.binding) as snap:
        assert snap.assertion(receipt.assertion_revisions[0]).object == draft.object
