"""Seed a Nexus store with the frozen dev-a1 corpus, in corpus order."""
import json, sys
from pathlib import Path
sys.path.insert(0, "/Users/soko/Cerebros/nexus-memory/src")
from nexus_memory.domain.models import MemoryInput, Scope
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.transport.mcp_server import _prepare_new_storage

# Scope is (namespace, actor) and it ISOLATES. Seeding as one actor and serving as
# another made a 13-memory store report active_memories=0 to the agent. Both sides
# now take the scope from the same place.
corpus = json.loads(Path(sys.argv[1]).read_text())
db = Path(sys.argv[2])
_prepare_new_storage(db)
service = MemoryService(SQLiteRepository(db), Scope(sys.argv[3], sys.argv[4]))
out = []
for m in corpus["memories"]:
    receipt = service.record(
        MemoryInput(content=m["content"], kind=m["kind"], tags=tuple(m["tags"])),
        idempotency_key=f"dev-a1-{m['id']}",
    )
    out.append({"corpus_id": m["id"], "memory_id": receipt.memory_id, "kind": m["kind"]})
print(json.dumps(out, indent=1))
