# Recomputation after review of `cbdf1f3`

Six instrument defects were found in review, all confirmed against the code and the saved
traces. This document records the repairs, what each one changed, and the figures recomputed
**from the saved traces with no model re-invoked**. Where a number here differs from an
earlier report, this one supersedes it.

Two model runs were spent, both limited to what could not be recomputed: validating enforced
isolation, and re-running `d4` with the stale advice **unlabelled**. Both are the same run —
[`run-dev-d4-isolated/`](run-dev-d4-isolated/).

## 1. The repairs

| # | Defect | Repair | What it changed |
| --- | --- | --- | --- |
| 1 | Isolation was requested, not enforced | `sandbox-exec` boundary + fixed forwarder ([`isolation.py`](isolation.py), [`model_forwarder.py`](model_forwarder.py)) | §2 |
| 2 | The stale memory announced itself as stale | evaluator tags stripped from delivered metadata | §3 |
| 3 | Delivered bytes reconstructed from the corpus | measured from responses ([`delivered_context.py`](delivered_context.py)) | §4 |
| 4 | Results paired to the wrong calls | join by `tool_use_id` ([`trace_parse.py`](trace_parse.py)) | §5 |
| 5 | Compliance heuristics accepted non-compliant work | structural checks + one executed test ([`score_compliance.py`](score_compliance.py)) | §6 |
| 6 | Timeout and infrastructure faults never reached scoring | normalised terminal ([`terminal_status.py`](terminal_status.py)) | §7 |

## 2. Isolation is now enforced, and the old control was vacuous

The previous probe set proxy variables and `PIP_NO_INDEX`, then read a failed `pip download`
as proof of blocked egress. **`PIP_NO_INDEX` makes pip fail without opening a socket**, so
that control could pass with the network wide open. Worse, a direct test showed **Claude Code
ignores proxy variables entirely** — the runner itself was never constrained, and the earlier
"positive control" demonstrated nothing about `NO_PROXY`.

Replaced with a kernel boundary. Each arm runs under `sandbox-exec` with all egress denied
and `localhost` re-admitted; the only thing listening there is a forwarder hardcoded to the
model endpoint, with no CONNECT verb and no way to name another host. The held-out checks,
the sibling arms' checkouts, the scoring areas and the source clone are denied `file-read*`.

Measured inside the boundary, on every arm of the isolated run:

| Probe | Result |
| --- | --- |
| `pip download click` (index **enabled**) | fails — `No matching distribution found` |
| `curl https://pypi.org/simple/click/` | fails — `Could not resolve host` (DNS is denied too) |
| read a held-out check file | fails inside, **succeeds outside** |
| the model endpoint | answers |

**Every probe is paired.** The first filesystem probe "passed" with *No such file or
directory* — which is also what an absent path says — so it proved nothing. A boundary is
now only recorded as demonstrated when the same action **succeeds outside the sandbox and
fails inside it**, and both halves are stored with the run.

## 3. The stale memory no longer announces itself

`c13` carried the tag `"outdated"`, and tags are **delivered**: they appear in search hits, in
`get` responses and in the rendered notes file. The first `d4` run therefore tested behaviour
against advice the model had been told was stale.

Evaluator vocabulary now lives only in `relevance`, which is never delivered. Verified in
both delivery paths of the re-run: the string `outdated` appears **nowhere** in either memory
arm's returned content.

The first `d4` run is preserved with the narrower description it earns: *arms declined advice
explicitly labelled outdated*. The re-run tests the intended condition.

## 4. Delivered context, measured rather than reconstructed

The old analyser searched for a memory id anywhere in the concatenated output and then charged
that memory's **entire body** to the arm. Search returns an excerpt. Now `search` hits
contribute excerpt bytes, `get` responses contribute body bytes, and repeat delivery is
counted.

| task | arm | reported before | measured now |
| --- | --- | --- | --- |
| d1 | nexus | 8 memories, 1,828 B | **6 full (1,431 B) + 2 excerpt-only** |
| d3 | nexus | 8 memories, 1,828 B | **6 full (1,368 B) + 2 excerpt-only** |
| d2 | nexus | 6 memories, 1,342 B | 6 full (1,342 B) — unchanged |
| d4 | nexus | 4 memories, 813 B | 4 full (813 B) — unchanged |
| all | notes | 13, 2,776 B | 13 full, 2,776 B — unchanged |

The error was confined to the Nexus arm on `d1` and `d3`, where search surfaced memories the
agent never fetched. Both were overstated by two memories and roughly 400 bytes.

## 5. Result pairing, and how much it mattered

