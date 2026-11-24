"""Read a correlation-probe log and say whether a hook can be tied to its MCP request.

Usage: python3 -I analyze.py <correlation-probe.jsonl> [--json]

The gateway needs one of two things to stop a request from consuming another call's lease:

* a per-call key: a value present in the PreToolUse hook payload for a call and in the MCP
  tools/call request for that same call, and in no other call's request; or
* a per-session key: a value present in every hook of one conversation and in the MCP server
  process (environment, initialize or request metadata) serving that conversation, and
  different between conversations.

This script only reports what the log shows. A key found once, in one session, is a lead, not an
established host contract: confirm it across sessions, concurrent conversations and a restart.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROBE_TOOL = "probe_echo"
PROBE_SERVER = "correlation-probe"
TRIVIAL = {"true", "false", "null", "none", "0", "1", "yes", "no", "on", "off"}


def distinctive(value: str) -> bool:
    """A value that could identify something: not a flag and not a short counter."""
    return value.lower() not in TRIVIAL and len(value) >= 6


def load(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def leaves(value: Any, prefix: str = "") -> dict[str, str]:
    """Scalar leaves of a JSON value, keyed by dotted path, as strings."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(leaves(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(leaves(item, f"{prefix}.{index}" if prefix else str(index)))
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value)
        if text:
            out[prefix] = text
    return out


def _is_probe_hook(record: dict[str, Any]) -> bool:
    tool = record.get("tool") or {}
    names = " ".join(str(tool.get(k) or "") for k in ("name", "server", "target"))
    return PROBE_TOOL in names or PROBE_SERVER in names


def hook_ids(record: dict[str, Any]) -> dict[str, str]:
    ids = {f"hook.{k}": v for k, v in (record.get("ids") or {}).items() if isinstance(v, str) and v}
    tool_id = (record.get("tool") or {}).get("id")
    if isinstance(tool_id, str) and tool_id:
        ids["hook.toolCall.id"] = tool_id
    return ids


