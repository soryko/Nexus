#!/usr/bin/env python3
"""Does the INSTALLED package actually hold a memory across a restart? Measured, not assumed.

An installation that imports, and a server that starts, and a write that returns a receipt
are three things that can all succeed while the one property this project claims does not
hold: that an agent stores a fact once and reads it back in a later session, on a different
process, with the same bytes it wrote.

So this drives the installed console command over MCP stdio, in separate processes, and
checks the claim end to end:

  1. START     launch the installed command against a fresh database
  2. STORE     `record` a memory, keep its receipt
  3. STOP      close the session; the process exits
  4. RESTART   launch it again, a NEW process against the same file
  5. RETRIEVE  `get` must return byte-identical content, kind and tags
  6. SEARCH    `search` must find it by a term from its body
  7. RETRY     `record` again with the SAME idempotency key -- the receipt must replay and
               the store must still hold exactly one memory

Step 7 is the one a manual check usually skips. A retry that quietly writes a second copy
looks identical to a successful retry from the caller's side, and only the count shows it.

    python3 tools/check_install.py --server /path/to/.venv/bin/nexus-memory
    python3 tools/check_install.py            # uses ./.venv/bin/nexus-memory

Exit 0 only if every step holds. One JSON object on stdout with `--json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CONTENT = ("Nexus install check: the payments retry budget is three attempts with "
           "exponential backoff, and the integration suite must run after any change to it.")
KIND = "procedure"
TAGS = ["payments", "install-check"]
TERM = "backoff"


def _structured(result) -> dict:
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


async def _run(server: str, db: str, ns: str, actor: str, calls) -> list[dict]:
    """One process: open a session, make the calls, close it. The process exits with it."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(
        command=server, args=["--db", db, "--namespace", ns, "--actor", actor])
    out = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, args in calls:
                out.append(_structured(await session.call_tool(name, args)))
    return out


class Unreachable(RuntimeError):
    """The server did not answer. A diagnosis, not a traceback.

    A command that exits immediately, an interpreter below the SQLite floor, a missing
    dependency -- all of them surface here as a closed transport, and all of them are the
    same message to the person running this: the thing you installed does not serve MCP.
    """


def _attempt(server: str, db: str, ns: str, actor: str, calls) -> list[dict]:
    try:
        return asyncio.run(_run(server, db, ns, actor, calls))
    except BaseException as exc:                  # ExceptionGroup included
        raise Unreachable(_why(exc)) from None


def _why(exc: BaseException, depth: int = 0) -> str:
    """The innermost useful message from a (possibly grouped) transport failure."""
    inner = getattr(exc, "exceptions", None)
    if inner and depth < 4:
        return _why(inner[0], depth + 1)
    msg = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {msg}"[:200]


def check(server: str, keep: bool = False) -> dict:
    ns, actor = "install-check", "local"
    key = f"install-check-{uuid.uuid4().hex[:12]}"
    tmp = tempfile.mkdtemp(prefix="nexus-install-check-")
    db = str(Path(tmp) / "nexus.sqlite3")
    steps: list[dict] = []
    ok = True

    def step(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and passed
        steps.append({"step": name, "passed": passed, "detail": detail})
        print(f"  {'ok ' if passed else 'BAD'} {name:<34} {detail}")

    try:
        # 1-2. START and STORE, in one process.
        wrote = _attempt(server, db, ns, actor, [
            ("record", {"content": CONTENT, "kind": KIND, "tags": TAGS,
                        "idempotency_key": key}),
            ("status", {}),
        ])
        receipt, st1 = wrote[0], wrote[1]
        mem_id = receipt.get("memory_id")
        rev_id = receipt.get("revision_id")
        step("start and store", bool(mem_id and rev_id),
             f"memory_id={str(mem_id)[:12]}… revision_id={str(rev_id)[:12]}…")
        step("one memory after the write", st1.get("active_memories") == 1,
             f"active_memories={st1.get('active_memories')}")
        if not mem_id:
            raise Unreachable("the server returned no memory_id for the first write")

        # 3-6. The first process is gone. A NEW one opens the same file.
        back = _attempt(server, db, ns, actor, [
            ("get", {"memory_id": mem_id}),
            ("search", {"query": TERM, "limit": 10}),
            ("status", {}),
        ])
        got, found, st2 = back
        step("restart: server starts again", bool(st2),
             f"active_memories={st2.get('active_memories')}")
        step("content is byte-identical", got.get("content") == CONTENT,
             "same bytes" if got.get("content") == CONTENT
             else f"differs: {str(got.get('content'))[:60]!r}")
        step("kind and tags survive", got.get("kind") == KIND
             and sorted(got.get("tags") or []) == sorted(TAGS),
             f"kind={got.get('kind')} tags={got.get('tags')}")
        step("revision id is stable", got.get("revision_id") == rev_id,
             f"{str(got.get('revision_id'))[:12]}…")
        hits = found.get("hits") or []
        step("search finds it", any(h.get("memory_id") == mem_id for h in hits),
             f"{len(hits)} hit(s) for {TERM!r}")

        # 7. RETRY with the same key, in a third process.
        again = _attempt(server, db, ns, actor, [
            ("record", {"content": CONTENT, "kind": KIND, "tags": TAGS,
                        "idempotency_key": key}),
            ("status", {}),
        ])
        replay, st3 = again
        step("retry replays the receipt", replay.get("memory_id") == mem_id
             and replay.get("revision_id") == rev_id,
             f"memory_id={str(replay.get('memory_id'))[:12]}… "
             f"revision_id={str(replay.get('revision_id'))[:12]}…")
        step("retry wrote no duplicate", st3.get("active_memories") == 1,
             f"active_memories={st3.get('active_memories')}")
    except Unreachable as exc:
        step("server answers MCP", False, str(exc))
        print("\n  The installed command did not serve MCP. Most often this is a runtime\n"
              "  below a floor: the server writes `startup_error: unsupported_runtime` to\n"
              "  its stderr and closes, which a client sees only as a closed transport.\n"
              "  Check both numbers:  python3 tools/install.py --check-only")
    finally:
        if keep:
            print(f"\n  database kept at {db}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    return {"server": server, "passed": ok, "steps": steps,
            "database": db if keep else "(removed)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    default = REPO / ".venv" / ("Scripts/nexus-memory.exe" if os.name == "nt"
                                else "bin/nexus-memory")
    ap.add_argument("--server", default=str(default))
    ap.add_argument("--json", default="")
    ap.add_argument("--keep-db", action="store_true")
    a = ap.parse_args()

    if not Path(a.server).exists():
        print(f"no installed command at {a.server}\n"
              f"  build one first:  python3 tools/install.py")
        return 2
    print(f"Nexus install check -- store, restart, retrieve, retry\n  {a.server}\n")
    rep = check(a.server, a.keep_db)
    print(f"\n{'PASS' if rep['passed'] else 'FAIL'}: "
          f"{sum(1 for s in rep['steps'] if s['passed'])}/{len(rep['steps'])} checks")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1) + "\n")
        print(f"-> {a.json}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
