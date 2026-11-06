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
RUN = SP / "a3run"
REPO = Path("/Users/soko/Cerebros/nexus-memory")
BENCH = REPO / "benchmarks" / "agent"
PYTEST_PY = "/private/tmp/claude-501/-Users-soko-Cerebros-nexus-memory/215651df-6e30-4d49-a919-8b24f46175e8/scratchpad/.venv-click/bin/python"
sys.path.insert(0, str(BENCH))
from build_fixture import score  # the same scorer the controls used

SEED = 20260911
MAX_TURNS = 30
WALL_CLOCK_S = 600

# The task prompt. Byte-for-byte identical in every arm. It states the observable symptom
# and the expected outcome; it does not name the function, the file or the mechanism.
# Authored by someone who had read the fix -- the same declared exposure the development
# corpus carries (capture-policy-a1 section 5), and the same reason this run cannot produce
# a benefit figure.
PROMPT = (
    "In this Click checkout there is a bug in how an option resolves its value from the "
    "environment through the context's automatic environment-variable prefix.\n\n"
    "Reproduction: a command with an option declared "
    "`@click.option(\"--mypath\", type=click.Path(exists=True), envvar=\"MYPATH\")`, invoked "
    "with `auto_envvar_prefix=\"AUTO\"` and with `AUTO_MYPATH` set to the empty string in the "
    "environment, raises instead of completing with `mypath` left as `None`.\n\n"
    "Fix the behaviour in `src/`, and extend the existing test suite to cover it. Do not "
    "change behaviour unrelated to this bug. When you are done, reply DONE.\n\n"
    "Before your first source edit, consult any available prior-work memory: Nexus memory "
    "tools or `NOTES-FROM-EARLIER-WORK.md`. If neither is available, proceed using the "
    "repository. Treat prior notes as potentially outdated and verify relevant claims "
    "against current code."
)

# protocol-a1 section 11.4 requires external sources to be blocked while the model API stays
# reachable, separated by an explicit allowlist. The first run enforced NOTHING: an arm
# downloaded click from PyPI and read the fixed implementation before patching. This is that
# allowlist. Both directions are controlled per arm, not assumed.
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
                 "--actor", "task-session"]}}},
        "tools": BASE_TOOLS + "," + NEXUS_TOOLS, "notes": False},
    "notes": {"mcp": {"mcpServers": {}}, "tools": BASE_TOOLS, "notes": True},
}


def prepare(arm: str) -> Path:
    """A fresh, isolated checkout per attempt (protocol-a1 section 3)."""
    dst = RUN / "arms" / arm / "repo"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RUN / "base" / "d1", dst, symlinks=True)
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


def invoke(arm: str, cwd: Path) -> dict:
    cfg = RUN / "arms" / arm / "mcp.json"
    cfg.write_text(json.dumps(ARMS[arm]["mcp"]))
    cmd = ["claude", "--bare", "-p", PROMPT, "--model", "deepseek-flash",
           "--mcp-config", str(cfg), "--strict-mcp-config",
           "--allowedTools", ARMS[arm]["tools"],
           "--disallowedTools", "WebSearch,WebFetch",
           "--permission-mode", "acceptEdits",
           "--disable-slash-commands",
           "--max-turns", str(MAX_TURNS),
           "--output-format", "stream-json", "--verbose"]
    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"
    env["ANTHROPIC_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
    env.update(NET)
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
    """Tool trace, terminal status and provider-reported usage."""
    calls, result = [], None
    for line in (RUN / "arms" / arm / "trace.jsonl").read_text().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "assistant":
            for b in d["message"].get("content", []):
                if b.get("type") == "tool_use":
                    calls.append({"name": b.get("name"), "input": b.get("input")})
        if d.get("type") == "user":
            content = d.get("message", {}).get("content", [])
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result":
                        text = b.get("content")
                        if isinstance(text, list):
                            text = " ".join(x.get("text", "") for x in text if isinstance(x, dict))
                        for c in reversed(calls):
                            if "result" not in c:
                                c["result"] = (text or "")[:4000]
                                break
        if d.get("type") == "result":
            result = d
    return {"tool_calls": calls, "result": result}


def patch_of(cwd: Path) -> str:
    subprocess.run(["git", "-C", str(cwd), "add", "-A", "-N"], check=True)
    return subprocess.run(["git", "-C", str(cwd), "diff"],
                          capture_output=True, text=True, check=True).stdout


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
    fixtures = json.loads((RUN / "base" / "fixtures.json").read_text())[0]
    checks, held = fixtures["checks"], Path(fixtures["held_checks"])

    order = list(ARMS)
    random.Random(SEED).shuffle(order)
    print(f"seed={SEED}  realised arm order: {' -> '.join(order)}\n")

    before = store_digest()
    print(f"frozen store digest before any arm: {before}\n")
    records = []
    for arm in order:
        cwd = prepare(arm)
        egress = subprocess.run(
            [sys.executable, "-m", "pip", "download", "click", "--no-deps", "-d",
             str(RUN / "egress-probe")],
            cwd=cwd, env={**os.environ, **NET}, capture_output=True, text=True, timeout=60)
        # pip creates its -d directory before it fails, so "directory absent" is the wrong
        # test and reported a blocked network as reachable. What matters is that nothing
        # was fetched into it.
        probe = RUN / "egress-probe"
        fetched = sorted(f.name for f in probe.iterdir()) if probe.exists() else []
        blocked = egress.returncode != 0 and not fetched
        print(f"[{arm}] egress control: pip download blocked = {blocked}", flush=True)
        if not blocked:
            print(f"[{arm}] ABORT: external source reachable "
                  f"(pip exit={egress.returncode}, fetched={fetched})", flush=True)
            return 1
        print(f"[{arm}] running ...", flush=True)
        rec = invoke(arm, cwd)
        rec["egress_blocked"] = blocked
        rec["egress_probe_exit"] = egress.returncode
        rec["egress_probe_stderr_tail"] = (egress.stderr or egress.stdout or "").strip().splitlines()[-1:] 
        rec.update(read_trace(arm))
        patch = patch_of(cwd)
        (RUN / "arms" / arm / "patch.diff").write_text(patch)
        rec["patch_bytes"] = len(patch)
        rec["patch_touches_src"] = "src/click/" in patch
        rec["scored"] = score(cwd, held, checks, PYTEST_PY, RUN / "scoring" / arm)
        res = rec.get("result") or {}
        u = res.get("usage", {})
        rec["terminal"] = {"is_error": res.get("is_error"), "subtype": res.get("subtype"),
                           "terminal_reason": res.get("terminal_reason"),
                           "num_turns": res.get("num_turns")}
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
        {"task": "d1", "seed": SEED, "order": order, "max_turns": MAX_TURNS,
         "wall_clock_s": WALL_CLOCK_S, "prompt": PROMPT,
         "store_digest_before": before, "store_digest_after": after,
         "store_unchanged": before == after, "records": records}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
