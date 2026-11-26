"""Stage B recall latency: nexus_recall in catalog mode, through the launcher and the fence.

usage: uv run python -I benchmarks/recall_latency/catalog.py <launcher.py> <server-executable> <new-empty-workdir> <n_memories> <n_calls>

The repo is enrolled through an approved folder, so its memory lives in the owner catalog. The script raises it to
maintain, records n_memories through the v2 server (every write goes through the fence), lowers it back to recall,
then opens a claude-hooks session and times warm nexus_recall calls. It uses the same corpus and prompts as run.py,
so its rows compare with docs/implementation/phase0-baseline.md §5. HOME, the registry, the catalog and the
repository all live under <workdir>; nothing real is touched.
"""
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import WORDS, text, token  # same corpus and prompts as the Phase 0 baseline


async def main(launcher: Path, server: Path, work: Path, n_mem: int, n_calls: int) -> dict:
    home = work / "home"
    home.mkdir(mode=0o700, parents=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_GLOBAL": "/dev/null",
           "NEXUS_CAPTURE_POLICY": "explicit"}
    folder = work / "work"
    repo = folder / "repo"
    repo.mkdir(parents=True)
    repo = repo.resolve()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=b", "-c", "user.email=b@example.invalid",
                    "commit", "-qm", "init", "--allow-empty"], check=True, env=env)

    def owner(*args: str) -> None:
        subprocess.run([sys.executable, "-I", str(launcher), *args], check=True, env=env, capture_output=True)

    def params(role: str) -> StdioServerParameters:
        return StdioServerParameters(command=sys.executable, args=["-I", str(launcher), role, str(server)],
                                     env={**env, "PWD": str(repo)}, cwd=str(repo))

    owner("approve-folder", str(folder.resolve()))
    # The first open creates the space. The server it starts sees end of input and exits.
    subprocess.run([sys.executable, "-I", str(launcher), "claude", str(server)], env={**env, "PWD": str(repo)},
                   cwd=repo, stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=60)
    owner("mode", str(repo), "maintain")
    async with stdio_client(params("claude")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for i in range(n_mem):
            res = await s.call_tool("record", {"content": text(i), "kind": "decision", "idempotency_key": f"bench-{i}"})
            assert not res.is_error, res.content
    owner("mode", str(repo), "recall")
    t_launch = time.perf_counter()
    async with stdio_client(params("claude-hooks")) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        init_ms = (time.perf_counter() - t_launch) * 1000
        samples, offered = [], 0
        for c in range(n_calls):
            target = (c * 7919) % max(n_mem, 1)
            prompt = f"what did we decide about {token(target)} and the {WORDS[c % len(WORDS)]} path?"
            t = time.perf_counter()
            res = await s.call_tool("nexus_recall", {"session_id": str(uuid.uuid4()), "cwd": str(repo), "prompt": prompt})
            samples.append((time.perf_counter() - t) * 1000)
            assert not res.is_error, res.content
            offered += "memory_id=" in json.dumps([getattr(x, "text", "") for x in res.content])
    q = statistics.quantiles(samples, n=100)
    return {"mode": "catalog (recall only, through the fence)", "memories": n_mem, "calls": n_calls,
            "launch_to_initialized_ms": round(init_ms, 1),
            "recall_ms": {"p50": round(q[49], 1), "p95": round(q[94], 1), "max": round(max(samples), 1),
                          "first": round(samples[0], 1)},
            "calls_with_an_offer": offered}


if __name__ == "__main__":
    out = asyncio.run(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]),
                           int(sys.argv[5])))
    print(json.dumps(out, indent=1))
