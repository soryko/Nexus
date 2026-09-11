# A1 — agent task baseline (protocol **draft**, not yet registered)

**Status: draft. No scored run may be executed against this document as it stands.**

This registers the *rules* of the measurement — arms, provenance, scoring, controls, and
what each figure may be read to mean. Source A is now `pallets/click`, and its candidate
tasks are surveyed in [`tasks-click-a1.md`](tasks-click-a1.md), itself a draft. The capture
policy, the memory corpus and the analysis are still unfilled: they are the slots in §15, and
this document becomes *registered* only when all of them are filled and frozen. Deviating after seeing results invalidates the run; the
correct response to a surprising number is a new run under an amended protocol, recorded as
an amendment below the line, exactly as [`protocol-v2.md`](../eval/protocol-v2.md) does.

This supersedes nothing. `protocol-v2` scores *retrieval* against frozen labels with no
agent in the loop; A1 scores an *agent's task outcomes* with and without
persistent memory. No A1 figure may be compared to a v1 or v2 figure.

## 1. The two questions, never collapsed

1. **Does persistent memory help?** Baseline versus Nexus.
2. **Does Nexus's retrieval add value beyond having notes?** Plain notes versus Nexus, over
   the *same captured information*.

Question 1 alone cannot attribute an improvement to Nexus. A result that answers 1 and not 2
is reported as answering 1 and not 2 — it is not evidence for the tool, only for the habit.

## 2. Arms

| # | Arm | Composition | Role |
| --- | --- | --- | --- |
| 1 | **Baseline** | Claude + ordinary repository tools, no persistent memory of any kind | Primary control for Q1 |
| 2 | **Nexus** | Identical, plus the Nexus MCP server holding the frozen captured memories | Treatment |
| 3 | **Plain notes** | Identical, plus the same captured information as one readable file in the checkout | Primary control for Q2 |
| 4 | **No-op MCP** | Identical, plus a stub MCP server exposing Nexus's tool schemas and returning valid empty results | Diagnostic only |

Arm 4 measures whether *offering* memory tools changes behaviour when no evidence exists —
wasted calls, altered strategy, a different plan shape. It **does not replace arm 1**: empty
responses cost turns and tokens, and they do not reproduce Nexus's latency or token profile.
Any claim that arm 4 is "baseline with a control server attached" is false and must not be
made. Arm 4 is optional per run; arms 1–3 are not.

## 3. Held fixed across every arm

Anything on this list that varies between arms voids the comparison for that task.

- Exact model identifier (the full name, never an alias — an alias moves), Claude Code
  version, `--effort` level, and any `--fallback-model` policy. A run in which the fallback
  fired is recorded as such and excluded from the scored set, not silently kept.
- Task prompt, byte for byte. Ordinary tool allowlist, permission mode, working directory,
  environment variables, and network reachability.
- Resource ceilings, wall-clock timeout, and retry policy.
- A fresh session and an isolated checkout per attempt. No `--continue`, no `--resume`, no
  shared transcript, no automatic memory, no carried scratch directory.
- Arm order randomised per task, and the randomisation seed recorded.

**The same model does not make runs deterministic.** Every task is run in `n` independent
trials per arm (`n` fixed in §15), attempts collapse to a per-task summary before any
comparison, and every figure carries its uncertainty. See §12.

## 4. The runner, verified against the pinned version

Verified by `claude --version` and `claude --help` on the measurement host, **2.1.258**. The
protocol depends on these being properties of the pinned version, not of the documentation.

- **`--bare` exists and does what the measurement needs.** Its help text: *minimal mode —
  skip hooks, LSP, plugin sync, attribution, auto-memory, background prefetches, keychain
  reads, and CLAUDE.md auto-discovery.* Without it, the host's user-level `CLAUDE.md` and
  auto-memory load into **every** arm, which means the "no persistent memory" baseline would
  silently contain a competing persistent-memory system. That is fatal to Q1 and to Q2, so
  `--bare` is mandatory, not a convenience.
