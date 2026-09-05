"""Convert a filled judging sheet into a judgments file, mechanically.

    python parse_assessor_sheet.py 2

The assessor's response is saved verbatim as `judging-v{n}-assessor.md` and is never
edited. This script is the only path from that file to `judgments-v{n}.json`, so the
transcription can be re-run and diffed instead of trusted. Adjudication is recorded as
separate fields on top of the parsed labels; it never rewrites them.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
QUERY = re.compile(r'^### (q\d+) — "(.*)"$')
FIELD = re.compile(r"^- (grade 2|grade 1|rationale|uncertainty): ?(.*)$")


def item_map(version: int) -> dict[str, str]:
    """item id -> corpus revision id, reproducing the frozen shuffle for that version."""
    corpus = json.loads((HERE / f"corpus-v{version}.json").read_text())
    flat = [r for m in corpus["memories"] for r in m["revisions"]]
    order = list(range(len(flat)))
    random.Random(corpus["shuffle_seed"]).shuffle(order)
    return {f"i{position + 1:02d}": flat[source]["revision_id"] for position, source in enumerate(order)}


FIELDS = ("grade 2", "grade 1", "rationale", "uncertainty")


def parse(version: int) -> tuple[dict, list[str]]:
    """Return (labels, normalisations).

    A missing field is normalised to empty under the sheet's own "everything unlisted is 0"
    rule, but the normalisation is REPORTED rather than applied silently: an absent field and
    a field the assessor deliberately left blank are different events, and a parser that
    cannot tell them apart makes an incomplete response look structurally complete.
    """
    text = (HERE / f"judging-v{version}-assessor.md").read_text()
    labels: dict[str, dict] = {}
    seen: dict[str, set[str]] = {}
    current = None
    for line in text.splitlines():
        header = QUERY.match(line)
        if header:
            current = header.group(1)
            labels[current] = {"2": [], "1": [], "rationale": "", "uncertainty": ""}
            seen[current] = set()
            continue
        field = FIELD.match(line)
        if field and current:
            name, value = field.group(1), field.group(2).strip()
            seen[current].add(name)
            if name.startswith("grade"):
                key = name.split()[1]
                labels[current][key] = [i.strip() for i in value.split(",") if i.strip()]
            else:
                labels[current][name] = value

    normalisations = [
        f"{qid}: '{name}' field absent from the response; normalised to empty under the sheet's "
        f"'everything unlisted is 0' rule"
        for qid in labels
        for name in FIELDS
        if name not in seen[qid]
    ]
    return labels, normalisations


def main() -> None:
    version = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    corpus = json.loads((HERE / f"corpus-v{version}.json").read_text())
    judgments, normalisations = parse(version)
    items = item_map(version)

    known = {q["query_id"] for q in corpus["queries"]}
    missing = known - set(judgments)
    unknown = set(judgments) - known
    bad = {q: [i for i in j["2"] + j["1"] if i not in items] for q, j in judgments.items()}
    bad = {q: v for q, v in bad.items() if v}
    if missing or unknown or bad:
        raise SystemExit(f"sheet does not match corpus: missing={sorted(missing)} unknown={sorted(unknown)} bad_items={bad}")

    grade_two = sum(len(j["2"]) for j in judgments.values())
    grade_one = sum(len(j["1"]) for j in judgments.values())
    pairs = len(known) * len(items)
    print(f"v{version}: {len(known)} queries x {len(items)} items = {pairs} pairs; "
          f"{grade_two} grade-2, {grade_one} grade-1, {pairs - grade_two - grade_one} grade-0")
    for qid in sorted(judgments):
        two = [f"{i}={items[i]}" for i in judgments[qid]["2"]]
        print(f"  {qid}: {two or 'no answer'}")
    for note in normalisations:
        print(f"  NORMALISED  {note}")


if __name__ == "__main__":
    main()
