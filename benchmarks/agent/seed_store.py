"""Build the arm-2 store from the corpus file, deterministically.

The development store was seeded by hand and never committed, so `nexus-dev.db` could not be
rebuilt from the repository -- which made the one artifact the nexus arm depends on the one
artifact the reviewer could not reconstruct. This does it from `corpus-dev-a1.json`, in
corpus order, with the corpus id as the idempotency key so a re-seed of the same corpus
produces the same rows.

Only the delivered fields are written: content, kind and tags. `relevance`,
`discoverability` and `outdated_note` are the evaluator's answer key and are withheld here
exactly as `render_notes.py` withholds them from arm 3 -- c13's "outdated" label is the very
thing d4 exists to test, and a delivered tag would announce it.

Usage:  python3 seed_store.py <db> <namespace> <actor> [--map <store-map.json>]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH = Path(__file__).parent
CORPUS = BENCH / "corpus-dev-a1.json"
DELIVERED_FIELDS = ("content", "kind", "tags")
WITHHELD_FIELDS = ("relevance", "discoverability", "outdated_note")


def seed(db: Path, namespace: str, actor: str) -> list[dict]:
    sys.path.insert(0, str(BENCH.parent.parent / "src"))
    from nexus_memory.domain.models import MemoryInput, Scope
    from nexus_memory.memory import MemoryService
    from nexus_memory.storage import SQLiteRepository
    from nexus_memory.transport.mcp_server import _prepare_new_storage

    _prepare_new_storage(db)
    service = MemoryService(SQLiteRepository(db), Scope(namespace, actor), None, None)
    rows = []
    for m in json.loads(CORPUS.read_text())["memories"]:
        receipt = service.record(
            MemoryInput(m["content"], m["kind"], tuple(m.get("tags", ())), None, None, ()),
            m["id"])
        rows.append({"corpus_id": m["id"], "memory_id": receipt.memory_id,
                     "kind": m["kind"], "revision_id": receipt.revision_id})
    return rows


def main() -> int:
    db, namespace, actor = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    out = Path(sys.argv[sys.argv.index("--map") + 1]) if "--map" in sys.argv else None
    if db.exists():
        print(f"refusing to seed over an existing store: {db}", file=sys.stderr)
        return 1
    rows = seed(db, namespace, actor)
    print(f"seeded {len(rows)} memories into {db} as ({namespace}, {actor}); "
          f"withheld {', '.join(WITHHELD_FIELDS)}")
    if out:
        out.write_text(json.dumps(rows, indent=1))
        print(f"store map -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
