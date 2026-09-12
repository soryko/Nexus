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
  canonical paths   every allow and deny path compared against its own `realpath`, per arm.
                    `write_profile` resolves denies and emits allows as given, so on a
                    symlinked scratch root the two lists spell one file two ways. A diverging
                    allow is over-restriction -- the positive controls below fail loudly -- but
                    which paths diverge is a property of the EXECUTION LAYOUT, so it is
                    reported for the layout a run will actually use rather than assumed.

The boundary paths are no longer mirrored here. `paths_for` used to reproduce
`run_arms_isolated.boundary_paths` by hand and had drifted out of step with it -- still naming
one store shared by three arms after the runner had moved to a private copy per arm-run. This
imports the runner and calls its own function, so what is validated is what will run.

Usage:  python3 validate_boundary.py <scratch-dir> <base-run-dir> <task> <python> [config]
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
CONFIG = sys.argv[5] if len(sys.argv) > 5 else None

import a1_config                                                        # noqa: E402

CFG = a1_config.load(CONFIG).require()
VENV_PY = Path(CFG.venv_python)                 # the interpreter Nexus itself runs on
ARMS = ("baseline", "nexus", "notes")
FWD_PORT = CFG.forwarder_port
DECOY_PORT = 8901
STUB_PORT = 8902
CORPUS_SIZE = CFG.corpus_size
MEMORY_PROBE_QUERY = CFG.memory_probe_query
SOURCE_CLONE = CFG.source_clone

# The store this validation builds, and the runner instance whose OWN `boundary_paths` is
# exercised. `paths_for` used to be a hand-written mirror of that function, described in its
# docstring as mirroring it -- and it had drifted: it still named `run/nexus-dev.db`, one
# store shared by three arms, after the runner had moved to a private copy per arm-run. A
# validator that mirrors the thing it validates stops validating it the first time either
# side moves, and nothing says when that happened.
MASTER = SCRATCH / "boundary-validation" / "master" / "corpus.db"

#: Set in `main`, once the master store exists -- the runner's own preflight refuses a
#: `store_master` that is not there yet, which is the point of that preflight.
RUNNER = None


def store_for(arm: str) -> Path:
    return RUNNER.arm_store(arm)


def _runner():
    """Import `run_arms_isolated` bound to this validation's scratch, task and store.

    Configuration is written out rather than mutated in place, so the module under test loads
    it exactly as a real run would.
    """
    import importlib
    saved_argv, saved_env = sys.argv[:], os.environ.get("A1_CONFIG")
    cfg = json.loads(Path(CONFIG or a1_config.DEFAULT_PATH).read_text())
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg["store_master"] = str(MASTER)
    cfg["corpus_digest"] = ""                   # the gate is exercised by the runner, not here
    path = SCRATCH / "boundary-validation" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg))
    sys.argv = ["run_arms_isolated.py", str(SCRATCH), TASK, "1"]
    os.environ["A1_CONFIG"] = str(path)
    try:
        sys.modules.pop("run_arms_isolated", None)
        return importlib.import_module("run_arms_isolated")
    finally:
        sys.argv = saved_argv
        if saved_env is None:
            os.environ.pop("A1_CONFIG", None)
        else:
            os.environ["A1_CONFIG"] = saved_env


def layout(run: Path) -> None:
    """Seed the master store the arms will be given private copies of.

    Built from the corpus by `seed_store.py` rather than copied from a hand-made file, so the
    validation runs against a store the repository can reproduce.

    The arm checkouts are NOT laid out here any more. They are laid out by the runner's own
    `prepare`, into the runner's own `OUT/arms/<arm>/repo`, because `boundary_paths` allows
    that path and no other. Laying them out somewhere else and then asking the runner for its
    paths produced a profile that denied the very directory the probe ran from -- which read,
    in the artifact, as the nexus arm being unable to reach the corpus.
    """
    # seeded through the project venv, not this interpreter: the store is Nexus's, and the
    # SQLite it must be built against is the one `.venv-sqlite` links, not whichever libsqlite
    # the python running this validator happens to have.
    MASTER.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(VENV_PY), str(BENCH / "seed_store.py"),
                    str(MASTER), CFG.namespace, CFG.actor,
                    "--map", str(run / "store-map.json")], check=True,
                   capture_output=True, text=True)


