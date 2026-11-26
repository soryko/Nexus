# A08 lead-review receipt

Working tree on `feat/nexus-memory-v2`, base `b83ab6a4762ffc10d075b31ef246909546ad9138`. No project commits or remote operations. Fixtures use only pytest temporary stores and synthetic Git objects.

## Checks

- Before implementation: `uv run pytest -q tests/retrieval/test_{traversal,packs,cache}.py --tb=line` exited 1. All 11 cases reached the missing retrieval package (1 failed, 10 fixture setup errors); this establishes absent functionality, not individual assertion failures.
- Review red, before traversal edits: `UV_OFFLINE=1 uv run pytest -q tests/retrieval/test_traversal.py -k review --tb=short` exited 1 with three assertion failures. The first-scanned hub beat the sparse branch; two identical support selections displaced the other complete group; withdrawal of only the definition evidence yielded `no_supported_path`.
- Review green: the same focused command exited 0 (3 passed). The fork costs six expansion rows including one hub probe; its three candidate scores are 3, 4 and 4. One charged assertion row yields both distinct support groups without a spurious cutoff. Repeats compare the entire pack except its intentionally fresh opaque ID. With file evidence withdrawn, the three-hop semantic bundle retains exactly its three sources (4 semantic reads, 1 proof read, 0 attached edges). A separate missing-motivation case emits no bundle and attempts no attachment.
- Final named gate: `UV_OFFLINE=1 uv run pytest -q tests/retrieval/test_traversal.py tests/retrieval/test_packs.py tests/retrieval/test_cache.py tests/core/test_search_core.py tests/atlas/test_queries.py` exited 0: **80 passed in 5.25s**.
- Adjacent: `UV_OFFLINE=1 uv run pytest -q tests/graph tests/spaces/test_retrieval_contract_edges.py` exited 0: **40 passed in 1.96s**. `git diff --check` exited 0. Runtime: uv 0.11.21, Python 3.13.14, linked SQLite 3.53.1.

The release fixture checks three directed semantic hops plus the separately counted file attachment, four exact sources, no cross-partition sentinel, and loss of rationale after withdrawal. High-degree tests use 500 genuine outgoing assertions plus deliberately inconsistent adjacency rows: 16 rejected rows and one probe on one seed plus two accepted alternatives on the other cost 19 expansion rows. A two-row allowance still reaches both seeds. The conflict tests distinguish a proved dispute from incomplete coverage at 128 of 129 proposed controls. Cache tests cover changed memory/graph sequences, trusted epoch, missing partition, restart and expiry. Output assertions measure the final JSON, not the sum of its citations.

## Boundaries for review

- Local single-partition service, not a transport or authority ingress. The caller must supply trusted current catalog context under the publication fence. Native per-turn allowances, publication charging and route admission are not exercised. Cache matching is deliberately strict, including host time.
- A cutoff without room to check for another row may conservatively return incomplete. Historical lexical discovery has no index in this provider. Conflict coverage describes recorded explicit controls only.
- Ranking is deterministic Stage A seed order, not RRF. Each completed expansion contributes its capped matching adjacency count, including rejected candidates but not the over-cap probe, to every child. If the global row allowance or deadline interrupts the scan before the degree is known, ranking conservatively charges the cap and marks the result incomplete; no extra census runs. Non-exact pruning compares ordered directed revision paths with their per-edge premise sets before spending alternative slots; exact traversal bypasses this pruning. File attachment selects the first eligible proof in deterministic order; this is not enumeration of every possible file proof.
- These gates do not establish latency guarantees, multi-space or live-store behavior, installed package compatibility or full-suite release readiness.
