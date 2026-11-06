# `dev-m1` provenance review — all 24 memories

**Reviewed provenance with limited automated leakage checks.** Not proof that no memory conveys a fix.

| corpus | `corpus-dev-m1.json`, sha256 `2d2e6962a60f4633acc7044d0af097819669f3ef5bd1dcabd5da9cd4a8dd5df8` |
| --- | --- |
| reviewed | 24 of 24 — **every memory an arm can receive**, not only the on-subject ones |
| fix-locality scan | measured |

## The finding

The one memory this review flags -- `m02`, which states both halves of k4's defect in the same two clauses as the fix's own rationale comment -- is a memory the fix-locality scan shows NOTHING for, on k4 or on any task. Every identifier in it predates the fix, and the four words it shares with k4's changed lines (`invocation`, `option`, `order`, `parameters`) are English prose from an added docstring, which is why they are excluded. Meanwhile the scan's own hits are `close`, `flag_value`, `is_flag`, `default`, `value`, `UNSET` and `click` -- the working vocabulary of every memory about option handling, on-subject and off. So on this task set the scan flags memories the reading clears and clears the memory the reading flags. That is the concrete form of the general claim that identifier scans cannot establish semantic leakage, and it is why the conclusion rests on reading.

## Per memory

| id | prov | subject | register | probe / prose | k1 k2 k3 k4 | fix-locality |
| --- | --- | --- | --- | --- | --- | --- |
| `h01` | capt | k2,k3 | describes existing behaviour | 1 / 4 | u u t u | k2:flag_value+is_flag |
| `h02` | capt | k2,k3 | describes existing behaviour | 1 / 3 | f f t f | k2:default+flag_value+is_flag, k3:UNSET |
| `h03` | capt | — | diagnostic guidance | 0 / 4 | u u u u | — |
| `h04` | capt | — | diagnostic guidance | 0 / 2 | u u u u | k2:flag_value, k3:value, k4:click+declaration+option |
| `h05` | capt | k2,k3 | describes existing behaviour | 2 / 2 | t t t t | k2:default+flag_value |
| `h06` | capt | — | diagnostic guidance | 1 / 3 | t t t t | — |
| `h07` | capt | k2,k3 | describes existing behaviour | 1 / 3 | t t t t | — |
| `h08` | capt | k3 | describes existing behaviour | 2 / 5 | t t t t | — |
| `h09` | capt | k3 | describes existing behaviour | 1 / 5 | f f t f | k2:flag_value |
| `h10` | capt | — | diagnostic guidance | 0 / 6 | u u u u | — |
| `h11` | capt | k3 | describes existing behaviour | 2 / 4 | t t t t | — |
| `h12` | capt | — | diagnostic guidance | 0 / 3 | u u u u | — |
| `h13` | capt | k2 | describes existing behaviour | 1 / 5 | t t t t | k2:flag_value |
| `m01` | deri | k1 | describes existing behaviour | 3 / 2 | t t t t | k1:close |
| `m02` ⚑ | deri | k4 | diagnostic guidance | 3 / 2 | t t t t | — |
| `m03` | deri | k2,k3 | describes existing behaviour | 1 / 3 | t t f t | k2:flag_value |
| `m04` | deri | — | diagnostic guidance | 1 / 3 | t t t t | — |
| `m05` | deri | k1 | describes existing behaviour | 1 / 3 | f f f f | k1:close, k4:callback |
| `m06` | deri | k4 | describes existing behaviour | 1 / 1 | f f f f | — |
| `x01` | dist | — | describes existing behaviour | 2 / 3 | t t t t | k4:click |
| `x02` | dist | — | describes existing behaviour | 2 / 1 | t t t t | k4:click |
| `x03` | dist | — | describes existing behaviour | 1 / 2 | t t t t | — |
| `x04` | dist | — | describes existing behaviour | 2 / 1 | t t t t | k4:click |
| `x05` | dist | — | describes existing behaviour | 3 / 2 | t t t t | — |

## Notes, per memory

