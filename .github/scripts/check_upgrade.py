#!/usr/bin/env python3
"""v0.1.0a1 -> candidate, on one database, in isolation.

The installation mechanism changed. The storage did not, and this is where that claim is
either true or it is not: a memory written and revised by the RELEASED server must still
be readable -- current content, revision history, identifiers, search, idempotent retry --
by the candidate server, with the database untouched in between.

Both servers are installed from their own sources by their own installers, into separate
environments. Nothing here uses the author's live database.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
PY = sys.argv[2]
OUT = Path(sys.argv[3]).resolve()
WORK = Path(tempfile.mkdtemp(prefix="nexus-upgrade-"))

NS, ACTOR = "upgrade-smoke", "local"
KEY = "upgrade-smoke-1"
V1 = "Deploys freeze at 16:00 on Fridays; the on-call owns any exception."
V2 = ("Deploys freeze at 16:00 on Fridays; the on-call owns any exception. "
      "Café rule — 日本語 \U0001f9ee never on a release day.")

report: dict = {"steps": [], "passed": False}


def say(name: str, detail: str) -> None:
    report["steps"].append({"step": name, "passed": True, "detail": detail})
    print(f"  ok  {name:<40} {detail}", flush=True)


class Bad(RuntimeError):
    pass


def clean_env(venv: Path) -> dict:
    e = {k: v for k, v in os.environ.items()
         if k not in ("PYTHONPATH", "PYTHONHOME", "UV_PYTHON")}
    e["UV_PROJECT_ENVIRONMENT"] = str(venv)
    return e


def build(ref: str, label: str) -> Path:
    """Install one ref from its OWN source with its OWN installer."""
    src = WORK / f"src {label}"
    src.mkdir(parents=True)
    tar = WORK / f"{label}.tar"
    with tar.open("wb") as fh:
        subprocess.run(["git", "-C", str(REPO), "archive", ref], stdout=fh, check=True)
    shutil.unpack_archive(str(tar), str(src), format="tar")
    tar.unlink()
    venv = WORK / f"env {label}"
    r = subprocess.run([PY, str(src / "tools" / "install.py"),
                        "--python", PY, "--venv", str(venv)],
                       capture_output=True, text=True, cwd=str(src),
                       env=clean_env(venv), timeout=900)
    if r.returncode != 0:
        raise Bad(f"installing {ref} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    server = venv / "bin" / "nexus-memory"
    if not server.exists():
        raise Bad(f"{ref} installed without a server")
    ver = subprocess.run([str(venv / "bin" / "python"), "-c",
                          "import importlib.metadata as m;print(m.version('nexus-memory'))"],
                         capture_output=True, text=True, env=clean_env(venv)).stdout.strip()
    say(f"installed {label}", f"{ref} -> {ver}")
    report[f"{label}_version"] = ver
    report[f"{label}_ref"] = ref
    return server


def call(driver_env: Path, server: Path, db: Path, calls: list) -> list:
    """Drive `server` over MCP using `driver_env`'s installed client helper.

    The driver is always the CANDIDATE environment: v0.1.0a1 has no packaged client
    helper -- that module is what this release adds -- and one client driving both
    servers is in any case the right shape for the comparison. What is under test is the
    server on the other end of the transport, not the client.
    """
    code = ("import json,sys;"
            "from nexus_memory.install_check import _attempt;"
            "print(json.dumps(_attempt(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],"
            " json.loads(sys.argv[5]))))")
    r = subprocess.run([str(driver_env / "bin" / "python"), "-c", code,
                        str(server), str(db), NS, ACTOR, json.dumps(calls)],
                       capture_output=True, text=True, cwd=str(WORK),
                       env=clean_env(driver_env), timeout=600)
    if r.returncode != 0:
        raise Bad(f"MCP call failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for want, got in zip(calls, out):
        if not isinstance(got, dict) or "error" in got or "text" in got:
            raise Bad(f"the server rejected {want[0]}: {got}")
    return out


def main() -> int:
    try:
        old = build("v0.1.0a1", "old")
        new = build("HEAD", "new")
        old_env, new_env = old.parent.parent, new.parent.parent
        if report["old_version"] == report["new_version"]:
            raise Bad("both environments report the same version; this would compare a "
                      "release against itself")

        db = WORK / "user data" / "memory.sqlite3"
        db.parent.mkdir(parents=True)

        # --- write and revise with the RELEASED server -------------------------------
        wrote = call(new_env, old, db, [
            ["record", {"content": V1, "kind": "constraint", "tags": ["deploys"],
                        "idempotency_key": KEY}]])[0]
        mem_id, rev1 = wrote["memory_id"], wrote["revision_id"]
        revised = call(new_env, old, db, [
            ["revise", {"memory_id": mem_id, "content": V2,
                        "expected_revision_id": rev1,
                        "idempotency_key": "upgrade-smoke-revise-1"}]])[0]
        rev2 = revised["revision_id"]
        if rev2 == rev1:
            raise Bad("the revision did not produce a new revision id")
        say("old server wrote and revised", f"{mem_id[:12]}… rev1={rev1[:8]}… "
                                            f"rev2={rev2[:8]}…")

        # A pre-upgrade backup, as the documented procedure requires.
        backup = WORK / "backup" / "memory-before-upgrade.sqlite3"
        backup.parent.mkdir(parents=True)
        subprocess.run([str(old_env / "bin" / "python"), "-c",
                        "import sqlite3,sys;"
                        "s=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);"
                        "d=sqlite3.connect(sys.argv[2]);s.backup(d);d.close();s.close()",
                        Path(db).resolve().as_uri().removeprefix("file:"), str(backup)],
                       check=True, capture_output=True, text=True,
                       env=clean_env(old_env), timeout=300)
        say("pre-upgrade backup taken", f"{backup.stat().st_size} bytes")

        # --- read it all back with the CANDIDATE server ------------------------------
        got = call(new_env, new, db, [
            ["get", {"memory_id": mem_id}],
            ["history", {"memory_id": mem_id}],
            ["search", {"query": "freeze", "limit": 10}],
            ["record", {"content": V1, "kind": "constraint", "tags": ["deploys"],
                        "idempotency_key": KEY}],
            ["status", {}],
        ])
        cur, hist, found, replay, st = got

        if cur.get("content", "").encode() != V2.encode():
            raise Bad(f"the candidate returned different current content: "
                      f"{cur.get('content')!r}")
        if cur.get("revision_id") != rev2:
            raise Bad("the candidate reports a different current revision")
        revs = [r.get("revision_id") for r in (hist.get("entries") or [])]
        if rev1 not in revs or rev2 not in revs:
            raise Bad(f"history lost a revision: {revs}")
        if not any(h.get("memory_id") == mem_id for h in found.get("hits") or []):
            raise Bad("the candidate's search does not find the migrated memory")
        differing = [f for f in ("memory_id", "revision_id", "operation_id",
                                 "durable_seq", "operation")
                     if replay.get(f) != wrote.get(f)]
        if differing:
            raise Bad(f"the original receipt did not replay on the candidate: {differing}")
        if st.get("active_memories") != 1:
            raise Bad(f"active_memories={st.get('active_memories')} after the retry")
        say("candidate read the old database",
            f"content identical ({len(V2.encode())} bytes), rev history {len(revs)}, "
            f"search hit, receipt replayed, active_memories=1")

        # --- rollback: the retained old environment, and the backup ------------------
        back = call(new_env, old, db, [["get", {"memory_id": mem_id}]])[0]
        if back.get("content", "").encode() != V2.encode():
            raise Bad("the retained old environment cannot read the database it wrote")
        say("rollback to the old environment", "same content, same ids")

        from_backup = call(new_env, old, backup, [["get", {"memory_id": mem_id}],
                                                  ["status", {}]])
        # The backup was taken after the revise and before the upgrade, so the state it
        # must hold is the CURRENT revision at that moment -- V2, not the original write.
        if from_backup[0].get("content", "").encode() != V2.encode():
            raise Bad("the pre-upgrade backup does not hold the state it was taken from")
        if from_backup[0].get("revision_id") != rev2:
            raise Bad("the backup's current revision is not the one taken")
        say("pre-upgrade backup restores",
            f"current revision {rev2[:8]}…, active_memories="
            f"{from_backup[1].get('active_memories')}")

        report.update({
            "passed": True, "memory_id": mem_id,
            "revision_1": rev1, "revision_2": rev2,
            "revisions_in_history": revs,
            "receipt": wrote, "replayed_receipt": replay,
        })
    except Bad as exc:
        report["failure"] = str(exc)
        print(f"\n  BAD {exc}", flush=True)
    except Exception as exc:                                        # noqa: BLE001
        report["failure"] = f"{exc.__class__.__name__}: {exc}"
        print(f"\n  BAD {exc.__class__.__name__}: {exc}", flush=True)
    finally:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=1) + "\n")
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{'PASS' if report['passed'] else 'FAIL'} -> {OUT}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
