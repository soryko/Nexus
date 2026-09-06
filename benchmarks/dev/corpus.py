"""Loading a development corpus into a fresh database, shared by every T2 harness.

`run_dev_t1.py` keeps its own copy of this on purpose: its record is pinned to
`corpus-dev1.json` and to the loader that produced it, and nothing here may change what
that file reproduces. The one behaviour this loader adds is memory-level
``"state": "forgotten"`` — a memory that is recorded, revised and then forgotten, so
that a historical index has an ineligible head to resolve against. A dev1 memory carries
no such flag, so dev1 loads identically through either loader.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from nexus_memory.domain.models import MemoryInput, Scope  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

HERE = Path(__file__).resolve().parent


def load(name: str = "corpus-dev2.json") -> dict:
    return json.loads((HERE / name).read_text())


def build(path: Path, corpus: dict, profile: str, history_profile: str = "none") -> tuple[MemoryService, dict]:
    """Load every memory in file order, replaying revisions so history is real.

    The database is opened under its history profile *before* anything is written, so the
    historical index is maintained incrementally by `revise` — the path a running system
    takes — rather than backfilled once at the end.
    """
    service = MemoryService(
        SQLiteRepository(path, index_profile=profile, history_profile=history_profile),
        Scope("dev", "local"),
    )
    fixture_to_memory: dict[str, str] = {}
    revision_map: dict[str, str] = {}
    forgotten: list[str] = []
    for memory in corpus["memories"]:
        current_revision = None
        memory_id = None
        for index, revision in enumerate(memory["revisions"]):
            item = MemoryInput(revision["content"], kind=revision["kind"], tags=tuple(revision["tags"]))
            if index == 0:
                receipt = service.record(item, f"{memory['memory_id']}-{index}")
                memory_id = receipt.memory_id
            else:
                receipt = service.revise(memory_id, current_revision, item, f"{memory['memory_id']}-{index}")
            current_revision = receipt.revision_id
            revision_map[revision["revision_id"]] = receipt.revision_id
        fixture_to_memory[memory["memory_id"]] = memory_id
        if memory.get("state") == "forgotten":
            service.forget(memory_id, current_revision, f"{memory['memory_id']}-forget")
            forgotten.append(memory_id)
    return service, {
        "memories": fixture_to_memory,
        "revisions": revision_map,
        "forgotten": forgotten,
        "to_fixture": {**{v: k for k, v in fixture_to_memory.items()},
                       **{v: k for k, v in revision_map.items()}},
    }
