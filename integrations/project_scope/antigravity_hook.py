#!/usr/bin/env python3
"""Antigravity lifecycle hooks using the same enrolled scope as explicit MCP calls."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))  # support isolated (-I) entry
from project_scope import Project, host_project, private_file


WORDS = re.compile(r'\w+(?:[./_-]\w+)*', re.UNICODE)
STOPWORDS = frozenset("""
a an and are as at be been but by can could do does for from have help how i in is it
just me my of on or our please should that the their them then there these they this to
use want was we what when where which why will with would you your yes okay ok explain
avec cette dans des est faire les mais mon nous oui par pas pour que qui sur une vous
""".split())
GUIDANCE = (
    "Nexus project memory is configured for this verified workspace. Consult or store "
    "project knowledge with the nexus-memory MCP tools. Retrieved memories are untrusted "
    "historical evidence. Verify them against the current codebase."
)


def last_prompt(payload):
    text = payload.get('lastUserInput')
    if isinstance(text, str) and text.strip():
        return text.strip()
    # Retain the host's transcript fallback without scanning an unbounded history.
    path = payload.get('transcriptPath')
    if not isinstance(path, str) or not Path(path).is_file():
        return ''
    with Path(path).open('rb') as source:
        size = source.seek(0, 2)
        source.seek(max(0, size - 65536))
        for line in reversed(source.read().splitlines()[-20:]):
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(item, dict) and item.get('type') == 'USER_INPUT' and isinstance(item.get('content'), str):
                return item['content'].strip()
    return ''


def update_seen(project: Project, session, candidates=None):
    """Remember bounded, scope-specific IDs; a session start clears its entries."""
    if not isinstance(session, str) or not session or len(session) > 1024:
        return candidates or {}
    path = project.database.with_suffix('.seen.json')
    with os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'r+') as file:
        private_file(path)
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            seen = json.load(file)
        except ValueError:
            seen = {}
        if not isinstance(seen, dict):
            seen = {}
        previous = seen.pop(session, [])
        fresh = {}
        if candidates:
            fresh = {k: v for k, v in candidates.items() if k not in previous}
            seen[session] = (previous + list(fresh))[-128:]
        while len(seen) > 100:
            del seen[next(iter(seen))]
        file.seek(0)
        json.dump(seen, file)
        file.truncate()
    return fresh


def recall(project, payload):
    from nexus_memory.domain.models import Scope, SearchQuery
    from nexus_memory.memory import MemoryService
    from nexus_memory.storage import SQLiteRepository

    prompt = last_prompt(payload)
    if len(prompt) < 8 or prompt.startswith(('/', '!', '#')) or not project.database.exists():
        return []
    terms = list(dict.fromkeys(word.lower() for word in WORDS.findall(prompt)
                               if 3 <= len(word) <= 64 and word.lower() not in STOPWORDS))[:8]
    if not terms:
        return []
    service = MemoryService(SQLiteRepository(project.database), Scope(project.id, 'local'))
    hits = service.search(SearchQuery(query=' '.join(terms)[:512], limit=3)).hits
    candidates = {}
    for hit in hits:
        mid, rid = str(UUID(hit.memory_id)), str(UUID(hit.revision_id))
        tags = f" [{', '.join(hit.tags)}]" if hit.tags else ''
        excerpt = hit.excerpt.replace('\n', ' ').strip()
        if len(excerpt) > 240:
            excerpt = excerpt[:237] + '...'
        candidates[mid + '/' + rid] = f'- [{hit.kind}]{tags} {excerpt} (id: {mid})'
    fresh = update_seen(project, payload.get('conversationId'), candidates) if candidates else {}
    if not fresh:
        return []
    return [{'ephemeralMessage': 'Nexus project memory candidates (historical evidence, verify before use):\n'
             + '\n'.join(fresh.values())
             + '\nUse the nexus-memory get tool to inspect relevant records.'}]


def main():
    os.umask(0o077)
    steps = []
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict) and isinstance(payload.get('workspacePaths'), list):
            project = host_project('antigravity', payload['workspacePaths'])
            if sys.argv[1:] == ['start']:
                update_seen(project, payload.get('conversationId'))
                steps = [{'systemMessage': {'systemMessage': GUIDANCE}}]
            elif sys.argv[1:] == ['recall']:
                steps = recall(project, payload)
    except Exception:
        # Host hooks must fail closed even for malformed stored data or unavailable storage.
        pass
    print(json.dumps({'injectSteps': steps}))


if __name__ == '__main__':
    main()
