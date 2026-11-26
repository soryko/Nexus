"""Volatile Stage A turn state, owned by one registered receiving context."""
from dataclasses import replace
from uuid import uuid4
from math import ceil

from nexus_memory.domain.errors import InvalidRetrievalBudget, OperationFailure
from nexus_memory.lifecycle.contracts import PreparedTurn


class Allowance:
    rows = ('expansion_rows', 'proof_premise_rows', 'conflict_rows')

    def __init__(self, budget, clock):
        self.id = str(uuid4())
        self.clock = clock
        self.expiry = clock() + 900
        self.calls = 2
        self.remaining = {k: getattr(budget, k) for k in self.rows}
        self.ms = budget.execution_deadline_ms
        self.bytes = budget.serialized_bytes

    def check(self):
        if self.clock() >= self.expiry:
            raise InvalidRetrievalBudget('turn expired')

    def search(self, query, run):
        self.check()
        if not self.calls or self.ms <= 0:
            raise InvalidRetrievalBudget('turn exhausted')
        self.calls -= 1
        limits = {k: min(v,getattr(query.budget,k)) for k,v in self.remaining.items()}
        budget = replace(query.budget, **limits, execution_deadline_ms=min(self.ms,query.budget.execution_deadline_ms))
        start = self.clock()
        try:
            pack = run(replace(query,budget=budget))
        except Exception:
            self.calls = 0  # unknown work consumed
            raise
        finally:
            self.ms -= ceil(1000*(self.clock()-start))
        for k in self.rows:
            self.remaining[k] -= getattr(pack,k)
        return pack

    def check_output(self,size):
        self.check()
        if size > self.bytes:
            raise InvalidRetrievalBudget('output exhausted')


class LifecycleService:
    def __init__(self, broker, context):
        self.broker, self.context = broker, context
        self.turn = self.prepared = None

    def capabilities(self):
        return self.context.capabilities

    def prepare_turn(self, query, context, budget):
        if context is not self.context:
            raise ValueError('unregistered context')
        env = self.broker.prepare_handle(replace(query,budget=budget))
        if isinstance(env,OperationFailure):
            raise ValueError('memory unavailable')
        import json
        handle = json.loads(self.broker.drain(env))['pack_id']
        return PreparedTurn(handle,self.broker.allowance.id,env.publication_id)

    def before_turn(self, turn, query):
        with self.broker.lock:
            if self.turn != turn:
                self.turn, self.prepared = turn, None
                self.broker.allowance = Allowance(query.budget,self.broker.clock)
            if self.prepared is None:
                self.prepared = self.prepare_turn(query,self.context,query.budget)
            return self.prepared
