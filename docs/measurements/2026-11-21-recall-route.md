# Which automatic recall route ran?

The running DSH Host registered **one** Loader entry named `@local/dsh-nexus-memory/hooks`. Its installed handler builds a local lexical query and **fetches bodies before inference**. An offer in this route is not an ID-only invitation for the model to call a tool.

## Identity and bounds of the proof

Host `Config.listConfigs` filtered by that exact name returned `include:nexus-memory-hooks` (patch ID `nexus-memory-hooks`). Querying that entry returned `<profile>/node_modules/@local/dsh-nexus-memory`. Its schema status was `unsupported`, not an absence of a Loader row. From that directory—not the repository checkout—the manifest identifies version **1.1.0** and export `./hooks → ./hooks.js`. SHA-256 of the observed files:

| File | SHA-256 |
|---|---|
| package.json | d180da88cdba89cae8a699348e825453f340d16804ec544d5f4ca8f8a58dfbb4 |
| hooks.js | 45e581ff376de3430beb7f0a588fcbdf84b9b48f8a64ae421cc3493583455566 |
| hooks-core.js | 7b37614b3d3916f3424c383318451cd8d82aecb63c5515d8508e38b9e41f11c8 |

In the installed entry, `apply` passes a native capability dispatcher to `installHooks`. Its `agent/pre-step` callback takes the enter decision and calls `recallQuery` once per eligible turn. The core builder selects human text, rejects sensitive input, removes short words and selected stop words, deduplicates lowercased terms, then joins quoted literals with OR. The installed bundle row matches the parsed defaults: 4,096 query-input bytes, 12 terms, 256 query bytes, 4 search hits, 2 reads, 10 s per operation, and 6,000 injected bytes including 3,500 reserved for capture. The replay config SHA-256 is `3bbb17d9b5fc1b89e65bbcaa6d6c01a081e50341faa6685a0d52a8928acc273e`. This identifies the bundled policy; live settings were not passed to replay.

The handler sends `mcp__nexus__search` with `advanced: true`. It iterates the first two ranks and issues a revision-pinned `mcp__nexus__get` for each valid identifier pair. It checks identity, currency, size and sensitivity before fitting the candidate text in the remaining injection budget. There is no model query or explicit search/get in this callback. By contrast, the [Codex adapter](../../src/nexus_memory/transport/codex.py) selects and filters lexical candidates on UserPromptSubmit, suppresses previously seen revisions, and supplies IDs, not body text. A subsequent explicit get is a separate event; Codex cannot stand in for this baseline.

## Correlated synthetic observation

In a fresh synthetic root turn, **“Please inspect the ceramic kiln.”** produced `"inspect" OR "ceramic" OR "kiln"`, advanced search, limit 4. The [smoke fixture](../../benchmarks/recall_quality/route-smoke.json) returned these ranks. Each reply was correlated to its installed callback's generated call ID; no live tool received the calls.

| Rank | Returned hit | Automatic exact read | Offered in the message |
|---|---|---|---|
| 1 | fixture-quartz / quartz-v1 | quartz-v1 | Yes: “Quartz tile cooled slowly.” |
| 2 | fixture-amber / amber-v1 | amber-v1 | No: reply identified amber-v2 as current |
| 3 | fixture-cobalt / cobalt-v1 | None | No |

The actual recall receipt reported `hitCount=3, reads=2, failed=false, usedBytes=743`, with only quartz in `memories`. This is a replay of the installed handler, **not** an end-to-end production search, an assessment of rank quality, or an answer-model run. No live prompts, database, sessions, credentials or settings are required. No model calls, profile edits or writes were performed by replay.

## Reproduce or supply a fixture store

From the repository root under the verified profile (Node v24.14.0 used here):

~~~sh
node benchmarks/recall_quality/route.mjs < benchmarks/recall_quality/route-smoke.json
node --test benchmarks/recall_quality/route.test.mjs
~~~

Outside that shell, set `NEXUS_RECALL_PACKAGE_DIR` to the exact package directory established by the live inspection above. The [adapter](../../benchmarks/recall_quality/route.mjs) checks manifest, entry and core hashes before import; a missing or changed artifact is not a fallback to locally rewritten code. It calls the installed registration function, substitutes an in-memory dispatcher for the native executor and drives only the registered pre-step callback. Each case starts with a new projection and agent. No capture or turn-end path runs.

Send `{cases:[{id,prompt,responses}]}` as JSON on stdin. Responses are raw structured results in invocation order (search hits, then get results); null represents a failed reply. To connect an isolated fixture store, probe with an empty response list, dispatch the observed search only to that store, then replay with that result to discover get arguments. Supply the correlated body results to the final replay. Only score the **complete** final trace; preparation probes are not additional model or search calls in the arm. Output contains exact call names/arguments, the injection string, injected `candidate_ids`, and the handler receipt. Insufficient/extra replies or a handler-reported failure set `complete:false`. An observed short-prompt skip instead has no calls, no receipt, and `complete:true`. Tests for these distinctions passed (2/2).
