"""Single-space retrieval. The host constructs context under its authority fence."""
from collections import OrderedDict
from dataclasses import asdict
import json
import sqlite3
from threading import Lock
from time import monotonic

from nexus_memory.domain.errors import InvalidGraphPattern, SpaceNotMounted, StalePack
from nexus_memory.domain.retrieval import TraverseQuery
from .anchors import AnchorProvider
from .packing import digest, pack
from .traversal import Search


class RecallService:
    def __init__(self,repository,binding,*,clock=monotonic,token_counter=None):
        self.repository,self.binding=repository,binding
        self.clock,self.counter=clock,token_counter
        self._cache=OrderedDict()
        self._lock=Lock()

    def _stamp(self,ctx):
        return digest(json.dumps([self.binding.to_json(),asdict(ctx)],sort_keys=True,separators=(',',':')))

    def _matches(self,snap,ctx):
        return len(ctx.snapshots)==1 and (ctx.snapshots[0].store_id,ctx.snapshots[0].memory_sequence,ctx.snapshots[0].graph_sequence)==(snap.store_id,snap.memory_event_seq,snap.graph_event_seq)

    def recall(self,query,context):
        deadline=self.clock()+min(query.budget.execution_deadline_ms,query.budget.query_deadline_ms)/1000
        expired=lambda:self.clock()>=deadline
        stamp=self._stamp(context)
        with self.repository.open_snapshot(self.binding) as snap:
            limits=set(); bundles=[]; seeds=()
            search=Search(snap,context,query,limits,expired)
            snap.db.set_progress_handler(lambda:int(expired()),200)
            try:
                if not self._matches(snap,context): limits.add('snapshot_mismatch')
                elif expired(): limits.add('deadline')
                else:
                    if query.mode=='historical':
                        snap=snap.historical(valid_as_of=query.valid_as_of or context.host_time,known_as_of=query.known_as_of or context.host_time)
                        search=Search(snap,context,query,limits,expired)
                    provider=AnchorProvider(lambda key: search.proof.evidence(key)=='eligible')
                    seeds=provider.search(query,snap,query.budget.candidate_pool)
                    if 'proof_budget' in search.proof.reasons: limits.add('proof_premise_rows')
                    limits.update(provider.limitations)
                    if len(seeds)>query.budget.seeds: limits.add('seeds'); seeds=seeds[:query.budget.seeds]
                    for state in search.paths(seeds):
                        bundle=search.bundle(state)
                        if bundle: bundles.append(bundle)
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode!=sqlite3.SQLITE_INTERRUPT: raise
                limits.add('deadline')
            finally:
                snap.db.set_progress_handler(None,0)
            if expired(): limits.add('deadline')
            result=pack(query,stamp,context.snapshots,seeds,search,bundles,self.counter)
            if expired() and 'deadline' not in limits:
                limits.add('deadline'); result=pack(query,stamp,context.snapshots,seeds,search,(),self.counter)
        with self._lock:
            self._cache[result.pack_id]=(self.clock()+900,result)
            while len(self._cache)>32 or sum(p.serialized_utf8_bytes for _,p in self._cache.values())>2*1024**2:
                self._cache.popitem(last=False)
        return result

    def traverse(self,query,context):
        if not isinstance(query,TraverseQuery): raise InvalidGraphPattern('finite anchored pattern required')
        return self.recall(query,context)

    def explain(self,pack_id,context):
        with self._lock:
            entry=self._cache.get(pack_id)
        if entry and self.clock()<entry[0] and self._stamp(context)==entry[1].context_digest:
            try:
                with self.repository.open_snapshot(self.binding) as snap:
                    if self._matches(snap,context): return entry[1]
            except (SpaceNotMounted,sqlite3.OperationalError): pass
        raise StalePack('stale pack')
