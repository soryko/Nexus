"""A1 development run, task d1, three arms, one attempt each.

Harness validation only (capture-policy-a1 section 5): no benefit figure may be computed
from this corpus. What this run is for is showing the plumbing works and that the arms
differ in the intended way.
"""
from __future__ import annotations
import json, os, random, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

SP = Path(sys.argv[1])
TASK = sys.argv[2]
RUN = SP / f"run-{TASK}"
REPO = Path("/Users/soko/Cerebros/nexus-memory")
BENCH = REPO / "benchmarks" / "agent"
PYTEST_PY = "/private/tmp/claude-501/-Users-soko-Cerebros-nexus-memory/215651df-6e30-4d49-a919-8b24f46175e8/scratchpad/.venv-click/bin/python"
sys.path.insert(0, str(BENCH))
from build_fixture import score  # the same scorer the controls used
from trace_parse import parse as parse_trace
from terminal_status import classify
import isolation

SEED = 20260911
FORWARDER_PORT = 8899
CORPUS_SIZE = 13
# A query the pre-run gate can pose without holding the answer key: it names the corpus's
# own subject matter, not any task's fix. A gate that returned nothing for every query would
# be a broken index, which is exactly what it is here to catch.
MEMORY_PROBE_QUERY = "click option parameter"
SOURCE_CLONE = "/private/tmp/claude-501/-Users-soko-Cerebros-nexus-memory/215651df-6e30-4d49-a919-8b24f46175e8/scratchpad/click"
FIXTURE_BASE = ""  # set in main()
MAX_TURNS = 30
WALL_CLOCK_S = 600

# The task prompt. Byte-for-byte identical in every arm. It states the observable symptom
# and the expected outcome; it does not name the function, the file or the mechanism.
# Authored by someone who had read the fix -- the same declared exposure the development
# corpus carries (capture-policy-a1 section 5), and the same reason this run cannot produce
# a benefit figure.
CONSULT = (
    "\n\nBefore your first source edit, consult any available prior-work memory: Nexus memory "
    "tools or `NOTES-FROM-EARLIER-WORK.md`. If neither is available, proceed using the "
    "repository. Treat prior notes as potentially outdated and verify relevant claims "
    "against current code."
)
TAIL = (
    " Fix the behaviour in `src/`, and extend the existing test suite to cover it. Do not "
    "change behaviour unrelated to this bug. When you are done, reply DONE."
)

# Every prompt states the observable symptom and the expected outcome. None names the
# function, the file or the mechanism. All were authored by someone who had read the fix --
# the same declared exposure the development corpus carries (capture-policy-a1 section 5),
# and the same reason no benefit figure may come from these runs.
PROMPTS = {
    "d1": (
        "In this Click checkout there is a bug in how an option resolves its value from the "
        "environment through the context's automatic environment-variable prefix.\n\n"
        "Reproduction: a command with an option declared "
        "`@click.option(\"--mypath\", type=click.Path(exists=True), envvar=\"MYPATH\")`, invoked "
        "with `auto_envvar_prefix=\"AUTO\"` and with `AUTO_MYPATH` set to the empty string in the "
        "environment, raises instead of completing with `mypath` left as `None`."
    ),
    "d2": (
        "In this Click checkout, an option that asks to show its environment variable but has "
        "none configured produces a misleading error message.\n\n"
        "Reproduction: `click.Command(\"cli\", params=[click.Option([\"--foo\"], "
        "show_envvar=True, required=True)])`, invoked with no arguments, exits 2 with a "
        "message that appends an environment-variable clause even though no environment "
        "variable is configured for that option. It should read "
        "`Error: Missing option '--foo'.`"
    ),
    "d3": (
        "In this Click checkout, an `UNSET` entry in a context's `default_map` is not handled "
        "correctly.\n\n"
        "Reproduction: a command declared with "
        "`context_settings={\"default_map\": {\"port\": UNSET}}` -- where `UNSET` is imported "
        "from `click._utils` -- and an option `@click.option(\"--port\", default=8000)`, "
        "invoked with no arguments, prints `port=None`. It should print `port=8000`."
    ),
    "d4": (
        "This checkout's test suite is configured to turn warnings into errors, so a test "
        "that uses an unregistered pytest marker fails at collection rather than merely "
        "warning.\n\n"
        "Reproduction: a test decorated `@pytest.mark.integration` errors during collection "
        "with `PytestUnknownMarkWarning: Unknown pytest.mark.integration`, because that "
        "marker is not registered in this project's pytest configuration.\n\n"
        "Register the `integration` marker in this project's pytest configuration so such a "
        "test collects and runs cleanly, and add one test under `tests/` that uses it."
    ),
}
D4_TAIL = (" Do not change behaviour unrelated to this. When you are done, reply DONE.")
PROMPT = PROMPTS[TASK] + (D4_TAIL if TASK == "d4" else TAIL) + CONSULT

