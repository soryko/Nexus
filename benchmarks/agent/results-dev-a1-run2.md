# A1 development run 2 — `d1`, three arms, explicit consultation policy

> **Figures here are superseded where they concern delivered context, task-requirement compliance, or termination.** Six instrument defects were found in review of `cbdf1f3`; the repairs and the recomputed figures are in [`results-recomputed.md`](results-recomputed.md). This document is kept as the record of what was reported at the time.

**Harness validation only.** The corpus and the task prompt were both authored by someone who
had read the fix ([`capture-policy-a1.md`](capture-policy-a1.md) §5). No benefit figure is
computed here and none may be. What this run had to establish is that **memory delivery
works** — which [`results-dev-a1.md`](results-dev-a1.md) could not, and which took four
attempts because of three defects in the harness, all mine.

Artifacts: [`run-dev-a1-attempt4/`](run-dev-a1-attempt4/). The first run is preserved unchanged in
[`run-dev-a1-attempt1/`](run-dev-a1-attempt1/).

## 1. What changed from run 1

- **A consultation policy**, added verbatim and identically to all three arms, no
  answer-specific queries or memory IDs:
  > Before your first source edit, consult any available prior-work memory: Nexus memory
  > tools or `NOTES-FROM-EARLIER-WORK.md`. If neither is available, proceed using the
  > repository. Treat prior notes as potentially outdated and verify relevant claims against
  > current code.
- **Network isolation, enforced and controlled** — the thing run 1 lacked entirely.
- **A memory-visibility gate**, added after two runs failed silently.

Model, ceilings and seed unchanged: `deepseek-flash`, `--max-turns 30`, 600 s, seed
`20260911`, realised order baseline → nexus → notes.

## 2. Acceptance criteria

| Criterion | Result |
| --- | --- |
| Nexus returns memory content | **met** — `status` 13 active, 2 `search`, 6 `get`, full bodies returned |
| Notes are read | **met** — read twice, before the first source edit |
| Delivered-context classification runs | **met** — §4 |
| Traces preserved | **met** — full stream JSONL per arm |
| Independent scoring completes | **met** — all three scored against held-out checks |

## 3. What happened

| | baseline | Nexus | plain notes |
| --- | --- | --- | --- |
| Hidden checks | pass | pass | pass |
| Terminal | completed | completed | completed |
| Turns | 20 | 37 | 29 |
| Tool calls | 19 | 36 | 28 |
| Wall clock | 29.1 s | 57.2 s | **192.5 s** |
| Input tokens | 8,648 | 17,278 | 13,974 |
| Memory calls | — | 9 reads (1 status, 2 search, 6 get) | file read ×2 |
| Egress control | blocked | blocked | blocked |
| Store digest | unchanged across all three arms | | |

The Nexus arm's sequence: `status` → `search "auto_envvar_prefix envvar empty value"` →
`search "Click option environment variable resolution"` → six `get` calls by id, all before
its first source edit. Both facts the corpus marks **necessary** for `d1` came back in full:

- `c01` — *"Environment-variable resolution treats an empty string as absent… returns a value
  only when it is truthy"*
- `c03` — *"Option overrides resolve_envvar_value to add the automatic-envvar path…"*

## 4. Delivered context, three buckets

Per `protocol-a1` §8, never merged into two.

| | delivered | bytes | necessary | useful support | outdated | irrelevant |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 0/13 | 0 | 0/2 | 0 | 0 | 0 |
| **Nexus** | 8/13 | 1,828 | **2/2** | 3 | **0** | 3 |
| **plain notes** | 13/13 | 2,776 | **2/2** | 4 | **1** | 6 |

Both memory arms delivered both necessary facts. The arms differ in what else arrived: the
rendered file delivers the whole corpus the moment it is opened — including `c13`, the
memory the corpus declares **outdated** — while retrieval delivered eight of thirteen and did
not surface `c13` at all.

**This is not evidence that retrieval is better.** The corpus is contaminated development
material, this is one task and one attempt, and the comparison is structural: a file has no
selection step, so "delivers everything" is what a file *is*. What the figures establish is
that **the classification runs, discriminates, and has a non-zero denominator** — which is
the harness property this run existed to check.

## 5. Three defects, and why the first two controls did not catch them

Run 1 → run 4 was four attempts at one measurement. Each failure was the harness, not the
model, and each is recorded because the pattern matters more than the individual bugs.

| # | Defect | Why it went unnoticed |
| --- | --- | --- |
| 1 | **No network isolation at all.** The plain-notes arm ran `pip download click` and read the fixed `resolve_envvar_value` from an upstream wheel before patching. | §11.4's control only examined the git object database. Every clause passed. |
| 2 | **Scope mismatch.** Corpus seeded as actor `prior-session`; server launched as `task-session`. Nexus scope is `(namespace, actor)` and it isolates, so a 13-memory store reported `active_memories: 0`. | No control asserted the agent could see anything. Run 1's report claimed "available: yes" on the strength of rows existing in the file. |
| 3 | **Config overwrite.** `invoke()` rewrites `mcp.json` from the harness's own `ARMS` dict at launch, discarding the corrected actor. | The pre-run check passed — against the hand-edited file the run then replaced. **A control that validates an artifact the run does not use is worse than no control.** |

All three are now gated, and both gates run per arm, immediately before the arm, against the
bytes the run will actually use:

- **Egress control** — a package download from inside the arm's own environment must fail,
  and the run aborts if it succeeds. (Its first version tested for the probe directory's
  absence; `pip` creates that directory before failing, so a blocked network read as
  reachable. It tests for fetched files now.)
- **Memory-visibility control** — the harness writes `mcp.json`, reads that file back,
  extracts `--db`/`--namespace`/`--actor`, and aborts unless exactly 13 memories are visible
  in that scope. It printed `scope=(a1-dev,agent) visible=13` before the Nexus arm ran.

## 6. Corrections to run 1's report

- **"Memory was available but never retrieved" is withdrawn.** It was never available: defect
  2 made the store invisible to the server in run 1 as well. Run 1 therefore provides **no
  observation of spontaneous adoption**, contrary to what its report and its successor both
  claimed. That question is still untested.
- The memorisation attribution was already withdrawn in `results-dev-a1.md` §4 after the
  exposure audit found the download.

## 7. Still not established

- **Whether memory helps.** Nothing here measures that, and the contaminated corpus forbids
  the attempt.
- **Spontaneous adoption** — see §6. Testing it needs a run with memory genuinely visible and
  no consultation instruction.
- **`d1`'s difficulty.** Four attempts, twelve arm-runs, every one passing, tells us little:
  the prompt was authored from the fix.
- **The discovery asymmetry** (§15 slot 2) is unaddressed; the consultation policy sidesteps
  it rather than settling it, which was its purpose.
- **Two accounting fields remain unusable.** `num_turns` is not comparable to `--max-turns`
  and its divergence is not constant: in run 2 baseline hit the cap at `num_turns: 31` while
  the other arms *completed* at 33 and 34 under the same ceiling. And the `max_turns` verdict
  row registered in §10 says **fail, leaves a partial patch** — but run 2's capped baseline
  produced a **complete, passing** patch. Truncation and patch completeness are separate
  facts and §10 conflates them. Both need amending before a scored run.
- The plain-notes arm took **192.5 s** against baseline's 29.1 s. Unexplained, single
  observation, recorded rather than interpreted.
