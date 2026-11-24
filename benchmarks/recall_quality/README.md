# Lumen paired recall pilot

Run from the repository root with Node and the project's SQLite-capable Python environment. The adapter requires the installed, pinned package identified by the [route receipt](../../docs/measurements/2026-11-21-recall-route.md). Resolve its package directory in the running profile, then set NEXUS_RECALL_PACKAGE_DIR to it if DSH_PROFILE_DIR is not provided. Do not substitute a checkout copy or install/change a profile to make this run.

    .venv-sqlite/bin/python benchmarks/recall_quality/run.py /tmp/recall-quality-new
    .venv-sqlite/bin/python -m pytest -q tests/dev/test_recall_quality_pilot.py

Use a new output path; the runner refuses to overwrite an earlier trial. A zero exit means the pair collection finished, **not** that the candidate won. Examine summary.json. The pre-run [manifest](manifest.json) SHA-256 is 5d79926a1ace325b7718287bbf54072373c498d176a1884e54d151017e48835f. Changed code, package or inputs require a separately labeled replication; do not update these hashes to make a rerun pass. Original key, policy, caps, runner and scorer were pinned after separate smoke prompts, before the first pilot replay.

## What the receipts measure

Each pair uses one temporary scoped store; cases share only the immutable corpus. The pre-step callback starts fresh. Rank is the one-based position in hit_ids. body_read_ids counts actual automatic real-store gets. offered_ids counts complete body offers after the callback's currency and fitting checks, not every search hit. A reads the plan emitted by the installed handler and verifies its final trace against the real replies. Shape-only discovery replays are not additional searches or scored gets. B replays the same store query and may skip baseline gets; no backfill, reranking, or new bodies are allowed. The tiny fixture bodies fit in full. Normalized evidence packets carry only the offered texts and stable labels.

Explicit search and exact-read control probes are separate from automatic search/read totals. Within-run digests cover full search pages and exact head/historical responses or expected not-found, plus unchanged generation. Runtime identifiers and timings vary between runs; compare stable labels, order and outcomes, not opaque control digests across databases. Call latency is local store execution, not production end-to-end latency. No inference or token usage is observed here.

## Pending answers and cost

No model calls are authorized. If separately approved, give an answer session only **one** entry from blind-packets.json, never the key, receipts, arm, or other answers. A coordinator may match its prompt to the sealed answer key **after** collection and provide the reviewer only the prompt, expected meaning, opaque ID and observed answer. Record a separate arm-blind judgment per ID; never use a default judgment for missing answers. Example input for judge.py:

    {"opaque-id": {"status":"success", "meaning_correct":true, "abstained":false,
                   "format_correct":true, "cited_labels":["r04"]}}

Meaning correctness is human-assessed; the scorer requires the intended action/abstention and checks the cited labels independently against required and actually delivered evidence. Format is separate. Use an empty judgment map to report pending results, or status other than success to mark a failed attempt. Retain actual answers and per-call usage alongside the judgments for audit.

    .venv-sqlite/bin/python benchmarks/recall_quality/judge.py /tmp/recall-quality-new/receipts.json judgments.json
    .venv-sqlite/bin/python benchmarks/recall_quality/token_ledger.py cost-ledger.json

The cost ledger accepts case_ids, calls, and call_ceiling (see its docstring). Supply the entire frozen roster and approved ceiling, including failed calls; unknown usage is not zero. The ledger accounts tokens only: do not infer a cost win without independently verifying safety, paired answers and citations. Neither a C/D run nor an answer-quality result is claimed here. Historical plain-notes handoffs are separate references, not Arm A.
