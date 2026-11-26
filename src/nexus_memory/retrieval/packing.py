"""Publish complete bundles only; measure the actual final serialized envelope."""
from hashlib import sha256
from secrets import token_urlsafe
from nexus_memory.domain.errors import InvalidRetrievalBudget
from nexus_memory.domain.retrieval import EvidencePack


def digest(text):
    return sha256(text.encode()).hexdigest()


def pack(query, context_digest, snapshots, seeds, search, bundles, counter):
    handle=token_urlsafe(18)
    def measure(items):
        status=('incomplete' if search.limits else 'contested' if any(b.contested for b in items) else
                'supported' if items else 'no_supported_path' if seeds else 'no_eligible_matches')
        return EvidencePack(1,handle,digest(query.to_json()),context_digest,snapshots,status,tuple(sorted(search.limits)),
            tuple(items),search.expanded,query.budget.proof_premise_rows-search.proof.remaining,
            query.budget.conflict_rows-search.proof.controls,'estimated' if counter is None else 'exact',0,0,
            'utf8_bytes_div_4' if counter is None else getattr(counter,'identity',type(counter).__qualname__),
            proof_edge_rows=search.proof_rows,proof_edges=search.proof_edges).measured(query.budget,counter)
    included=[]
    for b in bundles:
        try: measure([*included,b])
        except InvalidRetrievalBudget: search.limits.add('output_budget')
        else: included.append(b)
    while True:
        try: return measure(included)
        except InvalidRetrievalBudget:
            if not included: raise
            search.limits.add('output_budget'); included.pop()
