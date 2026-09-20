# What `--max-turns` bounds, and what `num_turns` counts

**Model-free.** The installed `claude` binary, the real forwarder, and a scripted loopback
stand-in for the provider. No external model request was issued and nothing was charged.

This answers one question, asked after A2-R:

> Why did some runs complete with `num_turns` of 49 or 57 under `--max-turns 45`, while
> capped runs report 46?

[`runner-a1.md`](runner-a1.md) §4 had already established that the flag and the envelope
counter are different quantities. It did not say what each one counts, so it could not say
whether a run reporting 57 had exceeded its budget, and it could not say what a budget of
*n* would actually buy the next registration.

**They are three different counters and only one of them is the flag's.** The answer below
is measured, by driving the real CLI through shapes where the candidate counts disagree.
Nothing here corrects an old figure or relabels a termination: every A2-R number is
arithmetically consistent with the rule, and the rule explains the discrepancy rather than
removing it.

---

## 1. What was executed

| | |
| --- | --- |
| executable | `/Users/soko/.local/bin/claude` → `/Users/soko/.local/share/claude/versions/2.1.270` |
| `claude --version` | `2.1.270 (Claude Code)` |
| sha256 | `a506b6d970a4cf44f6abdb53a81ddcd5d3b0ce042a95c502fe9d1f946bdb8807` |
| size / mtime | 207 500 480 bytes, 2026-09-14 11:56:55 |
| code signature | `com.anthropic.claude-code`, Mach-O thin arm64, timestamp 2026-09-12 20:11:53 |
| same binary as A2-R? | yes — every A2-R trace's `init` line records `claude_code_version 2.1.270` |

**Argument vector**, as `run_arms_isolated.invoke` builds it and as the diagnostic reuses it
(the diagnostic drops `sandbox-exec`, `--mcp-config` and `--strict-mcp-config`, which is the
only difference):

```
claude --bare -p <PROMPT> --model deepseek-flash
       --mcp-config <arm>/mcp.json --strict-mcp-config
       --allowedTools <per-arm> --disallowedTools WebSearch,WebFetch
       --permission-mode acceptEdits --disable-slash-commands
       --max-turns 45 --output-format stream-json --verbose
```

**Configuration.** `a2r-config-45.json`, `config_version: calib-v3`, digest
`22eb0a3766cdde73`, `max_turns: 45`, `wall_clock_s: 600`. `launched.json` records
`max_turns: 45` for every one of the twelve arm-runs, so the flag's value is not in question.

**Environment.** Ten names reach the child; 57 host names are withheld.

| set by the harness | inherited |
| --- | --- |
| `A2_PYTHON`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `TMPPREFIX` | `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TMPDIR`, `USER` |

No `MAX_THINKING_TOKENS`, no `CLAUDE_*` overrides, no settings file: `HOME` is inherited, so
a user-level configuration is reachable in principle and is **not** ruled out by this
diagnostic — see §6.

---

## 2. Four quantities, counted separately

Named apart on purpose. Calling all of them "turns" is what made the question hard.

| quantity | how it is counted |
| --- | --- |
| **model requests** | POSTs the stub actually received. Observed at the stub, never inferred. |
| **assistant messages** | distinct `message.id` in the trace — one per model response. The stream emits one entry *per content block*, so a message with a thinking block and a tool call arrives twice under one id; entries are counted separately and are not this. |
| **tool-use blocks** | `tool_use` blocks across all assistant messages. One message may carry several. |
| **tool-result blocks** | `tool_result` blocks returned. Each arrives as its own `user` stream entry even when several were batched into one API message. |

`num_turns` is the envelope field under measurement and is never used to derive any of them.

---

## 3. The measurements

`diagnose_turns.py` over `stub_turns.py`. Full rows in
[`results-turn-accounting.json`](results-turn-accounting.json).

