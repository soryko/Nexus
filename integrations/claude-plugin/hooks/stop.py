#!/usr/bin/env python3
"""Stop command hook: ask the model once whether a finished turn produced durable knowledge.

Ported from the Antigravity plugin's stop check. It runs only under the autonomous policy and
only in an enrolled project. It blocks the stop once (Claude Code's `decision: block`) with a
reason asking the model to `record`/`revise` a durable fact, or to reply NO_MEMORY, when the
turn edited files, ran a command while the checkout has changes, or the prompt stated a decision.

It stays quiet when the turn already wrote to Nexus or said NO_MEMORY, when Claude Code reports
the stop is already a hook-driven continuation (`stop_hook_active`), and beyond one nudge per
prompt and three per session. It reads the transcript tail and `git status`; it never reads or
writes memory itself, and never enrols anything.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scope import capture_policy, project  # noqa: E402

DECISION_PATTERNS = re.compile(
    r"\b(remember\s+this|architectur(al|e)\s+decision|we\s+decided|from\s+now\s+on|"
    r"record\s+this|save\s+to\s+nexus|decision:|constraint:|procedure:)",
    re.IGNORECASE,
)
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
NEXUS_WRITE = re.compile(r"^mcp__[A-Za-z0-9_-]*nexus[A-Za-z0-9_-]*__(record|revise)$")
QUESTION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
TAIL_BYTES = 4 * 1024 * 1024
MAX_PER_SESSION = 3


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_prompt(entry):
    """A user-typed prompt, not a tool result or harness-injected message."""
    if (entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain")
            or entry.get("isCompactSummary")):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


def last_turn(transcript_path):
    """The latest prompt's id and text, the tools its turn used, and its assistant text."""
    try:
        with open(transcript_path, "rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - TAIL_BYTES))
            lines = file.read().splitlines()
    except OSError:
        return None
    entries = []
    for raw in lines:
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    start = next((i for i in range(len(entries) - 1, -1, -1) if _is_prompt(entries[i])), None)
    if start is None:
        return None
    prompt = entries[start]
    tools, said = [], []
    for entry in entries[start + 1:]:
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        said.append(_text(content))
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                tools.append(block["name"])
    turn_id = prompt.get("uuid") or prompt.get("timestamp") or str(start)
    return {"turn_id": str(turn_id), "prompt": _text((prompt.get("message") or {}).get("content")),
            "tools": tools, "said": "\n".join(said)}


def git_evidence(checkout):
    """Short HEAD and the number of changed paths (capped at 5), or ("", 0) if git cannot answer."""
    git = "/usr/bin/git" if Path("/usr/bin/git").exists() else (shutil.which("git") or "git")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"}
    sha, changed = "", 0
    try:
        result = subprocess.run([git, "-C", str(checkout), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=1.5, env=env)
        if result.returncode == 0:
            sha = result.stdout.strip()
        result = subprocess.run([git, "-C", str(checkout), "status", "--porcelain", "--untracked-files=normal"],
                                capture_output=True, text=True, timeout=1.5, env=env)
        if result.returncode == 0:
            changed = min(5, len([line for line in result.stdout.splitlines() if line.strip()]))
    except (subprocess.SubprocessError, OSError):
        pass
    return sha, changed


def claim_nudge(state_path, session_id, turn_id):
    """True if this prompt may be nudged, recording the nudge; at most once per prompt, 3 per session."""
    try:
        fd = os.open(state_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return False
    with os.fdopen(fd, "r+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            state = json.load(file)
        except ValueError:
            state = {}
        if not isinstance(state, dict):
            state = {}
        session = state.get(session_id)
        if not isinstance(session, dict):
            session = {}
        turns = session.get("turns") if isinstance(session.get("turns"), list) else []
        if turn_id in turns or len(turns) >= MAX_PER_SESSION:
            return False
        state.pop(session_id, None)
        state[session_id] = {"turns": [*turns, turn_id]}
        while len(state) > 100:
            del state[next(iter(state))]
        file.seek(0)
        json.dump(state, file)
        file.truncate()
    return True


def decide(event):
    if capture_policy() != "autonomous" or not isinstance(event, dict) or event.get("stop_hook_active"):
        return None
    session_id, transcript = event.get("session_id"), event.get("transcript_path")
    if not isinstance(session_id, str) or not 0 < len(session_id) <= 256 or not isinstance(transcript, str):
        return None
    turn = last_turn(transcript)
    if turn is None or "NO_MEMORY" in turn["said"] or any(NEXUS_WRITE.match(name) for name in turn["tools"]):
        return None
    tools = set(turn["tools"])
    edited = bool(tools & EDIT_TOOLS)
    if tools & QUESTION_TOOLS and not edited:
        return None  # The turn ended on a question to the user; nothing is settled yet.
    stated = bool(DECISION_PATTERNS.search(turn["prompt"]))
    if not (edited or stated or "Bash" in tools):
        return None
    scoped = project(event.get("cwd"))
    if scoped is None:
        return None
    sha, changed = git_evidence(scoped.checkout)
    if not (edited or stated or changed):
        return None
    if not claim_nudge(Path(scoped.database).with_suffix(".claude_stop_nudge.json"), session_id, turn["turn_id"]):
        return None
    evidence = ", ".join(part for part in (f"HEAD={sha}" if sha else "",
                                           f"changed_files={changed}{'+' if changed >= 5 else ''}" if changed else "") if part)
    return (
        f"Nexus memory check{f' ({evidence})' if evidence else ''}: if this turn established a reusable "
        "decision, non-obvious constraint, working procedure, or root-caused failure, search Nexus first to "
        "avoid duplicates, then record or revise it with kind set, a space (call spaces to reuse one), short "
        "tags, and verified Git references. Skip routine progress, speculation, and anything easily recovered "
        "from the code. If nothing qualifies, reply with exactly: NO_MEMORY."
    )


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    try:
        reason = decide(event)
    except Exception:
        return  # A memory nudge must never break a stop.
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
