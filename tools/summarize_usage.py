#!/usr/bin/env python3
"""Summarize a Nexus usage log (``--usage-log``) for an automatic pilot. Offline, stdlib only.

It reads one JSON line per event, keeps the server instances that started inside the window, and
prints one JSON object: how many instances (one per Claude Code session, approximately) consulted
or captured, and every *reuse candidate* — a memory recorded by one instance and later fetched
or returned by search in a different instance. A candidate is evidence of retrieval, not of
usefulness; reading the session that reused it is what settles that.

Instrument health is reported beside the counts, never folded into them: malformed lines, gaps
in ``seq`` (lost lines), instances with no ``stop`` event (killed, or still running), failed
writes, reopened logs. A zero means something only when the health section is clean.

    python3 tools/summarize_usage.py --log usage.jsonl --since 2026-10-28T12:00:00+00:00
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CONSULT_TOOLS = frozenset({"search", "get", "history"})


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass
class Instance:
    key: str
    started: datetime | None = None
    first_seen: datetime | None = None
    stopped: bool = False
    seqs: list[int] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    clients: Counter = field(default_factory=Counter)
    treatments: set = field(default_factory=set)
    stop_failures: int = 0
    reopens: int = 0

    @property
    def opened(self) -> datetime | None:
        return self.started or self.first_seen


def read(log: Path) -> tuple[dict[str, Instance], list[int]]:
    instances: dict[str, Instance] = {}
    malformed: list[int] = []
    with log.open("r", encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue  # the logger may end a torn fragment with a newline of its own
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed.append(number)
                continue
            when = _time(event.get("ts")) if isinstance(event, dict) else None
            key = event.get("instance") if isinstance(event, dict) else None
            if when is None or not isinstance(key, str) or not isinstance(event.get("seq"), int):
                malformed.append(number)
                continue
            instance = instances.setdefault(key, Instance(key))
            instance.seqs.append(event["seq"])
            if instance.first_seen is None or when < instance.first_seen:
                instance.first_seen = when
            instance.treatments.add((event.get("instructions"), event.get("instructions_digest")))
            kind = event.get("event")
            if kind == "start":
                instance.started = when
            elif kind == "stop":
                instance.stopped = True
                instance.stop_failures = int(event.get("write_failures") or 0)
            elif kind == "reopened":
                instance.reopens += 1
            elif kind == "call":
                event["_when"] = when
                instance.calls.append(event)
                client = event.get("client")
                if isinstance(client, dict):
                    instance.clients[(client.get("name"), client.get("version"))] += 1
    return instances, malformed


def _committed(call: dict[str, Any]) -> bool:
    """The write happened, whether or not the client stayed to receive the result."""
    outcome = call.get("outcome")
    return outcome == "ok" or (outcome == "cancelled" and call.get("completed") == "ok")


def _gaps(seqs: list[int]) -> int:
    if not seqs:
        return 0
    present = set(seqs)
    return sum(1 for number in range(1, max(present) + 1) if number not in present)


def summarize(log: Path, since: datetime, until: datetime | None = None) -> dict[str, Any]:
    instances, malformed = read(log)
    window = [instance for instance in instances.values()
              if instance.opened is not None and instance.opened >= since and (until is None or instance.opened < until)]
    window.sort(key=lambda instance: instance.opened)

    def calls(instance: Instance) -> list[dict[str, Any]]:
        # A session opened inside the window can run past its end; its later calls are outside it.
        return [call for call in instance.calls if until is None or call["_when"] < until]

    outcomes: Counter = Counter()
    consulted = 0
    for instance in window:
        did_consult = False
        for call in calls(instance):
            tool, outcome = call.get("tool"), call.get("outcome")
            outcomes[f"{tool}:{outcome}"] += 1
            if outcome == "ok" and tool in CONSULT_TOOLS:
                did_consult = True
        consulted += did_consult

    in_window = {instance.key for instance in window}

    def first_in_window(tool: str) -> list[tuple[str, datetime, str, dict[str, Any]]]:
        # An operation's first appearance is the write; every later appearance is a replay. That is
        # decided over the whole log, in call order, before the window is applied: a replay inside the
        # window of a write made before it is not a pilot write, and a replaying instance may have
        # started earlier than the one that wrote. Only first appearances inside the window count.
        first: dict[str, tuple[datetime, str, dict[str, Any]]] = {}
        for when, key, call in sorted(
                ((call["_when"], instance.key, call) for instance in instances.values() for call in instance.calls
                 if call.get("tool") == tool and _committed(call) and isinstance(call.get("operation_id"), str)),
                key=lambda item: item[0]):
            first.setdefault(call["operation_id"], (when, key, call))
        return [(operation, when, key, call) for operation, (when, key, call) in sorted(first.items(), key=lambda item: item[1][0])
                if key in in_window and (until is None or when < until)]

    captures: dict[str, tuple[str, datetime]] = {}   # memory_id -> (instance, time) of its capture
    record_operations: set[str] = set()
    capturing: set[str] = set()
    for operation, when, key, call in first_in_window("record"):
        record_operations.add(operation)
        capturing.add(key)
        memory_id = call.get("memory_id")
        if isinstance(memory_id, str) and memory_id not in captures:
            captures[memory_id] = (key, when)
    captured = len(capturing)
    revise_operations = {operation for operation, *_ in first_in_window("revise")}

    reuse: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    retrieved_before_capture = 0
    for instance in window:
        for call in calls(instance):
            if call.get("outcome") != "ok":
                continue
            found = []
            if call.get("tool") in {"get", "history"} and isinstance(call.get("memory_id"), str):
                found = [(call["memory_id"], call["tool"])]
            elif call.get("tool") == "search" and isinstance(call.get("hit_ids"), list):
                found = [(memory_id, "search_hit") for memory_id in call["hit_ids"] if isinstance(memory_id, str)]
            for memory_id, via in found:
                origin = captures.get(memory_id)
                if origin is None or origin[0] == instance.key:
                    continue  # not captured in the window, or the same session: not reuse
                if call["_when"] < origin[1]:
                    # A memory can only be fetched once it exists, so this is a clock or logging fault.
                    # An equal timestamp is not: two servers can log in the same instant.
                    retrieved_before_capture += 1
                    continue
                if (memory_id, instance.key) in seen:
                    continue
                seen.add((memory_id, instance.key))
                reuse.append({"memory_id": memory_id, "captured_by": origin[0], "reused_by": instance.key,
                              "via": via, "at": call["_when"].isoformat()})

    clients: Counter = Counter()
    for instance in window:
        if instance.clients:
            clients[instance.clients.most_common(1)[0][0]] += 1
        else:
            clients[(None, None)] += 1
    treatments = sorted({treatment for instance in window for treatment in instance.treatments}, key=str)

    return {
        "window": {"since": since.isoformat(), "until": until.isoformat() if until else None},
        "instances": len(window),
        "instances_with_any_call": sum(1 for instance in window if calls(instance)),
        "instances_consulted": consulted,
        "instances_captured": captured,
        "records": len(record_operations),
        "revisions": len(revise_operations),
        "reuse_candidates": reuse,
        "calls_by_tool_outcome": dict(sorted(outcomes.items())),
        "clients": {f"{name}/{version}": count for (name, version), count in sorted(clients.items(), key=str)},
        "treatments": [{"instructions": on, "instructions_digest": digest} for on, digest in treatments],
        "health": {
            "malformed_lines": len(malformed),
            "malformed_line_numbers": malformed[:20],
            "instances_without_start": sum(1 for instance in window if instance.started is None),
            "instances_without_stop": sum(1 for instance in window if not instance.stopped),
            "seq_gaps": sum(_gaps(instance.seqs) for instance in window),
            "write_failures_reported_at_stop": sum(instance.stop_failures for instance in window),
            "reopens": sum(instance.reopens for instance in window),
            "retrievals_before_capture": retrieved_before_capture,
            "mixed_treatments": len(treatments) > 1,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--since", required=True, help="Aware ISO 8601 time; instances opened earlier are excluded")
    parser.add_argument("--until", default=None, help="Aware ISO 8601 time; instances opened at or after it are excluded")
    args = parser.parse_args(argv)
    since, until = _time(args.since), _time(args.until) if args.until else None
    if since is None or (args.until and until is None):
        print("summarize_usage: --since and --until must be timezone-aware ISO 8601 times", file=sys.stderr)
        return 2
    if not args.log.is_file():
        print("summarize_usage: the log file does not exist", file=sys.stderr)
        return 2
    print(json.dumps(summarize(args.log, since, until), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
