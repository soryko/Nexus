"""Execute the boundary the arms will run under, and record what held.

No model is invoked and no paid request is made. The five refusals are refused before a
socket to the provider is opened; the one ACCEPTED request goes to a loopback stub, which
is how the allowlist finally gets a positive control; and the only Claude Code invocation
is `--version`. What this validates is the repaired profile from `isolation.py` and the
repaired allowlist in `model_forwarder.py`, built exactly as `run_arms_isolated.py` builds
them, for every arm.

Four controls the third review added, each closing a gap where an instrument could have
reported success without having tested anything:

  memory over MCP   the arm's own path to the store -- inside the sandbox, `status` AND a
                    `search` with hits, with the WAL sidecars held live. It replaces a
                    `--help` that never opened a store and a `sqlite3.connect` that ran
                    outside the sandbox.
  store per arm     the store is allowed to the nexus arm and denied by name to the other
                    two, and the same probe is the negative control for those two.
  deny ordering     a denied path inside an ALLOWED subtree, which no other control covers
                    and on which `~/.claude/projects` depends.
  forwarder accept  a legitimate request driven over HTTP to the stub, so a forwarder that
                    had come to refuse everything can no longer pass.

Usage:  python3 validate_boundary.py <scratch-dir> <base-run-dir> <task> <python>
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent
sys.path.insert(0, str(BENCH))
import isolation
from model_forwarder import inspect_body

SCRATCH, BASE_RUN, TASK, PY = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
VENV_PY = REPO / ".venv-sqlite" / "bin" / "python"      # the interpreter Nexus itself runs on
ARMS = ("baseline", "nexus", "notes")
FWD_PORT = 8899
DECOY_PORT = 8901
STUB_PORT = 8902
CORPUS_SIZE = 13
MEMORY_PROBE_QUERY = "click option parameter"
SOURCE_CLONE = "/private/tmp/claude-501/-Users-soko-Cerebros-nexus-memory/215651df-6e30-4d49-a919-8b24f46175e8/scratchpad/click"


def layout(run: Path) -> None:
    """The directory shape `run_arms_isolated.prepare` produces, without running an arm.

    Including the store: the memory control needs a real one, and it is built here from the
    corpus by `seed_store.py` rather than copied from a hand-made file, so the validation
    runs against a store the repository can reproduce.
    """
    for arm in ARMS:
        dst = run / "arms" / arm / "repo"
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BASE_RUN / "base" / TASK, dst, symlinks=True)
        (run / "arms" / arm / "mcp.json").write_text("{}")
    # seeded through the project venv, not this interpreter: the store is Nexus's, and the
    # SQLite it must be built against is the one `.venv-sqlite` links, not whichever libsqlite
    # the python running this validator happens to have.
    subprocess.run([str(VENV_PY), str(BENCH / "seed_store.py"),
                    str(run / "nexus-dev.db"), "a1-dev", "agent",
                    "--map", str(run / "store-map.json")], check=True,
                   capture_output=True, text=True)


def paths_for(arm: str, run: Path, cwd: Path):
    """Mirrors `run_arms_isolated.boundary_paths`, including its arm condition: the store is
    allowed to the nexus arm and denied by name to the other two."""
    store = isolation.sqlite_read_paths(run / "nexus-dev.db")
    allow = isolation.default_allow_paths(cwd, PY) + [
        run / "arms" / arm,
        REPO / ".venv-sqlite", REPO / "src", REPO / "pyproject.toml"]
    deny = [BASE_RUN / "base" / "checks",
            *[(run / "arms" / a) for a in ARMS if a != arm],
            run / "scoring", Path(SOURCE_CLONE), BENCH]
    allow += store if arm == "nexus" else []
    deny += [] if arm == "nexus" else store
    return allow, deny


# --------------------------------------------------------------------------- forwarder --
REFUSABLE = [
    ("provider-side search tool",
     "POST", "/anthropic/v1/messages",
     {"model": "x", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}],
      "tools": [{"type": "web_search_20250305", "name": "web_search"}]}),
    ("provider-side code execution",
     "POST", "/anthropic/v1/messages",
     {"model": "x", "max_tokens": 16, "messages": [],
      "tools": [{"type": "code_execution_20250522", "name": "code_execution"}]}),
    ("provider-side MCP connector",
     "POST", "/anthropic/v1/messages",
     {"model": "x", "max_tokens": 16, "messages": [],
      "mcp_servers": [{"type": "url", "url": "https://example.invalid/mcp", "name": "x"}]}),
    ("route off the allowlist", "POST", "/anthropic/v1/models", {}),
    ("method off the allowlist", "GET", "/anthropic/v1/messages", None),
]
ACCEPTABLE_BODY = {"model": "x", "max_tokens": 16,
                   "messages": [{"role": "user", "content": "hi"}],
                   "tools": [{"name": "Read", "description": "read a file",
                              "input_schema": {"type": "object"}}]}


class _Stub(http.server.BaseHTTPRequestHandler):
    """Stands in for the provider, so the ACCEPTED path can be driven without paying for it.

    It records what actually arrived -- path, method and body -- which is the half the old
    control could not see: `inspect_body` says a body would be forwarded, not that `_route`
    resolved it, that `_forward` opened a connection, or that the upstream received the
    request intact.
    """
    received: list[dict] = []
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0) or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        _Stub.received.append({"path": self.path, "method": "POST", "body": body,
                               "has_auth_header": "x-api-key" in {k.lower() for k in self.headers}})
        msg = json.dumps({"type": "message", "role": "assistant",
                          "content": [{"type": "text", "text": "stub"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, *a):
        pass


def _wait(port: int) -> None:
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.1)


def forwarder_probes() -> dict:
    """Refusals AND one acceptance, both over HTTP.

    A refused request never opens a socket to the upstream, so the refusals cost nothing.
    The acceptance used to cost a paid call, so it was checked by calling `inspect_body`
    directly -- leaving `_route` and `_forward` with no positive control at all, exactly the
    vacuous-perfection failure `check_boundary` grew positive controls to close. It now goes
    over HTTP to a loopback stub, named to the forwarder through an environment variable the
    sandboxed arms cannot set, and the stub reports what it received.
    """
    stub = socketserver.TCPServer(("127.0.0.1", STUB_PORT), _Stub)
    stub.allow_reuse_address = True
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    env = dict(os.environ, A1_FORWARDER_STUB_UPSTREAM=f"127.0.0.1:{STUB_PORT}")
    srv = subprocess.Popen([sys.executable, str(BENCH / "model_forwarder.py"), str(FWD_PORT)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    _wait(FWD_PORT)
    out: dict = {}
    try:
        for label, method, path, body in REFUSABLE:
            conn = http.client.HTTPConnection("127.0.0.1", FWD_PORT, timeout=10)
            payload = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=payload,
                         headers={"content-type": "application/json"})
            r = conn.getresponse()
            text = r.read().decode()
            conn.close()
            out[label] = {"status": r.status, "refused": r.status in (403, 405, 413),
                          "message": (json.loads(text).get("error", {}).get("message", "")
                                      if text.startswith("{") else text)[:140]}
        # ...and the acceptance, all the way through to the stub
        conn = http.client.HTTPConnection("127.0.0.1", FWD_PORT, timeout=15)
        conn.request("POST", "/anthropic/v1/messages",
                     body=json.dumps(ACCEPTABLE_BODY).encode(),
                     headers={"content-type": "application/json", "x-api-key": "stub-not-a-key"})
        ar = conn.getresponse()
        atext = ar.read().decode()
        conn.close()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
        stub.shutdown()
        stub.server_close()

    got = _Stub.received[-1] if _Stub.received else {}
    forwarded_tools = [t.get("name") for t in ((got.get("body") or {}).get("tools") or [])]
    out["ordinary client-tool request accepted"] = {
        "status": ar.status,
        "refused": ar.status in (403, 405, 413),
        "expected_refused": False,
        "reached_upstream": bool(got),
        "upstream_path": got.get("path"),
        "tools_forwarded_intact": forwarded_tools == [t["name"] for t in ACCEPTABLE_BODY["tools"]],
        "body_echoed": '"stub"' in atext,
        "inspect_body_verdict": inspect_body(json.dumps(ACCEPTABLE_BODY).encode()),
    }
    acc = out["ordinary client-tool request accepted"]
    out["stub_upstream"] = f"127.0.0.1:{STUB_PORT}"
    out["refusals_held"] = all(v["refused"] for k, v in out.items()
                               if isinstance(v, dict) and "status" in v and "refused" in v
                               and k != "ordinary client-tool request accepted")
    out["acceptance_held"] = bool(acc["status"] == 200 and acc["reached_upstream"]
                                  and acc["upstream_path"] == "/anthropic/v1/messages"
                                  and acc["tools_forwarded_intact"])
    out["all_refusals_held"] = bool(out["refusals_held"] and out["acceptance_held"])
    return out


def loopback_scope(profile: Path, cwd: Path) -> dict:
    """The profile admits the forwarder's port and no other. A second local listener is the
    control: reachable outside the sandbox, refused inside it."""
    srv = subprocess.Popen(
        [sys.executable, "-c",
         f"import http.server,socketserver;socketserver.TCPServer.allow_reuse_address=True;"
         f"socketserver.TCPServer(('127.0.0.1',{DECOY_PORT}),"
         f"http.server.SimpleHTTPRequestHandler).serve_forever()"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", DECOY_PORT), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        return isolation.paired(profile, ["/usr/bin/curl", "-sS", "-m", "5", "-o", "/dev/null",
                                          f"http://127.0.0.1:{DECOY_PORT}/"], cwd)
    finally:
        srv.terminate()
        srv.wait(timeout=10)


def memory_reachable(profile: Path, cwd: Path, arm: str, run: Path) -> dict:
    """Retrieval over MCP, from inside the boundary, with the WAL sidecars live.

    This replaces `nexus-memory --help`. `--help` returns 0 before `main()` ever constructs
    a `SQLiteRepository`, so it reported a healthy server for a store the arm could not
    open; and the harness's other gate opened the store with `sqlite3.connect` from OUTSIDE
    the sandbox, where every path is readable. Between them, both gates passed on a store
    that was unreachable to the arm.

    The failure is intermittent by construction, which is why the probe forces the failing
    state rather than waiting for it: a WAL-mode store is `.db`, `-wal` and `-shm`, and the
    profile named only the first. With the sidecars checkpointed away the profile works;
    with something holding the store open -- a run -- it does not. A read-only connection is
    held across the probe so the sidecars exist, and cannot modify the corpus.

    For the arms that must NOT reach the store, the same probe is the negative control: it
    has to fail, and `paired` is not needed because the positive half is the nexus arm
    running the identical probe against the identical store.
    """
    exe = REPO / ".venv-sqlite" / "bin" / "nexus-memory"
    db = run / "nexus-dev.db"
    if not exe.exists() or not db.exists():
        return {"applicable": False, "reason": f"{exe if not exe.exists() else db} not present"}
    probe_py = run / "arms" / arm / "mcp_probe.py"       # BENCH is denied; the arm dir is not
    shutil.copy2(BENCH / "mcp_probe.py", probe_py)
    holder = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    holder.execute("select 1").fetchone()
    try:
        sidecars = sorted(p.name for s in ("-wal", "-shm")
                          if (p := Path(f"{db}{s}")).exists())
        r = isolation.probe(profile, [str(REPO / ".venv-sqlite/bin/python"), str(probe_py),
                                      str(exe), str(db), "a1-dev", "agent",
                                      MEMORY_PROBE_QUERY, str(CORPUS_SIZE)], cwd, timeout=120)
    finally:
        holder.close()
    try:
        detail = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = {"error": (r.stderr or r.stdout).strip()[-300:]}
    reached = r.returncode == 0
    return {"applicable": True, "arm_should_reach": arm == "nexus", "reached": reached,
            "as_intended": reached == (arm == "nexus"),
            "wal_sidecars_live_during_probe": sidecars,
            "active_memories": detail.get("active_memories"), "hits": detail.get("hits"),
            "detail": detail}


# The profile as it stood at 6fe65c3, reproduced verbatim so the repair is measured against
# it rather than merely asserted. `(allow default)` with a handful of denies left the whole
# benchmark directory readable, and `write_profile` dropped any deny whose path did not exist.
PRIOR_PROFILE = """(version 1)
(allow default)

