from dataclasses import replace
import pytest
from fixtures.v2.lease_case import lease_case, span
from graph_case import op
from nexus_memory.domain.errors import InvalidEvidence, InvalidInput
from nexus_memory.domain.graph import CurationBatch, Span
from nexus_memory.storage.graph_sqlite import GraphRepository


@pytest.mark.parametrize('fmt', ['sha1','sha256'])
def test_unicode_span_roundtrip_and_limits(tmp_path,fmt):
    from nexus_memory.graph.evidence import capture_git_span
    c=lease_case(tmp_path,fmt); binding=(c.bound,c.verifier)
    ref=c.refs['definition']; item=c.evidence[3]; cut=c.data.index('é'.encode())
    exact=span(c.data,cut,len(c.data)); item=replace(item,target=ref.revision_id,span=exact)
    r=c.service.curate(CurationBatch(evidence=(item,)),operation=op('slice'))
    with pytest.raises(InvalidEvidence):
        capture_git_span(binding,c.R,c.path,span(c.data,cut+1,len(c.data)))
    for path in ('missing','big'):
        with pytest.raises(InvalidEvidence):
            capture_git_span(binding,c.R,path,span(c.data,0,1))
    with pytest.raises(InvalidEvidence):
        c.service.curate(CurationBatch(evidence=(replace(item,source=replace(item.source,blob_oid=c.M)),)),operation=op('wrong'))
    with pytest.raises(InvalidInput): Span(0,65537,'0'*64)
    c.verifier.git.executable='no-such-git'
    with GraphRepository(c.repo.path).open_snapshot(c.binding) as snap:
        saved,=filter(lambda e:e.evidence_id==r.id_mappings[item.temp_id],snap.evidence(ref))
        assert saved.captured_bytes==c.data[cut:] and saved.span==exact
