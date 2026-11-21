"""Disposable, local lease acceptance case. No worker, transport or owner catalog."""
from dataclasses import replace
from hashlib import sha256
from datetime import datetime
from unittest.mock import patch
import os
import subprocess
from types import SimpleNamespace

from graph_case import case, op
from nexus_memory.domain.graph import (Applicability, AssertionDraft, CodeSource, CurationBatch,
    EntityDraft, EntityRef, EvidenceInput, MemorySource, Span, Validity)
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.domain.spaces import MemoryRef
from nexus_memory.domain.retrieval import ClaimRevision, PackSnapshot, TrustedQueryContext
from nexus_memory.git import GitCli, bind_repository
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


def clock(module, stamp):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(stamp).astimezone(tz)
    return patch(module+'.datetime', Fixed)


def span(data, start=0, end=None):
    end = len(data) if end is None else end
    return Span(start, end, sha256(data[start:end]).hexdigest())


def context(c, snapshot, commit):
    pack = PackSnapshot(c.binding.store_id, snapshot.memory_event_seq, '0'*64, 'fixture', snapshot.graph_event_seq)
    return TrustedQueryContext(c.bound.repository_id, commit, '2026-08-15T00:00:00Z', (pack,), 0, 0)


def lease_case(tmp_path, object_format='sha1'):
    from nexus_memory.graph.service import GraphService
    _, repo, binding = case(tmp_path)
    store = SQLiteRepository(repo.path, owner_schema6=True, configured_scope=binding.scope)
    root = tmp_path/'git'; root.mkdir()
    def git(*args):
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        return subprocess.check_output(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],cwd=root,env=env).decode().strip()
    git('init','-q','-b','release','--object-format='+object_format)
    data = 'test_rejoin_after_suspend: café e\u0301'.encode()
    path = 'test.txt'
    (root/path).write_bytes(data); (root/'big').write_bytes(b'x'*(2**20+1))
    git('add','.'); git('commit','-qm','release'); R=git('rev-parse','HEAD'); blob=git('rev-parse','HEAD:'+path)
    git('switch','-qc','main'); git('commit','--allow-empty','-qm','main'); M=git('rev-parse','HEAD')
    git('switch','-qc','unseen'); git('commit','--allow-empty','-qm','unseen'); U=git('rev-parse','HEAD')
    bound, verifier = bind_repository(store,binding.scope,root,None,GitCli())
    binding = replace(binding,repository_bindings=(bound,))
    service = GraphService(repo,binding,git_binding=(bound,verifier))
    memory = MemoryService(store,binding.scope)
    sentinel = MemoryService(store, Scope('unmounted','local')).record(MemoryInput('synthetic sentinel X'), 'sentinel')
    entities = ('renew_lease','D17','I42','test_rejoin_after_suspend','test.txt','D32')
    drafts=[]; evidence=[]; writes={}; sources={}
    for name,s,p,o,text,commit in (
        ('implements',0,'IMPLEMENTS',1,'D17: release retains a 30-second grace period.',R),
        ('motivation',1,'MOTIVATED_BY',2,'A suspended worker may rejoin during grace.',R),
        ('regression',3,'REGRESSION_FOR',2,'test_rejoin_after_suspend guards I42.',R),
        ('definition',3,'DEFINED_IN',4,None,R),
        ('main',0,'IMPLEMENTS',5,'D32: main removes the grace period.',M)):
        if text:
            with clock('nexus_memory.storage.sqlite','2026-08-10T09:00:00Z'):
                receipt=memory.record(MemoryInput(text,kind='decision'),name)
            writes[name]=receipt
            source=MemorySource(MemoryRef(binding.space_id,receipt.memory_id,receipt.revision_id)); raw=text.encode()
        else:
            source=CodeSource(bound.repository_id,R,path,blob); raw=data
        sources[name]=source
        key=name+'-source'; evidence.append(EvidenceInput(name,'support',source,span(raw),key))
        drafts.append(AssertionDraft(EntityRef(entities[s]),p,EntityRef(entities[o]),origin_kind='human_asserted',review_state='accepted',validity=Validity.unbounded(),applicability=Applicability('commit_set',bound.repository_id,(commit,),(key,)),support_groups=((key,),),temp_id=name))
    batch=CurationBatch(tuple(EntityDraft(e,'fixture',e) for e in entities),tuple(drafts),tuple(evidence))
    with clock('nexus_memory.graph.service','2026-09-01T12:00:00Z'):
        receipt=service.curate(batch,operation=op('lease-graph'))
    refs=dict(zip((d.temp_id for d in drafts),receipt.assertion_revisions,strict=True))
    qualified = {key:ClaimRevision(binding.space_id,ref.assertion_id,ref.revision_id) for key,ref in refs.items()}
    return SimpleNamespace(repo=repo,binding=binding,bound=bound,verifier=verifier,service=service,
        memory=memory,writes=writes,sources=sources,sentinel=sentinel,refs=refs,qualified=qualified,
        receipt=receipt,evidence=evidence,drafts=drafts,path=path,data=data,R=R,M=M,U=U)
