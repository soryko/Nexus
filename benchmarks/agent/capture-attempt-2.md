# Capture attempt 2 — 2026-09-12 — zero memories, and the ceiling was the wrong lever

The second execution of `run_capture.py`, after the repairs in
[`capture-attempt-1.md`](capture-attempt-1.md): `--repo` bound so a reference is storable, and
capture-only ceilings of 60 turns / 1200 s. Preserved whole at
`nexus-a1-fixtures/capture-attempt-2/`.

It produced **no memories at all**. The store's final digest, `4f53cda18c2baa0c`, is the digest
of an empty store.

## Both attempts side by side

| | calls | tool errors | first source edit | memory calls | memories | task outcome |
| --- | --- | --- | --- | --- | --- | --- |
| a1 `c1` | 41 | 3 | never | 0 | 0 | 0-byte patch, unchanged |
| a1 `c2` | 30 | 6 | never | 3 | 1 | 79-byte patch, unchanged |
| a2 `c1` | 64 | 5 | call **55** of 64 | **0** | 0 | **fixed** — 537 passed |
| a2 `c2` | 68 | 4 | call 33 of 68 | **0** | 0 | 2 failed / 582 passed (was 7 failed) |

All four runs ended at `max_turns`.

## What the extra turns actually bought

They bought engineering, and they cost recording.

`c1` solved its task for the first time. Its patch is two lines in `Option.__init__` —
`self._flag_needs_value = flag_value is not UNSET or self.default is UNSET` — and every
acceptance check passes. `c2` went from a 79-byte patch to 7 368 bytes and from 7 failing
checks to 2.

And memory calls went from 3 to **zero**. Doubling the ceiling did not give the session room
to record; it gave the session room to keep exploring, and it explored. `c1` made its first
and only edit at call 55 of 64: forty-nine `Bash` calls of investigation, one edit, nine calls
of verification, ceiling. There was never a turn in which the task was finished and budget
remained.

The inversion is the finding. **Attempt 1's `c2` recorded precisely because it was not making
progress on the task** — it never edited anything, and recorded three times. Given a budget it
could spend on the task, the same model spends all of it there. Recording is not competing
with the ceiling; it is competing with the task, and losing.

So the ceiling was the wrong lever, and raising it further would be the same lever again.

## What the evidence says the lever is

The capture prompt is assembled as task body + tail + capture instruction, and the tail ends
`When you are done, reply DONE.` The recording instructions therefore arrive **after** the
sentence that defines done. Nothing in the prompt requires a memory to exist before the work
is finished; "record as you work rather than all at the end" is advice, placed last, competing
with an explicit completion condition.

The evaluation arms have exactly the anchor that capture lacks. Their `consult` instruction
opens: *"Before your first source edit, consult any available prior-work memory."* It is
positional, it is checkable, and `score_compliance` reads compliance with it off the trace.

The symmetric fix for capture is one sentence of the same shape — *before your first source
edit, record what you have learned about this codebase* — placed before the completion
condition rather than after it. It targets the mechanism the traces show, rather than
enlarging the budget that the traces show is not the constraint.

That is a **proposal**, not a change made here. It has not been run.

## What is not proposed

- **Not more turns.** Two ceilings have been tried, 30 and 60, and the second produced fewer
  memories than the first.
- **Not an easier environment.** The reasoning in `capture-attempt-1.md` stands: a capture
  session records procedures about this repository, and the arms meet the environment as it
  is. Tool errors also fell from 9 to 4 between attempts without any change to the
  environment, so friction is not what is suppressing recording.
- **Not a different model, task set, schedule or corpus source.** None of those is implicated
  by anything measured.

## The standing rule

`capture-attempt-1.md` says a thin corpus is reported rather than re-rolled indefinitely, and
that the number of attempts is itself part of the A1 result. Two attempts and four model runs
are now on the record, with their traces. A third requires a reason drawn from evidence — this
document is the reason offered for one, and if it is taken and also fails, that is reported
too, not followed by a fourth on the same grounds.
