# Automatic capture and a live activity view

Status: proposal, 16 November 2026. Nothing below is built. Supersedes the "explicit only" capture stance in
`docs/guarantees.md:63` and `docs/pilot-p1.md:278` **only if approved**.

## Why

The enrolled store for this checkout holds 0 memories and 0 revisions (`status`, 16 Nov: `latest_durable_seq 0`).
Every other Nexus store on the machine is an evaluation fixture or a backup with at most 24 revisions. The plugin is
installed and its hooks run, but nothing ever writes:

- Capture is instruction-only. `capture_policy` changes the text sent at startup; no code captures. A hooked launch is
  forced to `explicit` (`src/nexus_memory/transport/mcp_server.py:89-97`), so the Claude Code plugin cannot be
  autonomous even by flag.
- There is no Stop, PreCompact or SessionEnd handler and no transcript reader in `src/`.
- Discovery is blocked. `nexus_pre_tool` denies `search` unless the prompt begins `Nexus explore:`
  (`src/nexus_memory/transport/codex.py:236-237`), so an empty store also cannot be searched by habit.
- Nothing shows activity. Atlas reads memories only; the usage log is off for every enrolled host
  (`integrations/project_scope/launcher.py:84-93`) and does not know the hook tools (`transport/usage.py:55`).

The 72-hour `p1-claude-auto` pilot already measured the outcome: 150 sessions, 0 calls, 0 captures.

## What others do (sources pinned 16 Nov 2026)

| Project | Capture trigger | Who shapes | Updates | What the user sees |
|---|---|---|---|---|
| Supermemory Claude plugin 0.2.0 (installed, locally patched) | async `Stop`, `SessionEnd` flush; user + assistant text, tools excluded | closed server LLM (DeepSeek here) | server-side, not visible | "N memories loaded / recalled N"; status line counts *accepted uploads* |
| claude-mem 82d3602 | `PostToolUse` (every tool), `Stop` summary, `SessionEnd` | async Haiku "observer" worker; typed observations or `<skip_summary>` | exact hash dedup, append-only | local React viewer: live SSE feed, per-session timeline, delete, worker logs, queue depth, preview of what session start will inject |
| mem0 plugin 0.3.1 / mem0 f1791da | hooks spool; flush every 5 exchanges, `PreCompact`, `SessionEnd`, 5 min idle | single ADD-only LLM pass, linked to related memories | md5 + "recently extracted" in prompt; contradictions become new dated facts | cloud-only plugin; self-hosted dashboard lists memories and every API request |
| Graphiti 869c1b2 | explicit `add_memory` | LLM entity/edge extraction | bi-temporal edges; contradicted facts invalidated, not deleted | none of its own (Neo4j browser) |
| basic-memory 4519c23 | `PreCompact` writes a model-free checkpoint note | none at capture; agent skills for decisions | markdown + git | your editor |
| Codex memories | background pass after a chat goes idle; off by default | separate extract and consolidation models | consolidation | per-chat toggle, reset |
| Claude Code auto memory | the model writes mid-session | the model | rewrite of `MEMORY.md` index | "Saved/Recalled N memories" lines |

Failures worth designing against, all observed in the sources or on this machine:

1. **Green UI, silent loss.** On this machine Supermemory's server failed 840 of 993 recent extraction runs (804 were
   DeepSeek "Insufficient Balance") while the plugin reported captures, because it only checks that the upload got an
   `id` (`hooks/lib/api.js:201`). claude-mem #3663 and Graphiti #1707 are the same failure.
2. **Cost drain.** claude-mem runs an observer per tool call; #5023 drained a weekly quota.
3. **Junk accumulation.** mem0 #4573 audited 10,134 entries and found 97.8% junk, including a 668-copy feedback loop.
4. **Lost turns.** Supermemory's local gate drops held turns from a session's first send (`transcript.js:168-175`).

## Design

Five parts. Each keeps Nexus's existing guarantee that the store holds exact text with revision history; the model
only proposes what to write, and Nexus's own `record`/`revise` path writes it.

### 1. Activity journal (new, sidecar)

A per-scope SQLite file beside the store, `<store>.activity.sqlite3`, so the memory store's schema and guarantees are
untouched. Append-only `events(seq, ts, session_id, kind, outcome, detail_json)`. Kinds:

`session_start`, `recall` (candidates offered, IDs), `pre_tool` (tool, allow/deny, reason), `turn_spooled`
(chars, cursor), `extract_started`, `extract_finished` (proposed, written, skipped as duplicate, skipped as noise,
tokens, ms), `extract_failed` (error class and message), `record_written` (memory ID, revision ID, kind),
`readback_ok` / `readback_failed`.

No prompt or answer text is stored in the journal; the spool (below) holds text until extraction and is purged after.

### 2. Capture spool (hooks; no model call in a hook)

