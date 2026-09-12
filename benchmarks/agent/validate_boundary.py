"""Execute the boundary the arms will run under, and record what held.

No model is invoked and no paid request is made: every forwarder probe here is refused
before a socket to the upstream is opened, and the only Claude Code invocation is
`--version`. What this validates is the repaired profile from `isolation.py` and the
repaired allowlist in `model_forwarder.py`, built exactly as `run_arms_isolated.py` builds
them, for every arm.

Usage:  python3 validate_boundary.py <scratch-dir> <base-run-dir> <task> <python>
"""
from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent
sys.path.insert(0, str(BENCH))
import isolation
from model_forwarder import inspect_body

SCRATCH, BASE_RUN, TASK, PY = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
ARMS = ("baseline", "nexus", "notes")
FWD_PORT = 8899
DECOY_PORT = 8901
SOURCE_CLONE = "/private/tmp/claude-501/-Users-soko-Cerebros-nexus-memory/215651df-6e30-4d49-a919-8b24f46175e8/scratchpad/click"


def layout(run: Path) -> None:
    """The directory shape `run_arms_isolated.prepare` produces, without running an arm."""
    for arm in ARMS:
        dst = run / "arms" / arm / "repo"
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BASE_RUN / "base" / TASK, dst, symlinks=True)
        (run / "arms" / arm / "mcp.json").write_text("{}")


def paths_for(arm: str, run: Path, cwd: Path):
    allow = isolation.default_allow_paths(cwd, PY) + [
        run / "arms" / arm, run / "nexus-dev.db",
        REPO / ".venv-sqlite", REPO / "src", REPO / "pyproject.toml"]
    deny = [BASE_RUN / "base" / "checks",
            *[(run / "arms" / a) for a in ARMS if a != arm],
            run / "scoring", Path(SOURCE_CLONE), BENCH]
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


def forwarder_probes() -> dict:
    """Refusals only. A refused request never opens a socket to the upstream, so this
    costs nothing; the accepted shape is checked against `inspect_body` directly rather
    than by sending it, for the same reason."""
    srv = subprocess.Popen([sys.executable, str(BENCH / "model_forwarder.py"), str(FWD_PORT)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", FWD_PORT), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    out = {}
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
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    # a legitimate request must NOT be refused -- an allowlist that rejects everything is
    # not an allowlist
    out["ordinary client-tool request accepted"] = {
        "refused": inspect_body(json.dumps(ACCEPTABLE_BODY).encode()) is not None,
        "expected_refused": False}
    out["all_refusals_held"] = (
        all(v["refused"] for k, v in out.items() if isinstance(v, dict) and "status" in v)
        and out["ordinary client-tool request accepted"]["refused"] is False)
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


def nexus_server_starts(profile: Path, cwd: Path) -> dict:
    """The memory server is an editable install inside the repository whose benchmark
    directory this profile denies. It must still import and start."""
    exe = REPO / ".venv-sqlite" / "bin" / "nexus-memory"
    if not exe.exists():
        return {"applicable": False, "reason": f"{exe} not present"}
    r = isolation.probe(profile, [str(exe), "--help"], cwd, timeout=60)
    return {"applicable": True, "starts": r.returncode == 0,
            "tail": (r.stderr or r.stdout).strip().splitlines()[-1:]}


# The profile as it stood at 6fe65c3, reproduced verbatim so the repair is measured against
# it rather than merely asserted. `(allow default)` with a handful of denies left the whole
# benchmark directory readable, and `write_profile` dropped any deny whose path did not exist.
PRIOR_PROFILE = """(version 1)
(allow default)

(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))

{denies}
"""


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
        bound["nexus_server_starts"] = nexus_server_starts(profile, cwd)
        record["arms"][arm] = bound
        held = (bound["all_hold"]
                and bound["loopback_limited_to_forwarder_port"]["demonstrates_boundary"]
                and (not bound["nexus_server_starts"].get("applicable")
                     or bound["nexus_server_starts"]["starts"]))
        ok &= held
        print(f"[{arm}] negative {bound['negative_controls']}")
        print(f"[{arm}] positive {bound['positive_controls']} "
              f"runner={bound.get('runner_version')}")
        print(f"[{arm}] decoy loopback port blocked inside / reachable outside: "
              f"{bound['loopback_limited_to_forwarder_port']['demonstrates_boundary']}")
        print(f"[{arm}] nexus memory server starts: {bound['nexus_server_starts']}")
        print(f"[{arm}] -> {'HELD' if held else 'DID NOT HOLD'}\n", flush=True)

    cwd0 = run / "arms" / "baseline" / "repo"
    record["prior_profile"] = prior_profile_leaked(run, cwd0)
    print("prior profile (6fe65c3), with the deny list it used:")
    for k, v in record["prior_profile"].items():
        print(f"  {k}: {v}")
    print()

    record["forwarder"] = forwarder_probes()
    ok &= record["forwarder"]["all_refusals_held"]
    print("forwarder allowlist:")
    for k, v in record["forwarder"].items():
        if isinstance(v, dict):
            print(f"  {k:38} {v}")
    record["all_hold"] = bool(ok)
    (BENCH / "boundary-validation.json").write_text(json.dumps(record, indent=1))
    print(f"\nALL BOUNDARY CONTROLS HOLD: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
