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
  7. RETRY     `record` again with the SAME idempotency key -- every field of the receipt
               must replay and the store must still hold exactly one memory

Step 7 is the one a manual check usually skips. A retry that quietly writes a second copy
looks identical to a successful retry from the caller's side, and only the count shows it.

One caveat about the word "installed", so the result is not read as more than it is. What
this exercises in every case is the installed console script, the environment's interpreter
-- and therefore both runtime floors -- and the resolved dependency set, driven the way a
client drives it. WHICH package code it exercises depends on how the environment was built:
`tools/install.py` builds a normal, non-editable installation, so the code under test is the
environment's own copy; a developer environment from `uv sync --frozen` is EDITABLE, so the
venv points at the checkout's `src/` and the code under test is the working tree's. A pass
from an editable environment therefore says nothing about whether the source checkout is
still needed. Only running this against a non-editable installation whose source is gone
establishes that, and that is what CI's installed-distribution job does.

    nexus-memory-check                        # the server beside this command
    nexus-memory-check --server /path/to/venv/bin/nexus-memory
    python -m nexus_memory.install_check      # the same thing, no console script

This ships INSIDE the package, so verifying an installation does not require the source
checkout that built it. With no `--server`, it checks the one installed alongside its own
interpreter -- see `default_server`.

Exit 0 only if every step holds, 1 if a check or the transport failed, 2 if there is no
command to check or the arguments are unusable. `--json PATH` also writes the full report
to PATH as a JSON file; the human-readable summary always goes to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Deliberately awkward bytes: a newline, a tab, non-ASCII text, an emoji outside the BMP and
# a combining sequence. The point is the SAMPLE, not the comparison. Python's `==` on str is
# already codepoint-exact -- "\u00e9" == "e\u0301" is False -- so the byte comparison below
# is exactly as strict as `==`, not stricter; it is written that way to make the unit
# explicit and to report a byte count. What the payload buys is coverage of the round trip:
# JSON encoding, a stdio transport, one process writing and another reading. That is where
# something gets re-encoded, escaped or line-folded, and an ASCII sample would sail through.
CONTENT = (
    "Nexus install check: the payments retry budget is three attempts with "
    "exponential backoff.\n"
    "\tCaveat \u2014 the \u201cintegration\u201d suite must run after any change: "
    "caf\u00e9 na\u00efve \u65e5\u672c\u8a9e \u0440\u0443\u0441\u0441\u043a\u0438\u0439 "
    "e\u0301 \U0001f9ee\n"
    "Final line, no trailing newline after this."
)
KIND = "procedure"
TAGS = ["payments", "install-check"]
TERM = "backoff"
# The full receipt a retry must replay. Named here so the check and the claim it
# makes in the docstring cannot drift apart.
RECEIPT_FIELDS = ("memory_id", "revision_id", "operation_id", "durable_seq",
                  "operation")


GUIDE = "https://github.com/soryko/Nexus/blob/main/docs/experimental-release.md"


def default_server(executable: str) -> Path:
    """The server installed beside `executable`, which is normally this interpreter.

    `absolute()`, deliberately, never `resolve()`. A virtual environment's `bin/python` is
    typically a symlink to the base interpreter it was created from, so resolving it first
    would take the parent of the BASE runtime and look for `nexus-memory` there: absent, or
    -- worse -- a different installation's copy, which would then be checked and reported
    as though it were this one. `absolute()` also leaves the path's own spaces and symlinked
    parent directories exactly as the caller has them.
    """
    name = "nexus-memory.exe" if os.name == "nt" else "nexus-memory"
    return Path(executable).absolute().with_name(name)