Results were attached to the most recently unresolved call. A model response may request
several tools at once, so two parallel reads could receive each other's output. Replaying the
old rule against the id-joined truth over every saved trace: **20 of 626 calls (3.2%) were
mis-attributed.** Every claim about what a tool returned, and about retrieval-before-edit
ordering, rested on that pairing. Orphan results are now recorded rather than reassigned, and
both parsers share one implementation.

## 6. Compliance: structural checks, one executed test, and an explicit remainder

The review broke the old scorer four ways at once — an arbitrary source edit, `assert True`, a
`status`-only consultation and the reply "NOT DONE" together scored **5/5**. Each heuristic
had confused a proxy for the thing.

The one requirement a machine can settle is now settled by execution: **the arm's own tests
are applied to the pre-fix tree and must fail there.** A test that passes before the fix is
not regression coverage, whatever it asserts. The rest are named for what they are —
`S1_touched_src` is structural and does not claim relevance — and what remains unsettled is
listed in every record under `unsettled_by_machine`.

The review's counterexample now scores **3/6**, with each check rejecting for its own reason.
It is kept as a regression test on the scorer:
[`test_compliance_counterexamples.py`](test_compliance_counterexamples.py).

### Recomputed, from saved traces

| task | arm | terminal | functional | compliance | original rule | amended rule | regression probe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| d1 | baseline | completed | pass | 4/5 | pass | pass | fails pre-fix |
| d1 | nexus | completed | pass | 5/6 | pass | pass | fails pre-fix |
| d1 | notes | completed | pass | **6/6** | pass | pass | fails pre-fix |
| d2 | baseline | completed | pass | 4/5 | pass | pass | fails pre-fix |
| d2 | nexus | completed | pass | **6/6** | pass | pass | fails pre-fix |
| d2 | notes | completed | pass | **6/6** | pass | pass | fails pre-fix |
| d3 | baseline | max_turns | pass | 4/5 | fail (truncated) | pass | fails pre-fix |
| d3 | nexus | max_turns | pass | **3/6** | fail (truncated) | pass | n/a — no test written |
| d3 | notes | max_turns | pass | 5/6 | fail (truncated) | pass | fails pre-fix |
| d4 | baseline | completed | pass | 3/4 | pass | pass | fails pre-fix |
| d4 | nexus | completed | pass | 4/5 | pass | pass | fails pre-fix |
| d4 | notes | completed | pass | 4/5 | pass | pass | fails pre-fix |

Two things the corrected instrument exposes that the old one hid. Every arm that wrote a test
wrote one that genuinely **fails pre-fix** — established by execution now, not inferred from a
regex. And **only three of twelve arms open their reply with `DONE`** as the prompt requires;
the substring test had been matching the word "done" inside prose summaries.

## 7. Termination is normalised before scoring

`invoke()` recorded `forced_verdict="timeout"` and the terminal record ignored it, reading
only the final envelope — which a killed or crashed run never emits. `env_fail` was registered
in §10 and never implemented.

One classification now draws on the forced verdict, envelope presence, `terminal_reason` and
`is_error` together; `env_fail` and `unknown` are **excluded from scored sets** rather than
being scored as failures; and the full evidence is stored beside the classification. No run in
the current set classifies as `env_fail`, so no figure above moves — the gap was in the
instrument, not in these results.

## 8. `d4` re-run: enforced boundary, unlabelled advice

[`run-dev-d4-isolated/`](run-dev-d4-isolated/) — the only new model runs.

| arm | boundary | visibility | terminal | functional | compliance | `c13` delivered | followed it |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | all three hold | — | completed | pass | 4/4 | — | — |
| nexus | all three hold | 13 | completed | pass | 4/5 | **yes, full body, unlabelled** | **no** |
| notes | all three hold | 13 | completed | pass | 4/5 | **yes, full body, unlabelled** | **no** |

All three registered the marker in `pyproject.toml`; none created a `setup.cfg`. The frozen
corpus digest is unchanged across all three arms.

**What this now supports:** both memory arms received stale advice that did not identify
itself as stale, and neither followed it. **What it still does not support:** that these arms
are robust to outdated memory in general. The task keeps resistance easy — `pyproject.toml` is
the only configuration file in the checkout and already carries a `markers` list, while `c13`
names a file that is not there. One attempt per arm, contaminated corpus, no benefit figure.

## 9. Still unmeasured

Whether memory improves coding outcomes. Spontaneous adoption. Any task's difficulty.
Robustness to outdated memory beyond the easy case. Required-evidence completeness — §8's
instrument does not exist, which is why `d4` reports memory coverage as **N/A** and leaves
evidence completeness blank rather than implying it is zero.
