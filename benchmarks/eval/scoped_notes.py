"""Synthetic wording check. Print prompts or replay scores without calling a model.

python benchmarks/eval/scoped_notes.py prompt explicit
python benchmarks/eval/scoped_notes.py score docs/measurements/scoped-notes-run.json
"""
import argparse
import json
from pathlib import Path

# Both arms receive the same scoped rule. Only the explicit arm repeats the residual.
# ID, base note, entailed restatement, question, controlling value and revision.
CASES = [
    ('a', 'T5@1: all workers use blue. F3@1 changes only interactive workers to gold.',
     'Batch workers still use blue under T5@1.', 'Batch worker color?', 'blue T5@1'),
    ('b', 'P1@2: all channels use tin. K4@1 changes only postal channels to zinc.',
     'Digital channels retain tin under P1@2.', 'Postal channel material?', 'zinc K4@1'),
    ('c', 'W3@1: all exports use tab. J4@2 changes only nightly exports to pipe.',
     'Daytime exports retain tab under W3@1.', 'Delimiter for a daytime export?', 'tab W3@1'),
    ('d', 'A3@1 covers inbound traffic only: slate. Q2@1 changes only inbound bulk traffic to teal.',
     'Inbound non-bulk traffic retains slate under A3@1.', 'Color for outbound bulk traffic?', 'unknown NONE'),
    ('e', 'L8@3: all regions use ash. M1@1 changes only the east region to elm.',
     'The west region retains ash under L8@3.', 'Material in the eastern region?', 'elm M1@1'),
    ('f', 'B1@2: all imports use clay. Z4@1 changes only compressed imports to wax.',
     'Uncompressed imports remain clay under B1@2.', 'Material for an import without compression?', 'clay B1@2'),
]


def prompt(arm):
    lines = ['Answer only from each case. Return one JSON object mapping case IDs to strings '
             'of the form "value revision". Use "unknown NONE" if not determined. No commentary.']
    for key, base, restatement, question, _ in CASES:
        note = f'{base} {restatement}' if arm == 'explicit' else base
        lines.append(f'{key}: {note} {question}')
    return '\n'.join(lines)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def score(raw):
    # Predeclared: JSON whitespace/order immaterial; no fences, extra keys, fuzzy
    # values or repaired output. Wrong shape scores zero, not a dropped trial.
    try:
        answers = json.loads(raw, object_pairs_hook=unique_keys)
    except ValueError:
        answers = None
    valid = isinstance(answers, dict) and set(answers) == {c[0] for c in CASES}
    return {key: bool(valid and answers[key] == expected) for key, *_, expected in CASES}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prompt').add_argument('arm', choices=['implicit', 'explicit'])
    commands.add_parser('score').add_argument('receipt', type=Path)
    args = parser.parse_args()
    if args.command == 'prompt':
        print(prompt(args.arm))
    else:
        for run in json.loads(args.receipt.read_text())['runs']:
            print(run['model'], run['arm'], score(run['response']))
