"""Every path and ceiling the harness needs, in one validated file, plus the environment an
arm is actually given.

Three of these were constants embedded in `run_arms_isolated.py`, and two of them named one
session's scratchpad by its UUID -- `PYTEST_PY` and `SOURCE_CLONE`. A reader on another host,
or the same host a week later, gets a `FileNotFoundError` mid-run at best; at worst the
source clone moves and the deny that withholds it silently stops naming anything. Neither is
detectable from the artifact, because a deny for a path that does not exist looks exactly
like a deny that works.

So configuration is data now, it is validated before anything runs, and `preflight` says
which field is wrong rather than failing at the point of use. A run records the resolved
configuration in its own artifact, so what a figure was produced under is readable without
the harness.

The child environment is the other half. `invoke()` handed each arm `dict(os.environ)` --
the operator's whole shell: every other provider's API key, editor and shell settings,
`CLAUDE_*` variables that change the runner's behaviour, and anything that happened to be
exported that day. None of it is held fixed across arms, so none of it belongs in a
measurement. `child_env` builds the environment from a declared list instead, and the run
records exactly which names crossed the boundary.

Usage:  python3 a1_config.py <config.json>        # preflight only, exits nonzero if invalid
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent
DEFAULT_PATH = BENCH / "a1-config.json"

# Names an arm's process is given, and nothing else. Each is here for a stated reason; a name
# not on this list does not reach an arm, whatever the operator's shell holds.
ENV_ALLOWLIST = {
    "PATH":        "resolve the runner, git and the interpreter",
    "HOME":        "the runner reads its own install and config under it",
    "LANG":        "byte-identical prompts need a stable text encoding",
    "LC_ALL":      "same",
    "TMPDIR":      "the runner's temporary files; inside the allowed read roots",
    "USER":        "git refuses some operations without an identity",
    "LOGNAME":     "same",
    "SHELL":       "the Bash tool spawns it",
    "TERM":        "absent, some tools assume a dumb terminal and change their output",
}
# Set by the harness itself, per arm, and never inherited.
#
# `A2_PYTHON` is the pinned interpreter, named explicitly because PATH could not carry it.
# Measured 2026-09-19 inside a real arm profile: the gate's `/bin/zsh -c` resolves `python3`
# to /opt/homebrew/bin/python3 (3.14.7, pytest present), while a LOGIN zsh -- which is what
# the arm's recorded `which -a python3` output matches -- resolves it to /usr/bin/python3
# (3.9.6, no pytest). `/etc/zprofile` runs `path_helper`, which rebuilds PATH with the system
# directories first; the operator's `~/.zprofile`, which would put Homebrew back in front, is
# not readable inside the arm boundary and never runs. So the documented `python3 -m pytest`
# passed the gate and failed for the agent, in every arm-run of the v2 sweep.
#
# PATH cannot be the fix: path_helper PREPENDS, so anything inherited is pushed below
# /usr/bin. An environment variable is not rewritten by shell startup, so it is the one
# channel that reaches the agent's shell unchanged.
ENV_HARNESS_SET = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "TMPPREFIX", "A2_PYTHON")
# Read from the operator's environment but NEVER written to an artifact.
ENV_SECRET = ("DEEPSEEK_API_KEY",)


@dataclass(frozen=True)
class Config:
    source_clone: str           # the Click clone fixtures are exported from; denied to arms
    pytest_python: str          # interpreter the task's own test suite runs under
    venv_python: str            # interpreter Nexus runs on (.venv-sqlite)
    nexus_server: str           # the MCP server executable
    config_version: str = "a1"  # identity of the CONFIGURATION, not of the file it came from
    forwarder_port: int = 8899
    corpus_size: int = 13
    max_turns: int = 30
    wall_clock_s: int = 600
    # Capture's own ceilings, and deliberately not the arms'. freeze-heldout-a1 section 3
    # registers `--max-turns 30` and 600 s under "arms, trials and ceilings", calibrated on
    # runs that only had to fix a bug. A capture run does that AND records as it goes, under a
    # prompt about three times as long, and attempt 1 hit the turn ceiling on both tasks with
    # one memory to show for it. Raising these leaves the evaluation instrument untouched: no
    # arm reads them.
    capture_max_turns: int = 60
    capture_wall_clock_s: int = 1200
    memory_probe_query: str = "click option parameter"
    repo: str = str(REPO)
    bench: str = str(BENCH)

    # The corpus, and the scope it is seeded under. These were constants in the runner naming
    # the development corpus, so a held-out run could not be configured without editing the
    # harness -- and an edited harness is a different instrument mid-experiment.
    store_master: str = ""      # the frozen store, copied per arm-run; "" = the legacy in-run one
    corpus_digest: str = ""     # the registered row digest of that store; "" = ungated, and said so
    namespace: str = "a1-dev"
    actor: str = "agent"
    notes_file: str = "notes-dev-a1.md"     # arm 3's rendering of the same corpus
    prompts: str = "prompts-a1.json"        # task prompt registration, relative to `bench`

    # (field, what it must be) -- checked by preflight in this order
    _PATHS = (("source_clone", "dir"), ("pytest_python", "exec"), ("venv_python", "exec"),
              ("nexus_server", "exec"), ("repo", "dir"), ("bench", "dir"))
    # Optional: validated only when set, since the development configuration predates them.
    _OPTIONAL_PATHS = (("store_master", "file"),)
    # Resolved against `bench` when relative, so a configuration stays portable.
    _BENCH_RELATIVE = ("notes_file", "prompts")

    def preflight(self) -> list[str]:
        """-> a list of problems, empty when the configuration is usable.

        Returns them all rather than raising on the first, so one run tells the operator
        everything that needs fixing.
        """
        bad: list[str] = []
        for name, kind in self._PATHS:
            p = Path(getattr(self, name))
            if not p.exists():
                bad.append(f"{name}: {p} does not exist")
            elif kind == "dir" and not p.is_dir():
                bad.append(f"{name}: {p} is not a directory")
            elif kind == "exec" and not os.access(p, os.X_OK):
                bad.append(f"{name}: {p} is not executable")
        if not 1 <= self.forwarder_port <= 65535:
            bad.append(f"forwarder_port: {self.forwarder_port} is not a port number")
        for name in ("corpus_size", "max_turns", "wall_clock_s", "capture_max_turns",
                     "capture_wall_clock_s"):
            if getattr(self, name) <= 0:
                bad.append(f"{name}: {getattr(self, name)} must be positive")
        for name, kind in self._OPTIONAL_PATHS:
            value = getattr(self, name)
            if value and not Path(value).exists():
                bad.append(f"{name}: {value} does not exist")
            elif value and kind == "file" and not Path(value).is_file():
                bad.append(f"{name}: {value} is not a file")
        for name in self._BENCH_RELATIVE:
            resolved = self.bench_path(name)
            if not resolved.is_file():
                bad.append(f"{name}: {resolved} is not a file")
        if self.corpus_digest and not re.fullmatch(r"[0-9a-f]{16}", self.corpus_digest):
            bad.append(f"corpus_digest: {self.corpus_digest!r} is not a 16-hex-digit digest")
        for name in ("namespace", "actor"):
            if not getattr(self, name).strip():
                bad.append(f"{name}: must not be empty")
        if not self.memory_probe_query.strip():
            bad.append("memory_probe_query: must not be empty")
        # only asked once the path is known to be a directory, so one wrong field yields one
        # problem rather than two descriptions of the same mistake
        if (src := Path(self.source_clone)).is_dir() and not (src / ".git").exists():
            bad.append(f"source_clone: {src} is not a git checkout")
        return bad

    def bench_path(self, name: str) -> Path:
        """A bench-relative field as an absolute path. An absolute value is left alone."""
        value = Path(getattr(self, name))
        return value if value.is_absolute() else Path(self.bench) / value

    def require(self) -> Config:
        if bad := self.preflight():
            raise SystemExit("configuration preflight failed:\n  " + "\n  ".join(bad))
        return self

    def as_recorded(self) -> dict:
        """What goes into the run artifact. No secret is read here, let alone written."""
        return {**asdict(self), "config_source": str(DEFAULT_PATH)}


def load(path: Path | str | None = None) -> Config:
    p = Path(path or os.environ.get("A1_CONFIG") or DEFAULT_PATH)
    if not p.exists():
        raise SystemExit(f"no configuration at {p}; copy a1-config.example.json and edit it")
    data = json.loads(p.read_text())
    # Keys beginning with "_" are comments -- the example template carries one, and a
    # configuration a reader cannot annotate is a configuration a reader gets wrong.
    data = {k: v for k, v in data.items() if not k.startswith("_")}
    known = {f for f in Config.__dataclass_fields__ if not f.startswith("_")}
    if unknown := sorted(set(data) - known):
        raise SystemExit(f"{p}: unknown configuration field(s): {', '.join(unknown)}")
    return Config(**data)


def child_env(base_url: str, api_key: str, extra: dict[str, str] | None = None,
              python: str | None = None) -> dict:
    """The environment one arm is given: the allowlist, plus what the harness sets itself.

    An allowlisted name that is unset on the host is simply absent -- it is not invented,
    because a value the harness made up is as much an uncontrolled variable as one it
    inherited.

    `A2_PYTHON` is set HERE rather than by each caller, so the gate and the runner cannot
    disagree about which interpreter the arm is supposed to use. That disagreement is the
    defect this repairs: they were computing the interpreter separately, by resolution rather
    than by name, and resolution differed between them. `python=` is for a caller that must
    pin a different one explicitly; it defaults to the configuration's own `pytest_python`.
    """
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_API_KEY"] = api_key
    env["A2_PYTHON"] = python or load().pytest_python
    env.update(extra or {})
    return env


def env_record(env: dict) -> dict:
    """What crossed the boundary, by NAME. Values are not recorded: one of them is a key."""
    return {"names": sorted(env),
            "inherited": sorted(k for k in env if k in ENV_ALLOWLIST),
            "set_by_harness": sorted(k for k in env if k in ENV_HARNESS_SET),
            "host_names_withheld": max(0, len(os.environ) - len(
                [k for k in env if k in ENV_ALLOWLIST]))}


def main() -> int:
    cfg = load(sys.argv[1] if len(sys.argv) > 1 else None)
    bad = cfg.preflight()
    for line in bad:
        print(f"  BAD  {line}")
    print(f"configuration {'INVALID' if bad else 'ok'}: "
          f"{len(Config._PATHS)} paths, forwarder :{cfg.forwarder_port}, "
          f"corpus {cfg.corpus_size}, ceilings {cfg.max_turns} turns / {cfg.wall_clock_s}s")
    env = child_env("http://127.0.0.1:1/anthropic", "not-a-key")
    print(f"child environment: {len(env)} names "
          f"({', '.join(sorted(env))}); {len(os.environ)} on the host")
    missing = [k for k in ENV_ALLOWLIST if k not in os.environ]
    if missing:
        print(f"  allowlisted but unset on this host (absent, not invented): "
              f"{', '.join(missing)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
