#!/usr/bin/env python3
"""Phase 1 scope matrix for the Claude Code plugin, in a disposable profile with synthetic data.

Nothing here touches ~/.claude*, the installed launcher, or any real store. It builds a throwaway HOME
holding a project registry and databases, runs a patched COPY of the PR #17 launcher against it, and drives
`claude -p` (real login, haiku, --no-session-persistence unless a scenario needs resume) with the plugin.
Pass criterion: no foreign-project marker ever appears in any hook output or model reply.

usage: claude_matrix.py WORKDIR OUT.json [--nexus-memory-repo PATH] [--only name,name]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "integrations" / "claude-plugin"
PATCH = REPO / "docs" / "patches" / "launcher-hook-root.patch"
PY = sys.executable
MARK = {"A": "ALPHA-MARKER-7f3a", "B": "BRAVO-MARKER-91c2", "E": "ECHO-MARKER-4d8e", "D": "DELTA-MARKER-2b6c"}


def sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    sh("git", "-C", str(path), "init", "-q")
    sh("git", "-C", str(path), "config", "user.email", "t@example.invalid")
    sh("git", "-C", str(path), "config", "user.name", "T")
    (path / "f.txt").write_text("x\n")
    sh("git", "-C", str(path), "add", ".")
    sh("git", "-C", str(path), "commit", "-qm", "i")
    return path


class World:
    def __init__(self, work: Path, nm_repo: Path):
        self.work = work
        self.home = work / "home"
        self.home.mkdir(mode=0o700, parents=True)
        bin_dir = work / "scope-bin"
        bin_dir.mkdir()
        for name in ("launcher.py", "project_scope.py"):
            (bin_dir / name).write_text(sh("git", "-C", str(nm_repo), "show",
                                          f"feat/project-scope-integration:integrations/project_scope/{name}"))
        stage = work / "patch-stage" / "integrations" / "project_scope"
        stage.mkdir(parents=True)
        shutil.copy(bin_dir / "launcher.py", stage / "launcher.py")
        subprocess.run(["git", "apply", str(PATCH)], cwd=work / "patch-stage", check=True)
        shutil.copy(stage / "launcher.py", bin_dir / "launcher.py")
        self.bin = bin_dir
        self.runtime = work / "runtime.sh"
        self.runtime.write_text(f"#!/bin/sh\nexport PYTHONPATH={REPO / 'src'}\nexec {PY} -m nexus_memory \"$@\"\n")
        self.runtime.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home)}
        self.repos = {n: make_repo(work / "repos" / n) for n in "ABCDE"}
        wt = work / "repos" / "A-worktree"
        sh("git", "-C", str(self.repos["A"]), "worktree", "add", "-q", str(wt))
        self.repos["A-worktree"] = wt
        (self.repos["A"] / "sub").mkdir()
        self.store = self.home / ".local/share/nexus-memory/project-scopes-v1"

    def launcher_cmd(self) -> list[str]:
        return [PY, "-I", str(self.bin / "launcher.py"), "claude", str(self.runtime)]

    def enroll(self, name: str) -> None:
        subprocess.run([PY, "-I", str(self.bin / "launcher.py"), "enroll", str(self.repos[name])],
                       check=True, env=self.env, capture_output=True)

    def project_id(self, name: str) -> str:
        reg = json.loads((self.store / "registry.json").read_text())["projects"]
        return reg[str(self.repos[name] / ".git")]["id"]

    def params(self, cwd: Path) -> StdioServerParameters:
        cmd = self.launcher_cmd()
        return StdioServerParameters(command=cmd[0], args=cmd[1:], cwd=cwd, env={**self.env, "PWD": str(cwd)})

    async def seed(self, name: str, n: int = 3) -> None:
        async with Client(self.params(self.repos[name])) as client:
            for i in range(n):
                r = await client.call_tool("record", {
                    "content": f"quasarflux decision {i}: {MARK[name]} use cedar pallets",
                    "kind": "decision", "idempotency_key": f"seed-{name}-{i}"})
                assert not r.is_error, r

    def claude_env(self) -> dict:
        return {**os.environ, "NEXUS_SCOPE_BIN": str(self.bin), "NEXUS_SCOPE_HOME": str(self.home)}

    def mcp_config(self) -> str:
        cmd = self.launcher_cmd()
        path = self.work / "mcp.json"
        path.write_text(json.dumps({"mcpServers": {"nexus-memory": {
            "command": cmd[0], "args": cmd[1:], "env": {"HOME": str(self.home)}}}}))
        return str(path)


def drive(world: World, cwd: Path, prompts: list[str], *, persist: str | None = None, resume: str | None = None,
          tools: bool = False, timeout: int = 300) -> dict:
    """Run one claude -p session over stream-json input; return timestamped hook events and replies."""
    args = ["claude", "-p", "--model", "haiku", "--strict-mcp-config", "--mcp-config", world.mcp_config(),
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-hook-events",
            "--plugin-dir", str(PLUGIN)]
    if persist:
        args += ["--session-id", persist]
    elif resume:
        args += ["--resume", resume]
    else:
        args += ["--no-session-persistence"]
    if not tools:
        args += ["--tools", ""]
    proc = subprocess.Popen(args, cwd=cwd, env={**world.claude_env(), "PWD": str(cwd)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    events: list[dict] = []

    def reader():
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e["_t"] = time.monotonic()
            events.append(e)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    for prompt in prompts:
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        proc.stdin.flush()
        want = sum(1 for e in events if e.get("type") == "result") + 1
        deadline = time.monotonic() + timeout
        while sum(1 for e in events if e.get("type") == "result") < want and time.monotonic() < deadline:
            time.sleep(0.2)
    proc.stdin.close()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    t.join(timeout=5)
    return summarize(events)


def summarize(events: list[dict]) -> dict:
    started, hooks = {}, []
    for e in events:
        if e.get("type") != "system":
            continue
        sub = e.get("subtype")
        if sub == "hook_started":
            started[e["hook_id"]] = e["_t"]
        elif sub == "hook_response":
            hooks.append({"event": e.get("hook_event"), "name": e.get("hook_name"), "outcome": e.get("outcome"),
                          "output": str(e.get("output") or ""), "stderr": str(e.get("stderr") or ""),
                          "ms": round((e["_t"] - started.get(e["hook_id"], e["_t"])) * 1000)})
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    return {"mcp": [(s["name"], s["status"]) for s in init.get("mcp_servers", []) if "nexus" in s["name"]],
            "hooks": hooks,
            "results": [str(e.get("result")) for e in events if e.get("type") == "result"],
            "assistant_text": [b.get("text", "") for e in events if e.get("type") == "assistant"
                               for b in (e.get("message", {}).get("content") or []) if isinstance(b, dict)],
            "usage": [e.get("usage") for e in events if e.get("type") == "result"]}


def is_ours(h: dict) -> bool:
    """Our hooks, told apart from the user's own plugins' hooks by content (the stream names no source)."""
    o = h["output"]
    if h["event"] == "SessionStart":
        return "Nexus project memory" in o
    if h["event"] == "UserPromptSubmit":
        if h["outcome"] == "error":
            return "nexus-memory" in h["stderr"] or "invalid_input" in h["stderr"]
        return o.strip() == "{}" or "memory_id=" in o or "No project decision" in o or "not enrolled" in o
    return False


def is_pre(h: dict) -> bool:
    return h["event"] == "PreToolUse" and (h["output"].strip() == "{}" or "permissionDecision" in h["output"])


def ours(run: dict, event: str) -> list[dict]:
    return [h for h in run["hooks"] if h["event"] == event and is_ours(h)]


def leaked(run: dict, foreign: list[str]) -> list[str]:
    text = "\n".join([h["output"] + h["stderr"] for h in run["hooks"]] + run["results"] + run["assistant_text"])
    return [m for m in foreign if m in text]


def context_of(h: dict) -> str:
    try:
        return json.loads(h["output"]).get("hookSpecificOutput", {}).get("additionalContext", "")
    except ValueError:
        return ""


async def direct_forgery(world: World) -> dict:
    """Call A's own server with other projects' cwds: only A's checkout (and its worktree) may be served."""
    res = {}
    async with Client(world.params(world.repos["A"])) as client:
        async def recall(cwd, sid):
            r = await client.call_tool("nexus_recall", {"cwd": str(cwd), "session_id": sid, "prompt": "Explain quasarflux"})
            return r.structured_content or {}
        res["A"] = "memory_id=" in json.dumps(await recall(world.repos["A"], "fa"))
        res["A/sub"] = "memory_id=" in json.dumps(await recall(world.repos["A"] / "sub", "fb"))
        res["A-worktree"] = "memory_id=" in json.dumps(await recall(world.repos["A-worktree"], "fc"))
        res["B_cwd_on_A_server"] = await recall(world.repos["B"], "fd")
        res["C_cwd_on_A_server"] = await recall(world.repos["C"], "fe")
        res["root_cwd"] = await recall(Path("/"), "ff")
        res["home_cwd"] = await recall(world.home, "fg")
        bad = await client.call_tool("nexus_pre_tool", {"cwd": str(world.repos["B"]), "session_id": "fd",
                                                        "tool_name": "mcp__nexus-memory__get",
                                                        "tool_input": {"memory_id": str(uuid.uuid4())}})
        res["pre_tool_foreign_cwd_decision"] = (bad.structured_content or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    return res


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))] if values else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--nexus-memory-repo", type=Path, default=Path("/Users/soko/Cerebros/nexus-memory"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    a.workdir.mkdir(parents=True, exist_ok=True)
    w = World(a.workdir, a.nexus_memory_repo)
    for n in "ABDE":
        w.enroll(n)
        anyio.run(w.seed, n)
    results: dict = {}

    def want(name):
        return not only or name in only

    if want("forgery"):
        results["forgery"] = anyio.run(direct_forgery, w)
    if want("cold_A"):
        r = drive(w, w.repos["A"], ["Explain quasarflux"], tools=True)
        rec = ours(r, "UserPromptSubmit")
        results["cold_A"] = {"mcp": r["mcp"], "cue": bool(ours(r, "SessionStart")),
                             "recall_outcomes": [h["outcome"] for h in rec],
                             "recall_context": [context_of(h)[:700] for h in rec],
                             "recall_has_ids": any("memory_id=" in context_of(h) for h in rec),
                             "recall_has_stored_prose": any(MARK["A"] in h["output"] for h in rec),
                             "pre_tool_decisions": [json.loads(h["output"]).get("hookSpecificOutput", {}).get("permissionDecision", "allow")
                                                    for h in r["hooks"] if h["event"] == "PreToolUse" and h["output"].startswith("{") and is_pre(h)],
                             "leak_B": leaked(r, [MARK["B"]]), "replies": [x[:160] for x in r["results"]],
                             "raw_ours": [h for h in r["hooks"] if h["event"] in ("SessionStart", "UserPromptSubmit", "PreToolUse")]}
    if want("B"):
        r = drive(w, w.repos["B"], ["Explain quasarflux"], tools=True)
        results["B"] = {"mcp": r["mcp"], "cue": bool(ours(r, "SessionStart")), "leak_A": leaked(r, [MARK["A"]]),
                        "recall_has_ids": any("memory_id=" in context_of(h) for h in ours(r, "UserPromptSubmit")),
                        "recall_outcomes": [h["outcome"] for h in ours(r, "UserPromptSubmit")]}
    if want("worktree"):
        r = drive(w, w.repos["A-worktree"], ["Explain quasarflux"])
        results["worktree"] = {"mcp": r["mcp"], "cue": bool(ours(r, "SessionStart")),
                               "recall_has_ids": any("memory_id=" in h["output"] for h in ours(r, "UserPromptSubmit")),
                               "leak_B": leaked(r, [MARK["B"]])}
    if want("unenrolled_C"):
        r = drive(w, w.repos["C"], ["Explain quasarflux", "and again", "a third prompt"])
        lines = [h for h in r["hooks"] if "not enrolled" in h["output"]]
        results["unenrolled_C"] = {"mcp": r["mcp"], "cue": bool(ours(r, "SessionStart")),
                                   "visible_nexus_lines": len(lines), "prompts_completed": len(r["results"]),
                                   "hook_errors": [h["stderr"][:100] for h in r["hooks"] if h["outcome"] == "error" and h["event"] == "UserPromptSubmit" and is_ours(h)],
                                   "leak": leaked(r, list(MARK.values()))}
    if want("store_unavailable"):
        d_id = w.project_id("D")
        broken = list(w.store.glob(d_id + ".sqlite3*"))
        for p in broken:
            p.chmod(0)
        try:
            r = drive(w, w.repos["D"], ["Explain quasarflux", "once more"])
            results["store_unavailable"] = {
                "mcp": r["mcp"], "prompts_completed": len(r["results"]),
                "hook_errors": [h["stderr"][:100] for h in r["hooks"] if h["outcome"] == "error" and h["event"] == "UserPromptSubmit" and is_ours(h)],
                "cue": bool(ours(r, "SessionStart")), "leak": leaked(r, list(MARK.values())),
                "raw_user_prompt_hooks": [h for h in r["hooks"] if h["event"] == "UserPromptSubmit"],
                "store_files": [(p.name[:8], oct(p.stat().st_mode & 0o777)) for p in broken]}
        finally:
            for p in broken:
                p.chmod(0o600)
    if want("write_denied"):
        e_id = w.project_id("E")
        files = list(w.store.glob(e_id + ".sqlite3*"))
        for p in files:
            p.chmod(0o400)
        w.store.chmod(0o500)
        try:
            r = drive(w, w.repos["E"], ["Explain quasarflux"])
            results["write_denied"] = {"mcp": r["mcp"], "recall_has_ids": any("memory_id=" in context_of(h) for h in ours(r, "UserPromptSubmit")),
                                       "recall_outcomes": [h["outcome"] for h in ours(r, "UserPromptSubmit")],
                                       "recall_context": [context_of(h)[:300] for h in ours(r, "UserPromptSubmit")],
                                       "errors": [h["stderr"][:100] for h in r["hooks"] if h["outcome"] == "error" and is_ours(h)],
                                       "leak_AB": leaked(r, [MARK["A"], MARK["B"]])}
        finally:
            w.store.chmod(0o700)
            for p in files:
                p.chmod(0o600)
    if want("resume"):
        sid = str(uuid.uuid4())
        r1 = drive(w, w.repos["A"], ["Say ok."], persist=sid)
        r2 = drive(w, w.repos["A"], ["Say ok again."], resume=sid)
        results["resume"] = {"first_cue": bool(ours(r1, "SessionStart")), "resume_cue": bool(ours(r2, "SessionStart")),
                             "resume_mcp": r2["mcp"], "session_id": sid}
    if want("compact"):
        r = drive(w, w.repos["A"], ["Say ok.", "/compact"])
        results["compact"] = {"session_start_hooks": [h["name"] for h in r["hooks"] if h["event"] == "SessionStart"],
                              "cue_events": len(ours(r, "SessionStart")), "results": [x[:80] for x in r["results"]]}
    if want("latency"):
        prompts = [p for _ in range(5) for p in ("Explain quasarflux. Reply ok.", "What is 2+2? Reply ok.",
                                                 "Describe cedar pallets quasarflux. Reply ok.", "Say hello. Reply ok.",
                                                 "Which decision covers quasarflux deliveries? Reply ok.")]
        r = drive(w, w.repos["A"], prompts)
        mine = ours(r, "UserPromptSubmit")
        others = [h for h in r["hooks"] if h["event"] == "UserPromptSubmit" and not is_ours(h)]
        ms = [h["ms"] for h in mine]
        ctx = [len(context_of(h)) for h in mine]
        results["latency"] = {"prompts": len(prompts), "completed": len(r["results"]), "nexus_hook_calls": len(mine), "prompts_with_ids": sum(1 for c in ctx if c),
                              "nexus_ms_p50": pct(ms, .5), "nexus_ms_p95": pct(ms, .95), "nexus_ms_max": max(ms, default=None),
                              "other_hooks_calls": len(others), "other_ms_p50": pct([h["ms"] for h in others], .5),
                              "other_ms_p95": pct([h["ms"] for h in others], .95),
                              "injected_chars_per_prompt": ctx, "injected_chars_total": sum(ctx),
                              "session_start_ms": [h["ms"] for h in ours(r, "SessionStart")]}
    bad = [k for k, f in (("cold_A", "leak_B"), ("B", "leak_A"), ("unenrolled_C", "leak"),
                          ("store_unavailable", "leak"), ("write_denied", "leak_AB")) if results.get(k, {}).get(f)]
    fg = results.get("forgery", {})
    bad += [k for k in ("B_cwd_on_A_server", "C_cwd_on_A_server", "root_cwd", "home_cwd") if fg.get(k)]
    results["wrong_project_disclosures"] = bad if bad else 0
    a.out.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str)[:7000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
