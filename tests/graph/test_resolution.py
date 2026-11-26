import pytest
from graph_case import case, op, batch
from nexus_memory.domain.errors import ResolutionConflict
from nexus_memory.domain.graph import EntityResolutionInput


def test_merge_split_preserves_original_ids(tmp_path):
    svc, repo, binding = case(tmp_path)
    r = svc.curate(batch(),operation=op('seed'))
    a,b = (r.id_mappings[k] for k in 'ab')
    change = EntityResolutionInput('merge',0,(b,),a)
    first = svc.resolve(change,operation=op('merge'))
    assert svc.resolve(change,operation=op('merge')) == first
    with pytest.raises(ResolutionConflict):
        svc.resolve(change,operation=op('duplicate'))
    with repo.open_snapshot(binding) as snap:
        assert snap.canonical(b) == a
        svc.resolve(EntityResolutionInput('split',1,(b,),a),operation=op('split'))
        assert snap.canonical(b) == a
    with repo.open_snapshot(binding) as snap:
        assert snap.canonical(b) == b
        assert snap.assertion(r.assertion_revisions[0]).object.entity_id == b