| case | cap | reqs | asst msgs | tool-use | tool-result | `num_turns` | subtype |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `final_only` | 45 | 2 | 1 | 0 | 0 | **1** | success |
| `sequential` | 45 | 5 | 4 | 3 | 3 | **4** | success |
| `text_with_tool` | 45 | 3 | 2 | 1 | 1 | **2** | success |
| `error_recovery` | 45 | 4 | 3 | 2 | 2 | **3** | success |
| `parallel` | 45 | 3 | **2** | **3** | 3 | **4** | success |
| `overshoot` | 45 | 5 | 4 | 9 | 9 | **10** | success |
| `overshoot` | **5** | 5 | 4 | 9 | 9 | **10** | success |
| `runaway` | **5** | 6 | **5** | 5 | 5 | **6** | error_max_turns |
| `runaway_parallel` | **5** | 6 | **5** | **10** | 10 | **6** | error_max_turns |
| `runaway` | 45 | 46 | **45** | 45 | 45 | **46** | error_max_turns |
| `runaway_parallel` | 45 | 46 | **45** | **90** | 90 | **46** | error_max_turns |

Three pairs carry the argument. Each is a pair because one row alone cannot settle it: in a
loop with one tool per message, model responses and tool calls are equal, and every
candidate rule fits.

**`runaway` vs `runaway_parallel` at the same cap — what the flag bounds.** Identical caps,
five model responses each, but five tool calls against ten (and 45 against 90 at cap 45).
The flag bound the model responses and was indifferent to the tool calls.

**`parallel` vs `sequential` — what `num_turns` counts on a normal completion.** Two model
responses against four, but `num_turns` is 4 in both. It tracked the tool calls, not the
responses.

**`runaway_parallel` at cap 45 — the capped path is a different counter.** 90 tool calls, and
`num_turns` is 46. Tool-call counting would have said 91.

---

## 4. Established

**A. `--max-turns N` bounds assistant messages — model responses in the agent loop — at
exactly N.** Measured at two caps, each with one and with two tool calls per message; the
count stops at N in all four. Corroborated in the binary, where the loop's terminal state
carries `{reason:"max_turns", turnCount:…}` and the envelope is built with
``errors:[`Reached maximum number of turns (${gr.maxTurns})`]``.

**B. On a normal completion, `num_turns` = tool-result deliveries + 1.** Equivalently
tool-use blocks + 1, and `user` stream entries + 1; all three are equal in every trace
examined, in A2-R and here, so they cannot be told apart behaviourally. The initial prompt
is the `+1`: `final_only` runs no tool and reports 1. The binary shows the counter feeding
`num_turns` on the non-capped paths being incremented once per `user` message
(`if(Le.type==="user"){…Ir++`), and the loop state initialised at `turnCount:1`.

**C. This counter is not bounded by the flag, and routinely exceeds it.** `overshoot` under
`--max-turns 5` completes normally with `num_turns: 10`. That is the reported anomaly,
reproduced deliberately: fewer model responses than the ceiling, more tool calls than the
ceiling.

**D. On the capped path, `num_turns` is reported as N + 1 and carries no information about
the work done.** 46 at cap 45 and 6 at cap 5, whether the run made 45 tool calls or 90. It
is the loop's own turn counter, which starts at 1.

**E. The CLI issues at least one model request that is not a loop response.** Both `runaway`
runs show requests = responses + 1, and the extra request carries no tool definitions;
stderr names it: `[claude-code:unrecognized_model] {"model":"deepseek-flash",
"query_source":"generate_session_title"}`. It was silently consuming the script's first turn
until it was separated, which is why the first tool result in an early draft answered a
command the agent never issued. **A request count is therefore not a response count**, and
any future per-request budget must say which it means.

### Every A2-R arm-run fits, with nothing rewritten

| cell | asst msgs | tool-use | `num_turns` | rule | subtype |
| --- | ---: | ---: | ---: | --- | --- |
| k1/baseline | 39 | 42 | 43 | tool-use + 1 | success |
| k1/notes | 38 | 56 | **57** | tool-use + 1 | success |
| k2/baseline | 33 | 39 | 40 | tool-use + 1 | success |
| k2/nexus | 32 | 48 | **49** | tool-use + 1 | success |
| k2/notes | 31 | 37 | 38 | tool-use + 1 | success |
| k1/nexus | **45** | 48 | 46 | cap + 1 | max_turns |
| k3/baseline | **45** | 55 | 46 | cap + 1 | max_turns |
| k3/nexus | **45** | 50 | 46 | cap + 1 | max_turns |
| k3/notes | **45** | 51 | 46 | cap + 1 | max_turns |
| k4/baseline | **45** | 50 | 46 | cap + 1 | max_turns |
| k4/nexus | **45** | 51 | 46 | cap + 1 | max_turns |
| k4/notes | **45** | 47 | 46 | cap + 1 | max_turns |