# Superseded by isolation.py. These variables were the old "allowlist": they asked pip and
# curl to route through a dead proxy, which Claude Code ignored entirely and which any client
# could ignore too. They are kept only so the arm environment is identical in the respects
# that are not the boundary; the boundary itself is now the sandbox profile.
NET = {
    "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
    "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
    "ALL_PROXY": "socks5://127.0.0.1:1", "all_proxy": "socks5://127.0.0.1:1",
    "NO_PROXY": "api.deepseek.com", "no_proxy": "api.deepseek.com",
    "PIP_NO_INDEX": "1", "PIP_INDEX_URL": "http://127.0.0.1:1/simple",
    "PIP_RETRIES": "0", "PIP_TIMEOUT": "2", "UV_OFFLINE": "1", "UV_NO_INDEX": "1",
}

BASE_TOOLS = "Read,Edit,Write,Bash,Glob,Grep"
NEXUS_TOOLS = "mcp__nexus__search,mcp__nexus__get,mcp__nexus__history,mcp__nexus__status"

ARMS = {
    "baseline": {"mcp": {"mcpServers": {}}, "tools": BASE_TOOLS, "notes": False},
    "nexus": {"mcp": {"mcpServers": {"nexus": {
        "command": str(REPO / ".venv-sqlite/bin/nexus-memory"),
        "args": ["--db", str(RUN / "nexus-dev.db"), "--namespace", "a1-dev",
                 "--actor", "agent"]}}},
        "tools": BASE_TOOLS + "," + NEXUS_TOOLS, "notes": False},
    "notes": {"mcp": {"mcpServers": {}}, "tools": BASE_TOOLS, "notes": True},
}


def prepare(arm: str) -> Path:
    """A fresh, isolated checkout per attempt (protocol-a1 section 3)."""
    dst = RUN / "arms" / arm / "repo"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RUN / "base" / TASK, dst, symlinks=True)
    if ARMS[arm]["notes"]:
        # capture-policy-a1 section 4: the same captured information, one readable file,
        # mechanically rendered. Placed at the checkout root, and committed so that the
        # agent's own patch is measured against a tree that already contains it.
        shutil.copy2(BENCH / "notes-dev-a1.md", dst / "NOTES-FROM-EARLIER-WORK.md")
        subprocess.run(["git", "-C", str(dst), "add", "NOTES-FROM-EARLIER-WORK.md"], check=True)
        subprocess.run(["git", "-C", str(dst), "-c", "user.email=fixture@localhost",
                        "-c", "user.name=A1 fixture", "commit", "--quiet", "-m", "notes"],
                       check=True)
    return dst


