"""Delivered context, MEASURED from tool responses rather than reconstructed from the corpus.

The previous version looked for a memory id anywhere in the concatenated tool output and
then charged that memory's ENTIRE body to the arm. Search returns an excerpt, not a body, so
a memory that only ever appeared as a one-line excerpt was counted as fully delivered. On d3
that inflated eight memories to full bodies when six had actually been fetched. The notes
path was worse: any tool input merely mentioning the filename was taken to mean the whole
file had been read.

This reads what came back:

  * `search` responses are parsed as JSON; each hit contributes its **excerpt** bytes.
  * `get` responses contribute their **content** bytes -- a full body.
  * the notes file contributes the bytes the Read/Bash call actually returned, and which
    memories' text is present in those bytes.
  * repeated delivery is counted, not deduplicated away.

Delivery is reported at two strengths, never merged: `full` (the body arrived) and
`excerpt_only` (the model saw a fragment and nothing more).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from trace_parse import parse

BENCH = Path(__file__).parent
NOTES_NAME = "NOTES-FROM-EARLIER-WORK"


def load_corpus(corpus: Path | None = None, mix: Path | None = None):
    """The corpus, its index, and the relevance source.

    The development corpus carries `relevance` inside each memory, because its author
    declared it there. The held-out corpus cannot: its mix is declared separately, after the
    memories exist and before any arm runs (mix-declaration-a1.md), and the corpus file is
    what the arms' store was built from. So relevance comes from the mix declaration when one
    is supplied, and from the memory itself when one is not.
    """
    c = json.loads(Path(corpus or BENCH / "corpus-dev-a1.json").read_text())
    declaration = json.loads(Path(mix).read_text()) if mix else None
    return c, {m["id"]: m for m in c["memories"]}, declaration


def bucket(mem, task, mix=None):
    rel = (mix["tasks"].get(task, {}).get("relevance", {}).get(mem["id"]) if mix
           else mem.get("relevance", {}).get(task))
    return {"necessary": "necessary", "support": "useful support",
            "outdated": "outdated"}.get(rel, "irrelevant")


def measure(run_dir: Path, task: str, arm: str, smap: dict, byid: dict) -> dict:
    t = parse(run_dir / "arms" / arm / "trace.jsonl")
    full: dict[str, int] = {}        # corpus_id -> bytes of body delivered
    excerpt: dict[str, int] = {}     # corpus_id -> bytes of excerpt delivered
    deliveries: list[tuple] = []     # (call_index, corpus_id, strength, bytes)

    for c in t["calls"]:
        res = c.get("result") or ""
        name = c.get("name") or ""
        if name.startswith("mcp__nexus__"):
            try:
                payload = json.loads(res)
            except (json.JSONDecodeError, TypeError):
                continue
            if "hits" in payload:
                for h in payload.get("hits") or []:
                    cid = smap.get(h.get("memory_id"))
                    if not cid:
                        continue
                    n = len((h.get("excerpt") or "").encode())
                    excerpt[cid] = excerpt.get(cid, 0) + n
                    deliveries.append((c["index"], cid, "excerpt", n))
            elif "content" in payload and payload.get("memory_id"):
                cid = smap.get(payload["memory_id"])
                if cid:
                    n = len((payload["content"] or "").encode())
                    full[cid] = full.get(cid, 0) + n
                    deliveries.append((c["index"], cid, "full", n))
        else:
            # the rendered notes file, via Read or via a shell command
            touches = NOTES_NAME in json.dumps(c.get("input") or {})
            if not touches or not res:
                continue
            for cid, mem in byid.items():
                body = mem["content"]
                # the file is rendered one bullet per memory; a body present in the
                # returned bytes is a full delivery, a truncated read delivers less
                if body in res:
                    full[cid] = full.get(cid, 0) + len(body.encode())
                    deliveries.append((c["index"], cid, "full", len(body.encode())))
                elif body[:60] in res:
                    n = len(res.encode()) and len(body[:60].encode())
                    excerpt[cid] = excerpt.get(cid, 0) + n
                    deliveries.append((c["index"], cid, "excerpt", n))

    full_ids = set(full)
    excerpt_only = set(excerpt) - full_ids
    return {"full": full, "excerpt": excerpt, "full_ids": full_ids,
            "excerpt_only": excerpt_only, "deliveries": deliveries,
            "bytes_full": sum(full.values()), "bytes_excerpt": sum(excerpt.values()),
            "orphans": t["orphans"], "unresolved": t["unresolved"]}


def report(run_dir: Path, task: str, corpus_path: Path | None = None,
           mix_path: Path | None = None, store_map: Path | None = None) -> None:
    corpus, byid, mix = load_corpus(corpus_path, mix_path)
    smap_file = store_map or (run_dir / "store-map.json")
    if smap_file.exists():
        smap = {r["memory_id"]: r["corpus_id"]
                for r in json.loads(smap_file.read_text())}
    else:
        # the held-out corpus carries each memory's store id, so no side file is needed
        smap = {m["memory_id"]: m["id"] for m in corpus["memories"] if m.get("memory_id")}
    need = {c for c, m in byid.items() if bucket(m, task, mix) == "necessary"}
    print(f"### {task}  ({run_dir.name})")
    for arm in ("baseline", "nexus", "notes"):
        if not (run_dir / "arms" / arm).exists():
            continue
        m = measure(run_dir, task, arm, smap, byid)
        allids = m["full_ids"] | m["excerpt_only"]
        print(f"--- {arm} ---")
        print(f"    delivered any : {len(allids)}/{len(byid)}     "
              f"repeat deliveries: {len(m['deliveries'])}")
        print(f"    full bodies   : {len(m['full_ids'])}  ({m['bytes_full']} bytes)")
        print(f"    excerpt only  : {len(m['excerpt_only'])}  ({m['bytes_excerpt']} bytes"
              f" across all excerpts)")
        for b in ("necessary", "useful support", "outdated", "irrelevant"):
            f = sorted(c for c in m["full_ids"] if bucket(byid[c], task, mix) == b)
            e = sorted(c for c in m["excerpt_only"] if bucket(byid[c], task, mix) == b)
            print(f"    {b:16} full={f} excerpt_only={e}")
        if not need:
            cov = "N/A (no necessary MEMORY declared; see the report on repository evidence)"
        else:
            cov = (f"{len(m['full_ids'] & need)}/{len(need)} as full bodies"
                   f"  ({len(allids & need)}/{len(need)} counting excerpts)")
        print(f"    necessary-memory coverage: {cov}")
        if m["orphans"] or m["unresolved"]:
            print(f"    trace warnings: orphan_results={m['orphans']} unresolved_calls={m['unresolved']}")


if __name__ == "__main__":
    a = sys.argv
    report(Path(a[1]), a[2],
           Path(a[a.index("--corpus") + 1]) if "--corpus" in a else None,
           Path(a[a.index("--mix") + 1]) if "--mix" in a else None,
           Path(a[a.index("--store-map") + 1]) if "--store-map" in a else None)
