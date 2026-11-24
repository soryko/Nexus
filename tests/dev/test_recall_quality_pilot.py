from collections import Counter
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'benchmarks/recall_quality'
sys.path.insert(0, str(HERE))
from fixture import Fixture, Unsafe
from run import load, verify, sha
from score import score_answer, summarize


def test_sealed_roster():
    assert sha(HERE/'manifest.json') == '5d79926a1ace325b7718287bbf54072373c498d176a1884e54d151017e48835f'
    assert verify()['external_calls_authorized'] == 0
    cases, key = load('cases.json'), load('key.json')
    assert len(set(c['prompt'] for c in cases)) == 20
    assert set(key) == {c['id'] for c in cases}
    assert list(Counter(c['class'] for c in cases).values()) == [4]*5


def test_isolation_and_explicit_controls(tmp_path):
    f = Fixture(tmp_path, load('corpus.json'))
    q = [{'query':'installer','limit':4}]
    before = f.controls(q)
    for label in ('r10','r11'):
        for token in (label, f.ids[label], *f.revisions[label]):
            with pytest.raises(Unsafe):
                f.guard({'hit_ids':[token]})
    for marker in ('OLDORCHID','BlueQuartz'):
        with pytest.raises(Unsafe):
            f.guard({'excerpt':marker})
    assert f.search({'query':'OLDORCHID OR BLUEQUARTZ','advanced':True})['hits'] == ()
    assert before == f.controls(q)
    with pytest.raises(ValueError, match='existing store'):
        Fixture(tmp_path, load('corpus.json'))


def test_blind_scores_action_citation_and_format_independently():
    row = {'offered_ids':['r01']}
    key = {'required':['r01'],'answerable':True}
    j = {'status':'success','meaning_correct':True,'format_correct':False,'cited_labels':['r01'],'abstained':False}
    assert score_answer(key,row,j) == {'action':True,'citation':True,'format':False}
    assert not score_answer(key,row,dict(j,cited_labels=['r02']))['citation']
    assert not score_answer(key,row,dict(j,abstained=True))['action']
    assert score_answer({'required':[],'answerable':False},row,dict(j,cited_labels=[],abstained=True))['action']
    assert score_answer(key,row,{'status':'timeout'}) is None


def test_quiet_gate_never_passes_by_discarding_evidence():
    key = {'one':{'required':['receipt'],'answerable':True}}
    def row(arm, ids):
        return {'case':'one','class':'mixed','arm':arm,'blind_id':arm,'complete':True,'safety':'pass',
                'offered_ids':ids,'body_read_ids':ids}
    base = row('A',['distractor','receipt'])
    result = summarize([base,row('B',[])],key,{'receipt'})
    assert result['quiet_gate'] == 'fail' and result['required_receipts_lost'] == 1
    for b, expected in [(row('B',['receipt']), 'inconclusive'), (dict(row('B',[]),safety='fail'),'disqualified')]:
        assert summarize([base,b],key,{'receipt'})['quiet_gate'] == expected
    assert summarize([base],key,{'receipt'})['incomplete_pairs'] == ['one']
    j = {'status':'success','cited_labels':['receipt'],'meaning_correct':True,'abstained':False,'format_correct':True}
    pair = [base,row('B',['receipt'])]
    assert summarize(pair,key,{'receipt'},{'A':j,'B':j})['quiet_gate'] == 'replicate'
    assert summarize(pair,key,{'receipt'},{'A':j,'B':dict(j,cited_labels=[])})['quiet_gate'] == 'fail'
