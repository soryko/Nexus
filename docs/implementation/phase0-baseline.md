# Cross-host plan, Phase 0 baseline (26 November 2026)

This is the baseline for Phase 0 of [the cross-host plan](../plans/cross-host-project-memory.md), recorded before any Stage B code. It was taken on macOS 26.1 arm64 with git 2.54.0, from `feat/nexus-memory-v2` at `db203ea`. Every number below is **measured** unless it is labelled otherwise. No live store was opened: schema versions come from each file's SQLite header. No live profile, plugin or installed runtime was changed.

## 1. Gates

| Gate | State | Evidence |
|---|---|---|
| v2 Linux CI on PR #29 | **Passed** | All six jobs green on `db203ea`, 24 Nov: suite, stage-a, dsh-provider, atlas-ui, fresh install on ubuntu-24.04 and on macos-15 |
| Flaky Atlas UI test | **Root-caused and fixed in the test.** A different, rarer flake remains open | §4 |
| Baseline recall latency | **Measured.** The p95 budget is not yet set | §5 |
| Linux git requirement | **Documented** | README "Requirements" |
| First writable DSH save | Not part of Phase 0 | It is the Phase 2 entry gate and needs owner-approved spend |

## 2. Installed artifacts per host

| Host | What runs | Source | Store schema |
|---|---|---|---|
| Claude Code | Plugin `nexus-memory@nexus-local` 0.2.0, user scope, from `~/.claude/plugins/cache/nexus-local/nexus-memory/0.2.0` | Byte-identical to `integrations/claude-plugin` at main `8e57c33`, apart from `__pycache__` | Project stores under `project-scopes-v1/`: v4 |
| Codex (this repo) | MCP `nexus_memory` through `project-scopes-v1/bin/launcher.py codex`, launcher interpreter `venvs/0.1.0a2`, server `venvs/codex-recall-efd755fc5d61` (package 0.1.0a2) | Project config `.codex/config.toml` | v4 |
| Antigravity | Plugin `~/.gemini/config/plugins/nexus-memory` 0.2.0 (`launch.mjs mcp`) | Identical to `integrations/antigravity-plugin` at main `8e57c33` (`diff -rq`, ignoring `__pycache__`) | v4 |
| DSH | `venvs/dsh-0.1.0a3-main-8e57c33/bin/nexus-memory` (package 0.1.0a3, SQLite 3.53.1), named in `deepseek-harness/.agents/integrations/nexus-memory/cordis.patch.yml` | See §3 | `dsh/deepseek-harness.sqlite3`: v4 |

Other stores: three pilot stores (v4), one owner-recovery store (v4), and the legacy `memory.sqlite3`, which carries the retired marker (`user_version` 2147483647). **No store anywhere is at v5 or v6**, so the v2 migration has not run on real data. A version change still sitting in a WAL file would not show in the header. All stores were last written before this record.

Installed runtimes in `~/.local/share/nexus-memory/venvs/`: 14. One is mislabelled. The directory `0.2.0` contains package version **0.1.0a3**, so a directory name does not identify the build: record the package version and the source commit. Every runtime links SQLite 3.54.1, except the two DSH runtimes, which link 3.53.1.

## 3. DSH integration code is not under version control

The plan asks Phase 0 to record the revisions of DSH's external integration code. Neither location has one:

| Path | State | Content fingerprint* | Files | Newest change |
|---|---|---|---|---|
| `~/dsh-plugins/nexus-mnemon-integration` (`@local/dsh-nexus-atlas`) | Not a Git repository | `d67c27201025f0bc` | 32 | 2026-11-25 |
| `~/Repos/deepseek-harness/.agents/integrations/nexus-memory` (`@local/dsh-nexus-memory`) | **Untracked** inside deepseek-harness (branch `fix/auto-review-codex-temperature`, `f3803c9697`, 101 dirty paths) | `bc1b4dd1cb89b6be` | 131 | 2026-11-25 |
| `@local/dsh-nexus-inspector` | Tarball `dist/dsh-inspector/local-dsh-nexus-inspector-0.1.0.tgz` (19 Nov) from the salvage checkout | sha256 `ddfad65717fbe1d0…` | — | 2026-11-19 |

\*The first 16 hex digits of SHA-256 over the sorted `shasum -a 256` manifest of regular files, excluding `node_modules`, `.git` and `.DS_Store`.

Consequence for Phase 2: until this code is committed somewhere, a Phase 2 change to the DSH host writer cannot be diffed against this baseline, only fingerprinted. The salvage checkout also holds uncommitted edits to `integrations/dsh-inspector/client.js` and `icon.svg`, made after the tarball was built.

## 4. The flaky Atlas test: root cause

`test_oversize_revision_is_reported_not_compared` failed 1 of 3 runs on Linux (24 Nov), on main and on v2 alike. Locally it failed 1 of 31 isolated runs (the first, cold, run).