**`h01`** — *source:* captured memory 69d96f6a-4a1b-4c71-8776-980b2c9ae317  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/_utils.py` matching `FLAG_NEEDS_VALUE` — 1 structural observation(s) against 4 prose claim(s).  
*register:* describes existing behaviour. Two notions of 'used without a value'. k2's fix hoists a `flag_value` assignment out of a `type is None` branch; nothing here states that.

**`h02`** — *source:* captured memory 72e74193-1089-4fa3-8569-4f6477f9c31f  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `_flag_needs_value = self\.default is UNSET` — 1 structural observation(s) against 3 prose claim(s).  
*register:* describes existing behaviour. Auto-detection in `Option.__init__`. REFUTED at k2's checkout -- a version mismatch, not an observed rotting.

**`h03`** — *source:* captured memory d4d9627a-20d4-44fc-8fe6-2d584ec5cc24  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* nothing — this memory has no probe, so its truth is `unknown` at every checkout and it is support, never on-subject.  
*register:* diagnostic guidance. `uv run` fails in the sandbox. Environment, not mechanism.

**`h04`** — *source:* captured memory 0f7a86dd-6127-4185-a52d-95e5d70653ed  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* nothing — this memory has no probe, so its truth is `unknown` at every checkout and it is support, never on-subject.  
*register:* diagnostic guidance. Parametrised option tests through CliRunner. Procedure.

**`h05`** — *source:* captured memory 7ffea59b-7e95-4868-a48a-ad3a4353e5b9  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `flag_value`, `default` — 2 structural observation(s) against 2 prose claim(s).  
*register:* describes existing behaviour. `default` and `flag_value` answer different questions. The k2 fix makes `flag_value` derive from `default` in one more case; this says the opposite of a rule, not the change.

**`h06`** — *source:* captured memory 5d520c47-c578-4f1b-a7f6-6342c164c874  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `pyproject.toml` matching `filterwarnings` — 1 structural observation(s) against 3 prose claim(s).  
*register:* diagnostic guidance. `filterwarnings = error` aborts collection. Environment.

**`h07`** — *source:* captured memory a151d3c1-8c56-4109-b3cd-312c5d4d89e4  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/parser.py` matching `_get_value_from_state` — 1 structural observation(s) against 3 prose claim(s).  
*register:* describes existing behaviour. Parser token consumption. Neither k2's nor k3's fix touches the parser.

**`h08`** — *source:* captured memory d9392ff7-c7da-4c3a-994d-8c609f2fdc41  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `def get_default`, `call: bool` — 2 structural observation(s) against 5 prose claim(s).  
*register:* describes existing behaviour. Callable defaults. k3's fix is about WHEN `UNSET` becomes `None`, not about calling factories.

**`h09`** — *source:* captured memory 833f2840-e0bc-4d8b-b281-1d0e47361edc  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `3024` — 1 structural observation(s) against 5 prose claim(s).  
*register:* describes existing behaviour. The issue-3024 reconciliation. k3's fix cites issues 3071/3079 and is a different mechanism; naming an issue number is not naming this one.

**`h10`** — *source:* captured memory 8c354573-e68d-4f02-8a38-8935b8ffc61a  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* nothing — this memory has no probe, so its truth is `unknown` at every checkout and it is support, never on-subject.  
*register:* diagnostic guidance. Running the suite outside `uv`. Environment.

**`h11`** — *source:* captured memory 7ae7099b-48a6-4e6d-9bed-e9b63180f3db  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `def consume_value`, `default_map` — 2 structural observation(s) against 4 prose claim(s).  
*register:* describes existing behaviour. The `consume_value` precedence chain. k3's fix changes the same method, so this is the captured memory nearest a fix site -- but it states the precedence ORDER, and the fix changes the UNSET normalisation POINT. Nearest, and still not the repair.

**`h12`** — *source:* captured memory c2a4bd0d-53e8-417d-a15d-c6b04e28cbd0  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* nothing — this memory has no probe, so its truth is `unknown` at every checkout and it is support, never on-subject.  
*register:* diagnostic guidance. CliRunner as the exercise path. Procedure.

