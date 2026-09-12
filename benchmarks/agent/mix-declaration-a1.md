# Declaring the held-out task–corpus mix — settled before the corpus exists

Addendum to [`freeze-heldout-a1.md`](freeze-heldout-a1.md) §2, written **before** the capture
run and therefore before any memory it governs exists. It does not change that registration;
it answers a question that registration leaves open and that cannot be answered honestly once
the answer would be convenient.

## The tension

Two rules in `freeze-heldout-a1` §2 pull against each other.

> The task mix … is fixed when the corpus is frozen at step 3 — declared before any arm runs,
> and after the memories exist.

> No human who has read this document may author, edit or select a memory in the held-out
> corpus.

Declaring the mix means saying, for each of `h1`–`h4`, whether captured memory is **useful**,
**unnecessary** or **outdated** for it, and labelling each necessary fact **discoverable** or
**absent** (`protocol-a1` §7 and §7b). Doing that requires reading `h1`–`h4`. Everyone who can
read `h1`–`h4` has read the registration that names them. So the only people who can declare
the mix are the people barred from touching the corpus — and if declaring counts as touching
it, the mix can never be declared by anyone.

## What is settled

**Declaring is not selecting, provided the corpus is invariant across the declaration.** The
bar in §2 protects the corpus from being shaped to the evaluation. A declaration that leaves
every memory exactly as captured shapes nothing; it reads the corpus and the tasks and writes
down a relationship that was already true when the capture run ended.

Concretely, the declarer **may**:

- read `h1`–`h4`, the freeze registration, and the frozen corpus;
- assign each held-out task one of the three mix categories;
- assign each memory a per-task relevance, and each necessary fact a discoverability label;
- record that a category has **no** instance.

The declarer **may not**, and no one else may either:

- add, edit, remove, re-word, re-tag or re-kind any memory, or change which memories are in
  the corpus — including by deleting one that turns out to be inconvenient;
- run the capture harness again, with or without the held-out tasks in view;
- change the held-out task set, its order, or the schedule;
- change the declaration after any arm-run has produced a result.

## What makes that checkable rather than merely promised

Three mechanical facts, all produced by `freeze_corpus.py` at the moment of freezing:

1. **The corpus is digested before the declaration is validated.** `corpus_digest` is the row
   digest of the frozen store. It goes into `a1-config.json`, and `run_arms_isolated.py`
   refuses to start against a master whose digest differs. A memory edited to suit the
   declaration changes that digest and stops every subsequent arm-run.
2. **The declaration is hashed into the freeze record.** `mix_declaration_sha256` fixes the
   declaration's bytes at freeze time, so a declaration altered after a result can be told
   from one that was not.
3. **The ordering is recorded.** The freeze record carries `frozen_utc`; each arm-run records
   its own start. A declaration is only admissible if it precedes every arm-run, and both
   timestamps are in the artifacts rather than in anyone's account of events.

None of the three prevents a determined author from re-capturing and re-declaring from
scratch. They establish that *this* corpus and *this* declaration are the ones the results
were produced under, which is the claim the evaluation actually needs.

## A missing category is a result

`freeze-heldout-a1` §2 already says it and it is repeated here because it is the rule most
likely to be quietly broken under time pressure:

> **If the captured corpus yields no outdated instance for any held-out task, that is reported
> as an unmet mix requirement.** It is not repaired by editing a memory afterwards, by
> re-selecting tasks, or by capturing again with the held-out tasks in view.

`freeze_corpus.py` enforces the reporting half: a category with no instance, a task with no
declaration, or a necessary fact with no discoverability label is printed as `UNMET`, written
into the freeze record, and left there. It also reports — as a note, not a failure — when
every necessary fact carries the same discoverability label, because §7b says such a set
measures retrieval efficiency or retrieval necessity but not both.

The script cannot enforce the other half. Nothing in a program can stop a person capturing
again. What it can do is make the second corpus visibly a second corpus, and that it does.

Settled 2026-09-12, before the capture run.
