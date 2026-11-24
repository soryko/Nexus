"""Offline accounting for a future, explicitly budgeted C/D cost experiment.

No provider calls. Count attempted query AND answer usage. Missing usage is
unknown, never zero. A favorable cost comparison is not a quality verdict.
"""
import argparse
import json
from pathlib import Path


def summarize(case_ids, calls, call_ceiling):
    """calls: [{case, arm: C|D, stage: query|answer, status, total_tokens}].

    C generates a query locally; D uses one model query. Both need one answer.
    Count known usage even on failures; retries, missing pairs or overruns
    invalidate the comparison rather than silently dropping a case.
    """
    ids = set(case_ids)
    if not ids or len(ids) != len(case_ids) or type(call_ceiling) is not int or call_ceiling < 0:
        raise ValueError('invalid case roster or budget')
    expected = {(i, arm, stage) for i in ids for arm, stage in
                [('C', 'answer'), ('D', 'query'), ('D', 'answer')]}
    known = {'C': 0, 'D': 0}
    seen, issues = set(), set()
    if len(calls) > call_ceiling:
        issues.add('call ceiling exceeded')
    for call in calls:
        key = call['case'], call['arm'], call['stage']
        if key not in expected:
            raise ValueError('unexpected call outside frozen protocol')
        if key in seen:
            issues.add('duplicate stage')
        seen.add(key)
        if call.get('status') != 'success':
            issues.add('unsuccessful call')
        n = call.get('total_tokens')
        if n is None:
            issues.add('unknown usage')
        elif type(n) is not int or n < 0:
            raise ValueError('invalid token count')
        else:
            known[call['arm']] += n
    if expected != seen:
        issues.add('incomplete pairs')
    return {'accounted_tokens': known, 'call_count': len(calls), 'issues': sorted(issues),
            'local_minus_model_tokens': None if issues else known['C'] - known['D']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path, help='JSON with case_ids, calls, call_ceiling')
    args = parser.parse_args()
    print(json.dumps(summarize(**json.loads(args.ledger.read_text())), indent=2))
