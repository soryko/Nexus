# Review of 2a966c1: five repairs, and the two defects the first integrated run found

The review of `2a966c1` raised seven findings. Five are repaired here. Two more were found
by *running* the repaired configuration rather than by reading it, and both are of the same
kind: a boundary that every model-free control certified, under which no run could have
worked.

No compliance figure changes. All fifteen arm-runs are recomputed; the ratios are identical
and **seven of the fifteen `first_delivery` attributions are corrected**.

## 1. The store was three files, and one was allowed

`boundary_paths` allowed `nexus-dev.db`. `profile_for` emits a file as a `literal`, so
`nexus-dev.db-wal` and `nexus-dev.db-shm` fell to the default deny, and a WAL-mode store
cannot be opened without them.

It fails only while a sidecar exists — which is to say only while something holds the store
open, which is to say only during a run. Checkpointed, the same profile reads it fine. That
is why it survived a validation run in which every control passed.

Neither gate could see it. `memory_visibility()` opened the database with `sqlite3.connect`
from the **harness**, outside the sandbox, where every path is readable. `nexus_server_starts()`
ran `nexus-memory --help`, which returns 0 before `main()` ever constructs a
`SQLiteRepository`.

Reproduced against the repair in one run, same store, same held-open connection, differing
only in the profile — `validate_boundary.prior_store_allow_blocked`:

| store allow | sidecars live | MCP `status` + `search` reached |
| --- | --- | --- |
| `nexus-dev.db` only — the 2a966c1 spelling | `-wal`, `-shm` | **no** |
| `.db`, `-wal`, `-shm` — `isolation.sqlite_read_paths` | `-wal`, `-shm` | **yes** |

The gate is now the arm's own path: `sandbox-exec` → the MCP server → `status` **and** a
`search` that returns hits, with a read-only connection held across the probe so the failing
state is the one under test. `mcp_probe.py` is the client; it is copied into the arm's own
directory because the benchmark directory is denied.

## 2. The store was allowed to every arm

Same line, no arm condition — the baseline and notes arms held file-read on the corpus they
are defined by not having. The run directory is still not allowed as a whole; the three
store files are named for the nexus arm and denied by name to the other two, and the same
MCP probe is now the negative control for those two (`reached=False`, as intended).

## 3. Naming the notes file is not reading it

`P2` credited any call whose **input** mentioned `NOTES-FROM-EARLIER-WORK` and whose result
ran past 200 characters. So

```
ls -la && cat NOTES-FROM-EARLIER-WORK.md 2>/dev/null || echo "NO NOTES FILE"
```

scored as prior-work content delivered — on the strength of a directory listing, in the
**nexus** arm, which has no notes file. The `|| echo` guarantees a zero exit, so the
`is_error` guard never fires.

Delivery is recognised by the notes' own text now, in the result, two substantial lines
minimum. Seven of fifteen attributions move:

| run / arm | was credited to | is credited to |
| --- | --- | --- |
| d1-attempt4 / nexus | `Bash` @3 | `mcp__nexus__search` @5 |
| d2 / nexus | `Bash` @3 | `mcp__nexus__search` @5 |
| d4 / nexus | `Bash` @3 | `mcp__nexus__search` @5 |
| d1-attempt4 / **baseline** | `Bash` @2 | none |
| d2 / **baseline** | `Bash` @2 | none |
| d3 / **baseline** | `Bash` @3 | none |
| d4-isolated / **baseline** | `Bash` @3 | none |

The four baseline rows are the sharper correction: the scorer was recording prior-work
delivery for arms that have no prior work. Their `P2` was `None` either way, so no ratio
moved, but the record claimed something false.

## 4. An undetected edit is unknown, not compliant

`P2` read "no edit detected" as "the delivery cannot have been late" and passed. The one
shape `first_edit_event` documents itself as missing — a write that reaches the deliverable
without spelling its path — was therefore also the shape that scored best.

It is `UNKNOWN` now: counted in neither half of the ratio, absent from `failed`, listed in
`unsettled` and in `unsettled_by_machine` for a reviewer. No arm-run in this corpus is
affected — all fifteen have a located edit — so the change is visible only in the
counterexample suite.

## 5. The forwarder had no positive control

Five refusals were driven over HTTP; the accepted shape was checked by calling
`inspect_body` directly, so `_route` and `_forward` were never exercised. A forwarder that
had come to refuse everything would have recorded `all_refusals_held: True`.

A legitimate request now goes over HTTP to a loopback stub, named to the forwarder through
`A1_FORWARDER_STUB_UPSTREAM` — read once at startup, loopback-only, printed at startup and
recorded in the artifact, and unsettable by an arm, which runs under `sandbox-exec` in a
process started after the forwarder and outside it. The stub reports what arrived:

```
ordinary client-tool request accepted  status=200  reached_upstream=True
                                       upstream_path=/anthropic/v1/messages
                                       tools_forwarded_intact=True
```

## 6. Deny-ordering now has a control (review finding 7)

Every negative control tested a path outside all of the allows, so each would have passed
against a profile that ignored denies entirely. The deny that withholds `~/.claude/projects`
from inside an allowed `~/.claude` — earlier sessions' transcripts, and this repository's
auto-memory — had nothing testing it.

`isolation.deny_ordering_probe` records three results, and the middle one is the reason it
is not simply "deny everything": a denied child inside an allowed subtree is blocked, an
allowed sibling in that same subtree stays readable, and the real `~/.claude/projects` pair
behaves like the synthetic one. It is a listing, not a read: the question is whether the
deny binds, and nothing needs to be copied out to answer it.

