# DSH Nexus capture pilot — Track 1 benchmark

This bench has three separate evidence levels: **controlled draft judgments**,
**real seeded retrieval**, and **optional fresh-model application**. It is not a
production plugin or evidence of prompt-free draft generation, human approval,
private transport, checkout routing, restart behavior, or organic reuse.

## Run locally

Use the Nexus project virtual environment (Python 3.12+ and the pinned MCP
dependency), Node, and a trusted DSH source checkout with the integration
dependencies already installed (the pure policy module imports Zod). Run from
Nexus root:

```sh
.venv/bin/python -B benchmarks/dsh_capture/run.py
.venv/bin/python -B -m unittest discover -s benchmarks/dsh_capture -v
```

Set `DSH_CHECKOUT` for both commands, or pass `--dsh-checkout` to the runner
(default `~/Repos/deepseek-harness`). A missing or incompatible policy fails
explicitly; there is no silent fallback or copied query implementation.

### Controlled draft judgments

Six invented assignments use Nexus-specific symptoms but deliberately arbitrary
markers. These are not Nexus defaults. Only the fixture key and synthetic prior
evidence contain the assignments. Judge **usefulness** (new, durable, concrete
and reusable under a recognizable trigger) separately from **support** (the cited
committed event establishes the condition, action, and claimed result/reason).
Passing the session/turn/checkout/event-ID checks is not semantic support. The
disputed-value and negated-value controls cite valid IDs and still fail support.
A passing test and routine progress can be factually supported but not useful.
Plan-only, recalled-only, missing, worker, cross-turn, uncommitted and changed
checkout evidence are also exercised. The judgment labels are authored fixture
adjudications, not estimates of a model’s accuracy or of production false-positive
rates. The retrieval seed does not depend on a model draft or a Save route.

To test real draft signal separately, give a fresh, tool-isolated model an
ordinary task plus the corresponding simulated same-turn event (not the curated
body, labels, or answer key). Let the same call solve the task and optionally
propose one brief lesson with source-event IDs. General guidance should ask for
a natural symptom or trigger when accurate, not for a proposal on every turn.
Include the routine controls and do not use an explicit Remember prompt. Have an
assessor blind to the fixture label judge usefulness and entailment separately
from the raw proposal/evidence; record no-proposal, false positive, unsupported
claim, and timing/token deltas versus ordinary task-only calls. This is still a
test-lane exercise, not evidence of private proposal ingress.

### Real seeded retrieval and DSH seam

The runner starts the Nexus checkout’s actual stdio MCP server and SQLite with
an explicit fresh temp DB, synthetic namespace/actor, and `--no-instructions`.
Only six curated seeds are written, only there. It never connects to the profile
MCP binding or starts DSH or a GUI; no live memory, configuration, or artifacts
are modified. Recall-off is a payload ablation with no recalled context,
not a recall-disabled Host turn. Exporting only the off payload does not start
Node or MCP. The paired runner computes the recall-on arm with advanced lexical
search and bounded exact revision gets.
Both arms get the identical symptom-framed user question, without answer,
intended ID, fixture label, or a request to search memory.

The Node subprocess imports only `hooks-core.js` (current checkout policy, not
Host `hooks.js`). It uses `Config.parse`, `recallQuery`, and budget/guard helpers
from that module with the hook settings in the current JSON-formatted
`cordis.patch.yml`; it ignores the live MCP entry. The runner reports source
fingerprints and budgets. The fresh-turn subset of `hooks.js` orchestration is
replayed: same search arguments, revision-pinned reads and bounded candidate
message layout using the actual `contextMessage` wrapper. This intentionally
does not exercise live events, Host capability dispatch, replay, multi-turn
budgets, or profile overrides. If `hooks.js` changes, review the small replay
and tests before attributing results to current behavior. Read errors fail the
benchmark instead of appearing as lexical misses.

Report intended memory rank, exact read, delivery, and answer bytes in context
separately; unrelated candidates are counted even on a miss. `checkpoint` is
an intentionally opaque miss and abstention control, not a grounded paraphrase;
it is never rescued by hand injection. The
initial local run had 5/6 intended IDs delivered (all five at rank 1), 6 positive
draft fixtures ready, and no controlled false positives. JSON reports actual
per-case stdio search/get time and serialized context bytes, plus process-inclusive
batch query/framing time and controlled rubric time. These are local mechanism
measurements, not Host timings or model token estimates.

### Optional fresh-model application (non-production)

No provider is called by the bench. Use an explicitly approved test model/endpoint
with tools, retrieval, memory, project instructions and prior conversation disabled.
Generate the two message payloads (each invocation recreates the disposable DB):

```sh
.venv/bin/python -B benchmarks/dsh_capture/run.py --model-input receipt off
.venv/bin/python -B benchmarks/dsh_capture/run.py --model-input receipt on
```

Send only the `messages` array for each arm, in separate new requests; keep model
and version, temperature, reasoning and max output tokens the same. Do not pass
the case name, files, answer key, full report, previous response or any tools.
For the on arm the only extra message is the context emitted by the automatic
policy replay. Do not add or repair context on a miss. For multiple pairs randomize
arm order; report every case, including absent replies and transport errors.

Record model/settings, exact answers, wall time and provider-reported input/output
usage if available. Score **correct use** only if the response recommends the
exact assignment for the stated symptom (not merely quotes the marker, lists it
as an alternative, or misstates it as independently verified). For off, record
abstention and guessing separately; a coincidentally correct off answer fails
that pair as a mechanism demonstration. Score retrieval from `run.py` separately.
Minimum mechanism evidence: one clean pair where on delivered the intended fact
and applied it and off did not supply it. The default report leaves all model
outcomes and token counts unmeasured. A successful external pair would establish
synthetic model application, not fresh **DSH Host** behavior or the real pilot’s
organic, approved-and-reused requirement.