- Hooks: `Stop` (async), `PreCompact`, `SessionEnd`, all registered in `integrations/claude-plugin/hooks/hooks.json`.
- Reader for Claude Code JSONL transcripts with a per-session cursor (last entry UUID) stored in the journal. A
  session with no cursor reads from its start, not just the last turn (avoids Supermemory's lost-turn bug).
- Selection: user text, assistant text, and a compact list of tool calls (tool name, file paths touched, command
  exit status). Thinking blocks, `<system-reminder>` and Nexus/Supermemory injected context are dropped.
- Privacy at capture time: strip `<private>…</private>`; reuse a redaction pass for keys, tokens, PEM blocks, URL
  credentials; honour a project exclusion list. Spool rows are deleted after a successful extraction.
- Batching (mem0 pattern, not claude-mem's per-call observer): extraction is due when the spool for a session reaches
  ~6,000 characters, on `PreCompact`/`SessionEnd`, or after 10 minutes idle.

### 3. Extractor (background, off the hot path)

- Runs as a detached worker started by the hook when a batch is due; one at a time per scope (file lock).
- Model call: `claude -p` headless with a Haiku-class model, so it uses the user's existing Claude Code login and
  needs no new API key. Input capped (default 24,000 characters per batch). Daily budget in the config; when spent,
  the journal records `extract_skipped_budget` and Atlas shows it.
- Output is strict JSON: a list of `{kind, content, tags, paths, supersedes?}` or `{"none": reason}`. Kinds are
  Nexus's own five. The prompt carries the current project's top lexical matches for the batch so the model can
  return `none` for duplicates or name a memory it `supersedes`. It is told to skip anything derivable from the
  code or git log (Claude Code's rule) and to write a durable fact with its why, never a transcript.
- Writes: `record` with idempotency key `sha256(session, cursor_from, cursor_to, index)` so a rerun cannot double
  write; references use the files the batch touched at the session's HEAD. `supersedes` becomes `revise` with the
  latest revision as `expected_revision_id`. Exact-hash duplicates are skipped in code as well.
- **Truthful success**: an extraction counts as written only after `get` reads the new revision back. Anything else is
  a failure event with its message.

### 4. Recall changes

- Remove the `Nexus explore:` gate for `search` (keep the deny for lifecycle tools). Keep the admitted-candidate path.
- `SessionStart` injects a capped index (titles + IDs of the 20 most recent and most cited memories, ≤ 2 KB), like
  claude-mem and `MEMORY.md`, so the agent knows what exists without a search.

### 5. Atlas: Activity and Health

Atlas stays local, loopback-only and secret-gated as it is today.

- **Health strip** at the top of every page: last capture (time ago), memories written today / this week, extraction
  failures in the last 24 h with the latest error text, spool backlog, budget used. Red when the last extraction
  failed or when sessions ran but nothing was spooled for 24 h.
- **Activity feed**: the journal in reverse order, refreshed live (Server-Sent Events from a new `/api/events`
  endpoint, which fits the current self-only CSP). Each `record_written` row links to the memory inspector.
- **Sessions**: one row per Claude Code session: prompts, turns spooled, batches, memories written, recalls offered,
  hook denials.
- **What the agent will see**: preview of the `SessionStart` index exactly as it would be injected.
- **Undo** (phase 2, Atlas's first write): `forget` a memory from the inspector, behind the existing cookie and
  a per-action confirmation. Optional review mode: proposals wait in an approval list instead of writing directly.

Also: a Claude Code status line segment, `Nexus ✓ 3 saved · 12m ago` or `Nexus ✗ extract failed`, from the journal.

## Slices

| # | Delivers | You can verify by |
|---|---|---|
| S1 | journal + logging of the existing recall and pre-tool hooks; search gate removed; Atlas Health strip and Activity feed | open Atlas, send a prompt in Claude Code, watch the `recall` row appear |
| S2 | transcript reader, cursor, spool, privacy pass, `Stop`/`PreCompact`/`SessionEnd` hooks; Atlas Sessions view | finish a turn, see `turn_spooled` with a character count |
| S3 | extractor worker, budget, writes with read-back, status line | after a working session, memories appear in Atlas with links back to the session |
| S4 | session-start index + preview; forget/undo; review mode | inspect the preview; forget a memory and see it disappear from recall |
| Seed | one-time import of the ~60 notes in `~/.claude/projects/-Users-soko-Cerebros-nexus-memory/memory/` as memories with `source_uri` pointing at each file | Atlas count goes from 0 to ~60 |

Acceptance for S1–S3 together: over 5 real working sessions, Atlas shows every session, at least one memory written
per session that changed code, zero extraction failures that are not shown in red, and a negative control (a session
with capture disabled) that writes nothing.

## Decisions this needs from the owner

1. **Approve automatic capture.** It reverses the explicit-only rule in this repo's `AGENTS.md` and
   `docs/guarantees.md`; both would be updated in S3.
2. **Write directly, or queue for review in Atlas?** Recommended: write directly with undo, because review queues
   that nobody visits recreate today's empty store.
3. **Extractor model and budget.** Recommended: Haiku via `claude -p`, 24,000 characters per batch, 40 batches/day.
4. **Seed import** of the existing notes.
