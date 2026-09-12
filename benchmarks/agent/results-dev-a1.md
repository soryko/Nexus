# A1 development run — task `d1`, three arms, one attempt each

**Harness validation only. No benefit figure is computed from this run and none may be.**
The corpus was authored by someone who had read the fixes
([`capture-policy-a1.md`](capture-policy-a1.md) §5), and so was the task prompt. What a
development run can establish is whether the plumbing works and whether the arms differ in
the intended way. On the first count it mostly does. On the second it did not, and the reason
is the most useful thing this run produced.

Artifacts: [`run-dev-a1-attempt1/`](run-dev-a1-attempt1/) — per-arm patch, full stream trace, MCP config, the
harness itself, the outbound tool inventories, and `records.json`.

## 1. Run record (`protocol-a1` §14)

| Field | Value |
| --- | --- |
| Task | `d1` — pre-fix `ef11be6e49`, fix `8d7f03dac8`, checks `tests/test_options.py` |
| Arms | baseline, Nexus, plain notes — one attempt each |
| Model | `deepseek-flash` (alias; underlying revision **unknown**) via `https://api.deepseek.com/anthropic` |
| Run window | 2026-09-11, 15:28–15:31 UTC |
| Claude Code | 2.1.258, `--bare` |
| Nexus commit | `81bb4ac`, `src/` clean |
| Ceilings | `--max-turns 30`; 600 s wall clock |
| Seed | `20260911` — realised order **baseline → nexus → notes** |
| Corpus | `corpus-dev-a1.json`, 13 memories, seeded in corpus order |
| Controls | §11.1 no-model: **1 failed** (not vacuous). §11.4 oracle: `isolated=True`, `checks_hidden=True` |
| Web search | 0 in every arm, and absent from every arm's outbound tool inventory |
| Cost | **not reported** — see [`runner-a1.md`](runner-a1.md) §3 |

## 2. What happened

| | baseline | Nexus | plain notes |
| --- | --- | --- | --- |
| Hidden checks | **pass** | **pass** | **pass** |
| Terminal | completed | completed | completed |
| Turns | 25 | 26 | 27 |
| Tool calls | 24 | 25 | 26 |
| Wall clock | 49.6 s | 51.1 s | 51.4 s |
| Input tokens | 11,666 | 15,560 | 12,558 |
| Cache-read tokens | 175,872 | 269,952 | 232,960 |
| Patch | 1,243 B | 1,236 B | 1,701 B |
| Memory calls | — | **0** | file read ×2 |

## 3. The Nexus arm never retrieved anything

Its 25 tool calls were `Bash`, `Read` and `Edit`. **Not one `mcp__nexus__*` call was made.**

`protocol-a1` asks for available, retrieved and used to be kept apart, and this run is why:

| | Established? | Evidence |
| --- | --- | --- |
| **available** | **yes** | Server connected; 7 `mcp__nexus__*` tools present in the arm's captured outbound request; store held all 13 memories |
| **retrieved** | **no** | Zero read calls; zero memory bodies returned |
| **used** | **unanswerable** | Nothing was retrieved, so there is nothing for the patch to have used |

A connected server established the first and nothing more, exactly as the distinction
predicted. **Memory delivery is therefore not validated by this run.** The one behaviour arm 2
exists to exercise did not occur.

**The plain-notes arm found its notes; the Nexus arm did not look for its store.** Arm 3 read
`NOTES-FROM-EARLIER-WORK.md` twice — though only *after* it had already opened `core.py`
(`retrieval_before_first_src_edit: false`). Neither arm was told its memory existed: §3
requires a byte-identical prompt, so the tools announce themselves in an inventory and the
file does not announce itself at all. A file at the checkout root was found by `ls`; a tool in
the inventory was never reached for. That asymmetry is unregistered, it is not a property of
either mechanism's quality, and **any Q2 comparison run this way compares discovery
accidents, not retrieval.** It must be settled in §15 slot 2 before a held-out run.

**Arm 2's token counts are higher, and the cause is not isolated.** It spent 33% more input
tokens and 54% more cache-read tokens than baseline while calling no memory tool. A larger
tool inventory does add context — but the three arms also took different numbers of turns and
made different tool calls, and those contributions are mixed together in these totals. The
request accounting available here does not separate schema overhead from execution
divergence, so the figures are reported as **observed differences between arms**, not as a
measurement of the cost of offering the tools. `protocol-a1` §2 predicts such an overhead;
this run does not measure it.

## 4. `d1` does not discriminate, and it is demonstrably contaminated

All three arms passed **the hidden functional checks**. That is not the same as satisfying
every task requirement, and it is not evidence of anything about memory. **One baseline
success does not establish that `d1` has no headroom** —
it establishes success on this attempt, at `n = 1`. The reason `d1` cannot support a benefit
claim is not its difficulty: it is contaminated development material, authored corpus and
authored prompt both, and now demonstrably contaminated in a second way as well.

**A second contamination, and it is not the one first reported.** All three arms produced a
byte-identical source patch, identical to the upstream fix:

```
upstream 8d7f03dac8:src/click/core.py   blob 18431cd
baseline / nexus / notes                blob 18431cd
```

The plain-notes arm also wrote the upstream `CHANGES.rst` entry word for word, issue number
included. **The first version of this report attributed that to memorisation. That was
wrong, and the claim is withdrawn in full.** The exposure audit the protocol now requires
(§11.5) was run afterwards over the complete agent-visible input, and the trace answers it
directly:

