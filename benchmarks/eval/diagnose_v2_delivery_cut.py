"""Why `cutoff_40` cut what it cut on v2. A diagnostic, not a measurement.

    .venv-sqlite/bin/python benchmarks/eval/diagnose_v2_delivery_cut.py

`run_v2_delivery.py` records what was delivered. It does not record the BM25 score
fractions selection decided on, because those are a reporting aid rather than part of the
budgeted path — the same rule that keeps the over-budget probe out of the development
harnesses' timed regions. This script reads them afterwards from the same pinned path and
the same fixture, so the recorded run stays a single execution.

It reads labels to colour the table. Nothing here feeds selection, and nothing here is a
result: the numbers are the ones already in the record, explained.

Output is written to `results-v2-delivery-cut-diagnostic.txt`.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks" / "dev"))

from budgeted_retrieval import CUTOFF_FRACTION, retrieve  # noqa: E402
from run_v2 import build_fixture, item_map  # noqa: E402

PROFILE = "stem"
FRACTION = CUTOFF_FRACTION["cutoff_40"]


def main() -> None:
    corpus = json.loads((HERE / "corpus-v2.json").read_text())
    judgments = json.loads((HERE / "judgments-v2.json").read_text())
    record = json.loads((HERE / "results-v2-delivery-regression.json").read_text())
    changed = record["losses_against_baseline"]["queries_whose_delivery_changed"]
    items = item_map(corpus)
    lines: list[str] = [
        "cutoff_40 on v2: where the cut fell, per query whose delivery changed",
        f"profile {PROFILE} · head-only · fraction {FRACTION} of the top hit's BM25 magnitude",
        f"pinned record: {record['pinned_revision'][:7]}",
        "",
    ]

    with tempfile.TemporaryDirectory() as work:
        service, live, _ = build_fixture(Path(work) / "diagnostic.sqlite3", corpus, PROFILE)
        for query in corpus["queries"]:
            qid = query["query_id"]
            if qid not in changed:
                continue
            grade2 = set(judgments["judgments"][qid]["2"])
            grade1 = set(judgments["judgments"][qid]["1"])
            out = retrieve(service, query["text"], policy="baseline", selection="all")
            if not out.pool:
                continue
            best = abs(out.pool_rank[out.pool[0]] or 0)
            lines.append(f"{qid}  {query['text']!r}   grade-2 {sorted(grade2)}  grade-1 {sorted(grade1)}")
            for position, memory_id in enumerate(out.pool[:6], start=1):
                rank = out.pool_rank.get(memory_id)
                if rank is None:
                    continue
                item = items[live[(memory_id, service.get(memory_id).revision_id)]]
                grade = "2" if item in grade2 else ("1" if item in grade1 else "0")
                fraction = abs(rank) / best if best else 0.0
                mark = "  KEPT " if fraction >= FRACTION or position == 1 else "  CUT  "
                lines.append(f"   rank {position}  {item}  grade {grade}  "
                             f"score {abs(rank):7.4f}  fraction {fraction:.4f}{mark}")
            lines.append("")

    text = "\n".join(lines)
    (HERE / "results-v2-delivery-cut-diagnostic.txt").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