- **`--bare` cannot authenticate on this host, and this is measured, not inferred.** Under
  it, Anthropic auth is strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` supplied via
  `--settings`; OAuth and keychain are never read. Neither is configured here, and
  `claude --bare -p … --output-format json` returns
  `"result":"Not logged in · Please run /login"` with `"terminal_reason":"api_error"`.
  Provisioning one is a **precondition of the first run of any arm**, and the choice is
  recorded in the run record because it determines which account the usage bills to.
- **Absence from `--help` means unadvertised, not unsupported, and the earlier draft got
  this wrong.** Tested against the parser — `claude <flag> mcp list`, a local subcommand
  that parses options without calling the API — with two controls: `--definitely-not-a-flag`
  is rejected (`error: unknown option`), and `--effort low` is accepted. Under that probe
  **`--max-turns`, `--system-prompt-file` and `--append-system-prompt-file` are all
  accepted**, and `--no-plugins` is rejected. The first draft claimed the first three did
  not exist, on `--help` evidence alone, and that claim is withdrawn.

  An earlier probe using `--version` instead of `mcp list` showed every flag "accepted",
  including the nonsense control: `--version` short-circuits before option validation. It
  established nothing and is recorded so the method is not repeated.

  **Parser acceptance is not behaviour.** A flag may be accepted and ignored. No accepted-
  but-unadvertised flag may be relied on until its behaviour is tested, and `--max-turns` in
  particular is a *candidate* in-run ceiling pending that test — see the budget rule below.
- Configuration is supplied explicitly: `--mcp-config` with `--strict-mcp-config` so no
  ambient MCP server can join an arm, `--setting-sources` / `--settings` for settings,
  `--add-dir` for any context directory, `--model`, `--permission-mode`, and
  `--output-format json` for a machine-readable result.
- `--disable-slash-commands` is set in every arm. Skills still resolve under `--bare`, and a
  skill firing in one arm and not another is an unrecorded variable.

**Budgets: enforcement and accounting are separate mechanisms and are registered
separately.** The result envelope is produced when a run *ends*. It can report that a run
overran; it cannot stop one. The first draft conflated the two by naming "a cost or token cap
read from the result envelope" as a ceiling, which is an accounting record described as a
control.

| Mechanism | What it is | Status |
| --- | --- | --- |
| Wall-clock timeout, enforced by the harness | A real hard stop | Available; registered as the ceiling |
| Post-run usage accounting from the result envelope | A record, read after the fact | Registered as accounting, never as a cap |
| An in-run spend or turn cap | A hard cap | **Not established.** Requires a verified runtime mechanism |

`--max-turns` is parser-accepted (above) and is the obvious candidate for the third row, but
it is unadvertised and its behaviour is untested. Until a test shows it actually terminates a
run at the stated turn count, the protocol claims no in-run cap, and a run that overruns its
intended spend is detected afterwards and reported, not prevented.

### The result envelope, captured verbatim

Taken from the failed `--bare` run above — a real envelope, not a documented one. The fields
the harness reads exist and are named:

| Field | Use |
| --- | --- |
| `is_error` | **Authoritative** run outcome |
| `terminal_reason` | Why it ended; `api_error` here |
| `num_turns` | Turn accounting |
| `duration_ms`, `duration_api_ms` | Latency, wall clock and API time separately |
| `total_cost_usd` | Spend accounting |
| `usage.{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}` | Token accounting |
| `permission_denials`, `session_id`, `result` | Trace and identification |

**`subtype` is not the outcome field, and a harness that reads it will mis-score every failed
run.** This envelope carries `"is_error":true` and `"subtype":"success"` *simultaneously*. A
harness keyed on `subtype` would have recorded an authentication failure as a successful run
with an empty patch — which §10 scores as `no_patch`, a **fail**, silently converting an
`env_fail` into evidence against whichever arm happened to hit it. The harness reads
`is_error`, and cross-checks `terminal_reason` to separate `env_fail` from a genuine failure.

**Still unverified:** this is an *error* envelope. Which fields are populated on a successful
run — `modelUsage` is empty here, and `usage` counts are all zero — is not established, and is
pinned from a successful run during harness validation before any scored run.

## 5. Task sources

| Source | Role | Limitation, stated up front |
| --- | --- | --- |
| **A** — a separate real repository | The main evaluation | One repository supports conclusions about that workload. It establishes nothing about repositories in general. |
| **B** — Nexus's own history | Development and regression cases only, always labelled | Prior implementation discussion and known fixes are substantial exposure. Never reported as a headline figure. |
| **C** — a controlled synthetic repository | Harness validation: isolation, scoring, memory delivery, failure attribution | Constructed tasks favour the behaviour they were built to exercise. Never reported as evidence of benefit. |

Source A is **`pallets/click`** — pure-stdlib, no runtime dependencies, tests needing only
`pytest`, and a `CliRunner` that exposes output, exit code and exception so a task outcome is
a value to compare rather than a judgement to make. The candidate tasks, the fixture check
each one passed, and the classes excluded are in [`tasks-click-a1.md`](tasks-click-a1.md).

Each task begins at its **own pinned pre-fix commit** — tasks are not forced onto one
snapshot — and the eventual solution must be unreachable from that checkout:
not through branches or tags, not through unreferenced git objects, not through issue or PR
text carried into the prompt, and not over the network. This is asserted by a control, not
by construction — see §11.4.

## 6. Memory provenance, registered explicitly

How the memories originate matters more than which repository they describe.

1. **Prior session.** The agent is exposed to project work and to the information available
   *at that time*. Nothing from the later task, its fix, or its hidden checks is in scope.
2. **Capture.** Memories are recorded under a fixed, written policy — before the later task
   is revealed. No memory is seeded from the solution or from the hidden acceptance checks.
   The capture policy is itself an input and is frozen and hashed with the corpus.
3. **Later session.** A fresh session runs the task, from identical initial code, under
   identical model and resource limits, in each arm.
4. **Scoring.** The resulting patch is scored by hidden acceptance checks plus a written
   rubric, with treatment labels concealed from the reviewer wherever practical.

Arm 3 receives the *same captured information* as arm 2, rendered as one readable file. If
the two arms' content differs in substance, Q2 is not being asked — it is being answered by
construction, in whichever direction the difference runs.

## 7. Task mix and selection rules

Tasks are selected under written rules, fixed before any task is run, and the mix must
include all three of:

- tasks where a captured memory is **useful**,
- tasks where it is **unnecessary** — which does **not** mean the task needs no evidence.
  These tasks still require facts obtained from the repository itself, and their rubrics
  declare those facts exactly as any other task's do. "Memory-unnecessary" is a statement
  about the *store*, never about the task's evidence demands,
- tasks where it is **outdated** — the memory is wrong now, and the correct behaviour is to
  notice and not follow it.

Selecting only memory-dependent tasks would overstate everyday benefit; the outdated cases
are the ones that can make a memory arm score *worse* than baseline, which is a real outcome
this protocol must be able to report. Development tasks, harness-validation tasks and
held-out tasks are disjoint sets, declared before execution and never rebalanced afterwards.

## 7b. Every necessary fact is labelled discoverable or not

Found during harness validation, and it changes what a benefit figure means.

For task `d1` the fix *parametrises an existing test*. The pre-fix checkout therefore
contains `test_empty_envvar` already asserting the convention for the explicit-envvar path —
the very fact the corpus records as `necessary`. The hidden check is hidden; the convention
it rests on is sitting in the agent's tree.

This is not a fixture defect and cannot be engineered away: when a fix touches an existing
test, the pre-fix version of that test is part of the repository the agent is given. What it
does mean is that **"necessary" and "unavailable" are different properties**, and the draft
used one word for both.

Each necessary fact is therefore labelled, with the task:

| Label | Meaning | What a memory arm's advantage measures |
| --- | --- | --- |
| **discoverable** | Obtainable from the fixture by reading it | Retrieval *efficiency* — the agent could have found it |
| **absent** | Not present in the fixture at all | Retrieval *necessity* — the agent could not have |

A benefit figure that mixes them overstates: an advantage on a discoverable fact says memory
saved effort, not that it supplied knowledge. Both are reported, separately, and the held-out
set must contain tasks of both kinds or it can only measure one of them.

## 8. Reported figures

Per arm, per task, and never collapsed into one score:

1. **Task correctness** — hidden acceptance checks pass or fail.
2. **Required-evidence completeness** — of the facts a task's rubric declares **necessary**,
   how many appear in the agent's work. Declared with the task, before any run.
3. **Delivered context, in three buckets, never two.** The first draft defined irrelevant
   context as "tokens the rubric does not mark necessary", which silently classes everything
   short of necessary as waste. Each task's rubric declares, in advance:
   - **necessary** — required to reach a correct outcome;
   - **useful support** — genuinely relevant and reasonably delivered, without being
     required. This is the grade-1 bucket `protocol-v2` already keeps, and for the same
     reason: a rule that penalises it would score a system for being helpful;
   - **irrelevant** — neither.

   Only the third is reported as irrelevant context. A figure that merges the second into
   the third is not reported at all.
4. **Latency** — wall clock, and the turn count from the result envelope.
5. **Total storage** — bytes the memory arm's store occupies, reported beside any benefit.

## 9. Denominators that must not be merged

- Correctness has one denominator per task set (**development**, **harness**, **held-out**),
  reported separately. A held-out figure never absorbs a development task.
- Required-evidence completeness is per task, against that task's own declared **necessary**
  fact count. Memory-unnecessary tasks are **not** assumed to have a zero denominator: per §7
  they normally require repository-obtained facts and are scored against them like any other
  task. A denominator is 0 only where a rubric genuinely declares no necessary fact, and only
  then is the figure **N/A**, never 0.0 — an unanswerable-by-construction denominator must
  not drag a mean down.
- Any mean prints its contributing count (`n/N`), so an exclusion can never be silent.

## 10. Non-binary verdicts, and which way each one biases

Every outcome that is not a clean pass or fail is classified before the run, and its bias
direction is derived from the scoring rule rather than from the verdict's name:

| Verdict | Cause | Scored as | Bias if mishandled |
| --- | --- | --- | --- |
| `timeout` | wall-clock ceiling hit | **fail**, and counted | Excluding it favours whichever arm is slower — usually the memory arms |
| `env_fail` | harness, network or auth fault | **excluded**, and reported | Scoring it as fail penalises an arm for the harness |
| `fallback_fired` | `--fallback-model` served the run | **excluded**, and reported | A different model in one arm voids that task's comparison |
| `no_patch` | agent produced no diff | **fail** | Treating it as N/A hides a real failure mode |
| `refused` | agent declined the task | **fail**, recorded verbatim | — |

A run whose excluded count is not reported alongside its scored count is not a valid run.

## 11. Negative controls, mandatory

A run in which the controls are not executed is not a valid run. Each runs from its own
fresh fixture; chaining them lets one control make the next pass for the wrong reason.

1. **No-model control.** Apply no patch and run the hidden acceptance checks. Every task
   must **fail**. A task that passes with no work is vacuous and is removed from the set —
   this is the check that catches a task already satisfied at the pinned commit.
2. **Empty-store experiment** (diagnostic, not a pass/fail control). Arm 2 with Nexus
   present and its store empty.
3. **Scrambled-memory experiment** (diagnostic, not a pass/fail control). Arm 2 with the
   store holding the *other* tasks' memories.

   **Neither has a guaranteed outcome, and the first draft was wrong to assign them one.**
   Either can beat arm 1 through chance, through changed agent behaviour under an extra tool
   — the effect arm 4 exists to measure — or because another task's memory carries
   transferable information about the repository. An improvement here is a **finding to
   investigate**, never a proof of leakage, and a non-improvement is not a clearance.

   Isolation is established separately and positively, not inferred from these: by the
   fixture construction, and by **access traces** — what the agent actually read, which
   tools it called, and what each returned. A leak is demonstrated by a trace showing the
   evidence arriving, not by an arithmetic comparison of two scores.
4. **Oracle-reachability control.** From the pinned checkout, assert the fix is unreachable
   *at runtime*: enumerate refs and unreferenced objects, and confirm no external source is
   reachable. Run before the task set is frozen, and again if the fixture is rebuilt.

   **Network policy is not all-or-nothing.** The runner's own model API connection must stay
   open — without it there is no run — while access to external sources (the upstream
   repository, issue trackers, package indexes, search) is blocked. The two are separated by
   an explicit allowlist, and the allowlist is recorded in the run record.

   **This control establishes retrieval isolation and nothing more.** It cannot establish
   that the model has never seen the fix: source A is a public repository whose history
   predates the model's training data. Memorisation is a limitation of the source, recorded
   in §13 and in every result, not something a fixture can remove.

Controls 1 and 4 are the two with guaranteed outcomes: they detect a task set that cannot
fail and one that cannot honestly succeed. Neither demonstrates benefit. Experiments 2 and 3
demonstrate nothing on their own and are reported as diagnostics beside the trace evidence.

## 12. Repetition and dispersion, replacing determinism

`protocol-v2` could require identical orderings across two passes. **This measurement cannot
and must not.** An agent run is not deterministic, and a protocol that asserted it would be
asserting an invariance it never tested — the error `protocol-v2` withdrew in its own A4.

Instead: `n` independent trials per arm per task, arm order randomised, seed recorded.

**Within-arm spread is not a significance threshold, and the first draft's rule — "a
difference smaller than the within-arm spread is not established" — is withdrawn.** It is a
threshold invented for the occasion, it ignores that the arms are *paired*, and comparing a
between-arm difference to a within-arm dispersion is not a test of anything.

What replaces it, declared before any scored run:

- **The unit of analysis is the task, not the attempt.** Arms run on the same tasks, so the
  comparison is paired on the task. Repeated attempts on one task are repeated measurements
  of that task and **must never be counted as independent tasks** — doing so would inflate
  the sample by `n` and narrow every interval by a factor the design does not earn.
- **Attempts collapse to a per-task, per-arm summary first** (the collapsing rule — mean, or
  pass-rate over `n` — is fixed in §15 before any run), and the paired analysis runs over
  those task-level summaries.
- **Uncertainty is reported as an interval over tasks**, by a method named in §15 before the
  run, together with the number of tasks contributing. The attempt-level spread is reported
  too, as a separate figure describing run-to-run variability — it is a property of the
  agent, not a measure of the comparison.
- **`n` and the task count are both fixed in advance.** Neither is adjusted after seeing
  results, and no arm is added or dropped once the run starts.

## 13. What this cannot establish

One repository, one agent, one model, one capture policy, tasks selected on the
implementation side by the person who built the tool being measured. It is a reproducible
diagnostic with explicit limitations.

It cannot establish: that Nexus helps on repositories or workloads other than the one
measured; that any result generalises to other agents or models; that a benefit would
survive a capture policy the agent applied on its own rather than one written in advance;
or any comparison against another memory system, none of which is run here.

**Nor can it establish that the agent had not already memorised the fix.** Source A is a
public repository and its history predates the model's training cutoff. Runtime isolation
(§11.4) stops the agent *retrieving* a fix; it does nothing about prior exposure.

**Its effect on the arms is unknown.** An earlier draft said exposure inflates every arm
equally and therefore left the between-arm comparison safe. That is withdrawn: it is the
convenient assumption, not the conservative one. Exposure could plausibly interact with an
arm rather than shift all of them — a memory naming the right subsystem could make recall of
a memorised fix more likely in arm 2 than in arm 1, and the result would look like a
retrieval benefit while being an elicitation effect. Nothing in this protocol measures which
happens. So: no figure, absolute or between-arm, may be defended by an equal-exposure
argument, and every reported difference on a task whose fix is public carries this caveat.

## 14. Run record, written into the results artifact

- Claude Code version, full model identifier, effort level, and whether any fallback fired.
- The commit of Nexus under test, and whether `src/` was dirty.
- SHA-256 of every input: the task set, the memory corpus, the capture policy, the rubrics,
  the hidden acceptance checks, and this protocol.
- The pinned commit for each task, and the oracle-reachability control's output.
- The verbatim `--output-format json` envelope of one run, with the field names the harness
  read from it.
- The randomisation seed, and the realised arm order per task.
- Every excluded run, with its verdict from §10.

## 15. Open slots — frozen before any scored run

What the draft cannot fill by itself. Until each is filled, hashed and recorded here, this
document is not registered.

1. ~~**The repository (source A).**~~ **Filled:** `pallets/click`, surveyed at `6aabf09`.
   Each task pins its own pre-fix commit; see the manifest.
2. **The task selection rules**, and the resulting disjoint development / harness / held-out
   sets, each with its pinned pre-fix commit, rubric, necessary-fact list, and hidden checks.
3. **The memory provenance**: the capture policy text, and the frozen corpus it produced.
4. **The arms as executed**: whether arm 4 runs, `n`, the ceilings, and the auth mechanism
   `--bare` will use.
5. **The analysis, fixed before any scored run** (§12): the rule collapsing `n` attempts to a
   per-task summary, the paired comparison over tasks, and the interval method with its
   contributing task count.
6. **The budget mechanism** (§4): the wall-clock ceiling, the accounting fields, and whether
   an in-run cap exists at all — which requires testing whether `--max-turns` behaves, not
   merely that it parses.
