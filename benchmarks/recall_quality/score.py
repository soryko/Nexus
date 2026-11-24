"""Offline score. A blind reviewer judges meaning, not exact answer strings."""
from collections import Counter, defaultdict


def score_answer(key, row, judgment):
    if not judgment or judgment.get('status') != 'success':
        return None
    for field in ('meaning_correct', 'format_correct', 'abstained'):
        if type(judgment.get(field)) is not bool:
            raise ValueError('expected blind boolean judgment')
    cites = set(judgment['cited_labels'])
    return {'action': judgment['meaning_correct'] and judgment['abstained'] != key['answerable'],
            'citation': cites == set(key['required']) and cites <= set(row['offered_ids']),
            'format': judgment['format_correct']}


def summarize(rows, key, receipts, judgments=None):
    counts = defaultdict(Counter)
    lost, regressions, missing = {}, [], []
    pairs = defaultdict(dict)
    for row in rows:
        case, arm = row['case'], row['arm']
        if arm not in ('A','B') or case not in key or arm in pairs[case]:
            raise ValueError('unexpected or duplicate arm')
        pairs[case][arm] = row
        if not row['complete']:
            continue
        reads = row['body_read_ids']
        vals = {'reads': len(reads), 'irrelevant': len(set(reads)-set(key[case]['required'])),
                'read_abstentions': int(not reads)}
        for group in (arm, arm + '/' + row['class']):
            counts[group].update(vals)
    pending = []
    for case in key:
        pair = pairs[case]
        if set(pair) != {'A','B'} or not all(r['complete'] for r in pair.values()):
            missing.append(case)
            continue
        a,b = pair['A'],pair['B']
        diff = set(key[case]['required']) & set(a['offered_ids']) - set(b['offered_ids'])
        if diff:
            lost[case] = sorted(diff)
        scores = [score_answer(key[case], r, (judgments or {}).get(r['blind_id'])) for r in (a,b)]
        if None in scores:
            pending.append(case)
        elif any(scores[0][f] and not scores[1][f] for f in ('action','citation')):
            regressions.append(case)
    unsafe = any(r.get('safety') == 'fail' for r in rows)
    incomplete = missing or any(r.get('safety') != 'pass' for r in rows)
    failed = (lost or regressions or counts['B']['irrelevant'] >= counts['A']['irrelevant'] or
              not any(set(r['offered_ids']) & set(key[r['case']]['required']) for r in rows if r['arm']=='B' and r['complete']))
    return {'counts': dict(counts), 'lost_evidence': lost, 'required_receipts_lost':
            sum(len(set(ids) & receipts) for ids in lost.values()), 'answer_regressions': regressions,
            'pending_answer_pairs': pending, 'incomplete_pairs': missing,
            'quiet_gate': ('disqualified' if unsafe else 'inconclusive' if incomplete else
                           'fail' if failed else 'inconclusive' if pending else 'replicate'),
            'cost_gate': 'inconclusive: no authorized query/answer calls'}
