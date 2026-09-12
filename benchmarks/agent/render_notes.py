"""Render the captured corpus into the plain-notes file arm 3 receives.

The two memory arms must differ in *access mechanism* and in nothing else
(``protocol-a1`` §2), so the rendering is mechanical and lives here rather than in a
person's judgement: same facts, same wording, same order, grouped by kind.

Measurement metadata is deliberately withheld. ``relevance`` is the answer key, and
``outdated_note`` says which memory is stale -- delivering either would hand arm 3
something arm 2's store does not contain, and ``outdated_note`` would additionally
defeat the outdated category in §7 by labelling its own instance.

Usage:  python render_notes.py corpus-dev-a1.json notes-dev-a1.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Fields a memory carries into the store, and therefore the only fields arm 3 may see.
DELIVERED = ("kind", "tags", "content")

HEADINGS = {
    "decision": "Decisions",
    "constraint": "Constraints",
    "procedure": "Procedures",
    "failure": "Observed failures",
    "observation": "Observations",
}


def render(corpus: dict) -> str:
    memories = corpus["memories"]
    withheld = sorted({key for m in memories for key in m if key not in (*DELIVERED, "id")})
    lines = [
        "# Notes from earlier work on this repository",
        "",
        f"Rendered from `{corpus.get('corpus_version', '?')}` by `render_notes.py`. "
        "Same content as the memory store, grouped by kind, in corpus order.",
        "",
    ]
    for kind, heading in HEADINGS.items():
        group = [m for m in memories if m["kind"] == kind]
        if not group:
            continue
        lines += [f"## {heading}", ""]
        for m in group:
            tags = ", ".join(m["tags"])
            lines += [f"- {m['content']}" + (f" _(tags: {tags})_" if tags else ""), ""]
    return "\n".join(lines).rstrip() + "\n", withheld


def main(argv: list[str]) -> int:
    corpus = json.loads(Path(argv[1]).read_text())
    text, withheld = render(corpus)
    Path(argv[2]).write_text(text)
    delivered = len(corpus["memories"])
    print(f"rendered {delivered} memories to {argv[2]}")
    print(f"withheld fields: {', '.join(withheld) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
