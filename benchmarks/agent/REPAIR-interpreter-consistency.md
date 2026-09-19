# Interpreter consistency — the repair, and what verified it

**Chosen 2026-09-19** as the single intervention following
[`DIAGNOSTIC-calib-a2-workflow.md`](DIAGNOSTIC-calib-a2-workflow.md). It addresses **observed
execution friction**. Whether it improves correctness is an experimental question this document
does not answer and does not claim — see [`REGISTRATION-DRAFT-a2r.md`](REGISTRATION-DRAFT-a2r.md).

No model was invoked and nothing was spent. Every figure below is from a command run on this
host, inside a real arm's own `sandbox.sb`.

---

## 1. The defect, reproduced through the path that produced it

The environment gate ran `PYTHONPATH=src python3 -m pytest …` under `/bin/zsh -c` and passed.
The arms ran the same documented command and got `No module named pytest` — in **10 of 11
failing v2 arm-runs and in all 16 passing ones**.

The arms' own records say which interpreter they resolved. Fifteen arm-runs ran
`which -a python3`; in **12 of them `/usr/bin/python3` is listed first**, and `python3 --version`
returns **Python 3.9.6**. The intended interpreter is Python 3.14.7 with pytest 9.1.1.

Run inside `c60/run-k1/attempt1/arms/baseline/sandbox.sb`, with that arm's own child
environment, varying **only** the shell startup:

| | `zsh -c` (what the gate ran) | `zsh -l -c` (login) | `zsh -i -c` (interactive) |
| --- | --- | --- | --- |
| `command -v python3` | `/opt/homebrew/bin/python3` | **`/usr/bin/python3`** | `/opt/homebrew/bin/python3` |
| version | 3.14.7 | **3.9.6** | 3.14.7 |
| `import pytest` | ok | **`ModuleNotFoundError`** | ok |
| `PYTHONPATH=src import click` | the arm's checkout | the arm's checkout | the arm's checkout |
| first PATH entries | `~/.grok/bin ~/.local/bin ~/bin` | **`/usr/local/bin /System/Cryptexes/App/usr/bin /usr/bin`** | `~/.grok/bin …` |

**The login column is the one the arms' recorded output matches.** The mechanism is visible in
the PATH row:

1. `/etc/zprofile` runs `/usr/libexec/path_helper -s`, which rebuilds `PATH` with the system
   directories **first**.
2. The operator's `~/.zprofile` would put Homebrew back in front — it runs
   `eval "$(/opt/homebrew/bin/brew shellenv zsh)"` — but the arm profile grants no read of
   `$HOME/.zprofile`, so it never runs.
3. `python3` is therefore `/usr/bin/python3`, 3.9.6, with no pytest.
4. `zsh -c` skips `/etc/zprofile` entirely and keeps the inherited PATH, so the gate never saw
   any of it.

**`click` resolved correctly in every column.** `PYTHONPATH=src` was never the problem; the
interpreter was. That distinction is why the repair touches the interpreter and leaves the
import path exactly as registered.

**This does not assert that the Bash tool literally passes `-l`.** It asserts what can be
checked and is sufficient either way: the documented commands must work, and resolve the same
interpreter, under **both** startups. A tool that picks either is then covered.

---

## 2. The repair

**One prepared toolchain, delivered by name rather than by resolution.**

| | |
| --- | --- |
| interpreter | `cfg.pytest_python` — already configured, already granted by the arm profile |
| identity | Python **3.14.7**, pytest **9.1.1** |
| channel | **`A2_PYTHON`**, set in `a1_config.child_env` |
| why not PATH | `path_helper` **prepends**, so any inherited entry is pushed below `/usr/bin`. An environment variable is not rewritten by shell startup. |
| why not granting `~/.zprofile` | that is dependence on a host shell customization, which is what failed |
| why not installing anything | there is no network inside the boundary, by design |

`A2_PYTHON` is set **in one place**, so the gate and the runner read one value instead of each
resolving their own. That disagreement is the whole defect; a repair that left them computing it
separately would only move where they disagree.

**The prompt now names it.** The `environment` block is the only prompt text that changed:

> …A prepared interpreter is provided as `$A2_PYTHON`, with the test runner already available;
> use it rather than plain `python3`, which on this machine resolves to a different interpreter
> that has no `pytest`. Import the checkout with `PYTHONPATH=src "$A2_PYTHON" …`, and run tests
> with `PYTHONPATH=src "$A2_PYTHON" -m pytest <paths> -q`…

