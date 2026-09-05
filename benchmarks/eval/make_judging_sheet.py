"""Emit the blind judging sheet from a frozen corpus.

    python make_judging_sheet.py [version]     # default 1

The sheet deliberately withholds anything that could steer a judgment:
no query categories, no scores, no result positions, no indication of what the
implementation would return, no earlier round of labels, and no ordering that
reflects authoring order.

It does disclose which items are earlier versions of which, because a judge
cannot reason about an outdated answer without knowing it was superseded.
That is corpus structure, not an implementation preference.

Everything version-specific is read from the corpus file rather than branched on
in code, so regenerating an older sheet reproduces it byte for byte:
`sheet_preamble` overrides the default instructions, and `shuffle_queries`
presents the queries in a seeded non-authoring order.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent

DEFAULT_PREAMBLE = [
    "**Nothing here has been judged yet, and no ranking work has been done.** Judgments",
    "freeze this benchmark; correcting a label afterwards requires a new version, never an",
    "edit in place.",
]


def build(version: int = 1) -> str:
    corpus = json.loads((HERE / f"corpus-v{version}.json").read_text())

    flat = []
    for memory in corpus["memories"]:
        for revision in memory["revisions"]:
            flat.append({"memory_id": memory["memory_id"], **revision})

    order = list(range(len(flat)))
    random.Random(corpus["shuffle_seed"]).shuffle(order)
    item_of = {flat[source]["revision_id"]: f"i{position + 1:02d}" for position, source in enumerate(order)}

    successor = {}
    predecessor = {}
    for memory in corpus["memories"]:
        revisions = memory["revisions"]
        for earlier, later in zip(revisions, revisions[1:]):
            successor[earlier["revision_id"]] = item_of[later["revision_id"]]
            predecessor[later["revision_id"]] = item_of[earlier["revision_id"]]

    lines = [
        f"# Nexus retrieval benchmark v{corpus['benchmark_version']} — judging sheet",
        "",
        *corpus.get("sheet_preamble", DEFAULT_PREAMBLE),
        "",
        "## How to judge",
        "",
        "Read the items once, then work through the queries. For each query, list the item IDs",
        "you consider `2` and those you consider `1`. Everything unlisted is `0`.",
        "",
        "| Grade | Meaning |",
        "| --- | --- |",
        "| `2` | Directly answers the information need |",
        "| `1` | Useful supporting evidence, but does not answer it |",
        "| `0` | Irrelevant |",
        "",
        "Add a short rationale per query, and say explicitly where you are uncertain — an",
        "uncertain label that is marked as such is far more useful than a confident guess.",
        "",
        "Judge the **content against the information need**, not what any system could retrieve.",
        "Some needs may have no answer here at all; leaving a query with no items listed is a",
        "valid and expected outcome.",
        "",
        "Where an item is marked as an earlier version of another, decide for yourself whether",
        "being superseded makes it irrelevant, still useful as background, or exactly what the",
        "need asks for.",
        "",
        f"## Items ({len(flat)})",
        "",
    ]

    for position, source in enumerate(order):
        entry = flat[source]
        item = f"i{position + 1:02d}"
        note = ""
        if entry["revision_id"] in successor:
            note = f" · _earlier version of {successor[entry['revision_id']]}_"
        elif entry["revision_id"] in predecessor:
            note = f" · _replaces {predecessor[entry['revision_id']]}_"
        lines.append(f"**{item}** · `{entry['kind']}` · tags: {', '.join(entry['tags'])}{note}")
        lines.append("")
        lines.append(f"> {entry['content']}")
        lines.append("")

    queries = list(corpus["queries"])
    if corpus.get("shuffle_queries"):
        random.Random(corpus["shuffle_seed"] + 1).shuffle(queries)

    lines += [f"## Queries ({len(queries)})", ""]
    for query in queries:
        lines.append(f"### {query['query_id']} — \"{query['text']}\"")
        lines.append("")
        lines.append(f"Information need: {query['intent']}")
        lines.append("")
        lines.append("- grade 2: ")
        lines.append("- grade 1: ")
        lines.append("- rationale: ")
        lines.append("- uncertainty: ")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    version = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    destination = HERE / f"judging-v{version}.md"
    destination.write_text(build(version))
    print(f"wrote {destination}")
