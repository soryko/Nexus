# A1 development run — `d4`, the outdated-memory task

> **Figures here are superseded where they concern delivered context, task-requirement compliance, or termination.** Six instrument defects were found in review of `cbdf1f3`; the repairs and the recomputed figures are in [`results-recomputed.md`](results-recomputed.md). This document is kept as the record of what was reported at the time.

> **Superseded in one respect beyond the recomputation:** `c13` carried the tag `outdated`, which is delivered metadata, so this run tested arms declining advice *explicitly labelled* stale. The unlabelled condition is [`results-recomputed.md`](results-recomputed.md) §8.

**Harness validation only.** `d4` closes the gap [`results-dev-d2-d3.md`](results-dev-d2-d3.md)
§3 identified: the corpus carried an outdated memory, but no task gave it an opportunity to
mislead. Artifacts: [`run-dev-d4/`](run-dev-d4/).

## 1. What makes it an outdated-memory task

`c13` says *"Test configuration lives in setup.cfg; add pytest settings to its
`[tool:pytest]` section."* In this checkout that is false, and following it fails **silently**
unless something forces the error:

- there is no `setup.cfg`;
- `pyproject.toml` owns `[tool.pytest.ini_options]`, so pytest never reads a `setup.cfg` even
  if one is created;
- that table sets `filterwarnings = ["error"]`, which turns an unregistered marker from a
  warning into a **collection error**.

So a marker registered per `c13` is ignored and the failure is observable, while the
checkout's own `pyproject.toml` — with a `markers` list already in it — is visible evidence
for the correct action. The task asks for a marker to be registered "in this project's pytest
configuration" and **never names the file**.

`d4` is **authored**, not an upstream fix commit. It has no pinned fix and no upstream test.
That is a real difference from `d1`–`d3` and is recorded rather than smoothed over.

## 2. Three model-free controls, all required to behave

Built and verified by [`build_d4.py`](build_d4.py) before any arm ran:

| Control | Requirement | Result |
| --- | --- | --- |
| **no-model** — unpatched tree | must **fail** (else vacuous) | fail — `1 error` |
| **stale-advice** — `c13`'s action applied (`setup.cfg` written) | must **fail** (else the memory cannot mislead) | **fail — `1 error`** |
| **correct-fix** — marker added to `pyproject.toml` | must **pass** (else unsatisfiable) | pass — `1 passed` |

The middle row is the one that makes `d4` worth having. It is not asserted that following the
stale advice is wrong; it is executed, and it fails.

## 3. Exposure was validated, not assumed

`protocol-a1` §11.5 requires the intended exposure condition to be checked rather than
presumed. For `d4` that means: did the stale advice actually reach the memory arms?

| arm | `c13` delivered | by what route |
| --- | --- | --- |
| baseline | no | no memory available |
| **nexus** | **yes** | retrieval returned `c07`, `c08`, `c12`, **`c13`** |
| **notes** | **yes** | whole file on first read |

This is the **first time retrieval surfaced `c13`** — it did not on `d1`, `d2` or `d3`, where
nothing in the task made it relevant. The exposure condition holds.

## 4. Outcome

| arm | functional | compliance | termination | delivered | outdated delivered | necessary coverage |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | pass | 5/5 | completed | 0/13 | — | **N/A** |
| nexus | pass | **6/6** | completed | 4/13, 813 B | **c13** | **N/A** |
| notes | pass | **6/6** | completed | 13/13, 2,776 B | **c13** | **N/A** |

All three registered `integration` in `pyproject.toml`. **None created a `setup.cfg`.** Both
memory arms were handed the stale advice and neither followed it.

**`d4` declares no necessary *memory*. It does not have zero required evidence, and the two
must not be conflated.** The correct action rests on a fact the agent must obtain — that this
project's pytest configuration lives in `pyproject.toml`'s `[tool.pytest.ini_options]` — and
that fact is necessary evidence. What is zero is the count of *memories* declared necessary,
because the repository supplies the evidence instead of the store.

So two figures, kept apart:

| Figure | `d4` |
| --- | --- |
| **Necessary-memory coverage** (how much of what the store had to supply arrived) | **N/A** — the store was required to supply nothing |
| **Required-evidence completeness** (whether the agent obtained the facts the task needs) | **not N/A** — one necessary fact, repository-discoverable |

`protocol-a1` §9's N/A rule applies to the first and not the second, and §7's warning that
"memory-unnecessary" is a statement about the *store* and never about the task's evidence
demands is exactly this case. The second figure is **not yet implemented** — §8's
required-evidence instrument does not exist — so `d4` reports memory coverage as N/A and
leaves evidence completeness unmeasured rather than implying it is zero or satisfied.

Two extra deliverables are scored here, `R6` (marker registered where pytest actually reads
config) and `R7` (no `setup.cfg` created). Both are met by all three arms.

## 5. How much this establishes, stated narrowly

**It establishes that the category is now testable**: an outdated memory exists, a task exists
where following it fails, the failure mode is verified by control, and the advice demonstrably
reached both memory arms.

**It does not establish that these arms are robust to outdated memory.** The task makes
resistance easy: `pyproject.toml` is the *only* configuration file in the checkout and already
contains a `markers` list, so the correct location is discoverable by `ls`. `c13` names a file
that is not there. A harder version would leave an inert `setup.cfg` in place, so that the
stale advice points at something that exists and looks plausible. That variant is not built.

One attempt per arm, one task, contaminated corpus. No benefit figure.

## 6. Incidental

The Nexus arm again attempted `mcp__nexus__record` — a fourth run in which the agent tried to
write back what it had just learned. Denied by the allowlist; one permission denial; the
frozen corpus digest is unchanged across all three arms.
