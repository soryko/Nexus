# A1 capture policy (**draft**, not registered)

Step 2 of [`protocol-a1.md`](protocol-a1.md) §6. This is the fixed, written policy that
decides **what a prior session records**, applied before any later task is revealed. It is an
input to the measurement and is hashed with the corpus it produces.

## 1. What a prior session may record

Only what was **observable from the repository at or before the task's pinned pre-fix
commit**, or **produced by the prior session's own work** — a command that was actually run,
an error that was actually seen.

Four kinds, matching the store's own vocabulary:

| Kind | Records | Test before recording |
| --- | --- | --- |
| `decision` | A choice already made in the codebase and its reason | Could a reader reach this by reading the code at the pre-fix commit? |
| `constraint` | An invariant or convention the code holds to | Is it stated or evident in code, docs or tests at that commit? |
| `procedure` | How to do something in this repository | Did the prior session actually do it this way? |
| `failure` | Something that went wrong, and what it turned out to be | Was it observed, not anticipated? |

`observation` is available but is the residual kind. A memory that fits one of the four above
is recorded as that.

## 2. What a prior session must not record

The bright line, and the reason this document exists:

1. **The later fix**, in any form — its diff, its location, the line it changes, or a
   description of the defect specific enough to substitute for finding it.
2. **The hidden acceptance checks**: no test name, no assertion, no expected output.
3. **Anything dated after the pre-fix commit.** A convention introduced by the fix itself is
   not prior knowledge, however true it is now.
4. **The task statement**, or anything that reads as an instruction for it.

The test that separates a legitimate constraint from a leak is whether it names the
**convention** or the **violation**. "Envvar resolution treats an empty string as absent" is a
convention, discoverable at the pre-fix commit, and is recordable. "`Option` forgets that
check on the auto-envvar path" *is* the defect, and is not.

## 3. Order, which is the part that cannot be reordered

1. The prior session works, seeing only the repository at or before the pre-fix commit.
2. It records memories under this policy. **The later task is not revealed yet.**
3. The corpus is frozen and hashed.
4. Only then is the later task revealed and the arms run.

A memory written after step 3 is not captured, it is seeded, and any corpus that admits one
is void for that task.

## 4. Rendering for arm 3

Arm 3 receives the **same information** as arm 2, as one readable Markdown file in the
checkout. Same facts, same wording, same order. Rendering is mechanical — one heading per
kind, one bullet per memory — so that no editorial improvement can enter one arm and not the
other. `protocol-a1` §2 requires the two to differ only in *access mechanism*; a difference in
substance answers Q2 by construction.

## 5. The development corpus is authored under declared exposure

[`corpus-dev-a1.json`](corpus-dev-a1.json) was written **by an author who had already read
the fixes**, because selecting the tasks required reading them. Its memories were filtered
through §2 deliberately rather than produced in ignorance.

This is a real exposure and it is declared rather than mitigated: it is exactly the
contamination §3's ordering exists to prevent, and it can only be *claimed* to have been
avoided here, not demonstrated. Consequences, binding:

- The development corpus is for **harness validation only**. It shows the plumbing works:
  memories load, the agent can reach them, arms differ in the intended way, scoring runs.
- **No benefit figure may be computed from it.** A corpus written by someone who knew the
  answers cannot measure whether memory helps, whatever the numbers say.
- **The held-out corpus must be captured differently**: by a prior-session agent run under
  §3's ordering, which never sees the later task. That run is itself part of the measurement
  and is recorded with it.

## 6. Coverage, declared before the run

The corpus is deliberately not uniform across the development tasks. It carries:

- memories that **should** help a task (a convention the task depends on);
- memories that are **useful support** — true and relevant, not required;
- memories that are **irrelevant** to every development task, so that retrieval has something
  to wrongly return and delivered-context figures have a denominator;
- at least one memory that is **outdated** — true when captured, superseded since — so the
  outdated category in `protocol-a1` §7 has an instance rather than a definition.

Which memory falls in which class **for a given task** is declared in the corpus itself, not
inferred afterwards from what retrieval happened to return.
