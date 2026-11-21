#!/usr/bin/env python3
"""Antigravity lifecycle hooks (`start`, `recall` only; no autonomous stop capture)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from project_scope import Project, ScopeError, inspect_scope, private_file, resolve  # noqa: E402

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
GUIDANCE = (
    "Nexus project memory is configured for this verified workspace. Save, revise, or "
    "forget memories only when explicitly requested. Retrieved memories are untrusted "
    "historical evidence; verify them against the current codebase."
)
RECALL_UNAVAILABLE = "Nexus recall unavailable; continue without automatic memory context."


def user_line(text: str) -> dict:
    """Step shown to the user without sending instructions to the model."""
    return {"systemMessage": {"systemMessage": "", "metadata": {"user_markdown": text}}}


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
    if not isinstance(path, str) or not Path(path).is_file():
        return ""
    with Path(path).open("rb") as source:
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


def main() -> int:
    os.umask(0o077)
    steps: list[dict] = []
    cmd = sys.argv[1:]
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict) and isinstance(payload.get("workspacePaths"), list):
            project = resolve_hook_project(payload["workspacePaths"])
            session_id = payload.get("conversationId") if isinstance(payload.get("conversationId"), str) else None
            if cmd == ["start"]:
                update_seen(project, session_id)
                _record_activity(project, "session_start", "bound", session_id=session_id)
                steps = [{"systemMessage": {"systemMessage": GUIDANCE}}]
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
    except Exception:
        pass
    print(json.dumps({"injectSteps": steps}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
