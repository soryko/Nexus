"""One trace parser, joining results to calls by `tool_use_id`.

The previous parsers attached each `tool_result` to the most recently unresolved call. A
model response may request several tools at once, and their results arrive as separate
blocks -- so with two parallel reads outstanding, each could receive the other's output.
Every claim about what a tool returned, and about the order of access, depended on that
pairing being right. It joins by id now.

Also opens `.jsonl` or `.jsonl.gz` transparently, so recomputation works on packaged runs.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def _open(path: Path):
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", errors="replace")
    if p.exists():
        return p.open(errors="replace")
    gz = p.with_suffix(p.suffix + ".gz")
    return gzip.open(gz, "rt", errors="replace")


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return content or ""


def parse(path) -> dict:
    """-> {calls: [...], result: envelope|None}

    Each call carries its own `id`, its `index` in issue order, and — once joined — the
    `result` text that actually came back for THAT id, plus `is_error`.
    """
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    envelope = None
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = d.get("type")
            if kind == "assistant":
                for b in d["message"].get("content", []):
                    if b.get("type") == "tool_use":
                        c = {"id": b.get("id"), "index": len(calls), "name": b.get("name"),
                             "input": b.get("input"), "result": None, "is_error": None}
                        calls.append(c)
                        if c["id"]:
                            by_id[c["id"]] = c
            elif kind == "user":
                content = d.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    continue
                for b in content:
                    if b.get("type") != "tool_result":
                        continue
                    tid = b.get("tool_use_id")
                    target = by_id.get(tid)
                    if target is None:
                        # An unmatched result is recorded, never silently reassigned to
                        # whichever call happened to be pending.
                        calls.append({"id": None, "index": len(calls), "name": "<orphan result>",
                                      "input": None, "result": _text(b.get("content")),
                                      "is_error": b.get("is_error"), "orphan_for": tid})
                        continue
                    target["result"] = _text(b.get("content"))
                    target["is_error"] = b.get("is_error")
            elif kind == "result":
                envelope = d
    return {"calls": calls, "result": envelope,
            "orphans": sum(1 for c in calls if c["name"] == "<orphan result>"),
            "unresolved": sum(1 for c in calls if c["result"] is None
                              and c["name"] != "<orphan result>")}


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        t = parse(p)
        print(f"{p}: calls={len(t['calls'])} orphans={t['orphans']} unresolved={t['unresolved']}")
