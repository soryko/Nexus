# Repairs after the second review of `6fe65c3`

The second review found four defects and confirmed two repairs. This document records what
changed, what was executed, and what the saved records say once recomputed. Where it differs
from [`results-recomputed.md`](results-recomputed.md), this one supersedes it — specifically
§2 (isolation) and §6 (compliance) there.

**Execution spent: none on models.** One fresh execution was needed and taken — the sandbox
boundary, which cannot be recomputed from a trace — and it costs nothing:
[`validate_boundary.py`](validate_boundary.py) runs the profile itself, `claude --version`,
and forwarder requests that are refused before any socket to the upstream opens. The twelve
development arms were **not** re-run; every figure below is recomputed from their saved
traces and patches.

| # | Finding | Repair | Section |
| --- | --- | --- | --- |
| P1 | The filesystem boundary exposed evaluation answers | reads denied by default; denies no longer existence-filtered | §1 |
| P2 | The forwarder permitted provider-side search | route, method and tool-definition allowlist; loopback narrowed to one port | §2 |
| P3 | Consultation scoring measured issue order, and missed `d4` and Bash edits | delivery clock; per-task edit surface; Bash write detection | §3 |
| P4 | A nonzero pytest exit did not establish regression coverage | three trees, and a per-task expected pre-fix signature | §4 |

The review also asked that the easy-case limitation on stale memory be preserved and that no
broader robustness claim be made. Nothing here touches the stale-memory variant, and no such
claim is added.

## 1. The filesystem boundary denies reads by default

The profile at `6fe65c3` was `(allow default)` plus a short deny list. Two consequences, both
reproduced before the repair and recorded in
[`boundary-validation.json`](boundary-validation.json) under `prior_profile`:

| Under the profile as it stood at `6fe65c3` | |
| --- | --- |
| the benchmark directory — saved patches, reports, corpus, task sheet | **readable** |
| a denied path that did not exist when the profile was written (`scoring/`) | **readable** |

The second is the `if p.exists()` filter in `write_profile`: a deny whose path was absent was
dropped from the profile entirely, and the scoring directory is created part-way through a
run.

The repaired profile denies `file-read*` outright and re-admits only the runtime and the
arm's own checkout — system roots, the interpreter, Claude Code's install and configuration,
the arm's `mcp.json`, and (for the `nexus` arm) the editable install the memory server
imports from. Denies are emitted **after** the allows, so they win inside an otherwise
allowed subtree: `src/` is readable and `benchmarks/agent/` inside the same repository is
not. They are no longer existence-filtered. `~/.claude/projects` and the other transcript
stores under `~/.claude` are denied too — they hold sessions that worked on this repository.

Measured on all three arms, freshly, with every negative control paired against its
unsandboxed twin:

| Negative control | baseline | nexus | notes |
| --- | --- | --- | --- |
| `pip download click` (index **enabled**) | blocked | blocked | blocked |
| `curl https://pypi.org/simple/click/` | blocked (DNS) | blocked | blocked |
| read a held-out check file | blocked in / works out | " | " |
| read a file in the benchmark directory | blocked in / works out | " | " |
| read a denied path created *after* the profile was written | blocked in / works out | " | " |
| reach a second loopback listener on another port | blocked in / works out | " | " |

A deny-by-default profile fails in a direction the old one could not: it can block the
runtime and still read as a perfect boundary, because the negative controls are blocked too.
So `all_hold` now also requires positive controls, and they are recorded with the run:

| Positive control | baseline | nexus | notes |
| --- | --- | --- | --- |
| the arm's own checkout is readable | yes | yes | yes |
| the interpreter runs | yes | yes | yes |
| `git` runs | yes | yes | yes |
| Claude Code starts (`--version`) | 2.1.258 | 2.1.258 | 2.1.258 |
| the Nexus memory server starts | yes | yes | yes |

Both of those controls earned their place during this work. The first profile aborted
`/bin/echo` with `SIGABRT` and no message — `dyld` needs the root directory entry, which a
blanket read-deny removes — and the second failed the interpreter because a venv's
`bin/python` is a symlink, so resolving it allowed the base interpreter and left
`pyvenv.cfg` unreadable. Under the old `all_hold` rule, each of those would have been
recorded as a boundary that held.

## 2. The forwarder accepts three things and refuses the rest

The previous forwarder was a boundary on the host axis only: one hardcoded upstream, no
`CONNECT`. It still forwarded any body to that upstream, and an arm holding `Bash` can
compose its own request — the API key is in its environment — asking the provider for a
server-side search tool. `--disallowedTools` governs what Claude Code offers the model; it
does not inspect a request the arm writes itself.

Three layers now, the first kernel-enforced:

- **port** — the sandbox admits `localhost:8899` and nothing else. The decoy-listener control
  above is what demonstrates this.
- **route and method** — `POST /v1/messages` and `POST /v1/messages/count_tokens` only.
- **tool definitions** — every `tools[]` entry must be a client-side definition. A
  provider-executed tool is selected by its `type`, so the type is what is gated; `mcp_servers`
  is refused outright.

Measured, freshly:

| Request | Result |
| --- | --- |
| `tools: [{type: web_search_20250305}]` | **403** — provider-executed tools are not forwarded |
| `tools: [{type: code_execution_20250522}]` | **403** |
| `mcp_servers: [...]` | **403** |
| `POST /anthropic/v1/models` | **403** — route not on the allowlist |
| `GET /anthropic/v1/messages` | **405** — method not forwarded |
| an ordinary request with client-side tool definitions | **accepted** |

The last row is the point of the exercise: an allowlist that refuses everything is not an
allowlist. Refusals are counted and written to stderr, so a run carries evidence either way.

