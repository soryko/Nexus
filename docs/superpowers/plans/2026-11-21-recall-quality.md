# Nexus recall: prove the improvement before changing the default

Status: installed handler identified and replayed in isolation ([route receipt](../../measurements/2026-11-21-recall-route.md)). The [first 20-case run](../../measurements/2026-11-21-recall-quality/summary.json) completed. Keep baseline A: B reduced irrelevant reads from 15 to 7 but dropped required failure receipt r05 on c19. No paid query/answer evaluation or rollout. The decision is whether automatic recall can read fewer irrelevant records or use fewer tokens without losing evidence. It is not whether receipts are generally less valuable than decisions.

One broad request read an unrelated test receipt. In a separate four-record [handoff trial](../../measurements/launch-handoff-run.json), both arms answered correctly; plain notes used 219 tokens and Nexus used 279, including a [119-token query call](../../measurements/launch-handoff-query.json). Neither observation measures the deployed route's typical cost or error rate.

## Delivery order

| Stage | Work | Exit evidence |
|---|---|---|
| 1. Identify the route | From the running DSH profile, resolve the loaded package, entry point, version/hash, prompt handler, query builder, search call, and component that fetches bodies. Trace one synthetic prompt through that artifact in isolation. Compare with the [Codex adapter](../../../src/nexus_memory/transport/codex.py); do not assume they match. | Redacted path/version receipt plus correlated query, offers and reads. If isolation or route identity cannot be verified, stop; label any local reproduction as such. |
| 2. Freeze the pilot | Build a disposable corpus and label the 20 cases in the [evaluation spec](../specs/2026-11-21-recall-evaluation.md). Pin the protocol, answer key, extractor and gate before viewing results. | Manifest and digest, model and budget, executable offline scorer with positive, abstention and wrong-citation tests. |
| 3. Replay locally | Run the unchanged route first, then vary only admission of automatic reads. Replay explicit searches unchanged. | Paired per-case receipts, including misses, suppressed useful receipts and isolation checks. No live store access or profile edits. |
| 4. Compare query cost | Hold admission fixed; compare deterministic terms with the prior model-query method. Run answer evaluation only after the offline checks pass and a model-call budget is approved. | End-to-end costs, paired answer/citation outcomes, transport failures separately identified. |
| 5. Decide | Apply the predeclared gates. Publish cases that got worse as well as totals. | Stop on tie or regression; a win justifies a second held-out run, not a default change. Any later opt-in trial must retain a switch back to the original path. |

Proposed outputs: fixture, runner and scorer under the benchmark tree; dated route receipt, frozen manifest and results under the measurements directory. Preserve the earlier receipts as historical evidence. No graph, schema migration, capture policy, provider installation or rollout in this plan.

**Independent option:** [compare a verified Git reference with HEAD](../specs/2026-11-21-git-reference-at-head.md). This is a separate read-only feature proposal, not a dependency or an outcome of the recall experiment.
