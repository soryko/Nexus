"""Reach the memory store the way an arm reaches it: over MCP, from inside the boundary.

The gate this replaces asked the wrong process. `memory_visibility()` opened the database
from the HARNESS, outside the sandbox, and `nexus_server_starts()` ran `nexus-memory
--help`, which never opens a store at all -- `main()` constructs `SQLiteRepository` only
after argument parsing, so `--help` returns 0 whether or not the store is reachable. Both
reported a healthy corpus while the arm's own read of it was denied.

The denial was real and is reproduced in `validate_boundary.wal_sidecars_readable`: the
profile allowed the `.db` file as a literal, and a WAL-mode store is three files. With
`nexus-dev.db-wal` and `-shm` on disk the server cannot open the database; with them absent
it can, so the failure appears only when something else holds the store open -- which is
exactly the state a run is in.

This probe runs under `sandbox-exec`, launches the server as the arm's MCP client would,
and requires BOTH halves: `status` must report the expected number of active memories, and
a `search` must return at least one hit. Status alone is not retrieval -- a store that
opens and answers no query is the same failure one layer down.

Usage:  python3 mcp_probe.py <server-exe> <db> <namespace> <actor> <query> [expect-active]
Stdout is one JSON object. Exit 0 only if both halves hold.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def _probe(exe: str, db: str, ns: str, actor: str, query: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=exe, args=["--db", db, "--namespace", ns, "--actor", actor])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            st = await session.call_tool("status", {})
            sr = await session.call_tool("search", {"query": query, "limit": 20})
    return {"status": _structured(st), "search": _structured(sr)}


def _structured(result) -> dict:
    """The structured payload if the server sent one, else the text block it fell back to."""
    data = getattr(result, "structuredContent", None)
    if data:
        return data
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text[:500]}
    return {}


def main() -> int:
    exe, db, ns, actor, query = sys.argv[1:6]
    expect = int(sys.argv[6]) if len(sys.argv) > 6 else None
    out: dict = {"db": db, "namespace": ns, "actor": actor, "query": query,
                 "sidecars_present": sorted(
                     Path(db + s).name for s in ("-wal", "-shm") if Path(db + s).exists())}
    try:
        got = asyncio.run(_probe(exe, db, ns, actor, query))
    except Exception as exc:                      # a denied read surfaces here, as it should
        out.update({"reachable": False, "retrieved": False,
                    "error": f"{type(exc).__name__}: {exc}"[:400]})
        print(json.dumps(out))
        return 1
    status, search = got["status"], got["search"]
    active = status.get("active_memories")
    hits = search.get("hits") or []
    out.update({
        "reachable": active is not None,
        "active_memories": active,
        "expected_active": expect,
        "active_as_expected": (expect is None or active == expect),
        "hits": len(hits),
        "retrieved": bool(hits),
        "first_hit": (hits[0].get("memory_id") if hits else None),
        "sqlite_version": status.get("sqlite_version"),
    })
    print(json.dumps(out))
    return 0 if (out["reachable"] and out["retrieved"] and out["active_as_expected"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
