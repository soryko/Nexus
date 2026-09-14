"""One A1 arm-run set: one task, one attempt, three arms in the frozen order.

Usage:  run_arms_isolated.py <scratch> <task> <attempt> <schedule.json>

Everything an attempt produces lands under `run-<task>/attempt<n>/`, and everything it reads
that it must not carry forward -- above all the memory corpus -- is a copy it is given. Both
of those are recent. `ATTEMPT` used to be parsed and recorded but appear in no path, so three
attempts wrote over one another and one record survived; and one store was shared by every arm
and every attempt, so a write in attempt 1 was visible to attempts 2 and 3.

Against the development corpus this is harness validation only (capture-policy-a1 section 5):
no benefit figure may be computed from a corpus authored by someone who had read the fixes.
What such a run shows is that the plumbing works and the arms differ in the intended way.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

SP = Path(sys.argv[1])
TASK = sys.argv[2]
ATTEMPT = int(sys.argv[3]) if len(sys.argv) > 3 else 1
SCHEDULE = Path(sys.argv[4]) if len(sys.argv) > 4 else None
RUN = SP / f"run-{TASK}"        # shared per task: the base checkout and its fixtures
OUT = RUN / f"attempt{ATTEMPT}"  # EVERYTHING one attempt produces, and nothing another does
BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))
import a1_config
from build_fixture import score  # the same scorer the controls used
from trace_parse import parse as parse_trace
from terminal_status import classify
import isolation
import schedule as sched
import task_set

# Every path and ceiling comes from the configuration file now, validated before anything
# runs. Two of these used to be constants naming one session's scratchpad by its UUID, so the
# harness worked on exactly one host on exactly one day -- and a deny for a path that has
# moved looks, in the artifact, exactly like a deny that works.
CFG = a1_config.load().require()
REPO = Path(CFG.repo)
PYTEST_PY = CFG.pytest_python
FORWARDER_PORT = CFG.forwarder_port
CORPUS_SIZE = CFG.corpus_size
MEMORY_PROBE_QUERY = CFG.memory_probe_query
SOURCE_CLONE = CFG.source_clone
MAX_TURNS = CFG.max_turns
WALL_CLOCK_S = CFG.wall_clock_s

# The fixtures, and beside them the hidden acceptance checks. This was `""` until `main()`
# assigned it, which made `boundary_paths` depend on an assignment in another function: called
# before it, the deny that withholds the checks came out as the RELATIVE path `checks`, which
# `write_profile` then resolved against whatever the process's working directory happened to
# be. A deny naming the wrong path is indistinguishable, in the profile and in the artifact,
# from a deny that works -- the failure mode this harness has already been bitten by twice. It
# was never anything but `RUN/base`, so it is that, once, here.
FIXTURE_BASE = str(RUN / "base")

# The task prompt. Byte-for-byte identical in every arm. Every prompt states the observable
# symptom and the expected outcome; none names the function, the file or the mechanism.
#
# These used to be a dict of literals here, which meant a held-out task could not be run
# without editing the harness -- and an instrument edited mid-experiment is a different
# instrument. They are data now, in `prompts-a1.json`, reconstructing d1-d4 byte-for-byte so
# the development runs stay comparable. It also keeps the held-out prompts authorable as data
# by someone who need not open the runner at all.
PROMPT_REGISTRATION = CFG.bench_path("prompts")
_reg = task_set.registration(PROMPT_REGISTRATION)
_spec = task_set.require(_reg, TASK, ("development", "heldout"), "run_arms_isolated.py")
# body + tail + environment + consult. `environment` is OPTIONAL and absent from
# prompts-a1.json, so every A1 prompt still reconstructs byte-for-byte; it exists because A1
# measured agents spending turns rediscovering how to run the checkout, which is a property of
# the harness and not of any arm. When present it is appended identically for all three arms.
PROMPT = (_spec["body"] + _reg["tails"][_spec["tail"]]
          + _reg.get("environment", "") + _reg["consult"])

# What an arm's environment is, and is not: `a1_config.ENV_ALLOWLIST` plus the two names the
# harness sets itself. It used to be `dict(os.environ)` -- the operator's whole shell, none of
# it held fixed across arms. The superseded proxy variables that used to sit here are gone
# rather than kept as decoration: they were never merged into the child environment, so the
# comment claiming they kept the arms identical described nothing the code did.

# The per-arm environment gate. On by default; A1's frozen runs predate it and reproduce with
# NEXUS_A1_ENV_GATE=0, which is how a historical run is re-executed unchanged.
ENV_GATE = os.environ.get("NEXUS_A1_ENV_GATE", "1") != "0"

BASE_TOOLS = "Read,Edit,Write,Bash,Glob,Grep"
NEXUS_TOOLS = "mcp__nexus__search,mcp__nexus__get,mcp__nexus__history,mcp__nexus__status"

ARMS = {
    "baseline": {"tools": BASE_TOOLS, "notes": False, "memory": False},
    "nexus": {"tools": BASE_TOOLS + "," + NEXUS_TOOLS, "notes": False, "memory": True},
    "notes": {"tools": BASE_TOOLS, "notes": True, "memory": False},
}

# The pristine corpus, and the copy one arm-run is given.
#
# There used to be one store for the whole run -- `RUN/nexus-dev.db` -- shared by every arm
# and every attempt, seeded once and never restored. Arm 2 holds Bash inside a profile that
# allows the store (it must: the server writes to it), so a write in attempt 1 was visible to
# attempts 2 and 3 and to the `nexus` - `notes` contrast. Detection existed and prevention did
# not: the digest was taken before the first arm and after the last, so a mutation was
# reported once the run it had already contaminated was over.
#
# Each arm-run now gets its own copy of a master that is never opened by an arm. Mutation is
# still detected, per arm-run rather than per run, but it can no longer reach anything.
STORE_MASTER = Path(CFG.store_master) if CFG.store_master else RUN / "nexus-dev.db"


def arm_store(arm: str) -> Path:
    """Deliberately NOT under `OUT/arms/<arm>/`, which the arm is allowed as a whole subtree.

    Put there, the store was readable by containment, and `sqlite_read_paths` -- the three-file
    allow that exists because a WAL store is `.db`, `-wal` and `-shm` -- stopped carrying any
    weight for the arm that needs it. The control that compares the one-file spelling against
    the three-file one is what noticed: both reached, where only the three-file one should.
    Kept outside, the store is allowed BY NAME and by nothing else, which is what the profile
    has always claimed.
    """
    return OUT / "stores" / arm / "nexus.db"


def mcp_config(arm: str) -> dict:
    if not ARMS[arm]["memory"]:
        return {"mcpServers": {}}
    return {"mcpServers": {"nexus": {
        "command": CFG.nexus_server,
        "args": ["--db", str(arm_store(arm)), "--namespace", CFG.namespace,
                 "--actor", CFG.actor]}}}


def provision_store(arm: str) -> Path | None:
    """Give this arm-run its own copy of the frozen store. Returns the copy, or None.

    A WAL store is three files and the sidecars carry committed frames, so all of them that
    exist are copied. The master itself is opened by nothing here -- not even to check it --
    which is what keeps it pristine across 36 arm-runs.
    """
    if not ARMS[arm]["memory"]:
        return None
    store = arm_store(arm)
    if store.parent.exists():
        shutil.rmtree(store.parent)
    store.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(STORE_MASTER) + suffix)
        if source.exists():
            shutil.copy2(source, str(store) + suffix)
    return store


def prepare(arm: str) -> Path:
    """A fresh, isolated checkout per attempt (protocol-a1 section 3)."""
    dst = OUT / "arms" / arm / "repo"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RUN / "base" / TASK, dst, symlinks=True)
    if ARMS[arm]["notes"]:
        # capture-policy-a1 section 4: the same captured information, one readable file,
        # mechanically rendered. Placed at the checkout root, and committed so that the
        # agent's own patch is measured against a tree that already contains it.
        shutil.copy2(CFG.bench_path("notes_file"), dst / "NOTES-FROM-EARLIER-WORK.md")
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

    The store is allowed to the NEXUS ARM ONLY, and what is allowed is that arm-run's own
    copy. It was previously allowed to all three, which handed the baseline and notes arms
    file-read on the corpus they are defined by not having; an arm holding Bash and
    `/usr/bin/sqlite3` needed only to guess the filename. The run directory is still not
    allowed as a whole; the three store files are named, and no more.

    What is NOT claimed here is a write deny on the nexus arm's store. The server that arm
    talks to opens the store read-write and migrates it on open, so denying writes would stop
    the arm running at all. `--allowedTools` withholds `record`/`revise`/`forget` at the
    client, which is a client-side gate and not a boundary. The boundary is that the store is
    a copy: the master is denied to every arm, and whatever this one writes dies with it.
    """
    allow = isolation.default_allow_paths(cwd, PYTEST_PY) + [
        OUT / "arms" / arm,                 # mcp.json, sandbox.sb, mcp_probe.py
        REPO / ".venv-sqlite", REPO / "src", REPO / "pyproject.toml",
    ]
    if ARMS[arm]["memory"]:
        allow += isolation.sqlite_read_paths(arm_store(arm))         # .db, -wal, -shm
    deny = [Path(FIXTURE_BASE) / "checks",
            *[(OUT / "arms" / a) for a in ARMS if a != arm],
            OUT / "scoring",                # created part-way through the run; denied anyway
            STORE_MASTER.parent if CFG.store_master else STORE_MASTER,
            Path(SOURCE_CLONE),
            BENCH]                          # saved patches, reports, corpus, task sheet
    if ARMS[arm]["memory"]:
        # every other arm-run's store, named as a tree. Its own three files are allowed above
        # and denies are emitted after allows, so this must not cover them.
        deny += [OUT / "stores" / a for a in ARMS if a != arm]
    if not ARMS[arm]["memory"]:
        # named explicitly rather than left to the default, so the artifact records that the
        # store was withheld from this arm rather than merely unmentioned. For these two arms
        # `isolation` emits a write deny beside the read deny; for the nexus arm it cannot,
        # because the server it runs opens the store read-write and migrates it. That arm's
        # isolation is the private copy above, not a permission.
        deny += [OUT / "stores", *isolation.sqlite_read_paths(arm_store(arm))]
    return allow, deny