Verified by assembling both registrations and diffing: `consult`, `tails`, `capture_instruction`
and every task body are **byte-identical**, and the assembled `k1` prompt differs by exactly one
line. **The registered requirement is untouched** — *"Fix the behaviour in `src/`, and extend the
existing test suite to cover it."*

---

## 3. The gate now tests the commands the agent will execute

Every documented command runs under **every startup in `STARTUPS`**, and must pass under all of
them **and agree**. Agreement is on a tagged marker line, not the last line of output, so a
login shell that prints something of its own cannot change the answer.

| check | what passing means |
| --- | --- |
| **pinned interpreter is named, not resolved** | `A2_PYTHON` is set, and `sys.executable`, the Python version and the pytest version are **identical under both startups** |
| **documented import resolves this checkout** | `PYTHONPATH=src "$A2_PYTHON" -c "import click"` resolves inside this arm's repo, under both |
| **documented test command executes a test** | pytest runs with rc 0, a non-empty passed count and no failures or errors, under both |
| **reproduction script runs from the checkout** | a script written into the checkout runs under the pinned interpreter, under both — the prompt tells every arm to work this way and nothing gated it |
| here-document works | unchanged |
| scratch file round-trips | unchanged |
| **egress denied (paired control)** | unchanged — boundary preserved |
| model forwarder reachable | unchanged |
| **heredoc scratch is arm-private** | unchanged — boundary preserved |

Against a real prepared arm, **all nine pass**, and the resolved runtime is recorded:

```
runtime: /Users/soko/Cerebros/nexus-a1-fixtures/.venv-click/bin/python
         python 3.14.7  pytest 9.1.1   (startups probed: login, non_login)
```

That block is written into `envcheck.json` **and into each arm's run record** as
`runtime_identity`, so a later reader can tell which interpreter a row was measured under rather
than inferring it from a configuration path that may have moved. When the gate is disabled the
field records `gated: false` and empty values — **not gated is unknown, not "the default"**.

---

## 4. The controls

A gate that cannot fail proves nothing, so the repaired gate was run against the defect it
missed and against three ways the repair could be hollow.

| control | result |
| --- | --- |
| **the v2 command, `PYTHONPATH=src python3 -m pytest`** | `non_login` rc=0, 23 passed; **`login` rc=1, `No module named pytest`** → the repaired gate **refuses** it |
| `A2_PYTHON` unset | refused: *"A2_PYTHON is not set in the arm's environment"* |
| `A2_PYTHON` pinned to an interpreter without pytest | refused, under both startups |
| two startups that both succeed but resolve **different** interpreters | refused — working is not the same as agreeing |
| two startups that both print **nothing** | refused — silence is not agreement |
| no configuration on the host at all | `A2_PYTHON` **absent, not invented**; the gate refuses, and the runner refuses even with the gate switched off |
| the repaired command | passes, identically, under both |

**13 counterexample tests** in [`test_interpreter_pinning.py`](test_interpreter_pinning.py),
host-independent: they stub `sandbox-exec` and `/bin/zsh` rather than running them, so the
decision logic is exercised on a machine without the fixtures. In CI.

---

## 5. Versioning, and what is kept separate

**The repaired configuration is `calib-v3`.** All four prompt digests changed, so **no v3 row
can be pooled with a v2 row by accident** — `identity.incompatible` refuses on
`prompt_digest` and `config_version` before anything else happens.

| | v2 (closed) | v3 (repaired) |
| --- | --- | --- |
| k1 | `6bbe10c01d7e520d` | `5f4f1e210aa7f7ea` |
| k2 | `d47e3eaa44a8d22c` | `0aa687b3be6b6a26` |
| k3 | `905bf3bd43df8756` | `b99a1fd5366f507f` |
| k4 | `6fd3cb4335a4c1dd` | `97ee8c80330b164a` |

**The closed calibration is not touched.** Its records, its `calibration-summary.json`, its
`LAUNCH-A2.md` identity table and its host-local `calib-config-{30,45,60}.json` all stand as
they were. **A v3 sweep needs NEW host-local configuration files**, created alongside the v2
ones and carrying `"config_version": "calib-v3"` — editing the v2 files in place would break the
provenance of every row already recorded against their digests.

## 6. What this repair does not establish

That correctness improves. That truncation falls. That any arm benefits. It removes a friction
that was **observed in both outcome groups**, which is a reason to measure again — not a
prediction about what the measurement will say.

## Reproducing

```bash
python3 benchmarks/agent/test_interpreter_pinning.py
python3 benchmarks/agent/verify_arm_environment.py <a prepared arm directory>
```
