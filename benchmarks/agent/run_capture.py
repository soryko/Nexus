"""The capture run: one prior session works `c1`, then `c2`, and records as it goes.

This is step 1 and step 2 of `capture-policy-a1` §3 and of `freeze-heldout-a1` §2, and it is
the piece the registration promised and nothing implemented: "a harness that reads the capture
set from this registration". Until it existed, the held-out corpus could only have been
authored the way the development corpus was -- by someone who had read the fixes -- which is
the exposure the held-out set exists to avoid.

It is NOT an arm. Nothing here is scored for benefit, no arm order is drawn, and the run
produces no figure. What it produces is a store.

Four things make it differ from an arm-run, and each of them is the point:

  writes       the arm allowlist withholds `record` and `revise`; this one grants them, and
               the store is the session's own rather than a copy thrown away afterwards.
  one store    `c1` and `c2` share it, in that order. The second task is a later session in
               the same repository and may legitimately see what the first recorded -- and
               may revise it, which is a capture event worth having.
  the policy   the prompt carries `capture-policy-a1` §1 and §2 -- what may be recorded and
               the bright line -- because a policy that lives only in a document the agent
               cannot read is not applied to anything.
  the horizon  the checkout is the repository at the task's pre-fix commit and nothing later,
               and the boundary denies `benchmarks/agent/` outright. The freeze document, the
               task sheet and the corpus all live there. The session is not told the held-out
               tasks exist, and cannot read that they do.

Usage:  run_capture.py <scratch> [<config.json>]
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

SP = Path(sys.argv[1])
BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import build_fixture                                                    # noqa: E402
import isolation                                                        # noqa: E402
from terminal_status import classify                                    # noqa: E402
from trace_parse import parse as parse_trace                            # noqa: E402

CFG = a1_config.load(sys.argv[2] if len(sys.argv) > 2 else None).require()
REPO = Path(CFG.repo)
CAP = SP / "capture"
STORE = CAP / "store" / "nexus.db"
TASKS = BENCH / "tasks-capture-a1.json"

# The capture session's tools. `record` and `revise` are the two the arms are denied; `forget`
# is withheld from this run as well, because a corpus is frozen by what was recorded and a
# session that can silently remove its own earlier judgement leaves a corpus whose provenance
# cannot be read back from the store. A correction is a `revise`, and a revision is evidence.
CAPTURE_TOOLS = ("Read,Edit,Write,Bash,Glob,Grep,"
                 "mcp__nexus__search,mcp__nexus__get,mcp__nexus__history,mcp__nexus__status,"
                 "mcp__nexus__record,mcp__nexus__revise")


def prompt_for(task: str) -> str:
    reg = json.loads(CFG.bench_path("prompts").read_text())
    spec = reg["tasks"].get(task)
    if spec is None:
        raise SystemExit(f"no prompt registered for {task}")
    if spec.get("role") != "capture":
        raise SystemExit(f"{task} is registered as {spec.get('role')!r}, not a capture task; "
                         f"this harness runs capture tasks only")
    return spec["body"] + reg["tails"][spec["tail"]] + reg["capture_instruction"]


def task_dir(task: str) -> Path:
    return CAP / "tasks" / task


def prepare(task: str, fixture: Path) -> Path:
    """A fresh checkout of the fixture, which is the repository at the pre-fix commit."""
    dst = task_dir(task) / "repo"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture, dst, symlinks=True)
    return dst


def boundary_paths(task: str, cwd: Path) -> tuple[list[Path], list[Path]]:
    """(allow, deny) for the capture session.

    The same deny-by-default shape the arms use, with one difference that matters: the store
    is allowed to this session for reading AND it is this session's own, because writing to it
    is the entire purpose. Everything that could reveal a later task is denied -- above all
    `BENCH`, which holds the freeze registration naming all six tasks, the task sheet stating
    every fix, and the development corpus.
    """
    allow = isolation.default_allow_paths(cwd, CFG.pytest_python) + [
        task_dir(task),                     # mcp.json, sandbox.sb
        STORE.parent,                       # its own store, read and written
        REPO / ".venv-sqlite", REPO / "src", REPO / "pyproject.toml",
    ]
    deny = [CAP / "base" / "checks",        # the hidden acceptance checks
            *[task_dir(t) for t in task_names() if t != task],
            CAP / "base",                   # the other task's fixture, at a later commit
            CAP / "scoring",
            Path(CFG.source_clone),         # carries every later commit, including the fixes
            BENCH]                          # the registration, the task sheet, the corpus
    return allow, deny


def task_names() -> list[str]:
    return [t["task"] for t in json.loads(TASKS.read_text())["tasks"]]


def store_digest(db: Path) -> str:
    """The same row digest the arm runner uses, so one number compares across both."""
    import hashlib, sqlite3
    con = sqlite3.connect(db)
    rows = list(con.execute(
        "select m.memory_id, m.current_revision_id, m.tombstoned, r.kind, r.tags_json, b.body "
        "from memories m join revisions r on r.revision_id = m.current_revision_id "
        "join blobs b on b.id = r.blob_id order by m.memory_id"))
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def store_counts(db: Path) -> dict:
    import sqlite3
    con = sqlite3.connect(db)
    active = con.execute("select count(*) from memories where tombstoned = 0").fetchone()[0]
    total = con.execute("select count(*) from memories").fetchone()[0]
    revisions = con.execute("select count(*) from revisions").fetchone()[0]
    con.close()
    return {"active_memories": active, "memories": total, "revisions": revisions,
            "superseded_revisions": revisions - total}


def create_store() -> None:
    """Create the store before the sandbox starts, so its WAL sidecars exist for the profile.

    `sqlite_read_paths` exists because a WAL store is three files; a profile written against a
    store that does not exist yet allows a path that later resolves differently. This also
    means the session's first `record` is not also the first `_migrate`, which is the
    initialization race the storage layer was fixed for.
    """
    if STORE.exists():
        raise SystemExit(f"refusing to capture into an existing store: {STORE}\n"
                         f"a capture run starts from nothing, or it is not a capture run")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "src"))
    from nexus_memory.transport.mcp_server import _prepare_new_storage
    from nexus_memory.storage import SQLiteRepository
    _prepare_new_storage(STORE)
    SQLiteRepository(STORE)                 # migrate now, under no contention
    import sqlite3
    holder = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    holder.execute("select 1").fetchone()
    holder.close()


def invoke(task: str, cwd: Path, prompt: str) -> dict:
    cfg = task_dir(task) / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"nexus": {
        "command": CFG.nexus_server,
        "args": ["--db", str(STORE), "--namespace", CFG.namespace, "--actor", CFG.actor]}}}))
    allow, deny = boundary_paths(task, cwd)
    profile = isolation.write_profile(task_dir(task) / "sandbox.sb", cwd, deny, allow,
                                      CFG.forwarder_port)
    cmd = ["sandbox-exec", "-f", str(profile),
           "claude", "--bare", "-p", prompt, "--model", "deepseek-flash",
           "--mcp-config", str(cfg), "--strict-mcp-config",
           "--allowedTools", CAPTURE_TOOLS,
           "--disallowedTools", "WebSearch,WebFetch",
           "--permission-mode", "acceptEdits",
           "--disable-slash-commands",
           "--max-turns", str(CFG.max_turns),
           "--output-format", "stream-json", "--verbose"]
    env = a1_config.child_env(f"http://127.0.0.1:{CFG.forwarder_port}/anthropic",
                              os.environ["DEEPSEEK_API_KEY"])
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    verdict = None
    try:
        done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=CFG.wall_clock_s)
        out, err = done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        verdict = "timeout"
    (task_dir(task) / "trace.jsonl").write_text(out)
    (task_dir(task) / "stderr.txt").write_text(err)
    return {"task": task, "started_utc": started.isoformat(),
            "wall_clock_s": round(time.monotonic() - t0, 1), "forced_verdict": verdict,
            "child_env": a1_config.env_record(env), "prompt": prompt}


def memory_calls(task: str) -> dict:
    """What the session did to the store, read off its own trace rather than the store.

    A count of rows says what survived; this says what was attempted, including a `record`
    the server refused. Both belong in the provenance of a corpus.
    """
    calls = parse_trace(task_dir(task) / "trace.jsonl")["calls"]
    by_name: dict[str, int] = {}
    errors = []
    for call in calls:
        if not call["name"].startswith("mcp__nexus__"):
            continue
        name = call["name"].removeprefix("mcp__nexus__")
        by_name[name] = by_name.get(name, 0) + 1
        if call["is_error"]:
            errors.append({"tool": name, "input": call["input"],
                           "result": (call["result"] or "")[:600]})
    return {"by_tool": by_name, "failed_calls": errors, "tool_calls_total": len(calls)}


def main() -> int:
    spec = json.loads(TASKS.read_text())
    tasks = [t for t in spec["tasks"]]
    CAP.mkdir(parents=True, exist_ok=True)

    print(f"capture run -> {CAP}")
    print(f"  tasks: {', '.join(t['task'] for t in tasks)} (in this order, and it is fixed: "
          f"capture-policy-a1 section 3)")

    # 1. fixtures, with the controls build_fixture already runs: the checks must fail without
    #    a patch, and the fix must be unreachable from the checkout.
    base = CAP / "base"
    report = []
    for task in tasks:
        built = build_fixture.build(Path(CFG.source_clone), task, base / task["task"])
        built["oracle"] = build_fixture.oracle_reachability(base / task["task"], task["fix"])
        built["no_model"] = build_fixture.score(
            base / task["task"], Path(built["held_checks"]), built["checks"],
            CFG.pytest_python, base / "scoring" / task["task"])
        ok = built["oracle"]["isolated"] and not built["no_model"]["passed"]
        print(f"  [{'OK ' if ok else 'BAD'}] fixture {task['task']} at {task['pre_fix'][:7]}: "
              f"isolated={built['oracle']['isolated']}  "
              f"no-model: {built['no_model']['summary']}")
        if not ok:
            print(f"  ABORT: {task['task']} fixture is not usable")
            return 1
        report.append(built)
    (base / "fixtures.json").write_text(json.dumps(report, indent=1))

    # 2. the boundary generator itself, once, before anything is spent
    ordering = isolation.deny_ordering_probe(CAP / "ordering-probe", CAP)
    print(f"  deny-after-allow ordering holds: {ordering['ordering_holds']}  "
          f"(~/.claude/projects blocked="
          f"{ordering['home_claude_projects'].get('demonstrates_boundary')})")
    if not ordering["ordering_holds"]:
        print("  ABORT: denies do not win inside an allowed subtree")
        return 1

    create_store()
    print(f"  store created empty at {STORE}  digest {store_digest(STORE)}\n")

    records = []
    for task, built in zip(tasks, report):
        name = task["task"]
        cwd = prepare(name, base / name)
        prompt = prompt_for(name)
        allow, deny = boundary_paths(name, cwd)
        profile = isolation.write_profile(task_dir(name) / "sandbox.sb", cwd, deny, allow,
                                          CFG.forwarder_port)
        bound = isolation.check_boundary(profile, cwd, base / "checks" / name,
                                         CFG.pytest_python, deny, BENCH)
        print(f"[{name}] boundary negative: {bound['negative_controls']}", flush=True)
        print(f"[{name}] boundary positive: {bound['positive_controls']}", flush=True)
        if not bound["all_hold"]:
            print(f"[{name}] ABORT: boundary not demonstrated -> {bound}", flush=True)
            return 1

        before = {"digest": store_digest(STORE), **store_counts(STORE)}
        print(f"[{name}] store before: {before}", flush=True)
        print(f"[{name}] running ...", flush=True)
        rec = invoke(name, cwd, prompt)
        rec["boundary"] = bound
        parsed = parse_trace(task_dir(name) / "trace.jsonl")
        rec["result"] = parsed["result"]
        rec["trace_health"] = {"orphan_results": parsed["orphans"],
                               "unresolved_calls": parsed["unresolved"]}
        rec["terminal"] = classify(rec)
        res = rec.get("result") or {}
        u = res.get("usage", {})
        rec["usage"] = {k: u.get(k) for k in
                        ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens")}
        rec["memory"] = memory_calls(name)
        subprocess.run(["git", "-C", str(cwd), "add", "-A", "-N"], check=True)
        patch = subprocess.run(["git", "-C", str(cwd), "diff"], capture_output=True, text=True,
                               check=True).stdout
        (task_dir(name) / "patch.diff").write_text(patch)
        rec["patch_bytes"] = len(patch)
        # Scored, but not as a benefit figure -- there is nothing to compare it against. It
        # says whether the memories were captured from work that actually succeeded, which is
        # a property of the corpus's provenance.
        rec["scored"] = build_fixture.score(cwd, Path(built["held_checks"]), built["checks"],
                                            CFG.pytest_python, CAP / "scoring" / name)
        rec["store_after"] = {"digest": store_digest(STORE), **store_counts(STORE)}
        print(f"  -> {rec['terminal']}  patch={rec['patch_bytes']}B  "
              f"checks: {rec['scored']['summary']}  wall={rec['wall_clock_s']}s")
        print(f"  -> memory calls: {rec['memory']['by_tool']}  "
              f"store after: {rec['store_after']}\n", flush=True)
        records.append(rec)

    final = {"digest": store_digest(STORE), **store_counts(STORE)}
    (CAP / "capture-records.json").write_text(json.dumps(
        {"kind": "capture", "policy": "capture-policy-a1.md",
         "registration": "freeze-heldout-a1.md section 2",
         "not_a_measurement": "This run produces a corpus, not a figure. No arm order is "
                              "drawn and nothing here is compared against anything.",
         "store": str(STORE), "store_final": final,
         "tasks": [t["task"] for t in tasks],
         "capture_tools": CAPTURE_TOOLS,
         "config": CFG.as_recorded(),
         "deny_ordering": ordering,
         "fixtures": report,
         "records": records}, indent=1))
    print(f"capture complete: {final}")
    print(f"records -> {CAP / 'capture-records.json'}")
    print("\nThe corpus is NOT frozen by this script. Run freeze_corpus.py next: it exports "
          "the rows, digests them, renders arm 3's notes and writes the registration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
