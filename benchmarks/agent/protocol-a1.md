# A1 — agent task baseline (protocol **draft**, not yet registered)

**Status: draft. No scored run may be executed against this document as it stands.**

This registers the *rules* of the measurement — arms, provenance, scoring, controls, and
what each figure may be read to mean. It does not yet name the repository, the tasks or the
memory corpus. Those are the four slots in §15, and this document becomes *registered* only
when they are filled and frozen. Deviating after seeing results invalidates the run; the
correct response to a surprising number is a new run under an amended protocol, recorded as
an amendment below the line, exactly as [`protocol-v2.md`](../eval/protocol-v2.md) does.

The measurement this replaces nothing of: `protocol-v2` scores *retrieval* against frozen
labels with no agent in the loop. A1 scores an *agent's task outcomes* with and without
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
trials per arm (`n` fixed in §15), and every figure is reported with its dispersion, never as
a single number. See §12.

## 4. The runner, verified against the pinned version

Verified by `claude --version` and `claude --help` on the measurement host, **2.1.258**. The
protocol depends on these being properties of the pinned version, not of the documentation.

- **`--bare` exists and does what the measurement needs.** Its help text: *minimal mode —
  skip hooks, LSP, plugin sync, attribution, auto-memory, background prefetches, keychain
  reads, and CLAUDE.md auto-discovery.* Without it, the host's user-level `CLAUDE.md` and
  auto-memory load into **every** arm, which means the "no persistent memory" baseline would
  silently contain a competing persistent-memory system. That is fatal to Q1 and to Q2, so
  `--bare` is mandatory, not a convenience.
- **`--bare` has an auth prerequisite that is not satisfied by default.** Under it, Anthropic
  auth is strictly `ANTHROPIC_API_KEY` or `apiKeyHelper` supplied via `--settings`; OAuth and
  keychain are never read. Neither is configured on this host today. Provisioning one is a
  precondition of the first scored run, and the choice is recorded in the run record because
  it determines which account the usage bills to.
- **`--max-turns` does not exist in this version** (`claude --help` matches it zero times).
  Turn ceilings therefore cannot be delegated to the CLI. The harness enforces its ceilings
  itself: wall-clock timeout, and a cost or token cap read from the result envelope.
- **`--bare`'s own help text names two flags this version does not have** —
  `--system-prompt-file` and `--append-system-prompt-file` match zero times, while
  `--system-prompt` and `--append-system-prompt` take inline values. The harness uses the
  inline forms. This is recorded because a harness written from the help text would fail at
  launch, and a harness that fell back silently would change what context each arm received.
- Configuration is supplied explicitly: `--mcp-config` with `--strict-mcp-config` so no
  ambient MCP server can join an arm, `--setting-sources` / `--settings` for settings,
  `--add-dir` for any context directory, `--model`, `--permission-mode`, and
  `--output-format json` for a machine-readable result.
- `--disable-slash-commands` is set in every arm. Skills still resolve under `--bare`, and a
  skill firing in one arm and not another is an unrecorded variable.

**Unverified and must be pinned during harness validation (§5, repo C):** the field names in
the `--output-format json` result envelope. The harness reads its cost, duration and turn
counts from that envelope, so one real run is captured verbatim and its fields pinned in the
run record *before* any scored run. No figure may be read from a field whose name was
assumed.

## 5. Task sources

| Source | Role | Limitation, stated up front |
| --- | --- | --- |
| **A** — a separate real repository | The main evaluation | One repository supports conclusions about that workload. It establishes nothing about repositories in general. |
| **B** — Nexus's own history | Development and regression cases only, always labelled | Prior implementation discussion and known fixes are substantial exposure. Never reported as a headline figure. |
| **C** — a controlled synthetic repository | Harness validation: isolation, scoring, memory delivery, failure attribution | Constructed tasks favour the behaviour they were built to exercise. Never reported as evidence of benefit. |

Source A is a manageable Python repository with reproducible tests. Each task begins at a
**pinned pre-fix commit**, and the eventual solution must be unreachable from that checkout:
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
- tasks where it is **unnecessary**, and
- tasks where it is **outdated** — the memory is wrong now, and the correct behaviour is to
  notice and not follow it.

Selecting only memory-dependent tasks would overstate everyday benefit; the outdated cases
are the ones that can make a memory arm score *worse* than baseline, which is a real outcome
this protocol must be able to report. Development tasks, harness-validation tasks and
held-out tasks are disjoint sets, declared before execution and never rebalanced afterwards.

## 8. Reported figures

Per arm, per task, and never collapsed into one score:

1. **Task correctness** — hidden acceptance checks pass or fail.
2. **Required-evidence completeness** — of the facts a task's rubric declares necessary, how
   many appear in the agent's work. Declared with the task, before any run.
3. **Irrelevant context** — tokens delivered that the rubric does not mark necessary.
4. **Latency** — wall clock, and the turn count from the result envelope.
5. **Total storage** — bytes the memory arm's store occupies, reported beside any benefit.

## 9. Denominators that must not be merged

- Correctness has one denominator per task set (**development**, **harness**, **held-out**),
  reported separately. A held-out figure never absorbs a development task.
- Required-evidence completeness is per task, against that task's own declared fact count.
  Tasks with zero necessary facts — the memory-unnecessary cases — have a denominator of
  **0** and are reported **N/A**, never 0.0. An unanswerable-by-construction denominator
  must not drag a mean down.
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
2. **Empty-store control.** Arm 2 with Nexus present and its store empty. It must score
   within the dispersion of arm 1. Scoring higher proves the harness is leaking evidence
   into the memory arm by some path other than the store.
3. **Scrambled-memory control.** Arm 2 with the store holding the *other* tasks' memories.
   It must not score above arm 1. Scoring higher means the benefit is not coming from the
   content being relevant.
4. **Oracle-reachability control.** From the pinned checkout, assert the fix is unreachable:
   enumerate refs and unreferenced objects, and confirm the network is unavailable. Run
   before the task set is frozen, and again if the fixture is rebuilt.

Controls 1 and 4 detect a task set that cannot fail and one that cannot honestly succeed.
Neither control demonstrates benefit.

## 12. Repetition and dispersion, replacing determinism

`protocol-v2` could require identical orderings across two passes. **This measurement cannot
and must not.** An agent run is not deterministic, and a protocol that asserted it would be
asserting an invariance it never tested — the error `protocol-v2` withdrew in its own A4.

Instead: `n` independent trials per arm per task, arm order randomised, seed recorded. Every
figure reports its spread across trials alongside its central value. A difference between
arms that is smaller than the within-arm spread is reported as **not established**, and is
never described as an improvement.

## 13. What this cannot establish

One repository, one agent, one model, one capture policy, tasks selected on the
implementation side by the person who built the tool being measured. It is a reproducible
diagnostic with explicit limitations.

It cannot establish: that Nexus helps on repositories or workloads other than the one
measured; that any result generalises to other agents or models; that a benefit would
survive a capture policy the agent applied on its own rather than one written in advance;
or any comparison against another memory system, none of which is run here.

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

The four the draft cannot fill by itself. Until each is filled, hashed and recorded here,
this document is not registered.

1. **The repository (source A).** Named, with the commit its fixtures are built from.
2. **The task selection rules**, and the resulting disjoint development / harness / held-out
   sets, each with its pinned pre-fix commit, rubric, necessary-fact list, and hidden checks.
3. **The memory provenance**: the capture policy text, and the frozen corpus it produced.
4. **The arms as executed**: whether arm 4 runs, `n`, the ceilings, and the auth mechanism
   `--bare` will use.