def write_arm_profile(arm: str, cwd: Path) -> Path:
    """This arm's mcp config and sandbox profile. Separated from `invoke` so that EVERY arm's
    profile exists before the FIRST arm is gated: the cross-arm probe runs a sibling sandbox,
    and a sibling whose profile has not been written yet cannot be distinguished from a
    sibling that is correctly denying access."""
    cfg = OUT / "arms" / arm / "mcp.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(mcp_config(arm)))
    allow, deny = boundary_paths(arm, cwd)
    return isolation.write_profile(OUT / "arms" / arm / "sandbox.sb", cwd, deny, allow,
                                   FORWARDER_PORT)


def invoke(arm: str, cwd: Path) -> dict:
    cfg = OUT / "arms" / arm / "mcp.json"
    profile = write_arm_profile(arm, cwd)
    cmd = ["sandbox-exec", "-f", str(profile),
           "claude", "--bare", "-p", PROMPT, "--model", "deepseek-flash",
           "--mcp-config", str(cfg), "--strict-mcp-config",
           "--allowedTools", ARMS[arm]["tools"],
           "--disallowedTools", "WebSearch,WebFetch",
           "--permission-mode", "acceptEdits",
           "--disable-slash-commands",
           "--max-turns", str(MAX_TURNS),
           "--output-format", "stream-json", "--verbose"]
    # Per-arm here-document scratch. zsh writes a heredoc body to $TMPPREFIX* and reads it
    # back; the default /tmp/zsh is unreadable under the profile, so every heredoc failed. It
    # is NOT $TMPDIR -- measured, setting that leaves heredocs failing. Pointing it inside the
    # arm's own directory keeps the fix arm-private: `allow` grants this arm its own directory
    # and `deny` withholds every sibling's, so one arm's heredoc bodies are unreadable to the
    # others. A shared grant under /private/tmp would have been a channel between arms.
    heredoc_tmp = OUT / "arms" / arm / "tmp"
    heredoc_tmp.mkdir(parents=True, exist_ok=True)
    # the sandbox denies every host but localhost; the forwarder is the only egress
    env = a1_config.child_env(f"http://127.0.0.1:{FORWARDER_PORT}/anthropic",
                              os.environ["DEEPSEEK_API_KEY"],
                              {"TMPPREFIX": str(heredoc_tmp / "zsh")})
    # Gate THIS arm, with the profile and environment it is about to run under -- not a
    # representative one built elsewhere. A1's arms were measured in a sandbox where the
    # documented interpreter invocation and here-documents did not work; none of it reached
    # `permission_denials`, and nothing was checking the thing the arm would actually use.
    # Refusing costs one row; not refusing cost 45 arm-runs.
    if ENV_GATE:
        import verify_arm_environment as VENV
        others = [OUT / "arms" / a for a in ARMS
                  if a != arm and (OUT / "arms" / a / "sandbox.sb").exists()
                  and (OUT / "arms" / a / "repo").exists()]
        gate = VENV.run(OUT / "arms" / arm, env, other_arm=others[0] if others else None)
        (OUT / "arms" / arm / "envcheck.json").write_text(json.dumps(gate, indent=1))
        if not gate["all_passed"]:
            failed = [c["check"] for c in gate["checks"] if not c["passed"]]
            raise SystemExit(f"[{arm}] environment gate FAILED: {failed}\n"
                             f"  Not spending on this arm. See {OUT}/arms/{arm}/envcheck.json")
        print(f"[{arm}] environment gate: {len(gate['checks'])} checks pass", flush=True)

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    # Written BEFORE the model is invoked, and never removed. `capture_output=True` buffers
    # the whole trace until the subprocess returns, so an interruption inside that window
    # leaves no trace, no record and no evidence the arm ever ran -- which is exactly how k4's
    # consumption was lost. This marker is what makes "launched" distinguishable from
    # "prepared but never started", and an arm that is launched and never accounted for stays
    # UNRESOLVED rather than disappearing.
    (OUT / "arms" / arm / "launched.json").write_text(json.dumps({
        "arm": arm, "task": TASK, "attempt": ATTEMPT,
        "launched_utc": started.isoformat(),
        "max_turns": MAX_TURNS, "wall_clock_s_limit": WALL_CLOCK_S,
        "identity": run_identity(),
    }, indent=1))
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
    (OUT / "arms" / arm / "trace.jsonl").write_text(out)
    (OUT / "arms" / arm / "stderr.txt").write_text(err)
    return {"arm": arm, "started_utc": started.isoformat(), "wall_clock_s": round(wall, 1),
            "forced_verdict": verdict, "child_env": a1_config.env_record(env)}


