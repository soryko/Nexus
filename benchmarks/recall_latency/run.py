"""Phase 0 recall-latency baseline: time nexus_recall against an installed runtime on a disposable store.

usage: uv run python -I benchmarks/recall_latency/run.py <server-executable> <new-empty-workdir> <n_memories> <n_calls>

The script times the client-side round trip over stdio MCP to a server that is already running: what a
host hook sees, minus the host's own dispatch. The store, the Git repository and HOME all live
under <workdir>, so nothing real is touched. Each memory has its own distinctive term and each
call targets one, with a fresh session per call. A corpus of shared words lets no candidate clear
the threshold and times only the no-offer path. Check calls_mentioning_memory, which should equal
n_calls, and should be 0 for an empty store. Results: docs/implementation/phase0-baseline.md §5.
"""
import asyncio, json, os, statistics, subprocess, sys, time, uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

WORDS = ("staging database queue retention worker cache index migration deploy schema token "
         "latency recall session hook revision store fence grant project space capture").split()


def token(i: int) -> str:
    """A pronounceable, distinctive term per memory, so each one is findable by its own word."""
    syl = ["ka", "lo", "mi", "ru", "te", "vo", "sa", "ne", "pi", "zu", "ga", "do"]
    out, n = [], i + 1
    while n:
        n, r = divmod(n, len(syl))
        out.append(syl[r])
    return "".join(out) + "x"


def text(i: int) -> str:
    w = [WORDS[(i * 7 + k * 3) % len(WORDS)] for k in range(8)]
    return f"Decision on {token(i)}: " + " ".join(w) + f". The {token(i)} {w[0]} path stays bounded."


async def main(server: str, work: Path, n_mem: int, n_calls: int) -> dict:
    repo = work / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(work / "home")}
    (work / "home").mkdir(exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
        (repo / "a.txt").write_text("a\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=b", "-c", "user.email=b@example.invalid",
                        "commit", "-qm", "init"], check=True, env=env)
    params = StdioServerParameters(command=server, args=[
        "--namespace", "bench", "--db", str(work / "bench.sqlite3"), "--hook-project-root", str(repo)],
        env=env, cwd=str(repo))
    t_launch = time.perf_counter()
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        init_ms = (time.perf_counter() - t_launch) * 1000
        tools = {t.name for t in (await s.list_tools()).tools}
        for i in range(n_mem):
            res = await s.call_tool("record", {"content": text(i), "kind": "decision",
                                               "idempotency_key": f"bench-{i}"})
            assert not res.is_error, res.content
        samples, offered = [], 0
        for c in range(n_calls):
            sid = str(uuid.uuid4())              # a fresh session, so per-session de-duplication never shortens a call
            target = (c * 7919) % max(n_mem, 1)
            prompt = f"what did we decide about {token(target)} and the {WORDS[c % len(WORDS)]} path?"
            t = time.perf_counter()
            res = await s.call_tool("nexus_recall", {"session_id": sid, "cwd": str(repo), "prompt": prompt})
            samples.append((time.perf_counter() - t) * 1000)
            assert not res.is_error, res.content
            body = json.dumps([getattr(x, "text", "") for x in res.content])
            offered += "memory" in body.lower()
    q = statistics.quantiles(samples, n=100)
    return {"server": server, "memories": n_mem, "calls": n_calls, "launch_to_initialized_ms": round(init_ms, 1),
            "recall_ms": {"p50": round(q[49], 1), "p95": round(q[94], 1), "max": round(max(samples), 1),
                          "first": round(samples[0], 1)},
            "calls_mentioning_memory": offered, "nexus_recall_listed": "nexus_recall" in tools}


if __name__ == "__main__":
    out = asyncio.run(main(sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
    print(json.dumps(out, indent=1))
