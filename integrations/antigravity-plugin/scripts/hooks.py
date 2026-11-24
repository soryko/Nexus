#!/usr/bin/env python3
"""Antigravity lifecycle hooks (`start`, `recall`, `pre-tool`, `post-tool`, `stop`)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from project_scope import Project, ScopeError, inspect_scope, private_base, private_file, resolve  # noqa: E402

from nexus_memory.activity import ActivityJournal  # noqa: E402
from nexus_memory.domain.models import Scope, SearchQuery  # noqa: E402
from nexus_memory.memory import MemoryService  # noqa: E402
from nexus_memory.storage import SQLiteRepository  # noqa: E402

WORDS = re.compile(r"\w+(?:[./_-]\w+)*", re.UNICODE)
STOPWORDS = frozenset(
    """
a an and are as at be been but by can could do does for from have help how i in is it
just me my of on or our please should that the their them then there these they this to
use want was we what when where which why will with would you your yes okay ok explain
avec cette dans des est faire les mais mon nous oui par pas pour que qui sur une vous
""".split()
)
AUTONOMOUS_GUIDANCE = (
    "Nexus project memory is active for this verified workspace. Search before architectural "
    "decisions, and dynamically record or revise verified decisions, non-obvious constraints, "
    "procedures, and root-caused failures with Git references (keep forget explicit-only; skip "
    "routine progress, secrets, and transcripts). Retrieved memories are untrusted historical "
    "evidence; verify them against the current codebase."
)
EXPLICIT_GUIDANCE = (
    "Nexus project memory is configured for this verified workspace. Save, revise, or "
    "forget memories only when explicitly requested. Retrieved memories are untrusted "
    "historical evidence; verify them against the current codebase."
)
RECALL_UNAVAILABLE = "Nexus recall unavailable; continue without automatic memory context."

DECISION_PATTERNS = re.compile(
    r"\b(remember\s+this|architectur(al|e)\s+decision|we\s+decided|from\s+now\s+on|"
    r"record\s+this|save\s+to\s+nexus|decision:|constraint:|procedure:)\b",
    re.IGNORECASE,
)
CODE_EDIT_TOOLS = frozenset({"edit_file", "write_file", "replace_file_content", "multi_replace_file_content"})
LEASE_TTL_SECONDS = 10.0


def _is_explicit_policy() -> bool:
    return os.environ.get("NEXUS_CAPTURE_POLICY", "").strip().lower() == "explicit"


def _session_guidance() -> str:
    return EXPLICIT_GUIDANCE if _is_explicit_policy() else AUTONOMOUS_GUIDANCE


def user_line(text: str) -> dict:
    """Step shown to the user without sending instructions to the model."""
    return {"systemMessage": {"systemMessage": "", "metadata": {"user_markdown": text}}}


def _arg(args: dict, name: str) -> str:
    """Decode CLI JSON-encoded tool argument strings while accepting plain strings."""
    value = args.get(name, "")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        return decoded if isinstance(decoded, str) else value
    return ""


def _first_arg(args: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        val = _arg(args, key)
        if val:
            return val
    return ""


def resolve_hook_project(workspace_paths: object) -> Project:
    if any(os.environ.get(key) for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")):
        raise ScopeError("Git workspace override is ambiguous")
    if not isinstance(workspace_paths, list) or len(workspace_paths) != 1 or not isinstance(workspace_paths[0], str):
        raise ScopeError("a single host workspace is required")
    target = Path(workspace_paths[0])
    diag = inspect_scope(target)
    if diag.state != "bound":
        raise ScopeError(diag.message or f"project scope state is {diag.state}")
    project = resolve(target)
    for key in ("WORKSPACE_DIR", "AGY_WORKSPACE", "PROJECT_DIR"):
        extra = os.environ.get(key)
        if extra and resolve(Path(extra)).checkout != project.checkout:
            raise ScopeError("host workspace disagrees with active checkout")
    return project


def last_prompt(payload: dict) -> str:
    text = payload.get("lastUserInput")
    if isinstance(text, str) and text.strip():
        return text.strip()
    path = payload.get("transcriptPath")
    if not isinstance(path, str):
        return ""
    transcript = Path(path)
    if transcript.is_symlink() or not transcript.is_file():
        return ""
    with transcript.open("rb") as source:
        size = source.seek(0, 2)
        source.seek(max(0, size - 65536))
        for line in reversed(source.read().splitlines()[-20:]):
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(item, dict) and item.get("type") == "USER_INPUT" and isinstance(item.get("content"), str):
                return item["content"].strip()
    return ""


def update_seen(project: Project, session: object, candidates: dict[str, str] | None = None) -> dict[str, str]:
    """Track bounded session-scoped IDs; session start clears prior entries."""
    if not isinstance(session, str) or not session or len(session) > 1024:
        return candidates or {}
    path = project.database.with_suffix(".seen.json")
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as file:
        private_file(path)
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            seen = json.load(file)
        except ValueError:
            seen = {}
        if not isinstance(seen, dict):
            seen = {}
        previous = seen.pop(session, [])
        fresh: dict[str, str] = {}
        if candidates:
            fresh = {k: v for k, v in candidates.items() if k not in previous}
            seen[session] = (previous + list(fresh))[-128:]
        while len(seen) > 100:
            del seen[next(iter(seen))]
        file.seek(0)
        json.dump(seen, file)
        file.truncate()
    return fresh


def _record_activity(
    project: Project,
    kind: str,
    outcome: str,
    *,
    session_id: str | None = None,
    detail: dict | None = None,
) -> None:
    try:
        journal = ActivityJournal(ActivityJournal.path_for_store(project.database))
        journal.record_event(kind, outcome, session_id=session_id, detail=detail)
    except Exception:
        pass


def _lease_file_path(*, create_allowed: bool) -> Path | None:
    override = os.environ.get("NEXUS_AGY_LEASE_FILE")
    if override:
        path = Path(override)
        if not path.is_absolute():
            return None
        if not create_allowed and not path.parent.is_dir():
            return None
        return path
    try:
        base = private_base(create=create_allowed)
    except (FileNotFoundError, ScopeError, OSError):
        return None
    if not base.is_dir():
        return None
    return base / ".agy_host_leases.json"


def _targets_other_mcp_server(payload: dict) -> bool:
    tc = payload.get("toolCall")
    if not isinstance(tc, dict):
        return False
    tname = str(tc.get("name") or "")
    if tname.startswith(("mcp_nexus-memory", "mcp_nexus_memory")):
        return False
    if tname.startswith("mcp_") and tname != "mcp_tool":
        return True
    args = tc.get("args")
    if isinstance(args, dict):
        server_name = _first_arg(args, ("ServerName", "serverName", "server_name"))
        if server_name:
            return "nexus-memory" not in server_name and "nexus_memory" not in server_name
    return False


def pre_tool(payload: dict) -> dict:
    """Record a short-lived host-attested workspace lease for Nexus MCP tool calls."""
    try:
        if _targets_other_mcp_server(payload):
            return {"decision": "allow"}
        if any(os.environ.get(key) for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")):
            return {"decision": "allow"}
        ws = payload.get("workspacePaths")
        if not isinstance(ws, list) or len(ws) != 1 or not isinstance(ws[0], str) or not ws[0].strip():
            return {"decision": "allow"}

        target = Path(ws[0])
        diag = inspect_scope(target)
        create_allowed = diag.state == "bound"
        lease_path = _lease_file_path(create_allowed=create_allowed)
        if lease_path is None:
            return {"decision": "allow"}
        if create_allowed:
            lease_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.path.islink(lease_path):
            return {"decision": "allow"}

        tc = payload.get("toolCall")
        raw_id = tc.get("id") if isinstance(tc, dict) else None
        tc_id = raw_id if isinstance(raw_id, str) and raw_id else ""
        now = time.time()

        with os.fdopen(os.open(lease_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as file:
            private_file(lease_path)
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                raw = json.load(file)
            except ValueError:
                raw = []
            entries = raw if isinstance(raw, list) else []
            active = [
                item
                for item in entries
                if isinstance(item, dict)
                and isinstance(item.get("ts"), (int, float))
                and isinstance(item.get("workspace"), str)
                and now - float(item["ts"]) <= LEASE_TTL_SECONDS
                and (not tc_id or item.get("id") != tc_id)
            ]
            active.append({"id": tc_id or uuid4().hex, "workspace": str(target), "ts": now})
            active = active[-32:]
            file.seek(0)
            json.dump(active, file)
            file.truncate()
    except Exception:
        pass
    return {"decision": "allow"}


def post_tool(payload: dict) -> dict:
    """Clear the host-attested workspace lease after the MCP tool call completes."""
    try:
        lease_path = _lease_file_path(create_allowed=False)
        if lease_path is None or not os.path.lexists(lease_path) or os.path.islink(lease_path):
            return {}
        tc = payload.get("toolCall")
        raw_id = tc.get("id") if isinstance(tc, dict) else None
        tc_id = raw_id if isinstance(raw_id, str) and raw_id else ""
        now = time.time()
        with os.fdopen(os.open(lease_path, os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as file:
            private_file(lease_path)
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                raw = json.load(file)
            except ValueError:
                raw = []
            entries = raw if isinstance(raw, list) else []
            remaining = [
                item
                for item in entries
                if isinstance(item, dict)
                and isinstance(item.get("ts"), (int, float))
                and isinstance(item.get("workspace"), str)
                and now - float(item["ts"]) <= LEASE_TTL_SECONDS
                and (not tc_id or item.get("id") != tc_id)
            ]
            file.seek(0)
            json.dump(remaining, file)
            file.truncate()
    except Exception:
        pass
    return {}


def recall(project: Project, payload: dict) -> list[dict]:
    prompt = last_prompt(payload)
    if len(prompt) < 8 or prompt.startswith(("/", "!", "#")) or not project.database.exists():
        return []
    terms = list(
        dict.fromkeys(
            word.lower()
            for word in WORDS.findall(prompt)
            if 3 <= len(word) <= 64 and word.lower() not in STOPWORDS
        )
    )[:8]
    if not terms:
        return []

    service = MemoryService(SQLiteRepository(project.database), Scope(project.id, "local"))
    hits = service.search(SearchQuery(query=" ".join(terms)[:512], limit=3)).hits
    session_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None

    if not hits:
        _record_activity(project, "recall", "empty", session_id=session_id, detail={"candidate_count": 0})
        return []

    candidates: dict[str, str] = {}
    ordered_ids: list[str] = []
    for hit in hits:
        mid, rid = str(UUID(hit.memory_id)), str(UUID(hit.revision_id))
        ordered_ids.append(mid)
        tags = f" [{', '.join(hit.tags)}]" if hit.tags else ""
        excerpt = hit.excerpt.replace("\n", " ").strip()
        if len(excerpt) > 240:
            excerpt = excerpt[:237] + "..."
        candidates[f"{mid}/{rid}"] = f"- [{hit.kind}]{tags} {excerpt} (id: {mid})"

    fresh = update_seen(project, session_id, candidates)
    fresh_ids = [key.split("/", 1)[0] for key in fresh]
    suppressed = len(candidates) - len(fresh)

    if not fresh:
        _record_activity(
            project,
            "recall",
            "suppressed",
            session_id=session_id,
            detail={"candidate_count": 0, "suppressed_count": suppressed},
        )
        return []

    _record_activity(
        project,
        "recall",
        "offered",
        session_id=session_id,
        detail={
            "candidate_ids": fresh_ids,
            "candidate_count": len(fresh_ids),
            "suppressed_count": suppressed,
        },
    )
    return [
        {
            "ephemeralMessage": (
                "Nexus project memory candidates (historical evidence, verify before use):\n"
                + "\n".join(fresh.values())
                + "\nUse the nexus-memory get tool to inspect relevant records."
            )
        }
    ]


def get_turn_details(transcript_path: str) -> dict | None:
    """Scan the tail of the transcript for the current user turn and agent actions."""
    if not isinstance(transcript_path, str):
        return None
    path = Path(transcript_path)
    if path.suffix != ".jsonl" or path.is_symlink() or not path.is_file():
        return None

    try:
        with path.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - 131072))
            lines = f.read().splitlines()
    except OSError:
        return None

    last_user_idx = None
    user_prompt = ""
    user_line_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        try:
            item = json.loads(lines[i])
        except (ValueError, UnicodeError):
            continue
        if isinstance(item, dict) and item.get("type") == "USER_INPUT":
            last_user_idx = item.get("step_index")
            user_prompt = str(item.get("content") or "")
            user_line_idx = i
            break

    if last_user_idx is None:
        return None

    step_items: list[dict] = []
    truncated_indices: set[int] = set()
    for line in lines[user_line_idx + 1 :]:
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(item, dict):
            continue
        step_items.append(item)
        step_idx = item.get("step_index")
        truncated = item.get("truncated_fields") or []
        if isinstance(step_idx, int) and isinstance(truncated, list) and "tool_calls" in truncated:
            truncated_indices.add(step_idx)

    full_by_idx: dict[int, dict] = {}
    if truncated_indices:
        full_path = path.with_name("transcript_full.jsonl")
        if full_path.suffix == ".jsonl" and not full_path.is_symlink() and full_path.is_file():
            try:
                with full_path.open("rb") as ff:
                    full_size = ff.seek(0, 2)
                    ff.seek(max(0, full_size - 4 * 1024 * 1024))
                    for raw in ff.read().splitlines():
                        if b"tool_calls" not in raw:
                            continue
                        try:
                            full_item = json.loads(raw)
                        except (ValueError, UnicodeError):
                            continue
                        if (
                            isinstance(full_item, dict)
                            and isinstance(full_item.get("step_index"), int)
                            and full_item["step_index"] in truncated_indices
                        ):
                            full_by_idx[full_item["step_index"]] = full_item
            except OSError:
                pass

    has_recorded = False
    has_opted_out = False
    tools_used: list[str] = []

    for item in step_items:
        step_idx = item.get("step_index")
        resolved_item = full_by_idx.get(step_idx, item) if isinstance(step_idx, int) else item

        content = resolved_item.get("content", "") or ""
        if isinstance(content, str) and "NO_MEMORY" in content:
            has_opted_out = True

        for tc in resolved_item.get("tool_calls", []) or []:
            if not isinstance(tc, dict):
                continue
            tc_name = str(tc.get("name") or "")
            args = tc.get("args") or {}
            tools_used.append(tc_name)
            if isinstance(args, dict) and tc_name in ("call_mcp_tool", "mcp_tool"):
                tname = _first_arg(args, ("ToolName", "toolName", "tool_name"))
                sname = _first_arg(args, ("ServerName", "serverName", "server_name"))
                if tname in ("record", "revise") and "nexus" in sname:
                    has_recorded = True
            elif tc_name.startswith(("mcp_nexus-memory", "mcp_nexus_memory")) and tc_name.endswith(("_record", "_revise")):
                has_recorded = True

    return {
        "turn_id": last_user_idx,
        "user_prompt": user_prompt,
        "has_recorded": has_recorded,
        "has_opted_out": has_opted_out,
        "tools_used": tools_used,
    }


def check_and_update_nudge_state(project: Project, session_id: str, turn_id: int) -> bool:
    """Return True if this turn is allowed to be nudged, and record the nudge."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 1024:
        return False
    path = project.database.with_suffix(".stop_nudge.json")
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as file:
        private_file(path)
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            state = json.load(file)
        except ValueError:
            state = {}
        if not isinstance(state, dict):
            state = {}

        sess = state.get(session_id, {"nudged_turns": [], "count": 0})
        if not isinstance(sess, dict):
            sess = {"nudged_turns": [], "count": 0}
        nudged_turns = sess.get("nudged_turns", [])
        if not isinstance(nudged_turns, list):
            nudged_turns = []
        count = sess.get("count", 0) if isinstance(sess.get("count"), int) else 0

        if turn_id in nudged_turns or count >= 3:
            return False

        nudged_turns.append(turn_id)
        sess["nudged_turns"] = nudged_turns[-32:]
        sess["count"] = count + 1
        state[session_id] = sess

        while len(state) > 100:
            del state[next(iter(state))]

        file.seek(0)
        json.dump(state, file)
        file.truncate()
        return True