**`h13`** — *source:* captured memory ee455fb1-3547-438f-8336-8a0d9852bb04  
*author had seen:* captured during A1's corpus capture, by a session that had not read A1's held-out tasks and for which k1-k4 DID NOT YET EXIST as a task set. That is a weaker claim than 'written blind to k1-k4' and is stated as the weaker one. Written against a Click revision NEWER than k1/k2/k4's checkouts.  
*probe supports:* `src/click/core.py` matching `convert_type\(None, flag_value\)` — 1 structural observation(s) against 5 prose claim(s).  
*register:* describes existing behaviour. `flag_value` as a value throughout, including type inference when no explicit type is given -- the PRE-fix coupling k2's fix breaks. It describes the state the fix changes, which is the opposite of conveying it.

**`m01`** — *source:* src/click/core.py at each task's own checkout  
*author had seen:* written during dev-m1's construction by an author who HAD seen k1-k4's subjects and their pre-fix trees. Whether that author also read the fix diffs is not established by any artifact, and is not assumed either way.  
*probe supports:* `src/click/core.py` matching `_close_callbacks`, `def close\(self\)`, `with ctx\.scope\(cleanup=False\)` — 3 structural observation(s) against 2 prose claim(s).  
*register:* describes existing behaviour. Where cleanup callbacks are held and who runs them, ending on 'closing it is the caller's responsibility'. k1's fix makes `Context.exit()` call `self.close()`. This names the mechanism and the site WITHOUT naming `exit()` or stating that it should close -- the reader still has to find the path that bypasses `close()`. Closest of the k1 memories; not the repair.

**`m02`** ⚑ FLAGGED — *source:* src/click/core.py at each task's own checkout  
*author had seen:* written during dev-m1's construction by an author who HAD seen k1-k4's subjects and their pre-fix trees. Whether that author also read the fix diffs is not established by any artifact, and is not assumed either way.  
*probe supports:* `src/click/core.py` matching `def get_help_option`, `def get_params`, `def iter_params_for_processing` — 3 structural observation(s) against 2 prose claim(s).  
*register:* diagnostic guidance. THE MATERIAL FINDING OF THIS REVIEW. It states both halves of k4's defect -- that `get_help_option` CONSTRUCTS an option each call, and that `iter_params_for_processing` compares the parameter OBJECTS -- and the fix's own in-code rationale reads 'avoid creating it multiple times. Not doing this will break the callback odering by iter_params_for_processing(), which relies on object comparison'. Same two clauses, minus the remedy (caching in `self._help_option`). Written by an author who had seen k4. No identifier scan could reach this: every name in it predates the fix. It is diagnosis, not repair, so it stays in the corpus -- but a k4 result under either policy may NOT be read as evidence that retrieval located the mechanism unaided.

**`m03`** — *source:* src/click/core.py at the k1/k2/k4 revisions; REFUTED at k3's  
*author had seen:* written during dev-m1's construction by an author who HAD seen k1-k4's subjects and their pre-fix trees. Whether that author also read the fix diffs is not established by any artifact, and is not assumed either way.  
*probe supports:* `src/click/core.py` matching `_flag_needs_value = flag_value is not None` — 1 structural observation(s) against 3 prose claim(s).  
*register:* describes existing behaviour. `_flag_needs_value = flag_value is not None`. True at k1/k2/k4, REFUTED at k3 -- the older form presented to the newer checkout.

**`m04`** — *source:* prompts-calib-a2.json `environment`, and REPAIR-interpreter-consistency.md  
*author had seen:* written from `prompts-calib-a2.json`'s `environment` block and REPAIR-interpreter-consistency.md. The author had seen the task ENVIRONMENT; no task's fix or checks bear on it.  
*probe supports:* `pyproject.toml` matching `\[project\]` — 1 structural observation(s) against 3 prose claim(s).  
*register:* diagnostic guidance. `PYTHONPATH=src` and a named interpreter. Environment.