def request_ids(record: dict[str, Any]) -> dict[str, str]:
    ids = {f"request._meta.{k}": v for k, v in leaves(record.get("meta")).items()}
    ids.update({f"request.params.{k}": v for k, v in (record.get("ids") or {}).items() if isinstance(v, str) and v})
    if record.get("rpc_id") is not None:
        ids["request.rpc_id"] = str(record["rpc_id"])
    return ids


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    pre_hooks = [r for r in records if r.get("source") == "hook" and r.get("event") == "PreToolUse" and _is_probe_hook(r)]
    all_hooks = [r for r in records if r.get("source") == "hook"]
    calls = [
        r for r in records
        if r.get("source") == "server" and r.get("event") == "request"
        and r.get("method") == "tools/call" and (r.get("tool") or {}).get("name") == PROBE_TOOL
    ]
    starts = [r for r in records if r.get("source") == "server" and r.get("event") == "start"]
    inits = [r for r in records if r.get("source") == "server" and r.get("event") == "request" and r.get("method") == "initialize"]

    # Per-call: a (hook path, request path) pair whose values tie each call to exactly one pre-hook.
    pair_hits: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for ci, call in enumerate(calls):
        req = request_ids(call)
        for hi, hook in enumerate(pre_hooks):
            for hpath, hval in hook_ids(hook).items():
                for rpath, rval in req.items():
                    if hval == rval:
                        pair_hits[(hpath, rpath)].append((ci, hi))
    per_call_keys = []
    for (hpath, rpath), hits in sorted(pair_hits.items()):
        calls_hit = [c for c, _ in hits]
        hooks_hit = [h for _, h in hits]
        one_to_one = len(set(calls_hit)) == len(hits) == len(set(hooks_hit))
        per_call_keys.append({
            "hook_path": hpath,
            "request_path": rpath,
            "calls_matched": len(set(calls_hit)),
            "calls_total": len(calls),
            "one_to_one": one_to_one,
            "covers_every_call": one_to_one and len(set(calls_hit)) == len(calls) > 0,
        })

    # Per-session: values the server process carries (env at start, initialize ids/meta) that also
    # appear in hook ids or hook environment.
    server_values: dict[str, set[str]] = defaultdict(set)
    for start in starts:
        for name, value in (start.get("env") or {}).items():
            if value != "<withheld>":
                server_values[value].add(f"server.env.{name}")
    for init in inits:
        for path, value in request_ids(init).items():
            if path != "request.rpc_id":
                server_values[value].add(f"initialize.{path}")
    # Only ids from the hook *payload* count: the host chose to tell the hook that value, and the
    # server can see it too. Environment both processes inherit from a common parent is the same
    # for every conversation of that host, so it is reported separately as an unconfirmed lead.
    unique_session: set[tuple[str, str]] = set()
    shared_env: set[str] = set()
    for hook in all_hooks:
        for hpath, value in hook_ids(hook).items():
            if distinctive(value):
                for spath in server_values.get(value, ()):
                    unique_session.add((hpath, spath))
        for name, value in (hook.get("env") or {}).items():
            if value != "<withheld>" and distinctive(value) and f"server.env.{name}" in server_values.get(value, ()):
                shared_env.add(name)

    # Process shape: how many server processes, and do hooks share the server's parent?
    server_pids = sorted({r.get("pid") for r in starts})
    server_ppids = sorted({r.get("ppid") for r in starts})
    hook_ppids = sorted({r.get("ppid") for r in all_hooks})

    # Ordering: does each probe call fall between some pre-hook and the next post-hook?
    def _t(r: dict[str, Any]) -> int:
        try:
            return int(r.get("hr"))
        except (TypeError, ValueError):
            return int(r.get("t") or 0) * 1_000_000

    ordering = []
    post_hooks = [r for r in records if r.get("source") == "hook" and r.get("event") == "PostToolUse" and _is_probe_hook(r)]
    for call in calls:
        tc = _t(call)
        before = [h for h in pre_hooks if _t(h) <= tc]
        after = [h for h in post_hooks if _t(h) >= tc]
        ordering.append({
            "label": (call.get("tool") or {}).get("label"),
            "pre_hooks_before": len(before),
            "post_hooks_after": len(after),
            "pre_hook_ms_before": round((tc - _t(before[-1])) / 1e6, 1) if before else None,
        })

    usable_per_call = [k for k in per_call_keys if k["covers_every_call"] and k["request_path"] != "request.rpc_id"]
    if not calls:
        verdict = "no_probe_calls"
    elif not pre_hooks:
        verdict = "no_pre_tool_hooks"
    elif usable_per_call:
        verdict = "per_call_key_found"
    elif unique_session:
        verdict = "session_key_found"
    else:
        verdict = "no_shared_identifier"

    return {
        "verdict": verdict,
        "counts": {"probe_calls": len(calls), "probe_pre_hooks": len(pre_hooks), "hooks": len(all_hooks), "server_starts": len(starts)},
        "per_call_keys": per_call_keys,
        "session_keys": [{"hook_path": h, "server_path": s} for h, s in sorted(unique_session)],
        # Same variable, same value in hooks and server. Usable only if a second concurrent
        # conversation shows it differs per conversation; one session cannot show that.
        "shared_env_leads": sorted(shared_env),
        "processes": {"server_pids": server_pids, "server_ppids": server_ppids, "hook_ppids": hook_ppids},
        "ordering": ordering,
        "request_meta_keys": sorted({k for c in calls for k in leaves(c.get("meta"))}),
        "pre_hook_keys": sorted({k for h in pre_hooks for k in (h.get("keys") or [])}),
        "caveat": "One session is a lead, not a host contract: confirm across sessions, concurrent conversations and a restart.",
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    report = analyze(load(Path(argv[0])))
    if "--json" in argv:
        print(json.dumps(report, indent=2))
        return 0
    print(f"verdict: {report['verdict']}")
    print(f"counts: {report['counts']}")
    for key in report["per_call_keys"]:
        print(f"per-call candidate: {key['hook_path']} == {key['request_path']} "
              f"({key['calls_matched']}/{key['calls_total']} calls, one_to_one={key['one_to_one']})")
    for key in report["session_keys"]:
        print(f"session candidate: {key['hook_path']} == {key['server_path']}")
    if report["shared_env_leads"]:
        print(f"inherited env shared by hooks and server (lead only, needs a second conversation): {', '.join(report['shared_env_leads'])}")
    print(f"processes: {report['processes']}")
    for row in report["ordering"]:
        print(f"call {row['label']}: pre-hooks before={row['pre_hooks_before']} post-hooks after={row['post_hooks_after']} "
              f"gap_ms={row['pre_hook_ms_before']}")
    print(f"request _meta keys: {report['request_meta_keys'] or 'none'}")
    print(f"pre-hook payload keys: {report['pre_hook_keys'] or 'none'}")
    print(report["caveat"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