def read_trace(arm: str) -> dict:
    """Tool trace and envelope, joined by tool_use_id (see trace_parse)."""
    t = parse_trace(OUT / "arms" / arm / "trace.jsonl")
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
    cfg = json.loads((OUT / "arms" / arm / "mcp.json").read_text())
    server = cfg.get("mcpServers", {}).get("nexus")
    if not server:
        return {"applicable": False}
    args = server["args"]
    db = args[args.index("--db") + 1]
    ns = args[args.index("--namespace") + 1]
    actor = args[args.index("--actor") + 1]

    probe = OUT / "arms" / arm / "mcp_probe.py"      # BENCH is denied; the arm dir is not
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


def store_digest(db: Path) -> str:
    """A digest of the store's CONTENT, not of its file.

    The first version of this hashed the database file and reported the frozen corpus as
    mutated across a run in which the agent made no memory call at all. The store is in WAL
    mode: opening it is enough to rewrite file bytes and grow the file. A file hash cannot
    tell a checkpoint from a write, so it cannot answer the question it was added to answer.
    This reads the rows instead.

    It takes the store as an argument because there is no longer one store: the master is
    digested once, before any arm runs, and each arm-run's own copy is digested on either
    side of that arm.
    """
    import hashlib, sqlite3
    con = sqlite3.connect(db)
    rows = list(con.execute(
        "select m.memory_id, m.current_revision_id, m.tombstoned, r.kind, r.tags_json, b.body "
        "from memories m join revisions r on r.revision_id = m.current_revision_id "
        "join blobs b on b.id = r.blob_id order by m.memory_id"))
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def run_identity() -> dict:
    """Everything that has to match for two arm-runs to be poolable.

    The registration promised the product revision would be recorded in the run record; it was
    not, and A1's had to be inferred from git history afterwards. It is recorded here, beside
    the harness revision, the configuration version and digest, and the ceiling AS APPLIED --
    the config's filename does not establish its `max_turns`.
    """
    import hashlib
    import subprocess as sp

    def rev(path: str) -> str | None:
        try:
            out = sp.run(["git", "-C", str(REPO), "log", "-1", "--format=%H", "--", path],
                         capture_output=True, text=True, timeout=30)
            return (out.stdout.strip() or None) if out.returncode == 0 else None
        except Exception:
            return None

    return {
        "product_revision": rev("src/nexus_memory"),
        "harness_revision": rev("benchmarks/agent"),
        "config_version": CFG.config_version,
        "config_digest": hashlib.sha256(
            json.dumps(CFG.as_recorded(), sort_keys=True).encode()).hexdigest()[:16],
        "prompt_registration": str(PROMPT_REGISTRATION),
        "prompt_digest": hashlib.sha256(PROMPT.encode()).hexdigest()[:16],
        "schedule_digest": None,
        "corpus_digest_registered": CFG.corpus_digest or None,
        "max_turns_applied": MAX_TURNS,
        "wall_clock_s_applied": WALL_CLOCK_S,
        "functional_scorer_version": __import__("build_fixture").FUNCTIONAL_SCORER_VERSION,
        "scorer_version": __import__("score_compliance").SCORER_VERSION,
    }