def canonical_path_divergence(allow: list, deny: list) -> dict:
    """Paths whose spelling differs from their canonical form, per list.

    `write_profile` resolves the deny list with `os.path.realpath` and emits the allow list as
    given. On a scratch root that is itself a symlink -- `/var/folders/...` for `/private/var/
    folders/...`, which is what `tempfile` hands out on macOS -- the two lists then spell the
    same file differently. The kernel matches the canonical form, so a diverging ALLOW simply
    fails to match: over-restriction, and the positive controls below catch it loudly rather
    than a boundary silently opening. It is reported per arm so the execution layout can be
    checked before a run rather than discovered during one.
    """
    def diverging(paths):
        spellings = {str(p) for p in paths}
        # `/etc` resolves to `/private/etc` and SYSTEM_READ_ROOTS deliberately carries both,
        # so a path whose canonical form is already in the same list is covered either way.
        return sorted({str(p): os.path.realpath(p) for p in paths
                       if str(p) != os.path.realpath(p)
                       and os.path.realpath(p) not in spellings}.items())
    return {"allow_paths_not_canonical": diverging(allow),
            "deny_paths_not_canonical": diverging(deny),
            "all_canonical": not diverging(allow) and not diverging(deny)}


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


def memory_reachable(profile: Path, cwd: Path, arm: str, run: Path, db: Path) -> dict:
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
    exe = Path(CFG.nexus_server)
    if not exe.exists() or not db.exists():
        return {"applicable": False, "reason": f"{exe if not exe.exists() else db} not present"}
    probe_py = run / "arms" / arm / "mcp_probe.py"       # BENCH is denied; the arm dir is not
    shutil.copy2(BENCH / "mcp_probe.py", probe_py)
    holder = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    holder.execute("select 1").fetchone()
    try:
        sidecars = sorted(p.name for s in ("-wal", "-shm")
                          if (p := Path(f"{db}{s}")).exists())
        r = isolation.probe(profile, [str(VENV_PY), str(probe_py),
                                      str(exe), str(db), CFG.namespace, CFG.actor,
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
    db = store_for("nexus")
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
    staging = SCRATCH / "boundary-validation"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    layout(staging)                 # seeds MASTER, which the runner's preflight requires
    global RUNNER
    RUNNER = _runner()
    # Everything below runs in the layout a real arm-run uses: the runner's attempt directory,
    # its checkouts, its per-arm stores.
    run = RUNNER.OUT
    for arm in ARMS:
        RUNNER.prepare(arm)
    # One private copy of the master per arm, exactly as an arm-run is given one. The stores
    # must be distinct: three arms sharing one was the defect this layout replaced.
    for arm in ARMS:
        RUNNER.provision_store(arm)
    distinct = {arm: str(RUNNER.arm_store(arm)) for arm in ARMS}
    if len(set(distinct.values())) != len(ARMS):
        print(f"ABORT: arms do not have distinct stores: {distinct}")
        return 1

    record: dict = {"task": TASK, "python": PY, "forwarder_port": FWD_PORT,
                    "config": CFG.as_recorded(), "master_store": str(MASTER), "arms": {}}
    ok = True
    stores = {}
    for arm in ARMS:
        cwd = run / "arms" / arm / "repo"
        # The runner's own function, given the runner's own cwd, so what is validated is what
        # will run. `boundary_paths` reads the arm's store path from the same module.
        allow, deny = RUNNER.boundary_paths(arm, cwd)
        stores[arm] = RUNNER.arm_store(arm)
        profile = isolation.write_profile(run / "arms" / arm / "sandbox.sb", cwd, deny, allow,
                                          FWD_PORT)
        bound = isolation.check_boundary(profile, cwd,
                                         Path(RUNNER.FIXTURE_BASE) / "checks" / TASK,
                                         PY, deny, BENCH)
        bound["canonical_paths"] = canonical_path_divergence(allow, deny)
        bound["loopback_limited_to_forwarder_port"] = loopback_scope(profile, cwd)
        bound["memory_reachable"] = memory_reachable(profile, cwd, arm, run, store_for(arm))
        bound["scratch_root_entry_only"] = scratch_root_entry_only(profile, run, cwd)
        record["arms"][arm] = bound
        mem = bound["memory_reachable"]
        held = (bound["all_hold"]
                and bound["loopback_limited_to_forwarder_port"]["demonstrates_boundary"]
                and bound["scratch_root_entry_only"]["entry_only"]
                and (not mem.get("applicable") or mem["as_intended"]))
        ok &= held
        cp = bound["canonical_paths"]
        print(f"[{arm}] configured paths are canonical: {cp['all_canonical']}"
              + ("" if cp["all_canonical"] else
                 f"  allow={len(cp['allow_paths_not_canonical'])} "
                 f"deny={len(cp['deny_paths_not_canonical'])} diverge"))
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