(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))

{denies}
"""


def scratch_root_entry_only(profile: Path, run: Path, cwd: Path) -> dict:
    """The runner's scratch root is openable; nothing under it is.

    Claude Code opens `/tmp/claude-<uid>` at startup, so the deny-by-default profile stopped
    every run with EPERM before a single token was spent -- and `claude --version`, the
    positive control, never touches it. The smoke test found this; no model-free control
    could have.

    The obvious repair is the dangerous one. That directory is the parent of this run's
    fixtures, the held-out checks, the source clone and every other session's scratchpad, and
    `allow_paths` emits a directory as a SUBPATH -- so adding it there grants the whole tree.
    Measured, not assumed: with the subpath spelling the arm reads the held checks; with the
    entry spelling it does not, and the runner still starts.
    """
    root = isolation.RUNNER_SCRATCH_ENTRIES[0]
    held = isolation._first_file(BASE_RUN / "base" / "checks" / TASK)
    wide = isolation.write_profile(
        run / "scratch-subpath.sb", cwd, [],
        isolation.default_allow_paths(cwd, PY) + [cwd, root], FWD_PORT, allow_entries=[])
    out = {
        "root": str(root),
        "entry_openable": not isolation.paired(profile, ["/bin/ls", str(root)], cwd)["blocked_inside"],
        "held_checks_under_it_denied": isolation.paired(
            profile, ["/bin/cat", str(held)], cwd)["demonstrates_boundary"] if held else None,
        "runner_starts": isolation.probe(profile, ["claude", "--version"], cwd).returncode == 0,
        # the spelling that would have been wrong, measured rather than argued about
        "subpath_spelling_would_expose_held_checks": (
            not isolation.paired(wide, ["/bin/cat", str(held)], cwd)["blocked_inside"]
            if held else None),
    }
    out["entry_only"] = bool(out["entry_openable"] and out["held_checks_under_it_denied"]
                             and out["runner_starts"])
    return out


def prior_store_allow_blocked(run: Path, cwd: Path) -> dict:
    """Reproduce the defect: the store allowed as ONE file, with the sidecars live.

    Asserted alone this is just a claim about SQLite. Run both ways it is a measurement, and
    it is the pair that matters -- the same probe, the same store, the same held-open
    connection, differing only in whether `-wal` and `-shm` are named in the profile. The
    old spelling must fail and the repaired one must succeed, or the repair fixed nothing
    and something else changed.
    """
    cwd_nexus = run / "arms" / "nexus" / "repo"
    db = run / "nexus-dev.db"
    exe = REPO / ".venv-sqlite" / "bin" / "nexus-memory"
    probe_py = run / "arms" / "nexus" / "mcp_probe.py"
    shutil.copy2(BENCH / "mcp_probe.py", probe_py)
    argv = [str(VENV_PY), str(probe_py), str(exe), str(db), "a1-dev", "agent",
            MEMORY_PROBE_QUERY, str(CORPUS_SIZE)]
    out = {}
    holder = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    holder.execute("select 1").fetchone()
    try:
        out["sidecars_live"] = sorted(p.name for s in ("-wal", "-shm")
                                      if (p := Path(f"{db}{s}")).exists())
        for label, store_paths in (("prior_allow_db_only", [db]),
                                   ("repaired_allow_db_and_sidecars",
                                    isolation.sqlite_read_paths(db))):
            allow = isolation.default_allow_paths(cwd_nexus, PY) + [
                run / "arms" / "nexus", REPO / ".venv-sqlite", REPO / "src",
                REPO / "pyproject.toml", *store_paths]
            prof = isolation.write_profile(run / f"{label}.sb", cwd_nexus, [], allow, FWD_PORT)
            r = isolation.probe(prof, argv, cwd_nexus, timeout=120)
            tail = (r.stdout or r.stderr).strip().splitlines()[-1:]
            out[label] = {"reached": r.returncode == 0, "tail": tail}
    finally:
        holder.close()
    out["repair_is_the_difference"] = bool(
        not out["prior_allow_db_only"]["reached"]
        and out["repaired_allow_db_and_sidecars"]["reached"])
    out["note"] = ("with the sidecars on disk the one-file allow cannot open the store; "
                   "with them checkpointed away it can, which is why no gate saw this")
    return out


def prior_profile_leaked(run: Path, cwd: Path) -> dict:
    """Read the benchmark directory, and a not-yet-created denied directory, under the OLD
    profile with the OLD deny list. Both should succeed -- that is the defect the review
    reported. The old list is reproduced here rather than reusing the repaired one, because
    the repair is partly the list itself: `BENCH` was never on it."""
    prior_denies = [BASE_RUN / "base" / "checks",
                    *[(run / "arms" / a) for a in ARMS if a != "baseline"],
                    run / "scoring", Path(SOURCE_CLONE)]
    denies = "\n".join(f'(deny file-read* (subpath "{p.resolve()}"))'
                        for p in prior_denies if p.exists())      # the old existence filter
    prof = run / "prior.sb"
    prof.write_text(PRIOR_PROFILE.format(denies=denies))
    bench_file = next(p for p in sorted(BENCH.glob("*.md")) if p.is_file())
    bench = isolation.probe(prof, ["/bin/cat", str(bench_file)], cwd)
    future = run / "scoring"
    shutil.rmtree(future, ignore_errors=True)
    future.mkdir(parents=True)
    (future / "answers.txt").write_text("scored answers\n")
    fut = isolation.probe(prof, ["/bin/cat", str(future / "answers.txt")], cwd)
    shutil.rmtree(future, ignore_errors=True)
    return {"bench_dir_readable_under_prior_profile": bench.returncode == 0,
            "bench_file": str(bench_file.relative_to(BENCH)),
            "future_deny_path_readable_under_prior_profile": fut.returncode == 0,
            "note": "both True is the defect; the repaired profile denies both"}


def main() -> int:
    run = SCRATCH / "boundary-validation"
    if run.exists():
        shutil.rmtree(run)
    run.mkdir(parents=True)
    layout(run)

    record: dict = {"task": TASK, "python": PY, "forwarder_port": FWD_PORT, "arms": {}}
    ok = True
    for arm in ARMS:
        cwd = run / "arms" / arm / "repo"
        allow, deny = paths_for(arm, run, cwd)
        profile = isolation.write_profile(run / "arms" / arm / "sandbox.sb", cwd, deny, allow,
                                          FWD_PORT)
        bound = isolation.check_boundary(profile, cwd, BASE_RUN / "base" / "checks" / TASK,
                                         PY, deny, BENCH)
        bound["loopback_limited_to_forwarder_port"] = loopback_scope(profile, cwd)
        bound["memory_reachable"] = memory_reachable(profile, cwd, arm, run)
        bound["scratch_root_entry_only"] = scratch_root_entry_only(profile, run, cwd)
        record["arms"][arm] = bound
        mem = bound["memory_reachable"]
        held = (bound["all_hold"]
                and bound["loopback_limited_to_forwarder_port"]["demonstrates_boundary"]
                and bound["scratch_root_entry_only"]["entry_only"]
                and (not mem.get("applicable") or mem["as_intended"]))
        ok &= held
        print(f"[{arm}] negative {bound['negative_controls']}")
        print(f"[{arm}] positive {bound['positive_controls']} "
              f"runner={bound.get('runner_version')}")
        print(f"[{arm}] decoy loopback port blocked inside / reachable outside: "
              f"{bound['loopback_limited_to_forwarder_port']['demonstrates_boundary']}")
        sre = bound["scratch_root_entry_only"]
        print(f"[{arm}] runner scratch root {sre['root']}: openable={sre['entry_openable']}, "
              f"held checks under it denied={sre['held_checks_under_it_denied']}, "
              f"runner starts={sre['runner_starts']} "
              f"(subpath spelling would expose them: "
              f"{sre['subpath_spelling_would_expose_held_checks']})")
        print(f"[{arm}] memory over MCP inside the sandbox: should_reach="
              f"{mem.get('arm_should_reach')} reached={mem.get('reached')} "
              f"active={mem.get('active_memories')} hits={mem.get('hits')} "
              f"wal={mem.get('wal_sidecars_live_during_probe')}")
        print(f"[{arm}] -> {'HELD' if held else 'DID NOT HOLD'}\n", flush=True)

    cwd0 = run / "arms" / "baseline" / "repo"
    record["prior_profile"] = prior_profile_leaked(run, cwd0)
    print("prior profile (6fe65c3), with the deny list it used:")
    for k, v in record["prior_profile"].items():
        print(f"  {k}: {v}")
    print()

    record["prior_store_allow"] = prior_store_allow_blocked(run, cwd0)
    ok &= record["prior_store_allow"]["repair_is_the_difference"]
    print("store allow, the one-file spelling against the repaired one, sidecars live "
          f"{record['prior_store_allow']['sidecars_live']}:")
    for k in ("prior_allow_db_only", "repaired_allow_db_and_sidecars"):
        print(f"  {k:34} reached={record['prior_store_allow'][k]['reached']}")
    print()

    ordering = isolation.deny_ordering_probe(run / "ordering-probe", cwd0)
    record["deny_ordering"] = ordering
    ok &= ordering["ordering_holds"]
    print("deny-after-allow ordering (the rule that withholds ~/.claude/projects from "
          "inside an allowed ~/.claude):")
    print(f"  denied child inside an allowed subtree, blocked: "
          f"{ordering['synthetic_denied_child']['demonstrates_boundary']}")
    print(f"  allowed sibling in the same subtree, still readable: "
          f"{not ordering['synthetic_allowed_sibling']['blocked_inside']}")
    print(f"  ~/.claude/projects, blocked inside an allowed ~/.claude: "
          f"{ordering['home_claude_projects'].get('demonstrates_boundary')}")
    print(f"  ordering_holds: {ordering['ordering_holds']}")
    print()

    record["forwarder"] = forwarder_probes()
    ok &= record["forwarder"]["all_refusals_held"]
    print("forwarder allowlist (five refusals over HTTP, one acceptance through to a "
          f"loopback stub at {record['forwarder']['stub_upstream']}):")
    for k, v in record["forwarder"].items():
        if isinstance(v, dict):
            print(f"  {k:38} {v}")
    record["all_hold"] = bool(ok)
    (BENCH / "boundary-validation.json").write_text(json.dumps(record, indent=1))
    print(f"\nALL BOUNDARY CONTROLS HOLD: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
