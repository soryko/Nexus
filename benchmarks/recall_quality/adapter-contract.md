# Fixture adapter boundary

Run `node benchmarks/recall_quality/route.mjs` with fixture JSON on stdin:

    {"cases":[{"id":"smoke","prompt":"fixture prompt","responses":[]}]}

Each case returns its ID, ordered calls (name and arguments), injection and candidate_ids. The pinned handler searches with advanced lexical OR (limit 4), then gets at most the first two revision-pinned hits. Responses are raw `{hits:[...]}` and `{memory_id,revision_id,current_revision_id,content,...}`; inspect `complete` to distinguish a finished replay from a probe. Probe with no responses to discover the actual query. Supply fixture search/get responses in call order to replay. No live MCP credentials, network endpoint, or user-memory access is needed. See the adapter and its route receipt for precise envelope types and caps.