def get_git_evidence(checkout: Path) -> tuple[str, list[str], bool]:
    """Get HEAD commit, touched files, and whether there are uncommitted or new changes."""
    sha = ""
    files: list[str] = []
    has_changes = False
    git_bin = "/usr/bin/git" if Path("/usr/bin/git").exists() else (shutil.which("git") or "git")
    git_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(Path.home()),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    try:
        res = subprocess.run(
            [git_bin, "-C", str(checkout), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            timeout=2,
            text=True,
            env=git_env,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    try:
        res = subprocess.run(
            [git_bin, "-C", str(checkout), "status", "--porcelain"],
            capture_output=True,
            timeout=2,
            text=True,
            env=git_env,
        )
        if res.returncode == 0 and res.stdout.strip():
            has_changes = True
            for line in res.stdout.splitlines()[:5]:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    files.append(parts[1])
    except (subprocess.SubprocessError, OSError):
        pass

    return sha, files, has_changes


def stop_capture(project: Project, payload: dict) -> dict:
    if _is_explicit_policy():
        return {}
    if payload.get("terminationReason") != "NO_TOOL_CALL":
        return {}
    if not payload.get("fullyIdle", False) or payload.get("continuationActive") or payload.get("error"):
        return {}

    session_id = payload.get("conversationId")
    transcript_path = payload.get("transcriptPath")
    if not isinstance(session_id, str) or not session_id or not isinstance(transcript_path, str) or not transcript_path:
        return {}

    turn = get_turn_details(transcript_path)
    if not turn or turn.get("turn_id") is None:
        return {}

    if turn["has_recorded"] or turn["has_opted_out"]:
        return {}

    tools_used = set(turn["tools_used"])
    has_code_edit = bool(tools_used & CODE_EDIT_TOOLS)
    if ("ask_question" in tools_used or "exit_plan_mode" in tools_used) and not has_code_edit:
        return {}

    has_run_cmd = "run_command" in tools_used
    has_decision_prompt = bool(DECISION_PATTERNS.search(turn["user_prompt"]))
    sha, touched_files, has_git_changes = get_git_evidence(project.checkout)

    if not (has_code_edit or has_decision_prompt or (has_run_cmd and has_git_changes)):
        return {}

    if not check_and_update_nudge_state(project, session_id, turn["turn_id"]):
        return {}

    evidence_parts = []
    if sha:
        evidence_parts.append(f"HEAD={sha}")
    if touched_files:
        evidence_parts.append(f"changed_files={len(touched_files)}{'+' if len(touched_files) >= 5 else ''}")

    evidence_str = f" ({', '.join(evidence_parts)})" if evidence_parts else ""

    reason = (
        f"Nexus Memory Check: Task turn completed{evidence_str}. "
        "If this turn established a reusable architectural decision, non-obvious constraint, "
        "working procedure, or root-caused failure, search first to avoid duplicates and call "
        "`record` or `revise` on `nexus-memory_nexus-memory` with `kind` in "
        "{decision, procedure, constraint, failure}, short `tags`, and verified Git `references` "
        '([{"path": "..."}]). If nothing qualifies as durable knowledge, reply with exactly: NO_MEMORY.'
    )

    _record_activity(project, "stop_capture", "nudged", session_id=session_id)
    return {
        "decision": "continue",
        "reason": reason,
    }


def main() -> int:
    os.umask(0o077)
    steps: list[dict] = []
    cmd = sys.argv[1:]
    try:
        payload = json.load(sys.stdin)
        if cmd == ["pre-tool"]:
            print(json.dumps(pre_tool(payload if isinstance(payload, dict) else {})))
            return 0
        if cmd == ["post-tool"]:
            print(json.dumps(post_tool(payload if isinstance(payload, dict) else {})))
            return 0
        if isinstance(payload, dict) and isinstance(payload.get("workspacePaths"), list):
            project = resolve_hook_project(payload["workspacePaths"])
            session_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None
            if cmd == ["start"]:
                update_seen(project, session_id)
                _record_activity(project, "session_start", "bound", session_id=session_id)
                steps = [{"systemMessage": {"systemMessage": _session_guidance()}}]
            elif cmd == ["recall"]:
                try:
                    steps = recall(project, payload)
                except Exception:
                    _record_activity(
                        project,
                        "runtime_error",
                        "recall_failed",
                        session_id=session_id,
                        detail={"error_code": "recall_failed"},
                    )
                    steps = [user_line(RECALL_UNAVAILABLE)]
            elif cmd == ["stop"]:
                print(json.dumps(stop_capture(project, payload)))
                return 0
    except Exception:
        pass
    if cmd == ["pre-tool"]:
        print(json.dumps({"decision": "allow"}))
    elif cmd in (["post-tool"], ["stop"]):
        print("{}")
    else:
        print(json.dumps({"injectSteps": steps}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
