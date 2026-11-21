from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import pytest
from graph_case import case, op, batch
from nexus_memory.domain.graph import (AssertionDraft, AssertionRevisionRef, EntityRef, LiteralRef,
                                        CurationBatch, EvidenceInput, MemorySource, Span)
from nexus_memory.domain.spaces import MemoryRef
from nexus_memory.domain.errors import InvalidRelation, InvalidInput, InvalidEvidence, RevisionConflict, IdempotencyConflict


def test_curation_is_atomic_and_idempotent(tmp_path):
    svc, repo, b = case(tmp_path)
    bad = replace(batch(), evidence=(EvidenceInput('edge','support',MemorySource(MemoryRef(b.space_id,'missing','r')),Span(0,1,'0'*64)),))
    with pytest.raises(InvalidEvidence):
        svc.curate(bad, operation=op('one'))
    with sqlite3.connect(repo.path) as db:
        for table in ('graph_entities','graph_assertions','graph_events','graph_operations'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    receipt = svc.curate(batch(), operation=op('one'))
    assert svc.curate(batch(), operation=op('one')) == receipt
    with pytest.raises(IdempotencyConflict):
        svc.curate(CurationBatch(), operation=op('one'))


def test_two_corrections_from_same_head(tmp_path):
    svc, repo, binding = case(tmp_path)
    first = svc.curate(batch(), operation=op('one'))
    ref, = first.assertion_revisions
    def stored_revision():
        with sqlite3.connect(repo.path) as db:
            return db.execute('SELECT * FROM graph_assertion_revisions WHERE space_id=? AND id=? AND rev=?',
                              (binding.space_id,ref.assertion_id,ref.revision_id)).fetchone()
    original = stored_revision()
    def correct(key):
        try:
            return svc.correct(ref.assertion_id,ref.revision_id,AssertionDraft(EntityRef(first.id_mappings['a']),'IMPORTS',EntityRef(first.id_mappings['b'])),operation=op(key))
        except RevisionConflict:
            return None
    with repo.open_snapshot(binding) as snap:
        old = snap.assertion(ref)
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(correct, ['two','three']))
        winner, = [r for r in outcomes if r]
        assert snap.assertion(winner.assertion_revisions[0]) is None
    with repo.open_snapshot(binding) as snap:
        assert stored_revision() == original
        assert snap.assertion(ref) == old
        assert snap.adjacent(old.subject, ('CALLS',), None, 2).refs == ()
        assert snap.adjacent(old.subject, ('IMPORTS',), None, 2).refs == winner.assertion_revisions


def test_graph_receipt_has_exact_revision_mappings(tmp_path):
    svc, repo, binding = case(tmp_path)
    r = svc.curate(batch(),operation=op('one'))
    ref, = r.assertion_revisions
    assert r.id_mappings['edge'] == ref.assertion_id and r.graph_event_seq == 1
    with repo.open_snapshot(binding) as snap:
        assert snap.assertion(ref).subject == EntityRef(r.id_mappings['a'])


def test_typed_endpoints_and_cross_space_refs_are_rejected(tmp_path):
    svc, repo, binding = case(tmp_path)
    for pred in ('IMPLEMENTS','MOTIVATED_BY','REGRESSION_FOR','DEFINED_IN','CALLS','IMPORTS','DEPENDS_ON','MENTIONS','SUPPORTS','DISPUTES','SUPERSEDES','APPLIES_TO'):
        with pytest.raises(InvalidInput):
            AssertionDraft(EntityRef('s'),pred,LiteralRef('str','x'))
    with pytest.raises(InvalidRelation):
        svc.curate(replace(batch(), assertions=(AssertionDraft(EntityRef('a'),'CALLS',EntityRef('foreign')),)),operation=op('bad'))
    with pytest.raises(InvalidEvidence):
        svc.curate(replace(batch(),evidence=(EvidenceInput('edge','support',MemorySource(MemoryRef('foreign','m','r')),Span(0,1,'0'*64)),)),operation=op('bad'))


def test_cyclic_premises_and_supersession_rejected(tmp_path):
    svc, _, _ = case(tmp_path)
    draft, = batch().assertions
    with pytest.raises(InvalidRelation):
        svc.curate(replace(batch(), assertions=(replace(draft,support_groups=(('other',),)),replace(draft,temp_id='other',support_groups=(('edge',),)))),operation=op('cycle'))
    a,b = svc.curate(replace(batch(),assertions=(draft,replace(draft,temp_id='other'))),operation=op('seed')).assertion_revisions
    with pytest.raises(InvalidRelation):
        svc.curate(CurationBatch(assertions=(AssertionDraft(a,'SUPERSEDES',b),AssertionDraft(b,'SUPERSEDES',a))),operation=op('cycle2'))


