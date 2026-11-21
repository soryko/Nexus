from dataclasses import replace
import sqlite3
from graph_case import op
from nexus_memory.domain.graph import CurationBatch, EntityRef, EvidenceWithdrawal
from nexus_memory.domain.retrieval import RecallQuery, RelationStep, TraverseQuery


def test_lease_path_has_three_semantic_hops_and_file_proof(lease, reader, ctx, compound):
    pack = reader.recall(compound, ctx())
    assert pack.status == 'supported'
    bundle, = pack.bundles
    assert [(e.step.predicate, e.step.direction) for e in bundle.path] == [
        ('IMPLEMENTS','forward'), ('MOTIVATED_BY','forward'), ('REGRESSION_FOR','inverse'), ('DEFINED_IN','forward')]
    assert pack.proof_edges == 1 and pack.proof_edge_rows == 1 and pack.expansion_rows == 4
    assert {c.source for c in bundle.citations} == {lease.sources[k] for k in ('implements','motivation','regression','definition')}
    assert 'synthetic sentinel' not in pack.to_json()


def test_missing_semantic_support(lease, reader, ctx, compound):
    lease.service.curate(CurationBatch(withdrawals=(EvidenceWithdrawal(lease.receipt.id_mappings['motivation-source'],1),)), operation=op('remove'))
    pack = reader.recall(compound,ctx())
    assert pack.status == 'no_supported_path' and not pack.bundles
    assert (pack.proof_edge_rows, pack.proof_edges) == (0, 0)
    historic = reader.recall(RecallQuery(text='grace',mode='historical'),ctx())
    assert historic.status == 'incomplete' and 'historical_index_unavailable' in historic.limitations


def test_cycles_hubs_and_exact_pattern_cutoff(lease, reader, ctx):
    # Build the high-degree endpoint in the authoritative graph, then interleave
    # rejected projection rows ahead of it: neither rejections nor probes are free.
    from nexus_memory.domain.graph import EntityDraft, EntityRef
    ids=lease.receipt.id_mappings
    source=ids['implements-source']
    base=replace(lease.drafts[0],subject=EntityRef(ids['renew_lease']),support_groups=((source,),),applicability=replace(lease.drafts[0].applicability,evidence=(source,)))
    for offset in range(0,500,16):
        keys=[f'hub{i}' for i in range(offset,min(500,offset+16))]
        lease.service.curate(CurationBatch(tuple(EntityDraft(k,'fixture',k) for k in keys),tuple(replace(base,temp_id='e'+k,object=EntityRef(k)) for k in keys)),operation=op('hub'+str(offset)))
    # Pollute only the disposable adjacency projection: rejected rows and its probe cost reads.
    with sqlite3.connect(lease.repo.path) as db:
        db.executemany('INSERT INTO graph_adjacency SELECT space_id,?,rev,subject,predicate,object,active FROM graph_adjacency WHERE rev=? AND id=?',
            [(f'000{i}',lease.refs['implements'].revision_id,lease.refs['implements'].assertion_id) for i in range(500)])
    q = TraverseQuery(anchors=('renew_lease','D17'),pattern=(RelationStep('IMPLEMENTS','forward'),),exact=True)
    # Give the second group a real path with the same predicate.
    d=replace(lease.drafts[0],subject=EntityRef(ids['D17']),object=EntityRef(ids['renew_lease']),support_groups=((ids['implements-source'],),),applicability=replace(lease.drafts[0].applicability,evidence=(ids['implements-source'],)))
    lease.service.curate(CurationBatch(assertions=(d,replace(d,temp_id='also'))),operation=op('two'))
    pack=reader.traverse(q,ctx())
    assert pack.status=='incomplete' and pack.expansion_rows==19 and len(pack.bundles)==2
    # With two rows total, the rejected first seed cannot starve seed two.
    tiny=reader.traverse(replace(q,budget=replace(q.budget,expansion_rows=2)),ctx())
    assert tiny.expansion_rows==2 and tiny.bundles and tiny.status=='incomplete'
    # An inverse step can only revisit the seed; this is not a cutoff.
    cycle=reader.traverse(replace(q,anchors=('D17',),pattern=q.pattern+(RelationStep('IMPLEMENTS','inverse'),)),ctx())
    assert cycle.status=='no_supported_path' and not cycle.bundles


