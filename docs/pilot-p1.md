# The P1 usage pilot

[Using Nexus during ordinary work](daily-use.md) describes a routine. This page is the
bounded attempt to find out what happens when someone actually follows it.

The question is narrow, and worth stating before the machinery:

> Can useful context be captured in one session, found in a later one, checked, and applied
> — with tolerable effort — and where does that workflow break?

> [!IMPORTANT]
> This pilot **cannot** establish that Nexus makes programming better. It has one
> participant, no control arm, no blinding, and a sample size chosen for patience rather
> than for power. What it can do is show whether the routine is followable, produce
> inspectable examples of reuse or its absence, and surface the friction that a design
> discussion would otherwise have to guess at. A finite negative result — "nothing was
> reused, here is why" — is a real outcome of this pilot, not a failure to run it.

---

## 1. What is being recorded, and by whom

One participant, one client, one project, one unchanged `(database, namespace, actor)`
configuration. A second project may be considered at closeout; adding one mid-pilot changes
what the records mean.

A **session** starts when a real programming task begins or resumes in a new agent
conversation, and ends when work stops or is abandoned. Several sessions can belong to one
task — give those a shared `task_alias` so repeated attempts stay visible instead of
collapsing into one row.

These do **not** count as sessions, and must not be logged as them:

- Installation checks, `nexus-memory-check` runs, and lifecycle smokes. That is setup
  evidence for the host, not evidence about ordinary use.
- Replaying the tutorial in [`examples/daily-use/`](../examples/daily-use/).
- Any work done specifically to produce a pilot row.

The routine says to consult **only when earlier project context could plausibly matter**, so
a session with no search in it is an ordinary and expected row. Do not manufacture a
consultation to make a record look complete.

## 2. Stopping rule

Stop at whichever comes first:

- **ten started sessions**, or
- **fourteen calendar days** after the first session starts — 336 hours, measured in UTC.

At the deadline, unfinished sessions stay unfinished. Do not extend the window to obtain a
later reuse, do not raise the limits, and do not manufacture work to fill them. Ten and
fourteen are practical limits on someone's patience; they were not derived from any power
calculation and no result here should be reported as though they were.

Stopping *early* is allowed and is itself a result: scope confusion, suspected data loss, or
disruption that makes the routine not worth following. Preserve the rows and say why.

## 3. Task outcome is not memory impact

These are recorded as two separate fields, because they come apart in both directions:

| | |
| --- | --- |
| A task that **succeeded** | can still have had memory that was stale, irrelevant, or never consulted |
| A task that **failed or was abandoned** | can still have had memory that was genuinely helpful along the way |

`impact` is a **self-report**. It says what the participant believed at the end of a
session, and nothing more. It is not a measurement, it is not adjudicated by the reporting
tool, and a column of `helped` is not evidence of benefit.

## 4. Private setup

The log lives **outside any checkout**. The repository carries templates, tests, and — only
at closeout, only after review — a sanitized account. It never carries the records.

Run this once, on the host chosen for the pilot:

```bash
pilot_log_dir="$HOME/.local/share/nexus-memory/pilots/p1-first-use"
mkdir -p "$pilot_log_dir/sessions"
test ! -e "$pilot_log_dir/manifest.json" || exit 2
cp examples/pilot/manifest.json "$pilot_log_dir/manifest.json"
```

The `test ! -e` guard is the point of the third line: it refuses rather than overwriting an
existing pilot manifest, which would silently discard the configuration a running pilot's
records are being validated against. Quote every path — the directory may contain spaces,
and an unquoted `$pilot_log_dir` would split into two arguments and create the wrong thing.

Then fill the private manifest from the **client entry that launches the server**.

