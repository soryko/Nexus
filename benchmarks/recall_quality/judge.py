"""Offline-only unblinding: apply supplied judgments without generating answers."""
import argparse
import json
from pathlib import Path

from run import HERE, load, sha, verify
from score import summarize

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('receipts', type=Path)
    p.add_argument('blind_judgments', type=Path)
    args = p.parse_args()
    verify()
    rows = json.loads(args.receipts.read_text())
    judgments = json.loads(args.blind_judgments.read_text())
    if any(row['config_hash'] != sha(HERE/'manifest.json') for row in rows):
        raise ValueError('receipts are not from this freeze')
    if set(judgments) - {r.get('blind_id') for r in rows}:
        raise ValueError('unknown blind id')
    print(json.dumps(summarize(rows, load('key.json'), {r['id'] for r in load('corpus.json') if r['receipt']},
                               judgments), indent=2))