| trace line | what the plain-notes arm did |
| --- | --- |
| 1084 | `python3 -m pip download click --no-deps -d /tmp/clickdl` |
| 1087 | unzipped the wheel and ran `grep -n "def resolve_envvar_value" -A 25 c/click/core.py` |
| **1399** | **first edit of `core.py`** |
| 2009 | `pip download click==8.1.0 --no-binary :all:` |
| 2012–2015 | read `CHANGES.rst` from the 8.1.0 sdist |
| 2060 | wrote the changelog entry |

It **read the fixed implementation out of an upstream package before it wrote its patch**,
and copied the changelog from a second download. The text was retrieved, not recalled. The
pristine fixture contains neither the issue number nor the string `autoenvvar_prefix`; the
audit's one hit in the Nexus trace was the substring `2146` inside a UUID, a false positive
of the audit's own search.

**This is an isolation failure, and it is the harness's.** `protocol-a1` §11.4 requires
external sources to be unreachable. Every clause of the control as drafted passed — no ref,
no remote, no dangling object carried the fix — because the control only ever examined the
git object database. **The harness enforced no network restriction at all.** The object
store was isolated; the host was not.

What follows, stated separately:

- **The plain-notes arm's result is void for this task.** Its pass and its patch are products
  of reading the answer. It is reported, not deleted, and it is not scored.
- **Baseline and Nexus show no such retrieval in their traces.** Neither ran a download;
  Nexus made no network-touching command at all. The strongest available claim is that no
  retrieval is evidenced in their traces — which is the instrument §11.3 names, and is not a
  clearance of anything.
- **Convergence on one blob is not evidence of memorisation.** For a four-line forced fix it
  is an unremarkable outcome, and §11.5 forbids reading a negative screen as proof either way.
- **The protocol gained two things from this**: §11.4 now asserts isolation over three
  surfaces — object database, network egress, host filesystem — each with its own control;
  and §11.5 registers the exposure audit as an audit, never a clearance.

The contaminating downloads (`click-8.5.0-py3-none-any.whl`,
sha256 `255bc959…`; `click-8.1.0.tar.gz`, sha256 `977c2134…`) were hashed and removed so they
could not reach a later run. The traces retain the record of what was read.

## 5. An instrument of mine was wrong and is replaced

The harness hashed the store's **file** before and after the arms, and reported the frozen
corpus as mutated — in a run where the agent made no memory call at all. The store is in WAL
mode; opening it rewrites file bytes and grew the file 143,360 → 151,552. **A file hash
cannot tell a checkpoint from a write**, so it could not answer the question it was added to
answer.

Replaced with a digest over the rows. **What the digest covers, named exactly:** for every
row of `memories` joined to its current revision — `memory_id`, `current_revision_id`,
`tombstoned`, `revisions.kind`, `revisions.tags_json`, and `blobs.body`. Re-derived over that
set: 13 memories, 13 revisions, 0 tombstoned, all 13 corpus bodies present, nothing extra —
digest `740b30b7db36f023`.

The supported claim is therefore **"the corpus bodies, kinds, tags and revision heads were
unchanged"**, which is narrower than "the database was unmutated". Receipts, the outbox, the
FTS index and the head tables are outside the digest and are not covered by it. The earlier
"unchanged=False" in the console log is an artifact of the file-hash instrument and is
superseded.

Worth keeping for a different reason: arm 2's inventory *does* carry `record`, `revise` and
`forget`. An allowlist withheld permission and none was attempted (0 permission denials), but
the tools are offered, so the check stays — with an instrument that measures content.

## 6. What this run validates, and what it does not

**Validated:** fixture construction and isolation; both model-free controls; three arms
executing to completion under the ceilings; patch extraction; independent scoring against
held-out checks; full trace capture; per-arm outbound tool-inventory capture; MCP server
launch and tool exposure; the store surviving a run unmutated.

**Not validated:** memory *delivery* — nothing was retrieved, so nothing downstream of
retrieval was exercised. Delivered-context bucketing has never run on a non-empty delivery.
Arm 3's rendered file was read late and its influence on the patch is not isolated from the
model's prior exposure.

**Not answerable from this run:** whether the task set is suitably challenging. One attempt
per arm says nothing about difficulty, and `d1`'s two contaminations — an authored corpus and
prompt, and an unisolated network — mean its outcomes carry no information about the model's
capability either. `d2` and `d3` are untried.

**Also not validated, and this is the run's own failure:** network isolation. It was neither
enforced nor checked, and the first report did not notice because no control looked.

## 7. What should happen next

1. **Enforce and verify network isolation** before any further arm runs. Done: a proxy
   allowlist with a negative control (a package download must fail) and a positive control
   (the model API must answer), both run per arm and recorded. Superseded by
   [`results-dev-a1-run2.md`](results-dev-a1-run2.md).
2. **Make memory consultation explicit** and rerun, so that delivery is exercised rather than
   left to discovery. Spontaneous adoption is a separate question, and *this* run is the one
   observation of it: tools were available and were never used.
3. **Settle the discovery asymmetry** (§15 slot 2) before any arm-2-versus-arm-3 comparison
   that is meant to answer Q2 by retrieval quality rather than by discovery behaviour.
4. **Do not read anything into the three equal passes.** One attempt per arm, on a task with
   an authored corpus, an authored prompt and an unisolated network, is uninformative.