def boundary_paths(arm: str, cwd: Path) -> tuple[list[Path], list[Path]]:
    """(allow, deny) for one arm's profile.

    Reads are denied by default, so the allow list has to name everything the arm legitimately
    needs: the runtime, its own checkout, its own `mcp.json`, and -- for the nexus arm -- the
    editable install the memory server runs from. BENCH is on the deny list and sits inside
    the repository the nexus server imports from, which is why denies are emitted after
    allows: `src/` is readable, `benchmarks/agent/` is not.

    The store is allowed to the NEXUS ARM ONLY. It was previously allowed to all three, which
    handed the baseline and notes arms file-read on the corpus they are defined by not
    having. Neither has the MCP tool, and `RUN` itself is unreadable so the filename has to
    be guessed -- but an arm holding Bash and `/usr/bin/sqlite3` needed only the guess, and
    nothing about the design required the allow. The run directory is still not allowed as a
    whole; the three store files are named, and no more.
    """
    allow = isolation.default_allow_paths(cwd, PYTEST_PY) + [
        RUN / "arms" / arm,                 # mcp.json, sandbox.sb, mcp_probe.py
        REPO / ".venv-sqlite", REPO / "src", REPO / "pyproject.toml",
    ]
    if arm == "nexus":
        allow += isolation.sqlite_read_paths(RUN / "nexus-dev.db")   # .db, -wal, -shm
    deny = [Path(FIXTURE_BASE) / "checks",
            *[(RUN / "arms" / a) for a in ARMS if a != arm],
            RUN / "scoring",                # created part-way through the run; denied anyway
            Path(SOURCE_CLONE),
            BENCH]                          # saved patches, reports, corpus, task sheet
    if arm != "nexus":
        # named explicitly rather than left to the default, so the artifact records that the
        # store was withheld from this arm rather than merely unmentioned
        deny += isolation.sqlite_read_paths(RUN / "nexus-dev.db")
    return allow, deny


def invoke(arm: str, cwd: Path) -> dict:
    cfg = RUN / "arms" / arm / "mcp.json"
    cfg.write_text(json.dumps(ARMS[arm]["mcp"]))
    allow, deny = boundary_paths(arm, cwd)
    profile = isolation.write_profile(RUN / "arms" / arm / "sandbox.sb", cwd, deny, allow,
                                      FORWARDER_PORT)
    cmd = ["sandbox-exec", "-f", str(profile),
           "claude", "--bare", "-p", PROMPT, "--model", "deepseek-flash",
           "--mcp-config", str(cfg), "--strict-mcp-config",
           "--allowedTools", ARMS[arm]["tools"],
           "--disallowedTools", "WebSearch,WebFetch",
           "--permission-mode", "acceptEdits",
           "--disable-slash-commands",
           "--max-turns", str(MAX_TURNS),
           "--output-format", "stream-json", "--verbose"]
    env = dict(os.environ)
    # the sandbox denies every host but localhost; the forwarder is the only egress
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{FORWARDER_PORT}/anthropic"
    env["ANTHROPIC_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    verdict = None
    try:
        done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=WALL_CLOCK_S)
        out, err = done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        verdict = "timeout"
    wall = time.monotonic() - t0
    (RUN / "arms" / arm / "trace.jsonl").write_text(out)
    (RUN / "arms" / arm / "stderr.txt").write_text(err)
    return {"arm": arm, "started_utc": started.isoformat(), "wall_clock_s": round(wall, 1),
            "forced_verdict": verdict}


def read_trace(arm: str) -> dict:
    """Tool trace and envelope, joined by tool_use_id (see trace_parse)."""
    t = parse_trace(RUN / "arms" / arm / "trace.jsonl")
    return {"tool_calls": [{"id": c["id"], "index": c["index"], "name": c["name"],
                            "input": c["input"], "result": (c["result"] or "")[:4000],
                            "is_error": c["is_error"]} for c in t["calls"]],
            "result": t["result"],
            "trace_health": {"orphan_results": t["orphans"],
                             "unresolved_calls": t["unresolved"]}}


def patch_of(cwd: Path) -> str:
    subprocess.run(["git", "-C", str(cwd), "add", "-A", "-N"], check=True)
    return subprocess.run(["git", "-C", str(cwd), "diff"],
                          capture_output=True, text=True, check=True).stdout