As the review said, this is a reachable path and not a finding about the runs already taken.
Nothing in the saved traces shows an arm composing its own request.

## 3. Consultation is scored on delivery, not on issue order

Two defects, one check.

**A call issued before an edit has not necessarily delivered before it.** With parallel tool
use the result can arrive afterwards. `trace_parse` now gives every call two positions on one
monotonic event counter — `issued_at` and `resolved_at` — and the check compares the
*arrival* of prior-work content against the *issue* of the first edit. This is not
hypothetical: **eight results across the saved traces arrived out of issue order**, in seven of
the fifteen arm-runs.

**The old check could not see two kinds of edit.** It looked for `Edit`/`Write` against
`src/click/`. `d4`'s deliverable is `pyproject.toml` and it touches no source at all, so the
check was vacuous there; and on any task a `Bash` heredoc, `sed -i`, `cp` or `git apply`
sidesteps it. The edit surface is now per-task, and `Bash` commands that both write and name
that surface count as edits.

Four new counterexamples, all rejected, in
[`test_compliance_counterexamples.py`](test_compliance_counterexamples.py): hits that arrive
after the edit; an edit made by heredoc; a `d4` `pyproject.toml` edit preceding consultation;
and the compliant shape, which is still accepted. The `Bash` write matcher is tested directly
against ten commands, five that write and five that do not.

## 4. The regression probe runs three trees

A nonzero pytest exit is not regression coverage. A syntax error in the added test, a
collection failure with an unrelated cause, and a test that was already failing on the
fixture all exit nonzero. The probe now requires a transition, and reads pytest's own JUnit
report rather than inferring failure from an exit code:

| tree | requirement |
| --- | --- |
| pristine, untouched | the added test is **absent** — or, if the arm extended an existing test, **passing** |
| pristine + the arm's test hunks | fails **in the way this task predicts** |
| pristine + the arm's full patch | **passes** |

`d4` is the one task where a collection error is the legitimate pre-fix signature — an
unregistered marker errors at collection — so for `d4` the cause is matched against the
report text, not just the exit code. Everywhere else a collection error is rejected.

Two things this surfaced. Node ids had to cover tests an arm **extends** as well as tests it
adds: every `d2` arm appended assertions to the existing `test_missing_envvar` without
defining anything, and a probe keyed only on added `def test_*` would have marked all three
inapplicable. And a missing test id makes pytest exit 4 with `ERROR: not found:`, which is
the *expected* baseline for a newly added test and must not read as a failure.

## 5. Recomputed, from the saved traces

All fifteen arm-runs, including `run-dev-d4-isolated`, whose scores had been produced by hand
and were still on the old scorer. `base`/`pre-fix`/`cand` are the three trees of §4.

| run | arm | terminal | functional | compliance | base | pre-fix | candidate | discriminates | failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| d1 | baseline | completed | pass | 4/5 | absent | failure | passed | yes | P1 |
| d1 | nexus | completed | pass | 5/6 | absent | failure | passed | yes | P1 |
| d1 | notes | completed | pass | **6/6** | passed | failure | passed | yes | — |
| d2 | baseline | completed | pass | 4/5 | passed | failure | passed | yes | P1 |
| d2 | nexus | completed | pass | **6/6** | passed | failure | passed | yes | — |
| d2 | notes | completed | pass | **6/6** | passed | failure | passed | yes | — |
| d3 | baseline | max_turns | pass | 4/5 | absent | failure | passed | yes | P1 |
| d3 | nexus | max_turns | pass | **3/6** | — | — | — | n/a | S3, E1, P1 |
| d3 | notes | max_turns | pass | 5/6 | absent | failure | passed | yes | P1 |
| d4 | baseline | completed | pass | 3/4 | absent | error | passed | yes | P1 |
| d4 | nexus | completed | pass | 4/5 | absent | error | passed | yes | P1 |
| d4 | notes | completed | pass | 4/5 | absent | error | passed | yes | P1 |
| d4-isolated | baseline | completed | pass | **4/4** | absent | error | passed | yes | — |
| d4-isolated | nexus | completed | pass | 4/5 | absent | error | passed | yes | P1 |
| d4-isolated | notes | completed | pass | 4/5 | absent | error | passed | yes | P1 |

`d3/nexus` wrote no test at all, so the probe is inapplicable there and `S3` fails for the
same reason — as it did before.

**No compliance figure changed.** Every arm that the old probe passed on a single pre-fix run
also clears the three-tree requirement, and every `P2` verdict survives the move from issue
order to delivery order. `E1_regression_fails_prefix` is renamed
`E1_regression_discriminates` and `P2_consulted_content_before_edit` is renamed
`P2_content_delivered_before_edit`, so the names say what is now checked; those renames are
the only difference in the `failed` lists.

That is the honest result and not a disappointing one: the repairs close paths by which a
non-compliant arm *could* have scored, and this corpus contains no arm that took them. It is
evidence about the instruments, not about the arms — and the counterexample suite, not this
table, is what shows the new checks bite.

## 6. What is still not established

- **The stale-memory variant remains the easy case.** Unchanged by this work, and no broader
  robustness claim is made.
- **No benefit figure comes from this corpus.** Development runs only, per
  `capture-policy-a1` §5.
- **`bash_mutates` matches on the path.** A write performed after `cd` into the deliverable
  directory would be missed. Recorded in every record's `unsettled_by_machine`.
- **Relevance is still unsettled by machine** — whether a source change addresses the
  reported defect, and whether an added test covers the defect rather than merely failing in
  the predicted way.
- **The boundary was validated on this host.** `sandbox-exec` profiles and the paths the
  runtime needs are macOS- and installation-specific; the positive controls are what would
  catch a different machine, and they run per arm before the arm does.
