# A06 lead-review receipt

Base: `44ee5c97ce06a1c173682a137a1725cb3548a648`, isolated `feat/nexus-memory-v2`. Uncommitted. No live catalog/store or model service used.

## Verification on disposable fixtures

- Before implementation: `uv run pytest -q tests/graph/test_mutations.py tests/graph/test_resolution.py tests/projections/test_jobs.py --tb=line`: exit 1, eight named tests failed at the missing graph implementation. An earlier fixture import error was fixed before counting this red.
- Final named gate: `uv run pytest -q tests/graph/test_mutations.py tests/graph/test_resolution.py tests/projections/test_jobs.py tests/core/test_memory_core.py`: exit 0, **45 passed in 6.50s**.
- Prior space contracts: `uv run pytest -q tests/spaces`: exit 0, **75 passed in 3.41s**.
- The named gate exercises partition-prefixed FK/index metadata, schema-4/5 refusal of schema 6, concurrent correction CAS, pinned read visibility, invalid endpoint/source rollback, exact supersession, resolution CAS, lease replacement and bounded retries.

## Review boundaries

Migration 006 runs only under explicit `owner_schema6=True`; legacy and explicit schema-5 targets remain unchanged. Graph reads use a separate mode=ro connection and never initialize profiles. Writers check the complete catalog partition identity inside each transaction. Historical revision adjacency is retained alongside active flags. Resolution changes do not rewrite entity IDs or assertion endpoints.

Queue completion records a digest; it does not invoke workers. Ready publication requires a trusted transactional validator. Git span capture and historical eligibility belong to A07; background generation rebuild and capture-worker integration are later-stage tasks. No installed-wheel, cross-platform or full-suite certification is claimed.