**Cause:** the test searched for `chain-token`, but `_chain(big=True)` makes the current revision the 40 KB `x…` text. Search covers current revisions only, so that search returns **no memories**. The test then waited for `#count` to contain "matching". That wait passed only because the unfiltered list rendered by `_open` already said "Showing 1–2 of 2 matching". When the empty search response landed before Playwright's first check, the count read "No memories match", and the wait could never succeed. A probe recorded exactly that sequence: "No memories match (of 2 in this project)" appears straight after the search.

A second test of the same kind failed in 2 of 5 full local runs: `test_filters_are_visible_and_one_reset_clears_them`. After Reset, it waited for "matching (48 in this project)", which the filtered text "of 23 matching (48 in this project)" also satisfies. The wait therefore passed on stale text, and the next, non-retrying assertion read the count before the reset search returned.

**Fix (test only, no product change):** the oversize test opens the oversize memory from the unfiltered list. The reset test waits for "of 48 matching". The dense-relation test, whose wait passed on stale text in the same way but had not yet failed, now waits for "of 1 matching". One stale-text wait is left: the last line of the citation-filter reset test waits for `"matching ("`. It is the test's final check, so it cannot cause a failure; it only fails to check. 

Before the fix, two tests failed: the reset test in 2 of 5 full runs, and the oversize test in 1 of 31 isolated runs. After the fix, across six full runs, neither of them failed. With a 400 ms delay on every search response, the old reset wait failed and the new one passed, one run each.

**A separate flake is still open.** `test_a_dense_relation_cannot_hide_another_in_the_connections_panel` failed once in those six full runs, and 0 times in 15 isolated runs. After a result was opened, the page showed "Not signed in or the project binding changed". The fix does not cause this: the changed wait comes before the click, and the failing step comes after it. The real error is hidden. In `static/app.js`, `sessionLost()` is the catch-all for every rejected promise, so a network error, a 503, a JSON parse error or a JavaScript exception all display as a lost session. Next step: record the browser console and the failing response in the failure evidence, then fix the actual error. Separately, give Atlas an error message that is not "Not signed in".

## 5. Recall latency baseline

Method: [`benchmarks/recall_latency/run.py`](../../benchmarks/recall_latency/run.py) drives `nexus_recall`, the tool the Claude Code `UserPromptSubmit` hook calls, over stdio MCP. It runs against a disposable store with synthetic memories, a fresh session per call, and 200 calls per row, on macOS 26.1 arm64. It measures the client-side round trip to a warm server. It does **not** include the host's own hook dispatch, which this record has not measured. Single run per row.

| Runtime | Memories | Launch → initialized | Recall p50 | Recall p95 | Max | Calls with an offer |
|---|---:|---:|---:|---:|---:|---:|
| main `8e57c33` (`dsh-0.1.0a3-main-8e57c33`) | 0 | 337 ms | 1.4 ms | 1.9 ms | 29.4 ms (first call) | 0 / 200 |
| main | 200 | 296 ms | 2.1 ms | 3.6 ms | 8.1 ms | 200 / 200 |
| main | 2000 | 326 ms | 3.2 ms | 4.9 ms | 7.3 ms | 200 / 200 |
| v2 `db203ea` (worktree venv) | 0 | 393 ms | 1.4 ms | 2.0 ms | 18.4 ms (first call) | 0 / 200 |
| v2 | 200 | 316 ms | 2.3 ms | 4.4 ms | 5.6 ms | 200 / 200 |
| v2 | 2000 | 314 ms | 3.2 ms | 3.5 ms | 3.9 ms | 200 / 200 |

The empty-store rows are the negative control: no offers. An earlier run used a corpus built from 22 shared words. At 2000 memories it produced **0 offers**, because no candidate cleared the lexical threshold, so it timed only the no-offer path. Those figures were discarded, and that corpus must not be reused.

Reading: on a warm server, server-side recall is about 5 ms at p95 for up to 2000 memories. That is roughly 800 times below the 4 s ceiling. v2 is not measurably slower than main here. The real stores are much smaller, at 150–230 KB each. The cost that could approach the ceiling is a cold start: a server launch takes about 0.3–0.4 s before the first call. That is where a budget matters. The p95 overhead budget itself is the owner's call (plan §3).

## 6. Gaps still open (from plan §4, not re-verified here)

- DSH: no profile, harness or plugin calls `forAuditedHost`, so a real writable DSH save is impossible until host wiring exists (Phase 2).
- Codex: it is unproven which completion event the supported build delivers (Phase 3).
- Antigravity: v2's launcher refuses an Antigravity launch that carries a scoped profile (catalog and grant file; `integrations/project_scope/launcher.py:85` at `db203ea`, added in `e2e157b`). The reason is not documented, and Phase 1 must establish it.
- Claude Code: the recall hook timeout is 2 s (`integrations/claude-plugin/hooks/hooks.json`); the plan raises it to the 4 s ceiling.
