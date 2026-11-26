"""A01 retrieval boundary cases from §§3, 7 and 8."""
from dataclasses import replace

import pytest

from nexus_memory.domain import retrieval as r
from nexus_memory.domain.errors import InvalidInput, InvalidRetrievalBudget
from nexus_memory.domain.graph import Applicability, EntityRef, Validity
from nexus_memory.domain.models import Scope

NOW = '2028-02-29T15:16:17.012Z'
SNAP = r.PackSnapshot('store', 1, 'a' * 64, 'v1', authority_epoch=2, policy_epoch=3)


def test_context_requires_live_epochs_for_every_store():
    assert not hasattr(r.TrustedQueryContext, 'from_json')
    context = r.TrustedQueryContext(None, None, NOW, (SNAP,), authority_epoch=2, policy_epoch=3)
    for changes in ({'authority_epoch': 1}, {'policy_epoch': 2}, {'authority_epoch': True},
                    {'policy_epoch': -1}, {'snapshots': ()}, {'snapshots': [SNAP]},
                    {'snapshots': (SNAP, replace(SNAP, store_id='other', policy_epoch=2))},
                    {'host_time': 'yesterday'}):
        with pytest.raises(InvalidInput):
            replace(context, **changes)


@pytest.mark.parametrize('field', ['valid_as_of', 'known_as_of'])
def test_historical_cutoffs_are_real_utc_instants(field):
    for construct in (lambda obj: r.RecallQuery(**obj), r.RecallQuery.from_json):
        query = {'text': 'when', 'mode': 'historical', field: NOW}
        assert getattr(construct(query), field) == NOW
        for bad in ('2028-02-29', NOW[:-1], NOW[:-1] + '+00:00', NOW.replace('T', ' '),
                    NOW.replace('2028', '2027'), NOW.replace(':16:', ':60:')):
            with pytest.raises(InvalidInput):
                construct({**query, field: bad})


def test_path_keeps_partition_intersection_and_exact_rank_tiebreak():
    a, b = EntityRef('a'), EntityRef('b')
    edge = r.PathEdge(r.ClaimRevision('sp', 'claim', 'rev-01'), r.RelationStep('CALLS', 'forward'), a, b)
    scope = Scope('ns', 'actor')
    applicability = Applicability('project_wide', 'repo', evidence=('proof',))
    validity = Validity('interval', NOW, '2029-01-01T00:00:00Z')
    rank = (1, 1, 1, 0, 1, 2, ('rev-01',))
    state = r.PathState('seed', b, (edge,), frozenset((a, b)), 1, (('proof',),),
                        scope=scope, applicability=applicability, validity=validity, rank=rank)
    assert (state.scope, state.applicability, state.validity) == (scope, applicability, validity)
    assert state.rank[-1] == ('rev-01',)
    for bad in (rank[:-1], (*rank[:-1], ('claim',)), (*rank[:-1], (1,))):
        with pytest.raises(InvalidInput):
            replace(state, rank=bad)


def test_path_rank_cannot_be_built_from_an_empty_revision_id():
    with pytest.raises(InvalidInput):
        r.ClaimRevision('sp', 'claim', '')


def test_missing_historical_index_is_incomplete_not_absence():
    assert r.HISTORICAL_INDEX_UNAVAILABLE == 'historical_index_unavailable'
    pack = r.EvidencePack(1, 'p', 'a' * 64, 'b' * 64, (SNAP,), 'incomplete',
                          (r.HISTORICAL_INDEX_UNAVAILABLE,), (), 0, 0, 0, 'estimated', 0, 0)
    measured = pack.measured(r.RecallBudget())
    assert r.EvidencePack.from_json(measured.to_json()) == measured
    with pytest.raises(InvalidInput):
        replace(pack, status='no_eligible_matches')


def test_budget_floor_and_intent_specific_depth():
    assert r.RecallBudget.from_json({'serialized_bytes': 1024}).serialized_bytes == 1024
    with pytest.raises(InvalidRetrievalBudget):
        r.RecallQuery.from_json({'text': 'why', 'budget': {'serialized_bytes': 1023}})
    for intent in ('rationale', 'compound'):
        assert r.RecallQuery(text='why', intent=intent).budget.semantic_depth == 3
        assert r.RecallQuery.from_json({'text': 'why', 'intent': intent, 'budget': {}}).budget.semantic_depth == 3
        assert r.RecallQuery.from_json({'text': 'why', 'intent': intent, 'budget': {'semantic_depth': 2}}).budget.semantic_depth == 2
