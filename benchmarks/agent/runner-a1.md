# A1 runner verification — DeepSeek Flash through the Anthropic-compatible endpoint

Fills the runner half of [`protocol-a1.md`](protocol-a1.md) §15 slot 4 (the auth mechanism
`--bare` will use, and the model as executed) and closes slot 6's open question. Everything
below was **executed on the measurement host on 2026-09-11**; nothing is carried from
documentation. Where a probe was wrong, the wrong probe is recorded rather than deleted, as
§4 already does for the `--version` probe.

## 1. The run identity, recorded before any scored run

| Field | Value | Provenance |
| --- | --- | --- |
| Provider | DeepSeek, via `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` | measured |
| Auth | `ANTHROPIC_API_KEY` set from `DEEPSEEK_API_KEY`; no OAuth, no keychain | measured |
| Requested model ID | `deepseek-flash` | set |
| **Returned** model ID | `deepseek-flash` | measured — see §2 |
| Claude Code version | 2.1.258 | measured (`claude --version`) |
| Nexus commit under test | `81bb4ac`, `src/` clean | measured (`git status`) |
| Reasoning | **on by default**, controllable per request | measured — see §5 |
| Execution date | 2026-09-11 | — |

The smoke test succeeded: `"result":"OK"`, `"is_error":false`,
`"terminal_reason":"completed"`, `num_turns` 1.

## 2. The model identifier is an alias, and the endpoint does not resolve it

`protocol-a1` §3 requires "the full name, never an alias — an alias moves". **This endpoint
cannot satisfy that rule from the API response.** A direct call to
`/v1/messages` returns `"model":"deepseek-flash"` verbatim: the alias is echoed, not resolved
to a dated or versioned identifier. Claude Code's envelope agrees
(`"canonicalModel":"deepseek-flash"`) because it derives the field from what was requested.

Claude Code also emits `[claude-code:unrecognized_model] {"model":"deepseek-flash"}` on
stderr for every run and passes the name through regardless. Two consequences follow, and
both are recorded rather than resolved:

- **`"provider":"firstParty"` in the envelope is wrong** and must not be read as provenance.
  The run record takes the provider from `ANTHROPIC_BASE_URL`, which is set by the harness.
- **§3's pinning requirement cannot be met by this endpoint, and the resolution is
  settled.** The run record carries the alias, the endpoint, and the **UTC run window**. The
  underlying revision is unknown and is recorded as unknown. A fingerprint probe is a
  *diagnostic* — it may detect that something changed — and is not a form of pinning; it is
  not offered as an alternative to one. No further model-selection approval is outstanding.

## 3. `total_cost_usd` is not a measurement of spend on this endpoint

The envelope reports `"costBasis":"unknown"`. Across four independent runs the reported cost
is reproduced **exactly** by a fixed price table of **$5.00/M input, $25.00/M output,
$0.50/M cache-read**:

| in | out | cache-read | reported | predicted | exact |
| --- | --- | --- | --- | --- | --- |
| 1331 | 28 | 0 | 0.007355 | 0.007355 | yes |
| 1479 | 232 | 2432 | 0.014411 | 0.014411 | yes |
| 4598 | 386 | 12672 | 0.038976 | 0.038976 | yes |
| 1332 | 563 | 1536 | 0.021503 | 0.021503 | yes |

**What this shows, stated no more strongly than the evidence allows.** The four observations
are exactly consistent with that rate table. They do **not** establish that the table is what
the implementation applies universally, nor why — the pricing code was not inspected, and four
points fitting three rates is a weak constraint. What is directly observed is
`"costBasis":"unknown"` on a model the CLI reports as unrecognised.

The operational consequence does not depend on resolving that. The token counts come from the
provider and remain valid accounting; the dollar figure has no established provenance, so **A1
omits it**. No result quotes a cost.

## 4. What was verified to work

| Capability | Result | Evidence |
| --- | --- | --- |
| Ordinary tool calls | **works** | `Read` + `Write`, 3 turns, `answer.txt` contained the value extracted from the fixture |
| Nexus MCP calls | **works** | `status`, `record`, `search` all executed under `--mcp-config` + `--strict-mcp-config`; memory `9d03…` verified **in SQLite directly**, not from the agent's own report |
| `--max-turns` | **works — it terminates** | see §6 |
| `--disallowedTools` | parser-accepted | see §7 |

The MCP store was checked independently: one row in `memories`, one in `revisions`, kind
`decision`, namespace `a1-probe`. The agent's summary and the database agree.

## 5. Reasoning is on by default, and the thinking-token counter reads zero regardless

Measured two ways:

- **Direct endpoint.** With no `thinking` field the response's first content block is
  `{"type":"thinking",…}`. With `"thinking":{"type":"disabled"}` the response is plain text,
  one output token. Reasoning is therefore **default-on and controllable**.
- **Through Claude Code.** A `stream-json` run shows assistant content blocks
  `['thinking','tool_use','thinking','tool_use','thinking','text']` — thinking is live —
  while the same run's envelope reports `output_tokens_details.thinking_tokens: 0` against
  `output_tokens: 483`.

So the envelope's thinking counter is **broken for this provider** and must not be used as
an A1 figure. Reasoning settings are held fixed across arms by holding the request shape
fixed, not by reading them back from the envelope.

