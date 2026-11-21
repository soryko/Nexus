from dataclasses import replace
import sqlite3
import pytest
from nexus_memory.domain.errors import StalePack
from nexus_memory.domain.models import MemoryInput
from test_packs import disputes


@pytest.mark.parametrize('mutation',['correction','dispute','epoch','unmount'])
def test_cached_pack_stales_after_correction_or_new_dispute(lease, reader, ctx, compound, mutation):
    context=ctx(); p=reader.recall(compound,context)
    assert reader.explain(p.pack_id,context)==p
    if mutation=='correction':
        w=lease.writes['implements']; lease.memory.revise(w.memory_id,w.revision_id,MemoryInput('corrected'),'fix')
    elif mutation=='dispute': disputes(lease,1)
    elif mutation=='unmount':
        with sqlite3.connect(lease.repo.path) as db: db.execute('DELETE FROM space_partitions WHERE space_id=?',(lease.binding.space_id,))
    else: context=replace(context,authority_epoch=1,snapshots=(replace(context.snapshots[0],authority_epoch=1),))
    with pytest.raises(StalePack): reader.explain(p.pack_id,context)


def test_expiry_and_restart(lease,ctx,compound):
    from nexus_memory.retrieval.service import RecallService
    now=[0]; service=RecallService(lease.repo,lease.binding,clock=lambda:now[0]); p=service.recall(compound,ctx())
    with pytest.raises(StalePack): RecallService(lease.repo,lease.binding).explain(p.pack_id,ctx())
    now[0]=900
    with pytest.raises(StalePack): service.explain(p.pack_id,ctx())
