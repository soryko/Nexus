"""Offline checks for the paired wording trial, including its frozen receipt."""
import hashlib
import json
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'benchmarks/eval/scoped_notes.py'
API = run_path(str(SCRIPT))


def test_score_requires_value_and_revision():
    expected = {case[0]: case[-1] for case in API['CASES']}
    score = API['score']
    assert all(score(json.dumps(expected)).values())
    expected['a'] = 'blue F3@1'
    assert sum(score(json.dumps(expected)).values()) == len(expected) - 1
    assert not any(score('{}').values())
    assert not any(score('not JSON').values())


def test_as_run_protocol_and_outputs():
    receipt = json.loads((ROOT / 'docs/measurements/scoped-notes-run.json').read_text())
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == receipt['protocol_sha256']
    assert [r['arm'] for r in receipt['runs']] == ['implicit', 'explicit']
    for run in receipt['runs']:
        assert run['prompt'] == API['prompt'](run['arm'])
        assert not any(API['score'](run['response']).values())  # both emitted Markdown fences