def main() -> int:
    allfx = json.loads((RUN / "base" / "fixtures.json").read_text())
    fixtures = next(f for f in allfx if f["task"] == TASK)
    checks, held = fixtures["checks"], Path(fixtures["held_checks"])

    # The order is READ from the frozen schedule, never re-derived. The old code built it
    # with `random.Random(SEED).shuffle(...)` inside each run -- a fresh generator from the
    # same constant every time, which recorded a seed and produced one order everywhere:
    # all eight saved development runs say `baseline -> nexus -> notes`.
    if SCHEDULE is None:
        print("no schedule given; a scored run requires one frozen before execution\n"
              "usage: run_arms_isolated.py <scratch> <task> <attempt> <schedule.json>")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    plan = sched.load(SCHEDULE)
    order = plan.order_for(TASK, ATTEMPT)
    print(f"schedule {SCHEDULE.name} digest={plan.schedule_digest[:16]} seed={plan.seed}\n"
          f"{TASK} attempt {ATTEMPT}: realised arm order {' -> '.join(order)}\n")

    # the deny-ordering control: once per run, not per arm -- it is a property of the
    # profile generator, not of any one arm's profile
    ordering = isolation.deny_ordering_probe(OUT / "ordering-probe", RUN)
    print(f"deny-after-allow ordering holds: {ordering['ordering_holds']}  "
          f"(denied child blocked={ordering['synthetic_denied_child']['demonstrates_boundary']}, "
          f"allowed sibling readable="
          f"{not ordering['synthetic_allowed_sibling']['blocked_inside']}, "
          f"~/.claude/projects blocked="
          f"{ordering['home_claude_projects'].get('demonstrates_boundary')})\n", flush=True)
    if not ordering["ordering_holds"]:
        print("ABORT: denies do not win inside an allowed subtree", flush=True)
        return 1

    # The master is digested once and compared against the digest the corpus was REGISTERED
    # with. Digesting it and calling the answer "frozen" would certify whatever is on disk;
    # the registered value is the only thing that can catch the wrong store being configured.
    before = store_digest(STORE_MASTER)
    print(f"master store: {STORE_MASTER}\n  digest {before}")
    if CFG.corpus_digest:
        if before != CFG.corpus_digest:
            print(f"ABORT: master store digest {before} is not the registered corpus digest "
                  f"{CFG.corpus_digest}", flush=True)
            return 1
        print(f"  matches the registered corpus digest\n")
    else:
        print("  UNGATED: no corpus_digest is registered in the configuration, so the master "
              "is taken as given\n")

    # Every arm's checkout and profile, written before the FIRST arm is gated. The cross-arm
    # probe runs a sibling sandbox; preparing arms lazily meant the first arm's probe pointed
    # at a profile that did not exist yet, which the probe could not tell apart from a sibling
    # that was correctly refusing.
    prepared = {a: prepare(a) for a in order}
    for a in order:
        write_arm_profile(a, prepared[a])
    print(f"prepared {len(prepared)} arm checkouts and profiles: {', '.join(order)}", flush=True)

    records = []
    for arm in order:
        cwd = prepared[arm]
        store = provision_store(arm)
        store_before = store_digest(store) if store else None
        if store and store_before != before:
            # the copy, not the corpus: a provisioning failure, not an arm's doing
            print(f"[{arm}] ABORT: provisioned store digest {store_before} != master {before}",
                  flush=True)
            return 1
        if store:
            print(f"[{arm}] store: private copy at {store}, digest {store_before}", flush=True)
        allow, deny = boundary_paths(arm, cwd)
        profile = isolation.write_profile(OUT / "arms" / arm / "sandbox.sb", cwd, deny, allow,
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
        cfg_path = OUT / "arms" / arm / "mcp.json"
        cfg_path.write_text(json.dumps(mcp_config(arm)))
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
        (OUT / "arms" / arm / "patch.diff").write_text(patch)
        rec["patch_bytes"] = len(patch)
        rec["patch_touches_src"] = "src/click/" in patch
        rec["scored"] = score(cwd, held, checks, PYTEST_PY, OUT / "scoring" / arm)
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
        if store:
            # Reported, not aborted on. A write here can no longer reach another arm-run, so
            # it is a fact about this one: the arm had `record`/`revise` withheld by the
            # allowlist, not withdrawn from the inventory, and whether it tried is a result.
            rec["store"] = {"path": str(store), "digest_before": store_before,
                            "digest_after": store_digest(store),
                            "master_digest": before}
            rec["store"]["unchanged"] = rec["store"]["digest_after"] == store_before
            if not rec["store"]["unchanged"]:
                print(f"[{arm}] NOTE: this arm-run's own copy of the store changed "
                      f"({store_before} -> {rec['store']['digest_after']}); it is a private "
                      f"copy, so nothing downstream sees it", flush=True)
        print(f"  -> {rec['terminal']}  patch={rec['patch_bytes']}B  "
              f"checks: {rec['scored']['summary']}  wall={rec['wall_clock_s']}s", flush=True)
        records.append(rec)
        # Persist THIS arm before starting the next. The row-level records.json is written
        # only after all three arms, so a row interrupted mid-way used to leave two completed
        # arm-runs with no record of their consumption at all -- spend that no budget could
        # count. One file per arm, written the moment it finishes.
        (OUT / "arms" / arm / "record.json").write_text(json.dumps(
            {"task": TASK, "attempt": ATTEMPT, "arm": arm,
             "identity": run_identity(), "record": rec}, indent=1))

    after = store_digest(STORE_MASTER)
    print(f"\nmaster store digest after all arms: {after}  unchanged={before == after}")
    mutated = [r["arm"] for r in records if not r.get("store", {}).get("unchanged", True)]
    if mutated:
        print(f"arm-runs whose private store changed: {', '.join(mutated)}")
    (OUT / "records.json").write_text(json.dumps(
        {"task": TASK, "attempt": ATTEMPT, "seed": plan.seed, "order": order,
         "schedule": str(SCHEDULE), "schedule_digest": plan.schedule_digest,
         "config": CFG.as_recorded(), "scorer_version": __import__(
             "score_compliance").SCORER_VERSION,
         "functional_scorer_version": __import__(
             "build_fixture").FUNCTIONAL_SCORER_VERSION,
         "max_turns": MAX_TURNS,
         "wall_clock_s": WALL_CLOCK_S, "prompt": PROMPT,
         "deny_ordering": ordering,
         "store_master": str(STORE_MASTER),
         "corpus_digest_registered": CFG.corpus_digest or None,
         "store_digest_before": before, "store_digest_after": after,
         "store_unchanged": before == after,
         "arm_runs_with_mutated_store": mutated,
         "prompt_registration": str(PROMPT_REGISTRATION),
         "identity": {**run_identity(), "schedule_digest": plan.schedule_digest},
         "records": records}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
