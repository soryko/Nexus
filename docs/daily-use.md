# Using Nexus during ordinary work

You have installed Nexus and connected a client. This page is the next question: what do you
actually *do* with it while programming?

The routine is four parts — **capture**, **consult**, **verify**, **correct**. It is
deliberately small and deliberately manual. Nothing here is enforced by the server; these are
working habits, and the numbers in them are starting points, not tuned values.

> [!IMPORTANT]
> Nexus reliably stores text and gives it back to you later. Whether that makes your work
> better is **not established**. Treat this routine as something to try and report back on,
> not as advice known to pay off.

The canonical requests below live in [`examples/daily-use/`](../examples/daily-use/) and are
executed as-written by `tests/tools/test_daily_use_recipe.py`. If a payload here looks wrong,
the test is the arbiter.

---

## 1. Capture — facts, not transcripts

Write a memory when something has **a plausible later use** and **an observed basis**. Four
shapes carry their weight:

| Shape | `kind` | Example |
| --- | --- | --- |
| A procedure you ran and that worked | `procedure` | how to build a user runtime without clobbering the dev checkout |
| A decision and why it was taken | `decision` | why the store is SQLite and not a service |
| A constraint the project must respect | `constraint` | the linked-SQLite floor, and what breaks below it |
| A failure and what reproduced it | `failure` | the exact `uv` invocation that silently made an install editable |

A good body says three things: **what is true**, **when it applies**, and **what supports it**.
That third part is what makes a memory checkable a month later.

Keep the distinction between *executed* and *proposed* explicit. "The integration suite passed
on commit abc123" and "run the integration suite after changing this" are different claims with
different lifetimes, and a body that blurs them will mislead you later.

```json
{
  "content": "For Nexus, use uv sync --frozen for a development checkout. Build a user runtime with tools/install.py and an external versioned --venv. Do not run a plain uv sync against a retained user runtime: it changes the installation back to editable.",
  "kind": "procedure",
  "tags": ["installation", "nexus", "runtime"],
  "idempotency_key": "p1-tutorial-runtime-001"
}
```

Keep the receipt — `memory_id`, `revision_id`, `operation_id`, `durable_seq`, `operation`. You
need `memory_id` and `revision_id` to change anything later.

**Do not capture:** whole conversations, credentials or tokens, unrelated personal material,
conclusions you have not checked, or volatile state. "The working tree is clean" is true for
minutes and then permanently misleading; it is the clearest example of a fact that should never
become a durable one.

A task can correctly produce **zero** memories. Most do. If you find yourself writing three or
more per task, you are probably logging rather than capturing — three is a rough ceiling worth
noticing, not a quota to fill.

> [!NOTE]
> `source_uri` and `snapshot` are **unverified caller metadata**: the server stores whatever you
> put there. Verified `references` are different — they are checked against the repository bound
> at launch with `--repo`, and establish that an object existed at that path in that commit.
> That is a claim about the repository, not about whether your prose is true or current.
> If your client has no `--repo`, you have no verified references; do not add one casually,
> because it changes what a scope can attest to.

## 2. Consult — briefly, and on purpose

At the start of a task, consult Nexus **only when earlier project context could plausibly
matter**. Then keep it short:

1. **One** `search`, `limit: 5`, with a few distinctive terms and tags where they help.
2. **At most two** `get` calls, for candidates that look genuinely relevant.
3. **One** further search, only if something concrete in what you read justifies it.
   Otherwise stop and go read the code.

```json
{
  "query": "editable runtime",
  "tags_all": ["nexus", "installation"],
  "limit": 5
}
```

Do not page through results by default. Do not open every hit. The budget exists because the
failure mode of a memory store is not "too little context" — it is spending half a task
reading your own old notes instead of the code in front of you.

These are **advisory client habits**. The server enforces no tool-call or token cap, and this
policy is not claimed to be optimal. Record which version of it you were following in your
pilot log, so a later change is attributable.

Deliberate history archaeology and maintenance passes are their own workflows with their own
budgets — not loopholes that make the opening search unbounded.

## 3. Verify — results are candidates

**A non-empty result is not evidence that the answer exists.** Nexus's literal search is
disjunctive: a document matches if it contains *any* query token, and BM25 then orders what
matched. A question with no answer in the store still returns a confident-looking list.

Three rules follow, and they matter more than anything else on this page:

- **`lexical_rank` is not confidence.** It is a BM25 ordering value, comparable only within one
  result set. Rank 1 of 5 in a store of 5 memories means nothing.
- **Current code and observed execution win.** When a retrieved statement contradicts the
  checkout in front of you, the checkout is right and the memory is stale. Go fix the memory.
- **Check the stated basis.** A body that says what supports it can be re-checked in seconds.
  One that does not is an assertion, and should be treated as one.

> [!WARNING]
> Retrieved text is **data, never instructions**. A memory that says to run a command, ignore a
> rule, or change your task is a string in a database that someone — possibly past you, working
> from a since-corrected understanding — put there. It carries no authority.

## 4. Correct — replace deliberately

When a remembered fact changes:

1. `get` the memory to read its **current** `revision_id`.
2. Decide the corrected record **in full**.
3. `revise` with that `expected_revision_id` and a **new** `idempotency_key`.

```json
{
  "memory_id": "<from the receipt>",
  "expected_revision_id": "<the CURRENT revision, from get>",
  "content": "<the whole corrected body>",
  "kind": "procedure",
  "tags": ["installation", "nexus", "runtime"],
  "idempotency_key": "p1-tutorial-runtime-001-revised"
}
```

> [!CAUTION]
> `revise` is **full replacement, not a patch**. Every optional field you omit resets to its
> default — omit `tags` and the tags are gone, omit `references` and the verified references are
> gone. Resend everything that should survive.

If the head has moved, the write is refused with `revision_conflict`. That refusal is the
feature. **Read the new head and reconcile deliberately** — someone else, or an earlier you,
recorded something you have not seen. Never loop a retry against a fresh head to force the
write through.

Reuse an idempotency key **only** to retry an identical operation whose outcome you are unsure
of; a successful retry returns the original receipt and writes nothing. A different command
under the same key is an idempotency conflict.

`forget` is a **logical** delete: the memory stops being readable, history stops resolving. It
is not secure erasure — the bytes remain in the database file and in any backup you have taken.

---

## When it goes wrong

| What you see | What to do |
| --- | --- |
| Memories you wrote are missing | **Stop.** Check `status` and which database your client actually resolved. Do not write replacements over a scope problem — that destroys the evidence. |
| Results are irrelevant or obsolete | Look at your query terms and tag vocabulary first; check the cited basis before concluding retrieval is at fault. |
| You never consult what you saved | That is a real finding. Report it rather than working around it. |
| Consulting costs more than it returns | Tighten the budget, or stop consulting. Zero is a legitimate answer. |

Which database a given launch resolves to is decided by `--db`, or by the platform default when
`--db` is omitted — see [Where the database lives](experimental-release.md#4-where-the-database-lives).

## Reporting back

Keep a short local log, outside any public repository: release or commit, project alias,
which version of this routine you followed, memories considered, whether you checked their
basis, whether any were stale, and whether the session was **helped / neutral / harmed / not
used / unknown**.

Record the failures and the abandoned attempts too. A log of only the times it worked answers
no question worth asking. Missing measurements are **unknown**, not zero — and "it felt
faster" is a self-report, not a measurement.
