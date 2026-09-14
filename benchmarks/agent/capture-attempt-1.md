# Capture attempt 1 — 2026-09-12 — one memory, both tasks unsolved

The first execution of `run_capture.py`. It is recorded here rather than discarded, because
`mix-declaration-a1.md` says the one thing a program cannot prevent is someone capturing
again, and the one thing it can do is make the second corpus visibly a second corpus. This is
what makes attempt 2 the second.

Preserved in full at `nexus-a1-fixtures/capture-attempt-1/`: both traces, both patches, both
sandbox profiles, the MCP configs, the store, the forwarder log and `capture-records.json`.
Nothing was deleted.

## What it produced

| | c1 | c2 |
| --- | --- | --- |
| terminal | `max_turns` | `max_turns` |
| wall clock | 280.2 s | 102.8 s |
| patch | **0 bytes** | 79 bytes |
| hidden checks | 2 failed, 535 passed (unchanged) | 7 failed, 577 passed (unchanged) |
| tool calls | 41, all `Bash` | 30 |
| memory calls | **none** | `record` ×2, `status` ×1 |
| memories after | 0 | 1 |

Final corpus: **one memory**, row digest `2963ae7d07a1fe7d`. Neither task was fixed. Both runs
completed and exited 0 — the harness did not fail; the sessions ran out of turns.

The single memory is a `decision` about flag options with `default=True` taking their
`flag_value` as the effective default. It is admissible under the policy — a convention
already stated in `Option.__init__` and in the 8.3.0 changelog entry — and it is not a leak.
It is also not a corpus.

## Why, with the evidence

**1. The harness refused the first `record`.** The session cited `src/click/core.py` and
`CHANGES.rst` for the convention it had just read. The server answered
`verification_unavailable: reference verification is unavailable in this mode`
(`memory/service.py`), because `run_capture.py` built the MCP config without `--repo` and the
service requires a binding and a verifier for any reference. The session re-recorded the same
fact with the citation stripped. The capture policy asks whether a fact is "stated or evident
in the code"; a harness that refuses to store *where* answers that question with a shrug.

**2. Environment friction consumed the turn budget.** Observed in the traces:
`command not found: python`; `ModuleNotFoundError: No module named 'click'` (a `src/` layout
with no `PYTHONPATH`); and `can't create temp file for here document: operation not
permitted`, which makes `<<EOF` — the obvious way to write a reproduction script — unusable.
Nine tool errors across the two capture runs, against **zero** across the three development
arm-runs checked (`run-dev-a1-attempt4`, `run-dev-d2`, `run-dev-d4-isolated`).

**3. The ceiling was calibrated on a different job.** `--max-turns 30` is registered in
`freeze-heldout-a1` §3 under *arms, trials and ceilings*, against runs that had only to fix a
bug. A capture run fixes the bug **and** records as it goes, under a prompt roughly three
times as long. Both tasks hit 30.

## What changed for attempt 2, and what deliberately did not

**Changed — the harness bug.** `--repo` now binds the arm's own checkout in the capture MCP
config, so a reference is storable.

**Changed — capture's ceilings, and only capture's.** `capture_max_turns` (60) and
`capture_wall_clock_s` (1200) are separate configuration fields. The arms' `max_turns` 30 and
`wall_clock_s` 600 are registered and are untouched; no arm reads the capture fields. This is
decided before any held-out arm has run and with no held-out result in existence.

**Deliberately NOT changed — the environment.** The friction in cause 2 is real and it would
have been easy to remove: put the interpreter on `PATH`, set `PYTHONPATH=src`, open a writable
temp directory. That was rejected. A capture session records `procedure` memories — "how
something is done in this repository" — and the evaluation arms face the environment as it
stands. A capture run given an easier environment writes procedures that are false for every
arm that later reads them, which converts a harness convenience into a corpus defect. Capture
gets more turns to work through the same friction, not less friction.

**Deliberately NOT changed — the task set, the prompts, the schedule, the arms.** Nothing in
the registration moves because a capture run went badly.

## If attempt 2 also yields too little

It is reported, not re-rolled indefinitely. A corpus that cannot supply `protocol-a1` §7's
three categories is an unmet mix requirement, and `freeze_corpus.py` prints it `UNMET` and
writes it into the freeze record whether or not anyone likes the number. Each further attempt
gets its own document beside this one, and the count of attempts is itself part of what the
A1 result reports.
