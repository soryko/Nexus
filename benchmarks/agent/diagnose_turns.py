"""What does `--max-turns` bound, and what does the envelope's `num_turns` count?

Model-free: the real `claude` binary, the real forwarder, and `stub_turns.py` standing in for
the provider. Nothing leaves the host and nothing is charged. Each case runs the CLI with the
SAME arguments `run_arms_isolated.invoke` builds, minus the sandbox and the MCP config, and
reports four quantities separately:

  model requests     counted by the STUB, from requests it actually received
  assistant messages distinct `message.id` in the trace -- one per model response
  tool_use blocks    across all assistant messages; an assistant message may carry several
  tool_result blocks returned to the model

...beside the envelope's own `num_turns`, which is the quantity under measurement and is
never used to derive any of the four.

    python3 diagnose_turns.py [--out <dir>] [--max-turns N] [--cases a,b,c]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import stub_turns                                                       # noqa: E402

CASES = ["final_only", "sequential", "parallel", "text_with_tool", "error_recovery",
         "runaway"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def tally(trace: str) -> dict:
    ids, tool_use, tool_result, text, thinking = [], 0, 0, 0, 0
    per_message_tools: list[int] = []
    envelope = None
    user_entries = 0
    for line in trace.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        t = o.get("type")
        if t == "assistant":
            m = o.get("message", {})
            ids.append(m.get("id"))
            n = 0
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    tool_use += 1
                    n += 1
                elif b.get("type") == "text":
                    text += 1
                elif b.get("type") == "thinking":
                    thinking += 1
            per_message_tools.append(n)
        elif t == "user":
            user_entries += 1
            cont = (o.get("message") or {}).get("content")
            if isinstance(cont, list):
                tool_result += sum(1 for b in cont
                                   if isinstance(b, dict) and b.get("type") == "tool_result")
        elif t == "result":
            envelope = o
    per_id: collections.Counter = collections.Counter()
    for mid, n in zip(ids, per_message_tools):
        per_id[mid] += n
    return {
        "assistant_messages": len(per_id),
        "assistant_entries": len(ids),
        "tool_use_blocks": tool_use,
        "tool_result_blocks": tool_result,
        "user_entries": user_entries,
        "text_blocks": text,
        "thinking_blocks": thinking,
        "max_tool_use_in_one_message": max(per_id.values(), default=0),
        "num_turns": (envelope or {}).get("num_turns"),
        "subtype": (envelope or {}).get("subtype"),
        "terminal_reason": (envelope or {}).get("terminal_reason"),
        "is_error": (envelope or {}).get("is_error"),
        "envelope_present": envelope is not None,
    }


def run_case(case: str, max_turns: int, out: Path, claude: str) -> dict:
    stub_port, fwd_port = free_port(), free_port()
    log = out / f"{case}-stub.json"
    stub = subprocess.Popen(
        [sys.executable, str(BENCH / "stub_turns.py"), "--port", str(stub_port),
         "--script", case, "--log", str(log)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    fwd = subprocess.Popen(
        [sys.executable, str(BENCH / "model_forwarder.py"), str(fwd_port)],
        env={**os.environ, "A1_FORWARDER_STUB_UPSTREAM": f"127.0.0.1:{stub_port}"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not (wait_for(stub_port) and wait_for(fwd_port)):
            raise SystemExit(f"[{case}] stub or forwarder did not come up")
        work = out / f"{case}-cwd"
        work.mkdir(parents=True, exist_ok=True)
        env = a1_config.child_env(f"http://127.0.0.1:{fwd_port}/anthropic",
                                  "stub-not-a-key", {"TMPDIR": str(work)})
        cmd = [claude, "--bare", "-p", "diagnostic", "--model", "deepseek-flash",
               "--allowedTools", "Bash",
               "--disallowedTools", "WebSearch,WebFetch",
               "--permission-mode", "acceptEdits",
               "--disable-slash-commands",
               "--max-turns", str(max_turns),
               "--output-format", "stream-json", "--verbose"]
        t0 = time.monotonic()
        done = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                              timeout=180)
        wall = round(time.monotonic() - t0, 1)
        (out / f"{case}-trace.jsonl").write_text(done.stdout)
        (out / f"{case}-stderr.txt").write_text(done.stderr)
        row = {"case": case, "max_turns": max_turns, "cli_exit": done.returncode,
               "wall_s": wall, **tally(done.stdout)}
    finally:
        for p in (fwd, stub):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    if log.exists():
        lg = json.loads(log.read_text())
        row["model_requests"] = lg["requests_received"]
        row["scripted_requests"] = lg["scripted_turns_served"]
        row["unscripted_requests"] = lg["requests_received"] - lg["scripted_turns_served"]
    else:
        row["model_requests"] = row["scripted_requests"] = row["unscripted_requests"] = None
    row["stub_log"] = str(log)
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--max-turns", type=int, default=45)
    ap.add_argument("--cases", default=",".join(CASES))
    args = ap.parse_args(argv[1:])
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("no `claude` on PATH")
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="turns-"))
    out.mkdir(parents=True, exist_ok=True)

    ident = {
        "claude_which": claude,
        "claude_realpath": str(Path(claude).resolve()),
        "claude_version": subprocess.run([claude, "--version"], capture_output=True,
                                         text=True).stdout.strip(),
        "python": sys.executable,
        "max_turns_flag": args.max_turns,
    }
    print(json.dumps(ident, indent=1))

    rows = []
    for case in args.cases.split(","):
        case = case.strip()
        if not case:
            continue
        print(f"\n--- {case} (--max-turns {args.max_turns}) ---", flush=True)
        row = run_case(case, args.max_turns, out, claude)
        rows.append(row)
        print(json.dumps({k: row[k] for k in
                          ("model_requests", "assistant_messages", "tool_use_blocks",
                           "tool_result_blocks", "num_turns", "subtype", "cli_exit")}))
    (out / "turns.json").write_text(json.dumps({"identity": ident, "rows": rows}, indent=1)
                                    + "\n")
    hdr = (f"{'case':<16}{'reqs':>6}{'asstMsg':>9}{'toolUse':>9}{'toolRes':>9}"
           f"{'turns':>7}   subtype")
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['case']:<16}{str(r['model_requests']):>6}{r['assistant_messages']:>9}"
              f"{r['tool_use_blocks']:>9}{r['tool_result_blocks']:>9}"
              f"{str(r['num_turns']):>7}   {r['subtype']}")
    print(f"\nartifacts -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
