"""Single-mount model surface. Success always means catalog-admitted queue bytes."""
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, UTC
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock
from time import monotonic

from nexus_memory.domain.errors import (InvalidInput, InvalidRetrievalBudget, NexusError, OperationFailure,
    PublicationBusy, StalePack, StorageIntegrityError, UnsupportedCapability)
from nexus_memory.domain.graph import AssertionDraft, CurationBatch
from nexus_memory.domain.models import MemoryInput, ReferenceInput, SearchQuery
from nexus_memory.domain.retrieval import PackSnapshot, RecallQuery, TraverseQuery, TrustedQueryContext
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.graph.service import GraphService
from nexus_memory.memory import MemoryService
from nexus_memory.retrieval.service import RecallService
from nexus_memory.spaces.authority import AuthorityFence
from nexus_memory.spaces.catalog import _identity
from nexus_memory.spaces.queue import PublicationQueue
from nexus_memory.storage.graph_sqlite import GraphRepository
from nexus_memory.storage.repository import WriteContext
from nexus_memory.storage.sqlite import SQLiteRepository


class ExactReader(SQLiteRepository):
    SCHEMA_VERSION = _target_schema_version = 6


EXACT_WRITES = {'record','revise','forget'}
GRAPH_WRITES = {'curate','correct'}
PACK_READS = {'explain','read_pack'}
ROUTES = EXACT_WRITES | GRAPH_WRITES | PACK_READS | {'get','history','search','status','recall','traverse'}


def failure(error):
    code = error.code if isinstance(error,NexusError) else 'invalid_input'
    try:
        return OperationFailure(code,code in {'publication_busy','authority_busy'})
    except InvalidInput:
        return OperationFailure('invalid_input',False)


