from dataclasses import replace
from graph_case import op
from nexus_memory.domain.graph import CurationBatch
from nexus_memory.domain.retrieval import RecallQuery


def disputes(c, n, accepted=True):
    e=c.receipt.id_mappings['motivation-source']
    draft=replace(c.drafts[0], predicate='DISPUTES',subject=c.refs['motivation'],object=c.refs['implements'],support_groups=((e,),),applicability=replace(c.drafts[0].applicability,evidence=(e,)),review_state='accepted' if accepted else 'proposed')
    for start in range(0,n,16):
        c.service.curate(CurationBatch(assertions=tuple(replace(draft,temp_id=f'd{i}') for i in range(start,min(start+16,n)))),operation=op(f'disputes{start}'))


def test_low_rank_dispute_is_not_hidden(lease, reader, ctx, compound):
    disputes(lease,1)
    p=reader.recall(compound,ctx())
    assert p.status=='contested' and p.bundles[0].disputes and p.bundles[0].contested


def test_conflict_lookup_exhaustion_is_incomplete(lease, reader, ctx):
    disputes(lease,129,False)
    p=reader.recall(RecallQuery(anchors=('renew_lease',)),ctx())
    assert p.status=='incomplete' and p.conflict_rows==128 and 'conflict_rows' in p.limitations
    assert not any(b.contested for b in p.bundles)


def test_pack_omits_entire_unsupported_or_oversized_bundle(lease, reader, ctx, compound):
    p=reader.recall(compound,ctx())
    assert 'café' in p.to_json() and p.serialized_utf8_bytes==len(p.to_json().encode())<=6400
    assert p.token_count_kind=='estimated' and p.token_count==(p.serialized_utf8_bytes+3)//4
    small=reader.recall(replace(compound,budget=replace(compound.budget,serialized_bytes=1024)),ctx())
    assert small.status=='incomplete' and not small.bundles and small.serialized_utf8_bytes<=1024
    from nexus_memory.retrieval.service import RecallService
    class Words:
        def count(self,text): return len(text.split())
    p=RecallService(lease.repo,lease.binding,token_counter=Words()).recall(compound,ctx())
    assert p.token_count_kind=='exact' and p.token_count==len(p.to_json().split())


def test_lexical_anchor_is_scoped(lease, reader, ctx):
    assert reader.recall(RecallQuery(text='suspended'),ctx()).status=='supported'
    assert reader.recall(RecallQuery(text='sentinel'),ctx()).status=='no_eligible_matches'