The `thinking` block's `signature` field equals the message `id` — it is a placeholder, not
an Anthropic signature. Untested: whether multi-turn replay of thinking blocks survives it.

## 6. `--max-turns` behaves — the third row of §4's budget table is now filled

`protocol-a1` §4 registered an in-run cap as **"Not established. Requires a verified runtime
mechanism"**, with `--max-turns` a parser-accepted candidate whose *behaviour* was untested.
It is now tested. A task requiring nine sequential `Write` calls, run with `--max-turns 2`:

```
is_error: true   subtype: error_max_turns   terminal_reason: max_turns   num_turns: 3
files created: a1.txt a2.txt        (2 of 9)
```

The run was **stopped**, not merely reported on. An in-run turn cap exists.

Two details the harness must encode:

- **`num_turns` is not the cap.** A cap of 2 produced `num_turns: 3`. The envelope's counter
  and the flag's units are not interchangeable; the ceiling is recorded as the flag value.
- **A capped run leaves a partial patch** — here, two of nine files. This is a new verdict
  class, absent from §10, and it is not `timeout` and not `env_fail`. It is registered in
  §10 as `max_turns`: **fail, and counted**. Excluding it favours whichever arm reaches the
  cap more often; which arm that is is a result to be read off the run, not an assumption to
  be stated in advance.

## 7. Provider-side web search is real, reachable, and must be disabled explicitly

The concern was correct, and it is stronger than a documentation note. Offering the endpoint
an Anthropic-style server tool produced a genuine server-side search:

```
{"type":"server_tool_use","name":"web_search","input":{"query":"today's top news story"}}
{"type":"web_search_tool_result", …}
```

The search executes **on DeepSeek's servers**. `protocol-a1` §11.4's allowlist governs the
runner host's network and would not have stopped it — the oracle-reachability control as
written does not cover this path at all.

**Configured exclusion and verified exclusion are different claims, and only the second is
worth anything.** Parser acceptance of `--disallowedTools`, plus a default run that happened
not to search, would establish neither. So the outbound request itself was inspected: a
logging proxy stood in for the endpoint, captured the first `POST /v1/messages?beta=true`,
and the run was refused before any token was spent.

| Configuration | Tools in the outbound request | search / fetch entries |
| --- | --- | --- |
| `--bare -p`, no flag | `Bash`, `Edit`, `Read` | **none** |
| `--bare -p --disallowedTools WebSearch,WebFetch` | `Bash`, `Edit`, `Read` | **none** |

Two things follow, and the second is the correction:

- **Verified:** no web-search tool — named or server-typed — is offered to the model in the
  outbound request. Search is excluded from the effective inventory, established by reading
  the request rather than by reading a flag or a counter.
- **`--disallowedTools` is a no-op in this configuration.** The two inventories are
  identical. It does **not** remove an otherwise-available tool here, because search was
  never in the inventory to begin with. The earlier claim that the flag is what excludes
  search is withdrawn; the flag is kept as a cheap guard against a future default change,
  not as the mechanism.

**An inventory is per-configuration, so the assertion is per-arm.** The capture above is for
a minimal run. Each arm's actual flag-set — its `--allowedTools`, its `--mcp-config` — makes
its own inventory, and each is captured through the proxy before the arm runs and recorded
with the run. The envelope's `server_tool_use.web_search_requests` is still asserted to be
`0` for every scored run, as a second and weaker check: a zero counter alone is not proof,
and a non-zero one is an `env_fail`.

## 8. Two probes of mine were wrong, and are recorded

1. **A parser probe run under `zsh` established nothing.** `claude $flag mcp list` with an
   unquoted variable does not word-split in zsh, so `--effort low` was passed as a single
   token and **the known-good control failed**. A probe whose positive control fails is
   void. Re-run under `bash`: `--disallowedTools` accepted, `--effort low` accepted,
   `--definitely-not-a-flag` rejected. Both controls behaved, so the result stands.
2. **A price table fitted to one run was wrong.** $5/$25 reproduced the smoke test exactly
   and was falsified by the next three runs, which carry cache-read tokens. The fit in §3
   holds only because it was checked against four runs including the three that broke it.

## 9. Still not established

- Whether `deepseek-flash` is stable over the run window. §2's alias problem means a move
  would be silent. No fingerprint probe is defined yet.
- Whether thinking blocks replay correctly across multi-turn tool use under the placeholder
  signature.
- `contextWindow: 200000` and `maxOutputTokens: 32000` in the envelope are Claude Code's
  defaults for an unrecognised model. Neither has been checked against the provider.
- Everything §13 of the protocol already says. Swapping the model does **not** retire the
  memorisation caveat, but the chronology claim is withdrawn. What is established is that
  `pallets/click` is public and widely mirrored, so prior exposure is **possible**. That these
  particular fixes predate this model's training data is **not** established: no training
  window is published for the model in use.

## 10. Host hygiene, noted not fixed

`~/.zshrc` line 16 emits `command not found: Export` on every shell, and exports
`DEEPSEEK_API_KEY` twice (lines 67 and 73). The later export wins. If the two values differ,
a run launched from a login shell and one launched from the terminal could authenticate as
different accounts — which §4 requires the run record to pin, because it determines billing.
Not investigated further: reading the key material was declined.
