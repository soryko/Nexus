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

## What the declaration must record — settled 2026-09-12

The registered rule stands: **the existing author may declare the mix.** Independence between
the person who authored the prompts and harness and the person who declares the mix would
strengthen a later evaluation, and it is deliberately *not* made an A1 requirement — adding one
here would be a new prerequisite invented after the harness was built.

What makes that admissible is disclosure rather than independence, so four things are recorded
before any held-out arm runs, and `freeze_corpus.py` refuses a declaration missing any of them:

1. **The frozen corpus digest** — `declared_against_corpus_digest`, which must equal the row
   digest the freeze computes. A mismatch means a memory changed between freezing and
   declaring, which is the one thing this document says declaring may never do.
2. **Each task's category and its supporting memory references** — `memory` (useful /
   unnecessary / outdated) and `relevance` (corpus id → necessary / support / outdated), with
   `discoverability` on every necessary fact.
3. **The declarer's prior exposure** — name, and whether they read the held-out prompts,
   authored them, authored the harness, and read the freeze registration. Stated, not avoided.
4. **Any missing category, as an unmet requirement** — printed `UNMET`, written into the
   freeze record, and left there.

The declaration must leave the corpus and the task selection unchanged, and both halves are
checked rather than promised: the digest binds the corpus, and the declared task keys must be
exactly the registered held-out set — a declaration that adds or drops a task is refused.

That second half was, for a while, a check that could not fail. The expected task list was
read from the declaration's own keys, so "no mix category declared for h3" passed
unconditionally. It reads the registration now.

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