> [!WARNING]
> Do not derive `database_path`, `namespace`, or `actor` from `status` — it reports none of
> the three. Read them from the client configuration, as
> [the triage table in the routine](daily-use.md#when-it-goes-wrong) describes. And do not
> change `--actor` to label this pilot: the actor **partitions** the store, so relabelling
> it points the client at a different set of memories and the pilot would begin against an
> empty partition.

## 5. The manifest

One file, `manifest.json`, at the root of the private log directory. It records what the
sessions were produced by, so a later change to any of it is visible rather than silently
averaged in.

[`examples/pilot/manifest.json`](../examples/pilot/manifest.json) is the blank. Its nulls are
deliberate: the reporting tool refuses an unfilled manifest rather than summarizing rows
whose provenance is unknown.

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. Bump only alongside a documented format change, which starts a new segment. |
| `pilot_id` | Names this segment. Every session must carry the same value. |
| `routine_revision` | The commit of `docs/daily-use.md` being followed. |
| `service_version` | The installed Nexus version in use. |
| `client_name` / `client_version` | Which client, and which build of it. |
| `project_alias` | A private alias. Not the repository name. |
| `server_command` | The command the client actually launches. |
| `database_path`, `namespace`, `actor` | The effective scope. All three, because all three have to match for a memory to be findable. |
| `started_at` | Set when the **first session starts**, not while the tooling is being built. Aware UTC. |
| `session_limit`, `day_limit` | `10` and `14`. The reporter flags rows beyond them; it does not discard them and does not authorize more. |

## 6. The session record

One file per session, `sessions/s01.json`, `sessions/s02.json`, and so on.
[`examples/pilot/session.json`](../examples/pilot/session.json) is the blank.

**Create the record when the work begins.** That is the whole reason the file exists before
its outcome does: a session that is interrupted, abandoned, or simply forgotten about leaves
an open record behind instead of disappearing. A log assembled only from finished work is a
log of successes.

`started_at` is filled at creation and **stays** the original start even when the rest of
the record is completed hours later.

### Enumerated fields

| Field | Values |
| --- | --- |
| `state` | `open` · `completed` · `abandoned` |
| `consultation` | `attempted` · `not_attempted` · `unknown` |
| `task_outcome` | `completed` · `partial` · `failed` · `abandoned` · `unknown` |
| `impact` | `helped` · `neutral` · `harmed` · `not_used` · `unknown` |

`completed` describes the **record**, not the task and not the benefit: a completed record
may hold a failed task, and `unknown` stays valid in a completed record. Leave a judgement
you have not actually made as `unknown` rather than rounding it to `neutral`.

A search that returned nothing useful is `consultation: attempted`. It is not
`not_attempted` — the attempt happened and its result is the finding.

### Not consulting, and giving up

`not_attempted` needs a reason in `note`, and the honest ones are short: *no earlier context
could plausibly have helped*, *there was nothing in the store about this yet*, or *I forgot
the store existed*. That last one is a finding about the routine, not an embarrassment to be
tidied away — a routine nobody remembers to follow is the most useful thing this pilot could
discover, and it is invisible if the row is quietly dropped or backfilled with a search that
never happened.

Abandoned work is recorded the same way: set `state: abandoned`, set `task_outcome` to what
actually became of the task, and say in `note` what stopped it — interrupted, blocked,
overtaken, or simply not worth finishing. Leave `impact` at `unknown` unless you genuinely
formed a view before stopping.

### Counts and durations

`search_calls`, `get_calls`, and `capture_calls` count **attempted MCP calls the participant
saw**, successful or failed; `capture_calls` counts `record` calls. Do not reconstruct them
afterwards from a database count — that measures what survived, not what was attempted.

`consult_seconds` and `logging_seconds` are **directly measured** nonnegative seconds. An
impression of how long something took goes in `note`, as prose, and never into a numeric
field.

> [!IMPORTANT]
> `null` means **not counted**. `0` means counted, and the count was zero. These are
> different observations and the reporter keeps them apart everywhere: a metric with no
> known values reports `observed_sum: null`, never `0`. Leaving a field null is honest;
> filling it with a zero you did not observe is the one thing that would make this log worse
> than no log.

### Evidence lists

Each of `captured_memories`, `reuse_evidence`, and `friction_codes` has three states, and
the middle one is the one worth being careful about:

- `null` — not tracked, or not yet reviewed. **Unknown.**
- `[]` — tracked or reviewed, and there was nothing. **A real observation of none.**
- a list — the entries below.

`captured_memories` entries are `{"memory_id", "revision_id"}` — the receipt fields.

`reuse_evidence` entries are
`{"memory_id", "revision_id", "capture_session_id", "basis_checked", "action_note"}`:

- `capture_session_id` names the earlier session that captured it, or is `null` for a
  pre-pilot memory whose origin is not established. The reporter checks a supplied link and
  counts a `null` one as **unlinked** — it never infers a source and never drops the entry.
- `basis_checked` is `true` / `false` / `null`: whether the memory's stated basis was
  actually re-checked against the code or execution in front of you.
- `action_note` states the **concrete action** the memory influenced. It must not contain a
  transcript or a memory body.

### Friction codes

`connection` · `scope` · `capture_effort` · `no_relevant_hit` · `irrelevant_hit` ·
`stale_memory` · `verification_effort` · `correction_conflict` · `consultation_overhead` ·
`logging_burden` · `other`

Each code counts at most once per session; the detail goes in `note`. They are **triage
categories, not established root causes** — `no_relevant_hit` records that nothing useful
came back, not that ranking is at fault.

## 7. What a repeated memory ID does not prove

A search hit is not reuse. The same `memory_id` appearing twice is not reuse either. Before
anything is called demonstrated cross-session reuse, a person has to confirm all of:

1. the same memory identity, in **separate** sessions — or explicit pre-pilot provenance;
2. an **applicable** revision, given what the memory actually says and when it applies;
3. a basis that was **checked**, or is honestly marked unknown;
4. a **concrete changed action**, named in `action_note`.

Same-session capture-then-`get`, a bare search hit, and recalling the tutorial are none of
these. Historical or unverified-source reuse is reported separately rather than erased — it
is evidence of something, just not of the thing above.

The reporting tool cannot do any of this. It checks that a claimed link is internally
consistent; the judgement is human.

## 8. Logging that did not happen on time

If a session was worked and never logged, add the row **retrospectively and say so**: leave
every measurement you cannot now reconstruct as `null`, and record in `note` that the row
was reconstructed after the fact. A retrospective row with honest nulls is worth more than a
missing row, and much more than a plausible-looking reconstruction.

Aim to spend **no more than about two minutes** logging a session. That is a usability
target, not a threshold anything is tested against. If logging becomes a burden, record
`logging_burden` and then simplify the format or stop — do **not** add automation mid-pilot,
which would hide the burden that is itself one of the findings. Any format or policy change
ends this segment and begins a separately versioned one; do not aggregate across the break.

## 9. Reading the log

```bash
python3 tools/summarize_pilot.py --log-dir "$pilot_log_dir"
```

The script is offline and dependency-free. It reads only the directory you name, prints one
JSON object on stdout, and exits `2` with a diagnosis on stderr if any input is malformed —
a broken or duplicated record stops the report rather than quietly vanishing from it.

What the summary establishes is **shape and arithmetic**. It reports counts, coverage, and
flagged protocol deviations. It computes no benefit percentage, no ROI, no token or money
saving, no confidence interval, and no recommendation, because none of those follow from
what is in the log. Treat its output as private until it has been reviewed.

Read the raw records alongside it. Passing validation means the rows are well-formed, which
is not the same as their being true.

## 10. Closeout

At the stopping point, inspect the records and the summary together, review each claimed
reuse by hand against §7, and separate the kinds of friction — setup, capture, retrieval
miss, stale content, verification effort, correction conflict, logging burden — before
attributing anything to ranking.

Then choose exactly **one** next action, and name the observations that justify it:

| Observation | Next action |
| --- | --- |
| Concrete later reuse with acceptable burden and no recurring defect | Keep the routine; consider one second-project pilot, separately approved |
| A reproducible correctness or isolation failure | Pause the affected use; fix that defect first, from a minimal reproduction |
| Repeated onboarding or scope confusion | One diagnostic or documentation change, tied to those cases |
| Repeated missed retrieval despite suitable recorded content | Build a small retrieval regression corpus **before** proposing ranking or semantic changes |
| Capture overhead, or little consultation | Reduce the routine, or stop. Do not assume automatic extraction is the answer |
| Unknown outcomes, or too few opportunities | Close as inconclusive, and state what was missing |

These are product decisions, not significance tests. One reproducible correctness failure
can justify acting on its own; a frequency count cannot establish a cause.

A shared closeout goes in `docs/measurements/`, and contains runtime and routine versions,
duration and stop reason, coverage, examples stripped of private content, the limits above,
and the single next action. It must contain no raw session files, memory bodies, private
paths, or real project identifiers. Writing the private summary and publishing a sanitized
one are two separate decisions.
