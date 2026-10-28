"""Memory available / retrieved / used, declared before the records were read.

A connected server establishes only the first. These three are not one measure and are never
collapsed:

  available  the server was reachable, its tools were in the arm's outbound inventory, and
             the store held the frozen corpus. A property of the harness, not the agent.
  retrieved  the agent called a Nexus read tool AND at least one call returned a memory body.
  used       a retrieved memory is load-bearing in the patch.

The third cannot be established from the patch alone for task d1: protocol-a1 section 7b
labels both of d1's necessary facts DISCOVERABLE, so the agent could have read them out of
the checkout. The strongest available evidence is ordering -- retrieval before the first
source edit -- and that is evidence, not proof. Reported as such.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

RUN = Path(sys.argv[1]) / "a1run"
NECESSARY = {"c01": "resolve_envvar_value returns a value only when it is truthy",
             "c03": "Option overrides resolve_envvar_value to add the automatic-envvar path"}
READ_TOOLS = {"mcp__nexus__search", "mcp__nexus__get", "mcp__nexus__history", "mcp__nexus__status"}
WRITE_TOOLS = {"mcp__nexus__record", "mcp__nexus__revise", "mcp__nexus__forget"}

data = json.loads((RUN / "records.json").read_text())
corpus = json.loads((Path("/Users/soko/Cerebros/nexus-memory/benchmarks/agent/corpus-dev-a1.json")).read_text())
bodies = {m["id"]: m["content"] for m in corpus["memories"]}

print(f"task={data['task']}  seed={data['seed']}  order={' -> '.join(data['order'])}")
print(f"frozen store unchanged across all arms: {data['store_unchanged']}\n")

rows = []
for rec in data["records"]:
    arm = rec["arm"]
    calls = rec["tool_calls"]
    nexus_calls = [c for c in calls if (c["name"] or "").startswith("mcp__nexus__")]
    read_calls = [c for c in nexus_calls if c["name"] in READ_TOOLS]
    write_attempts = [c for c in nexus_calls if c["name"] in WRITE_TOOLS]
    # which corpus memories actually came back in a tool result
    returned = sorted({cid for c in read_calls
                       for cid, body in bodies.items()
                       if body[:60] in (c.get("result") or "")})
    # notes arm: did it ever read the rendered file?
    notes_reads = [c for c in calls
                   if "NOTES-FROM-EARLIER-WORK" in json.dumps(c.get("input", {}))
                   or "NOTES-FROM-EARLIER-WORK" in (c.get("result") or "")[:2000]]
    patch = (RUN / "arms" / arm / "patch.diff").read_text()
    # ordering evidence: first memory access vs first edit of the source file
    def first_index(pred):
        for i, c in enumerate(calls):
            if pred(c):
                return i
        return None
    i_mem = first_index(lambda c: c["name"] in READ_TOOLS) if arm == "nexus" else \
            first_index(lambda c: "NOTES-FROM-EARLIER-WORK" in json.dumps(c.get("input", {}))) \
            if arm == "notes" else None
    i_edit = first_index(lambda c: "core.py" in json.dumps(c.get("input", {})))
    rows.append({
        "arm": arm,
        "available": arm == "nexus",
        "n_tool_calls": len(calls),
        "nexus_read_calls": len(read_calls),
        "nexus_write_attempts": len(write_attempts),
        "memories_returned": returned,
        "necessary_returned": [c for c in returned if c in NECESSARY],
        "notes_file_reads": len(notes_reads),
        "retrieval_before_first_src_edit": (None if i_mem is None or i_edit is None
                                            else i_mem < i_edit),
        "patch_touches_resolve_envvar": "resolve_envvar_value" in patch,
        "checks_passed": rec["scored"]["passed"],
        "failing_functions": rec["scored"]["failing_functions"],
        "terminal": rec["terminal"],
        "wall_clock_s": rec["wall_clock_s"],
        "usage": rec["usage"],
        "web_search_requests": rec["web_search_requests"],
        "permission_denials": len(rec.get("permission_denials") or []),
    })

for r in rows:
    print(f"--- {r['arm']} ---")
    for k, v in r.items():
        if k != "arm":
            print(f"    {k}: {v}")
(RUN / "analysis.json").write_text(json.dumps(rows, indent=1))