def _runtime(python: Path) -> str | None:
    """-> "python X.Y.Z, sqlite A.B.C" for an interpreter, or None if it will not answer."""
    probe = ("import sys, sqlite3; "
             "print('python %s, sqlite %s' % ('.'.join(map(str, sys.version_info[:3])), "
             "sqlite3.sqlite_version))")
    try:
        r = subprocess.run([str(python), "-c", probe],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def _floors() -> str:
    """Both floors, read from the installed package that enforces one and declares the other.

    The source checkout may be gone; these come from the installation itself, so the numbers
    printed here are the numbers this very server will refuse to start below.
    """
    try:
        from nexus_memory.storage.sqlite import SQLiteRepository
        sq = ".".join(map(str, SQLiteRepository.MINIMUM_SQLITE))
    except Exception:
        sq = "?"
    try:
        from importlib.metadata import metadata
        py = (metadata("nexus-memory")["Requires-Python"] or "?").lstrip(">=, ")
    except Exception:
        py = "?"
    return f"python >= {py}, sqlite >= {sq}"


def _diagnose(server: str) -> None:
    """Why the transport closed, using what is here rather than what built it.

    The old advice pointed at `tools/install.py`, which is exactly the file someone running
    an installed checker may not have. So probe the interpreter sitting beside the server --
    the one the client actually launches -- and print both of its numbers next to both
    floors, which is the comparison that answers this in one line.
    """
    print("\n  The installed command did not serve MCP. Most often this is a runtime\n"
          "  below a floor: the server writes `startup_error: unsupported_runtime` to\n"
          "  its stderr and closes, which a client sees only as a closed transport.")
    python = Path(server).absolute().with_name("python")
    found = _runtime(python) if python.exists() else None
    print(f"\n  required : {_floors()}")
    if found:
        print(f"  installed: {found}   ({python})")
    else:
        print(f"  installed: could not probe an interpreter at {python}")
    print(f"\n  Installation and upgrade instructions: {GUIDE}")


def _structured(result) -> dict:
    # Both spellings. The MCP Python client exposes this field as `structured_content`;
    # the wire name is `structuredContent`, and reading only the wire spelling off the
    # client object silently finds nothing and falls through to re-parsing the text block.
    # That fallback happens to work here, which is exactly why the miss is easy to keep.
    for attr in ("structured_content", "structuredContent"):
        data = getattr(result, attr, None)
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
        # Compare the bytes. Not because `==` is too weak -- it is not, see the note on
        # CONTENT -- but because the unit under test is what the server stored and returned,
        # and the isinstance guard makes a non-str payload fail the check rather than raise.
        returned = got.get("content")
        same = (isinstance(returned, str)
                and returned.encode("utf-8") == CONTENT.encode("utf-8"))
        step("content is byte-identical", same,
             f"{len(CONTENT.encode('utf-8'))} bytes, identical" if same
             else f"differs: {str(returned)[:60]!r}")
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
        # Every field of the receipt, not two of them. A retry that replays the identifiers
        # while moving `durable_seq` has not replayed the original write, and comparing only
        # the ids is how that would go unnoticed. Missing on BOTH sides fails the check
        # rather than passing vacuously.
        differing = [f for f in RECEIPT_FIELDS
                     if replay.get(f) != receipt.get(f) or receipt.get(f) is None]
        step("retry replays the whole receipt", not differing,
             f"all {len(RECEIPT_FIELDS)} fields match"
             if not differing else
             "differs on " + ", ".join(
                 f"{f}: {receipt.get(f)!r} -> {replay.get(f)!r}" for f in differing))
        step("retry wrote no duplicate", st3.get("active_memories") == 1,
             f"active_memories={st3.get('active_memories')}")
    except Unreachable as exc:
        step("server answers MCP", False, str(exc))
        _diagnose(server)
    finally:
        if keep:
            print(f"\n  database kept at {db}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    return {"server": server, "passed": ok, "steps": steps,
            "database": db if keep else "(removed)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="nexus-memory-check",
        description="Store, restart, retrieve, retry -- against the installed command.")
    ap.add_argument("--server", default=None,
                    help="the nexus-memory command to check "
                         "(default: the one installed beside this one)")
    ap.add_argument("--json", default="", metavar="PATH",
                    help="also write the full report to this file as JSON")
    ap.add_argument("--keep-db", action="store_true")
    a = ap.parse_args(argv)

    # Resolved here rather than as an argparse default so the path is computed from the
    # interpreter actually running, not from whatever imported this module first.
    server = a.server or str(default_server(sys.executable))

    if not Path(server).exists():
        print(f"no installed command at {server}\n"
              f"  install one first: {GUIDE}")
        return 2
    print(f"Nexus install check -- store, restart, retrieve, retry\n  {server}\n")
    rep = check(server, a.keep_db)
    print(f"\n{'PASS' if rep['passed'] else 'FAIL'}: "
          f"{sum(1 for s in rep['steps'] if s['passed'])}/{len(rep['steps'])} checks")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1) + "\n")
        print(f"-> {a.json}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