class Broker:
    def __init__(self,catalog,grant,*,clock=monotonic,checkout=None,verifier=None):
        self.catalog,self.grant,self.clock = catalog,grant,clock
        self.binding,locator = catalog._resolve(grant,None,False)
        self.path = Path(locator)
        self.checkout = checkout
        self.queue = PublicationQueue(catalog)
        self.fence = AuthorityFence(catalog,self.queue)
        self.repo = ExactReader.open_readonly(self.path)
        self.memory = MemoryService(self.repo,self.binding.scope,checkout.repository if checkout else None,verifier)
        self.graph = GraphRepository(self.path)
        self.recall = RecallService(self.graph,self.binding,clock=clock)
        self.times = OrderedDict()
        self.lock = RLock()
        self.native = False
        self.allowance = None

    def _context(self,host_time):
        with self.graph.open_snapshot(self.binding) as snap:
            seq = (snap.memory_event_seq,snap.graph_event_seq)
            digest = sha256(json.dumps([snap.store_id,*seq]).encode()).hexdigest()
            epoch = self.grant.epoch
            mark = PackSnapshot(snap.store_id,seq[0],digest,'stage-a',seq[1],epoch,1)
        return TrustedQueryContext(self.checkout.repository.repository_id if self.checkout else None,
            self.checkout.resolved_commit if self.checkout else None,host_time,(mark,),epoch,1)

    def _validate(self,admission):
        row = admission.space
        if self.path != Path(row['locator']) or _identity(self.path) != (row['dev'],row['ino']):
            raise StorageIntegrityError('store changed')

    def drain(self,env):
        payload = self.queue.drain(env.publication_id, expected=env)
        if (env.grant_id,env.epoch) != (self.grant.grant_id,self.grant.epoch) or payload != env.payload or sha256(payload).hexdigest() != env.response_digest:
            raise PublicationBusy('publication unavailable')
        return payload

    def _write(self,name,args):
        key = args.pop('idempotency_key')
        ctx = WriteContext(self.binding.space_id,OperationRef(self.grant.writer_stream_id,key),
                           self.grant.principal_id,session_id=self.grant.session_id)
        target = {} if name=='record' else {k:args.pop(k) for k in ('memory_id','expected_revision_id')}
        if name=='forget':
            if args: raise InvalidInput('invalid arguments')
        else:
            refs = tuple(ReferenceInput(**ref) for ref in args.pop('references',()))
            target['input'] = MemoryInput(**args,references=refs)
        prepared = getattr(self.memory,'prepare_'+name)(**target,idempotency_key=key,context=ctx)
        return lambda admission: admission.exact(self.repo,prepared)

    def _graph_write(self,name,args):
        operation = OperationRef(self.grant.writer_stream_id,args.pop('operation_key'))
        if name=='curate':
            if set(args) != {'batch'}: raise InvalidInput('invalid arguments')
            args['batch'] = CurationBatch.from_json(args['batch'])
        else:
            args['replacement'] = AssertionDraft.from_json(args['replacement'])
        git_binding = (self.checkout.repository,self.memory.verifier) if self.checkout else None
        preparer = GraphService(self.graph,self.binding,git_binding=git_binding,prepare_only=True)
        return getattr(preparer,name)(**args,operation=operation)

    def _plan(self,name,args,handle):
        if name in EXACT_WRITES: return self._write(name,args)
        if name in GRAPH_WRITES: return self._graph_write(name,args)
        if name in PACK_READS or name in {'recall','traverse'}:
            if self.native and self.allowance is None: raise InvalidRetrievalBudget('no trusted turn')
            if name in PACK_READS:
                if set(args) != {'pack_id'}: raise InvalidInput('invalid arguments')
                pack_id = args['pack_id']
                if pack_id not in self.times: raise StalePack('pack unavailable')
                timestamp = self.times[pack_id]
            else:
                if set(args) != {'query'}: raise InvalidInput('invalid arguments')
                query = (RecallQuery if name=='recall' else TraverseQuery).from_json(args['query'])
                timestamp = datetime.now(UTC).isoformat().replace('+00:00','Z')
                ctx = self._context(timestamp)
                run = lambda q: getattr(self.recall,name)(q,ctx)
                pack = self.allowance.search(query,run) if self.allowance else run(query)
                pack_id = pack.pack_id
                self.times[pack_id] = timestamp
                if len(self.times)>32: self.times.popitem(last=False)
            def checked(_):
                pack = self.recall.explain(pack_id,self._context(timestamp))
                return {'pack_id':pack.pack_id} if handle else pack
            return checked
        if name=='search': return lambda _: self.memory.search(SearchQuery(**args))
        if name=='status':
            if args: raise InvalidInput('invalid arguments')
            return lambda _: {**asdict(self.memory.status()),'lifecycle':'native' if self.native else 'generic_explicit'}
        if name in {'get','history'}: return lambda _: getattr(self.memory,name)(**args)
        raise UnsupportedCapability('unknown operation')

    def _dispatch(self,name,args,*,handle=False):
        with self.lock:
            try:
                plan = self._plan(name,dict(args),handle)
                charge = not handle and name in PACK_READS | {'recall','traverse'} and self.allowance
                def commit(admission):
                    obj = plan(admission)
                    if hasattr(obj,'to_json'): payload = obj.to_json().encode()
                    else: payload = json.dumps(obj if isinstance(obj,dict) else asdict(obj),ensure_ascii=False,separators=(',',':')).encode()
                    if charge: charge.check_output(len(payload))
                    return payload
                sid = self.binding.space_id
                env = self.fence.publish(self.grant,self.queue.reserve(1<<20,monotonic()+2),source_space_ids=(sid,),
                    destination_space_id=sid if name in EXACT_WRITES | GRAPH_WRITES else None,
                    validate_sources=self._validate,commit=commit)
                if charge: charge.bytes -= len(env.payload)
                return env
            except (KeyError,TypeError,ValueError,NexusError) as exc:
                return failure(exc)
            except Exception:
                return OperationFailure('internal_error',False)

    def dispatch(self,name,args):
        return self._dispatch(name,args)

    def prepare_handle(self,query):
        return self._dispatch('recall',{'query':query.to_json()},handle=True)