def memory_visibility(arm: str, profile: Path, cwd: Path) -> dict:
    """Assert the agent will actually REACH the corpus -- over MCP, from inside the boundary.

    Three runs were lost to one defect and two bad controls. Nexus scope is (namespace,
    actor) and it isolates: a store seeded as one actor reports active_memories=0 to a server
    launched as another. A hand-edited mcp.json did not help, because invoke() rewrites that
    file from ARMS at launch -- so the pre-run check validated an artifact the run replaced.
    This reads the config as written by the harness itself, immediately before the arm runs.

    The previous version then answered the question in the wrong process. It opened the
    database with `sqlite3.connect` from the HARNESS, outside the sandbox, where every path
    is readable -- so it reported `visible=13` for a store the arm could not open at all.
    Paired with `nexus-memory --help`, which returns 0 without constructing a repository, the
    two gates between the corpus and the run could both pass while the arm's own first
    `search` failed.

    So the gate is the arm's own path now: `sandbox-exec` -> the server -> `status` AND a
    `search` that returns hits, with a live WAL held open across the probe so the sidecar
    case is the one being tested rather than the checkpointed one that happens to work.
    """
    import sqlite3
    cfg = json.loads((RUN / "arms" / arm / "mcp.json").read_text())
    server = cfg.get("mcpServers", {}).get("nexus")
    if not server:
        return {"applicable": False}
    args = server["args"]
    db = args[args.index("--db") + 1]
    ns = args[args.index("--namespace") + 1]
    actor = args[args.index("--actor") + 1]

    probe = RUN / "arms" / arm / "mcp_probe.py"      # BENCH is denied; the arm dir is not
    shutil.copy2(BENCH / "mcp_probe.py", probe)
    # Hold the store open so `-wal` and `-shm` exist while the probe runs. A read-only
    # connection is enough to materialise them and cannot touch the frozen corpus.
    holder = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    holder.execute("select 1").fetchone()
    try:
        sidecars = sorted(Path(db + s).name for s in ("-wal", "-shm") if Path(db + s).exists())
        r = isolation.probe(profile, [str(REPO / ".venv-sqlite/bin/python"), str(probe),
                                      str(REPO / ".venv-sqlite/bin/nexus-memory"), db, ns,
                                      actor, MEMORY_PROBE_QUERY, str(CORPUS_SIZE)],
                            cwd, timeout=120)
    finally:
        holder.close()
    try:
        detail = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = {"error": (r.stderr or r.stdout).strip()[-400:]}
    return {"applicable": True, "db": db, "namespace": ns, "actor": actor,
            "wal_sidecars_live_during_probe": sidecars,
            "inside_sandbox": True, "exit": r.returncode,
            "reached": r.returncode == 0, "detail": detail,
            "visible": detail.get("active_memories")}


