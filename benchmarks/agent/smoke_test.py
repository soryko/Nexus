"""One model run through the repaired boundary: a reply, a repository read, an edit, and a
Nexus retrieval.

`claude --version` and `nexus-memory --help` establish that two processes start. Neither
establishes that the request/tool loop survives the restrictions, and the loop is what the
repairs changed: reads are denied by default, the forwarder accepts two routes and refuses
provider-executed tools, and the store is three files allowed to one arm. Any of those can
be wrong in a way every model-free control passes -- which is how the one-file store allow
survived a validation run in which every control held.

So this is the smallest run that exercises all four surfaces at once, on a prompt with no
task in it and nothing to score. It is NOT an arm-run: it produces no compliance figure, no
functional verdict and no row in the development matrix. It answers one question -- does the
loop work under this exact configuration -- and the evidence it leaves is the trace and the
terminal record.

The configuration is imported from `run_arms_isolated`, not restated: a smoke test that
builds its own profile tests its own profile.

Usage:  smoke_test.py <scratch> <base-run-dir> <task> <out-dir>
        DEEPSEEK_API_KEY must be in the environment; it is never written to an artifact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRATCH, BASE_RUN, TASK, OUT = (Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3],
                                Path(sys.argv[4]))
BENCH = Path(__file__).parent
sys.argv = [sys.argv[0], str(SCRATCH), TASK]      # run_arms_isolated parses these at import
sys.path.insert(0, str(BENCH))
import run_arms_isolated as R                                            # noqa: E402
import isolation                                                        # noqa: E402
from terminal_status import classify                                    # noqa: E402
from trace_parse import parse                                           # noqa: E402

ARM = "nexus"          # the only arm that exercises all four surfaces
MAX_TURNS = 12
WALL_CLOCK_S = 300

# No task, no defect, nothing to fix: naming one would make this a cheap arm-run whose
# result someone would eventually quote. Each instruction targets one surface, and each
# leaves a mark a reader can check in the trace without trusting the agent's summary.
PROMPT = (
    "Do exactly these three things in this repository checkout, then stop.\n\n"
    "1. Use the Nexus memory search tool to look up what earlier work recorded about "
    "environment-variable resolution. Quote one sentence from what it returns, verbatim.\n"
    "2. Read `src/click/decorators.py` and report its first line, verbatim.\n"
    "3. Create a file `SMOKE.txt` at the repository root whose only content is the single "
    "line `smoke`.\n\n"
    "Do not change anything else, do not run the test suite, and do not attempt to fix any "
    "bug. When all three are done, reply DONE followed by the quoted sentence and the "
    "quoted first line."
)


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set; refusing to run", file=sys.stderr)
        return 2
    R.FIXTURE_BASE = str(BASE_RUN / "base")
    # deliberately not derived from BASE_RUN: this directory is removed and rebuilt below,
    # and a name that can collide with the fixture tree would delete the fixture
    R.RUN = SCRATCH / "smoke-run"
    if R.RUN.resolve() == BASE_RUN.resolve() or BASE_RUN.resolve().is_relative_to(R.RUN.resolve()):
        print(f"refusing to run: the run directory {R.RUN} contains the fixture {BASE_RUN}",
              file=sys.stderr)
        return 2
    R.MAX_TURNS, R.WALL_CLOCK_S, R.PROMPT = MAX_TURNS, WALL_CLOCK_S, PROMPT
    # ARMS was built against the RUN that existed at import; the store path has to follow
    R.ARMS[ARM]["mcp"]["mcpServers"]["nexus"]["args"] = [
        "--db", str(R.RUN / "nexus-dev.db"), "--namespace", "a1-dev", "--actor", "agent"]
    run = R.RUN
    shutil.rmtree(run, ignore_errors=True)
    (run / "arms" / ARM).mkdir(parents=True)
    shutil.copytree(BASE_RUN / "base" / TASK, run / "arms" / ARM / "repo", symlinks=True)
    subprocess.run([str(R.REPO / ".venv-sqlite/bin/python"), str(BENCH / "seed_store.py"),
                    str(run / "nexus-dev.db"), "a1-dev", "agent"], check=True,
                   capture_output=True, text=True)

    cwd = run / "arms" / ARM / "repo"
    allow, deny = R.boundary_paths(ARM, cwd)
    profile = isolation.write_profile(run / "arms" / ARM / "sandbox.sb", cwd, deny, allow,
                                      R.FORWARDER_PORT)
    bound = isolation.check_boundary(profile, cwd, Path(R.FIXTURE_BASE) / "checks" / TASK,
                                     R.PYTEST_PY, deny, R.BENCH)
    ordering = isolation.deny_ordering_probe(run / "ordering-probe", cwd)
    print(f"boundary negative: {bound['negative_controls']}")
    print(f"boundary positive: {bound['positive_controls']} runner={bound['runner_version']}")
    print(f"deny ordering holds: {ordering['ordering_holds']}")
    if not (bound["all_hold"] and ordering["ordering_holds"]):
        print("ABORT: boundary did not hold; no model call made")
        return 1

    (run / "arms" / ARM / "mcp.json").write_text(json.dumps(R.ARMS[ARM]["mcp"]))
    vis = R.memory_visibility(ARM, profile, cwd)
    print(f"memory reachable inside the sandbox: reached={vis['reached']} "
          f"active={vis['visible']} hits={vis['detail'].get('hits')} "
          f"wal={vis['wal_sidecars_live_during_probe']}")
    if not vis["reached"]:
        print(f"ABORT: store not reachable from inside -> {vis['detail']}")
        return 1

    fwd = subprocess.Popen([sys.executable, str(BENCH / "model_forwarder.py"),
                            str(R.FORWARDER_PORT)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    started = datetime.now(timezone.utc)
    try:
        rec = R.invoke(ARM, cwd)
    finally:
        fwd.terminate()
        fwd.wait(timeout=10)
    refusals = (fwd.stderr.read() or "").strip()

    t = parse(run / "arms" / ARM / "trace.jsonl")
    rec["result"] = t["result"]
    rec["terminal"] = classify(rec)
    names = [c["name"] for c in t["calls"]]
    text = ((t["result"] or {}).get("result") or "").strip()
    smoke = cwd / "SMOKE.txt"
    surfaces = {
        "model_response": bool(text),
        "repository_read": any(n in ("Read", "Glob", "Grep") for n in names)
                           or any(n == "Bash" for n in names),
        "repository_edit": smoke.exists() and smoke.read_text().strip() == "smoke",
        "nexus_retrieval": any(n.startswith("mcp__nexus__") for n in names),
        "nexus_search_returned_hits": any(
            c["name"] == "mcp__nexus__search" and not c.get("is_error")
            and '"hits"' in (c.get("result") or "") for c in t["calls"]),
    }
    usage = ((t["result"] or {}).get("usage") or {})
    record = {
        "what_this_is": "integrated smoke test; not an arm-run, no figure may be taken from it",
        "utc": started.isoformat(), "task_fixture": TASK, "arm": ARM,
        "runner_version": bound["runner_version"], "prompt": PROMPT,
        "max_turns": MAX_TURNS, "wall_clock_limit_s": WALL_CLOCK_S,
        "wall_clock_s": rec["wall_clock_s"],
        "terminal": rec["terminal"], "surfaces": surfaces,
        "all_surfaces_exercised": all(surfaces.values()),
        "tool_calls": names, "final_reply": text[:600],
        "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                            "cache_read_input_tokens",
                                            "cache_creation_input_tokens")},
        "forwarder_refusals_during_run": refusals[-800:],
        "boundary": {"negative_controls": bound["negative_controls"],
                     "positive_controls": bound["positive_controls"],
                     "deny_ordering_holds": ordering["ordering_holds"],
                     "memory_reachable_inside": {k: vis[k] for k in
                                                 ("reached", "visible",
                                                  "wal_sidecars_live_during_probe")}},
        "trace_health": {"orphan_results": t["orphans"], "unresolved_calls": t["unresolved"]},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run / "arms" / ARM / "trace.jsonl", OUT / "trace.jsonl")
    subprocess.run(["gzip", "-9f", str(OUT / "trace.jsonl")], check=True)
    shutil.copy2(run / "arms" / ARM / "sandbox.sb", OUT / "sandbox.sb")
    (OUT / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps({k: record[k] for k in
                      ("terminal", "surfaces", "all_surfaces_exercised", "usage",
                       "wall_clock_s")}, indent=1))
    print(f"\ntrace + terminal record -> {OUT}")
    return 0 if record["all_surfaces_exercised"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