def test_schema_six_is_explicit_and_all_keys_are_partitioned(tmp_path):
    from nexus_memory.storage.sqlite import SQLiteRepository
    from nexus_memory.domain.errors import UnsupportedSchema
    _, repo, _ = case(tmp_path)
    for kwargs in ({}, {'owner_schema5':True}):
        with pytest.raises(UnsupportedSchema):
            SQLiteRepository(repo.path, **kwargs)
    with sqlite3.connect(repo.path) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name GLOB 'graph_*' OR name='projection_jobs')")
        for table, in tables:
            for _, index, *_ in db.execute(f'PRAGMA index_list({table})'):
                assert db.execute(f'PRAGMA index_info({index})').fetchone()[2] == 'space_id', index
            for fk, in db.execute('SELECT distinct id FROM pragma_foreign_key_list(?)',(table,)):
                assert db.execute('SELECT "from" FROM pragma_foreign_key_list(?) WHERE id=? AND seq=0',(table,fk)).fetchone()[0] == 'space_id'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_real_foreign_partition_and_cursors(tmp_path):
    from nexus_memory.domain.errors import CursorExpired
    from nexus_memory.domain.graph import EntityDraft
    from nexus_memory.domain.models import Scope
    from nexus_memory.graph.service import GraphService
    svc,repo,b = case(tmp_path)
    with sqlite3.connect(repo.path) as db:
        db.execute('INSERT INTO space_partitions VALUES(?,?,?,?)',(b.store_id,'other','local','other-space'))
    other = replace(b,scope=Scope('other','local'),space_id='other-space')
    first = svc.curate(batch(),operation=op('seed'))
    a = EntityRef(first.id_mappings['a'])
    d = AssertionDraft(a,'CALLS',a)
    with pytest.raises(InvalidRelation):
        GraphService(repo,other).curate(CurationBatch(assertions=(d,)),operation=op('foreign'))
    svc.curate(CurationBatch(assertions=(d,)),operation=op('second'))
    with repo.open_snapshot(b) as snap:
        page = snap.adjacent(a,('CALLS',),None,1)
        assert page.next_cursor
        assert snap.adjacent(a,('CALLS',),page.next_cursor,1).refs != page.refs
    with repo.open_snapshot(other) as snap:
        assert snap.assertion(first.assertion_revisions[0]) is None
        assert not snap.adjacent(a,('CALLS',),None,1).refs
        with pytest.raises(CursorExpired):
            snap.adjacent(a,('CALLS',),page.next_cursor,1)
    svc.curate(CurationBatch(entities=(EntityDraft('c','symbol','c'),)),operation=op('advance'))
    with repo.open_snapshot(b) as snap, pytest.raises(CursorExpired):
        snap.adjacent(a,('CALLS',),page.next_cursor,1)


def test_supersede_records_two_distinct_exact_revisions(tmp_path):
    from nexus_memory.domain.graph import SupersessionInput
    svc,repo,b = case(tmp_path)
    seed = svc.curate(batch(),operation=op('one'))
    old, = seed.assertion_revisions
    a = EntityRef(seed.id_mappings['a'])
    replacement = AssertionDraft(a,'MENTIONS',a,temp_id='next')
    control = AssertionDraft(AssertionRevisionRef('next','next'),'SUPERSEDES',old,support_groups=((old.revision_id,),))
    change = SupersessionInput(old,replacement,control)
    receipt = svc.supersede(change,operation=op('replace'))
    assert svc.supersede(change,operation=op('replace')) == receipt
    new,edge = receipt.assertion_revisions
    assert len({old.assertion_id,new.assertion_id,edge.assertion_id}) == 3
    with repo.open_snapshot(b) as snap:
        assert snap.assertion(edge).subject == new and snap.assertion(edge).object == old
        assert snap.control_relations(old,None,1).refs == (edge,)
        assert snap.support_groups(edge) == ((old.revision_id,),)


@pytest.mark.parametrize('pred',('IMPLEMENTS','MOTIVATED_BY','REGRESSION_FOR','DEFINED_IN','CALLS','IMPORTS','DEPENDS_ON','MENTIONS','SUPPORTS','DISPUTES','SUPERSEDES','APPLIES_TO'))
def test_closed_endpoint_matrix(pred):
    entity = EntityRef('e')
    rev = AssertionRevisionRef('a','r')
    for s in (entity,rev,LiteralRef('str','x')):
        for o in (entity,rev,LiteralRef('str','x')):
            valid = ((pred in ('SUPPORTS','DISPUTES','SUPERSEDES') and s==o==rev) or
                     (pred=='MENTIONS' and s in (entity,rev) and o==entity) or
                     (pred in ('IMPLEMENTS','MOTIVATED_BY','REGRESSION_FOR','DEFINED_IN','CALLS','IMPORTS','DEPENDS_ON') and s==o==entity))
            if valid:
                assert AssertionDraft(s,pred,o).predicate == pred
            else:
                with pytest.raises(InvalidInput):
                    AssertionDraft(s,pred,o)