**`m05`** — *source:* CONSTRUCTED CONTRADICTION. No revision of this repository is asserted. It is refuted by `Context.__init__`, which holds `_close_callbacks` and `_exit_stack` as separate attributes, and by `Context.close()`, which walks the list.  
*author had seen:* written during dev-m1's construction by an author who HAD seen k1-k4's subjects and their pre-fix trees. Whether that author also read the fix diffs is not established by any artifact, and is not assumed either way.  
*probe supports:* `src/click/core.py` matching `NOT _close_callbacks` — 1 structural observation(s) against 3 prose claim(s).  
*register:* describes existing behaviour. CONSTRUCTED CONTRADICTION for k1. Asserts no revision of this repository; refuted one read away at `Context.__init__`. Tests whether an agent notices a memory the checkout refutes.

**`m06`** — *source:* CONSTRUCTED CONTRADICTION. No revision of this repository is asserted. It is refuted by the method's own body, which builds `set(ctx.help_option_names)` and returns `list(all_names)` -- an unordered set, so declaration order is not preserved.  
*author had seen:* written during dev-m1's construction by an author who HAD seen k1-k4's subjects and their pre-fix trees. Whether that author also read the fix diffs is not established by any artifact, and is not assumed either way.  
*probe supports:* `src/click/core.py` matching `NOT all_names = set\(ctx\.help_option_names\)` — 1 structural observation(s) against 1 prose claim(s).  
*register:* describes existing behaviour. CONSTRUCTED CONTRADICTION for k4. Asserts ordering is preserved and stable; k4's fix documents the opposite. Refuted at `Command.get_help_option_names`.

**`x01`** — *source:* written for dev-m1; no source revision asserted  
*author had seen:* written during dev-m1's construction by an author who had seen k1-k4, and chosen deliberately to bear on none of them.  
*probe supports:* `src/click/termui.py` matching `def style`, `def secho` — 2 structural observation(s) against 3 prose claim(s).  
*register:* describes existing behaviour. termui styling. No task's fix touches `src/click/termui.py`.

**`x02`** — *source:* written for dev-m1; no source revision asserted  
*author had seen:* written during dev-m1's construction by an author who had seen k1-k4, and chosen deliberately to bear on none of them.  
*probe supports:* `src/click/shell_completion.py` matching `class CompletionItem`, `shell_complete` — 2 structural observation(s) against 1 prose claim(s).  
*register:* describes existing behaviour. shell completion. No task's fix touches it.

**`x03`** — *source:* written for dev-m1; no source revision asserted  
*author had seen:* written during dev-m1's construction by an author who had seen k1-k4, and chosen deliberately to bear on none of them.  
*probe supports:* `src/click/exceptions.py` matching `class UsageError` — 1 structural observation(s) against 2 prose claim(s).  
*register:* describes existing behaviour. `UsageError`. No task's fix touches `src/click/exceptions.py`.

**`x04`** — *source:* written for dev-m1; no source revision asserted  
*author had seen:* written during dev-m1's construction by an author who had seen k1-k4, and chosen deliberately to bear on none of them.  
*probe supports:* `src/click/types.py` matching `class Path`, `resolve_path` — 2 structural observation(s) against 1 prose claim(s).  
*register:* describes existing behaviour. the `Path` type. No task's fix touches `src/click/types.py`.

**`x05`** — *source:* written for dev-m1; no source revision asserted  
*author had seen:* written during dev-m1's construction by an author who had seen k1-k4, and chosen deliberately to bear on none of them.  
*probe supports:* `src/click/core.py` matching `def get_command`, `def list_commands`, `invoke_without_command` — 3 structural observation(s) against 2 prose claim(s).  
*register:* describes existing behaviour. `Group` dispatch. In `core.py`, which every fix touches, but on subcommand resolution, which none of them does.

## What this review does not establish

- that no memory conveys a fix semantically -- reading is not measurement
- that the derived memories' author did not read the fix diffs; no artifact records either way
- that a `true` probe certifies every sentence of the prose it labels