def store_digest() -> str:
    """A digest of the store's CONTENT, not of its file.

    The first version of this hashed the database file and reported the frozen corpus as
    mutated across a run in which the agent made no memory call at all. The store is in WAL
    mode: opening it is enough to rewrite file bytes and grow the file. A file hash cannot
    tell a checkpoint from a write, so it cannot answer the question it was added to answer.
    This reads the rows instead.
    """
    import hashlib, sqlite3
    con = sqlite3.connect(RUN / "nexus-dev.db")
    rows = list(con.execute(
        "select m.memory_id, m.current_revision_id, m.tombstoned, r.kind, r.tags_json, b.body "
        "from memories m join revisions r on r.revision_id = m.current_revision_id "
        "join blobs b on b.id = r.blob_id order by m.memory_id"))
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def main() -> int:
    allfx = json.loads((RUN / "base" / "fixtures.json").read_text())
    fixtures = next(f for f in allfx if f["task"] == TASK)
    global FIXTURE_BASE
    FIXTURE_BASE = str(RUN / "base")
    checks, held = fixtures["checks"], Path(fixtures["held_checks"])

    order = list(ARMS)
    random.Random(SEED).shuffle(order)
    print(f"seed={SEED}  realised arm order: {' -> '.join(order)}\n")

    # the deny-ordering control: once per run, not per arm -- it is a property of the
    # profile generator, not of any one arm's profile
    ordering = isolation.deny_ordering_probe(RUN / "ordering-probe", RUN)
    print(f"deny-after-allow ordering holds: {ordering['ordering_holds']}  "
          f"(denied child blocked={ordering['synthetic_denied_child']['demonstrates_boundary']}, "
          f"allowed sibling readable="
          f"{not ordering['synthetic_allowed_sibling']['blocked_inside']}, "
          f"~/.claude/projects blocked="
          f"{ordering['home_claude_projects'].get('demonstrates_boundary')})\n", flush=True)
    if not ordering["ordering_holds"]:
        print("ABORT: denies do not win inside an allowed subtree", flush=True)
        return 1

    before = store_digest()
    print(f"frozen store digest before any arm: {before}\n")
    records = []
    for arm in order:
        cwd = prepare(arm)
        allow, deny = boundary_paths(arm, cwd)
        profile = isolation.write_profile(RUN / "arms" / arm / "sandbox.sb", cwd, deny, allow,
                                          FORWARDER_PORT)
        bound = isolation.check_boundary(profile, cwd, Path(FIXTURE_BASE) / "checks" / TASK,
                                         PYTEST_PY, deny, BENCH)
        print(f"[{arm}] boundary negative: {bound['negative_controls']}", flush=True)
        print(f"[{arm}] boundary positive: {bound['positive_controls']} "
              f"runner={bound.get('runner_version')}", flush=True)
        if not bound["all_hold"]:
            print(f"[{arm}] ABORT: boundary not demonstrated -> {bound}", flush=True)
            return 1
        # the memory-reachability gate: the arm's own path to the store, inside the boundary
        cfg_path = RUN / "arms" / arm / "mcp.json"
        cfg_path.write_text(json.dumps(ARMS[arm]["mcp"]))
        vis = memory_visibility(arm, profile, cwd)
        if vis["applicable"]:
            print(f"[{arm}] memory reachable inside the sandbox: scope=("
                  f"{vis['namespace']},{vis['actor']}) active={vis['visible']} "
                  f"hits={vis['detail'].get('hits')} "
                  f"wal={vis['wal_sidecars_live_during_probe']}", flush=True)
            if not vis["reached"] or vis["visible"] != CORPUS_SIZE:
                print(f"[{arm}] ABORT: corpus not reachable from inside the boundary "
                      f"-> {vis['detail']}", flush=True)
                return 1
        print(f"[{arm}] running ...", flush=True)
        rec = invoke(arm, cwd)
        rec["boundary"] = bound
        rec["memory_visibility"] = vis
        rec.update(read_trace(arm))
        patch = patch_of(cwd)
        (RUN / "arms" / arm / "patch.diff").write_text(patch)
        rec["patch_bytes"] = len(patch)
        rec["patch_touches_src"] = "src/click/" in patch
        rec["scored"] = score(cwd, held, checks, PYTEST_PY, RUN / "scoring" / arm)
        res = rec.get("result") or {}
        u = res.get("usage", {})
        rec["terminal"] = classify(rec)
        rec["terminal"]["envelope"] = {
            "is_error": res.get("is_error"), "subtype": res.get("subtype"),
            "terminal_reason": res.get("terminal_reason")}
        rec["usage"] = {k: u.get(k) for k in
                        ("input_tokens", "output_tokens", "cache_read_input_tokens",
                         "cache_creation_input_tokens")}
        rec["web_search_requests"] = u.get("server_tool_use", {}).get("web_search_requests")
        rec["permission_denials"] = res.get("permission_denials")
        # The corpus is frozen. Arm 2's inventory still offers record/revise/forget -- an
        # allowlist withholds permission, it does not withdraw the tool -- so mutation is
        # checked rather than assumed.
        if arm == "nexus":
            rec["store_digest_after"] = store_digest()
        print(f"  -> {rec['terminal']}  patch={rec['patch_bytes']}B  "
              f"checks: {rec['scored']['summary']}  wall={rec['wall_clock_s']}s", flush=True)
        records.append(rec)

    after = store_digest()
    print(f"\nfrozen store digest after all arms: {after}  unchanged={before == after}")
    (RUN / "records.json").write_text(json.dumps(
        {"task": TASK, "seed": SEED, "order": order, "max_turns": MAX_TURNS,
         "wall_clock_s": WALL_CLOCK_S, "prompt": PROMPT,
         "deny_ordering": ordering,
         "store_digest_before": before, "store_digest_after": after,
         "store_unchanged": before == after, "records": records}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
