from pathlib import Path
from runpy import run_path

import pytest

summarize = run_path(str(Path(__file__).resolve().parents[2] / 'benchmarks/recall_quality/token_ledger.py'))['summarize']


def row(arm, stage, tokens, status='success'):
    return dict(case='demo', arm=arm, stage=stage, total_tokens=tokens, status=status)


def test_full_cost_includes_answer_not_just_query():
    calls = [row('C', 'answer', 100), row('D', 'query', 30), row('D', 'answer', 60)]
    assert summarize(['demo'], calls, 3)['local_minus_model_tokens'] == 10


def test_failures_count_when_known_and_missing_pairs_are_not_savings():
    calls = [row('D', 'query', 11, 'failed'), row('D', 'query', None)]
    out = summarize(['demo'], calls, 1)
    assert out['accounted_tokens']['D'] == 11
    assert out['local_minus_model_tokens'] is None
    assert set(out['issues']) == {'unsuccessful call', 'unknown usage', 'duplicate stage', 'incomplete pairs', 'call ceiling exceeded'}
    with pytest.raises(ValueError):
        summarize(['demo'], [row('C', 'query', 0)], 3)