## 7. What the smoke test found

`claude --version` and `nexus-memory --help` establish that two processes start. The
integrated smoke test — one model response, one repository read, one edit, one Nexus
retrieval, no task and nothing to score — found two failures on its first two executions
that no model-free control had seen.

**The runner could not open its own scratch root.** Every run died in 0.4 s with
`EPERM: operation not permitted, open '/tmp/claude-501'`, before emitting anything.
`--version` never touches it.

The obvious repair is the dangerous one, and it is measured rather than argued about: that
directory is the parent of the fixtures, the held-out checks, the source clone and every
other session's working directory, and `allow_paths` emits a directory as a **subpath**. So
`profile_for` grew `allow_entries`, which emits a `literal` — the directory entry, openable,
contents still denied. `validate_boundary.scratch_root_entry_only` records both spellings:

| spelling | runner starts | held checks under it |
| --- | --- | --- |
| `(subpath …)` | yes | **readable** |
| `(literal …)` — the entry | yes | denied |

**Then Bash failed on every call.** With the root openable the run completed, the model
answered, `Read` and `Edit` worked, `mcp__nexus__search` returned hits — and all four `Bash`
calls returned `EPERM … open '/private/tmp/claude-501/<slug>'`, one directory further down.
An arm would have looked like an arm that chose not to use Bash. Four of the saved
development runs open with a Bash call.

The slug is the checkout path with `/` replaced by `-`, so `isolation.runner_scratch_for`
names one directory belonging to one arm. `runner_scratch_usable` is a positive control now:
create, write, read back, list.

### The run that passed

```
boundary negative  network_egress_blocked, dns_and_https_blocked, held_checks_unreadable,
                   bench_dir_unreadable, future_path_denied ....................... all True
boundary positive  own_checkout_readable, interpreter_runs, git_runs, runner_starts,
                   runner_scratch_usable .......................................... all True
deny ordering ..................................................................... holds
memory inside the sandbox ......... reached, active=13, hits=6, wal=[-shm, -wal] live
terminal .......................................................................... completed
tool calls ........ mcp__nexus__search, Read, mcp__nexus__get, Bash (no errors)
surfaces .......... model_response, repository_read, repository_edit, nexus_retrieval,
                    nexus_search_returned_hits ................................... all True
usage ............. 5 209 in / 623 out / 9 472 cache read       wall clock 8.4 s
trace health ...... 0 orphan results, 0 unresolved calls
```

Preserved in `smoke-a1/`: the gzipped trace, the terminal record, and the sandbox profile
the run actually used. It is **not an arm-run** — no compliance figure, no functional
verdict, no row in the development matrix — and the record says so in its first field.

## Running the pieces

| what | how |
| --- | --- |
| Nexus suite | `.venv-sqlite/bin/python -m pytest -q` → 333 passed, 2 skipped |
| boundary controls | `python3 validate_boundary.py <scratch> <base-run> <task> <click-python>` |
| smoke test | `smoke_test.py <scratch> <base-run> <task> <out>`; needs `DEEPSEEK_API_KEY` |
| scorer counterexamples | `<click-venv>/bin/python test_compliance_counterexamples.py <click-venv>/bin/python` |
| recompute the figures | `<click-venv>/bin/python recompute.py <click-venv>/bin/python` |
| rebuild the store | `.venv-sqlite/bin/python seed_store.py <db> a1-dev agent` |

The counterexample suite is **not covered by the Nexus suite** and must not be described as
if it were. It is a script with a `main()`, despite the `test_` filename; `pyproject.toml`
sets `testpaths = ["tests"]`, so pytest never reaches this directory, and pointing pytest at
the file collects zero tests and reports "no tests ran". The interpreter is not free choice
either: scoring reads pytest's JUnit report through `xml.etree`, and under this repository's
own `.venv-sqlite` python that import fails with `No module named expat`, aborting inside
`score_compliance._pytest` rather than reporting a scorer defect. The click venv's python
3.14 is what these cases run against.

## Still open before held-out registration

**All four are now closed — see [`results-preparation.md`](results-preparation.md).** They
are left stated here as they were registered, so the record shows what was open at the time
this document was written rather than being rewritten to look complete.

Four items, none of them repaired here, all of them required before the held-out corpus is
frozen:

1. **Seeded ordering across task–attempt pairs.** `random.Random(SEED)` is reconstructed
   from the same constant in every run, so all eight saved runs record the order
   `baseline → nexus → notes`. Randomised arm order is currently a constant, and the
   protocol's repeated-trials clause depends on it not being one.
2. **Instrument errors reported separately from non-compliance.** `_pytest` catches only
   `ET.ParseError`; anything else aborts scoring, and a broader `except` would silently turn
   a parser failure into `E1 = False`. An instrument that could not run is not an arm that
   failed, and the record cannot presently tell them apart.
3. **Per-check verdicts retained in recomputed records.** `recompute.py` keeps `compliance`
   and `failed` but drops `checks`, so `recomputed.json` carries `checks: null` and the new
   `UNKNOWN` verdict cannot be read back without re-running the scorer.
4. **Explicit runtime and path configuration, and a minimal child environment.**
   `PYTEST_PY` and `SOURCE_CLONE` hardcode one session scratchpad; `invoke()` passes
   `dict(os.environ)` to the arm, so the whole host environment crosses the boundary; and
   the `NET` dict carries a comment describing behaviour the code does not have, since
   nothing merges it.