def mentions(lease, name, start, end, groups=None):
    ids = lease.receipt.id_mappings
    source = ids['implements-source']
    base = lease.drafts[0]
    return replace(base, temp_id=name, predicate='MENTIONS', subject=EntityRef(ids[start]),
                   object=EntityRef(ids[end]), support_groups=groups or ((source,),),
                   applicability=replace(base.applicability, evidence=(source,)))


def test_review_rank_summed_capped_degree(lease, reader, ctx):
    # Make the earlier-scanned branch the hub regardless of generated assertion IDs.
    branches = ('D17', 'I42')
    receipt = lease.service.curate(CurationBatch(assertions=tuple(
        mentions(lease, 'to'+b, 'D32', b) for b in branches)), operation=op('fork'))
    hub, sparse = sorted(branches, key=lambda b: receipt.id_mappings['to'+b])
    leaves = ('renew_lease', 'test.txt', 'test_rejoin_after_suspend')
    lease.service.curate(CurationBatch(assertions=(mentions(lease, 'sparse', sparse, leaves[0]), *(
        mentions(lease, f'hub{i}', hub, end) for i, end in enumerate(leaves)))), operation=op('leaves'))
    q = TraverseQuery(anchors=('D32',), pattern=(RelationStep('MENTIONS','forward'),)*2)
    q = replace(q, budget=replace(q.budget, edges_per_endpoint=2, finalist_bundles=1))
    context = ctx()
    p = reader.traverse(q, context)
    assert p.expansion_rows == 6 and 'edges_per_endpoint' in p.limitations  # fork + hub/probe + sparse
    assert p.bundles[0].path[0].object == EntityRef(lease.receipt.id_mappings[sparse])
    assert replace(reader.traverse(q, context), pack_id=p.pack_id) == p

    # Check the accumulated scores themselves: neither the cap probe nor a
    # sibling's scan position can contribute an extra unit to either hub path.
    from nexus_memory.retrieval.traversal import Search, State
    root = EntityRef(lease.receipt.id_mappings['D32'])
    with lease.repo.open_snapshot(lease.binding) as snap:
        all_paths = replace(q, budget=replace(q.budget, finalist_bundles=3))
        search = Search(snap, context, all_paths, set(), lambda: False)
        states = search.paths([State(0, root, frozenset((root,)))])
        assert [st.degree for st in states] == [3, 4, 4] and search.expanded == 6


def test_review_prune_distinct_support_signatures(lease, reader, ctx):
    ids = lease.receipt.id_mappings
    a, b = sorted((ids['implements-source'], ids['motivation-source']))
    edge = mentions(lease, 'alternative', 'D32', 'D17', ((a,), (a,), (b,)))
    lease.service.curate(CurationBatch(assertions=(edge,)), operation=op('alternatives'))
    q = TraverseQuery(anchors=('D32',), pattern=(RelationStep('MENTIONS','forward'),))
    q = replace(q, budget=replace(q.budget, alternative_states=2))
    context = ctx()
    p = reader.traverse(q, context)
    assert p.expansion_rows == 1
    assert [bundle.support_groups for bundle in p.bundles] == [((a,),), ((b,),)]
    assert p.status == 'supported' and replace(reader.traverse(q, context), pack_id=p.pack_id) == p


def test_review_optional_file_proof(lease, reader, ctx, compound):
    lease.service.curate(CurationBatch(withdrawals=(EvidenceWithdrawal(
        lease.receipt.id_mappings['definition-source'], 1),)), operation=op('withdraw-definition'))
    p = reader.recall(compound, ctx())
    assert p.status == 'supported'
    bundle, = p.bundles
    assert bundle.claim == lease.qualified['regression']
    assert [e.step.predicate for e in bundle.path] == ['IMPLEMENTS', 'MOTIVATED_BY', 'REGRESSION_FOR']
    assert {c.source for c in bundle.citations} == {lease.sources[k] for k in ('implements','motivation','regression')}
    assert (p.expansion_rows, p.proof_edge_rows, p.proof_edges) == (4, 1, 0)