All twelve. Every capped arm-run made **exactly 45** model responses; the completed ones made
31 to 39. **49 and 57 are not budget overruns.** k1/notes made 38 model responses against a
ceiling of 45 and used 56 tool calls doing it; k2/nexus made 32 and used 48. Neither came
close to the cap.

**Consequence for reading A2-R.** `num_turns` is not comparable across termination states:
43 and 57 are tool-call counts, 46 is the ceiling plus one. Ranking arm-runs by it, or
averaging it, mixes two units. The comparable quantity across all twelve is **assistant
messages**, and it is not in any published A2-R artifact — it is recoverable from the
retained traces, as it was here.

---

## 5. Not established

- **Why the two paths report different units.** Both strings are visible in the binary and
  the behaviour is reproducible, but "`num_turns` means tool calls on one path and model
  responses on the other" is a description of what it does, not of what it is for. Treat the
  field as two fields.
- **Whether tool-use or tool-result deliveries drive the completed-path counter.** They are
  equal in every trace, including under parallel tool calls, because the stream splits a
  batched result into one `user` entry each. No observed shape separates them, and none was
  contrived.
- **Whether any other mechanism can stop the loop earlier.** Wall clock, budget and
  `error_during_execution` are separate paths (`error_max_budget_usd` appears beside
  `error_max_turns` in the binary). This diagnostic exercised the turn cap only.
- **Whether a user-level configuration could change the effective cap.** `HOME` is inherited
  by the arm, so a settings file is reachable in principle. Not audited; A2-R's records do
  not carry one. Anything depending on it stays unverified.
- **Whether subagents count against the parent's cap.** Ten of the twelve A2-R arm-runs emit
  at least one `task_started` system entry (1 to 4 each), yet all twelve report
  `subagent_stats.spawned: 0`, `completed: 0`, `max_depth: 0`. Those two records disagree
  about whether anything was spawned, and nothing here resolves it. Not exercised by this
  diagnostic, and no claim is made — but it is the one thing that could put model responses
  in a trace that the parent's cap did not authorise, and all twelve traces fit the rule in
  §4 without needing it.

---

## 6. What the next registration may say

**It may say what its budget controls:** `--max-turns N` buys **N model responses**. That is
the quantity to register, and it is independently verified — by the paired experiment in
§3, not by reading `num_turns` back.

**It may not use envelope `num_turns` as a hard bound.** It is not one. Under a ceiling of
45 an arm-run can finish normally reporting 57.

**It should record assistant messages per arm-run**, which the harness does not do today. It
is the only one of the four quantities that is comparable across terminations, and it is what
the cap acts on. `diagnose_turns.tally` computes all four from a saved trace and is what
produced the tables above.

**A registration that needs a bound on tool calls needs a different mechanism.** None was
found here, and `--max-turns` is not it: `runaway_parallel` made 90 tool calls under a
ceiling of 45.

---

## 7. Reproducing this

Nothing below contacts a provider.

```bash
python3 benchmarks/agent/diagnose_turns.py --out <dir> --max-turns 45 \
    --cases final_only,sequential,parallel,text_with_tool,error_recovery,overshoot,runaway,runaway_parallel
python3 benchmarks/agent/diagnose_turns.py --out <dir> --max-turns 5 \
    --cases runaway,runaway_parallel,overshoot,sequential
```

`stub_turns.py` is reached through `A1_FORWARDER_STUB_UPSTREAM`, the existing stub-transport
hook, which accepts a loopback address only and which a sandboxed arm cannot set. The
forwarder's own route and tool allowlists stand between the CLI and the network exactly as
they do in a paid run.

The cheap half of the finding is in CI:
`benchmarks/agent/test_turn_accounting.py` — 12 checks, including the one that fails if a
script is quietly edited so that `parallel` stops disagreeing with `sequential`, and the one
that fails if the tally starts counting stream entries as messages. The expensive half is the
integration experiment above; a test cannot assert a vendored binary's behaviour without
pinning the binary, and the binary's identity is recorded in §1 instead.
