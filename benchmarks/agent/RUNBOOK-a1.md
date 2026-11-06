# A1 held-out execution runbook

The order below is the one `capture-policy-a1` §3 fixes and cannot be reordered. Step 3 is the
hinge: **a memory written after it is seeded, not captured, and voids the corpus.**

Prerequisites, all verified 2026-09-12 except the key:

    export DEEPSEEK_API_KEY=...        # the only thing not already in place
    PYC=/Users/soko/Cerebros/nexus-a1-fixtures/.venv-click/bin/python
    SP=<a scratch directory>           # durable enough to outlive 38 model runs

    python3 benchmarks/agent/a1_config.py          # configuration preflight
    "$PYC" benchmarks/agent/verify_preparation.py "$PYC"    # 6 sections, all must hold

## 0. The forwarder

Every arm reaches the model endpoint through it and nothing else; the sandbox denies all other
egress. Start it before any run and leave it up.

    python3 benchmarks/agent/model_forwarder.py &

## 1–2. Capture

The session sees `c1` then `c2` at their pre-fix commits, records under the policy carried in
its own prompt, and never learns the held-out tasks exist. `benchmarks/agent/` is denied to it,
which is where the registration, the task sheet and this file live.

    python3 benchmarks/agent/run_capture.py "$SP"

Builds both fixtures (three controls each), proves the boundary, creates an empty store, runs
the two tasks in order, and writes `$SP/capture/capture-records.json`. Two model runs.

## 3. Freeze — and only then reveal the held-out tasks

Rehearse first; it writes nothing:

    python3 benchmarks/agent/freeze_corpus.py "$SP" --dry-run

Then declare the mix. Copy `mix-heldout-a1.example.json` to `mix-heldout-a1.json` and fill it
from the frozen corpus, under the rules settled in [`mix-declaration-a1.md`](mix-declaration-a1.md):
declaring is not selecting, and **no memory may change**. Then:

    python3 benchmarks/agent/freeze_corpus.py "$SP" --mix benchmarks/agent/mix-heldout-a1.json

It exports the corpus, digests the rows, copies the store to a durable master, renders arm 3's
notes, validates the mix against `protocol-a1` §7 and §7b, and prints the four values to set in
`a1-config.json`: `store_master`, `corpus_digest`, `corpus_size`, `notes_file`. Set all four.
A missing mix category is printed `UNMET` and written into the freeze record — that is the
reported result, not something to repair by capturing again.

Commit `corpus-heldout-a1.json`, `notes-heldout-a1.md`, `freeze-record-heldout-a1.json` and
`mix-heldout-a1.json` before running an arm. They are the registration.

## 4. The 36 arm-runs

Build the held-out fixtures once:

    "$PYC" benchmarks/agent/build_fixture.py \
        /Users/soko/Cerebros/nexus-a1-fixtures/click \
        benchmarks/agent/tasks-heldout-a1.json "$SP/run-<task>/base"

then, for each of the 12 rows of `schedule-heldout-a1.json` (4 tasks × 3 attempts):

    python3 benchmarks/agent/run_arms_isolated.py "$SP" <task> <attempt> \
        benchmarks/agent/schedule-heldout-a1.json

The arm order is read from the schedule, never derived. Each arm-run gets a private copy of the
master store, and `run-<task>/attempt<n>/` holds everything that attempt produces — nothing is
overwritten by the next one. The runner aborts before spending if the master's digest is not
the registered one, if the boundary is not demonstrated, or if the corpus is not reachable from
inside the sandbox.

Preserve full output. Redirect each run to a file rather than piping it through `tail`: the
initialization defect diagnosed on 2026-09-12 sat unread in `artifacts/diagnostics/` for
fourteen reruns because the suite output was being truncated to its last line.

## 5. Reporting

`recompute.py` and `delivered_context.py` both take a run directory, which is now
`run-<task>/attempt<n>/`. Report per task and per arm, never collapsed
(`freeze-heldout-a1.md` §4), with `total_cost_usd` omitted and `num_turns` unreported.
