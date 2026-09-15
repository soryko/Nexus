"""Re-check every frozen identifier and the ledger, immediately before a calibration launch.

At the repository ROOT, not under `benchmarks/agent`: a file there would change the
`harness_revision` this script exists to verify.

It exists because no single guard covers the ways the instrument can move:

  a commit under benchmarks/agent   changes harness_revision   -- caught by the revision check
                                    (a commit to a ROOT-level file changes neither, which is
                                    why the checkout SHA is reported rather than pinned)
  an UNCOMMITTED edit there         changes the executing code but NOT the recorded revision,
                                    because `git log` reads commit history, not the worktree
                                    -- caught only by the clean-tree check
  an edit to calib-config-*.json    changes neither: those files are gitignored, so
                                    `git status --porcelain` does not mention them
                                    -- caught only by the configuration digest
  an activated virtualenv           changes neither, and is not a file at all: it changes what
                                    `python3` means inside every arm, because PATH is
                                    allowlisted -- caught only by the PATH check below

Exits nonzero if anything has moved.  Usage:  python3 preflight-a2.py
"""
import os, subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "benchmarks/agent"))
import a1_config, identity as I, run_calibration as RC

# No checkout SHA is pinned here. Pinning one made this file invalidate itself: every commit
# to a root-level note moves HEAD while changing nothing the measurement records. What is
# frozen is the MEASURED identity -- the two revisions and the digests, which are exactly what
# a row records and what decides whether two rows may be pooled. HEAD is reported, not
# asserted, and the HEAD this script approves is the checkout to run from and to leave alone.
FROZEN = {"product": "2cd531f9d7c274a065533e58ba3fde8582f8c269",
          "harness": "9e5e1e040094b7d44e5712d3028afa5af3f45776",
          "config": {30: "69df5d49a39142c4", 45: "396c0d2d48a22eed", 60: "b64cc9c137544b7a"},
          "prompt": {"k1": "6bbe10c01d7e520d", "k2": "d47e3eaa44a8d22c",
                     "k3": "905bf3bd43df8756", "k4": "6fd3cb4335a4c1dd"},
          "charged": 7_825_690}
def git(*a): return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()
bad = []
def ck(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'BAD'} {name:34s} {got}")
    if not ok:
        bad.append(f"{name}: {got!r} != {want!r}")

head = git("rev-parse", "HEAD")
print(f"      {'checkout (reported, not pinned)':34s} {head}")
ck("product_revision", git("log", "-1", "--format=%H", "--", "src/nexus_memory"), FROZEN["product"])
ck("harness_revision", git("log", "-1", "--format=%H", "--", "benchmarks/agent"), FROZEN["harness"])
ck("working tree (tracked)", git("status", "--porcelain") or "clean", "clean")
for c, want in FROZEN["config"].items():
    ck(f"config_digest c{c}", I.config_digest(a1_config.load(REPO / f"benchmarks/agent/calib-config-{c}.json")), want)
cfg = a1_config.load(REPO / "benchmarks/agent/calib-config-30.json")
for t, want in FROZEN["prompt"].items():
    ck(f"prompt_digest {t}", I.prompt_digest(cfg, t), want)
# The shell's own PATH reaches the arms. `PATH` is on a1_config's allowlist by necessity --
# it resolves the runner, git and the interpreter -- so whatever `python3` means in the shell
# that launches the sweep is what `python3` means inside every arm's sandbox. A launch from a
# shell with `.venv` activated put `.venv/bin` first; that prefix is not among the profile's
# readable subpaths, so the interpreter died at startup with `Failed to import encodings
# module` and the per-arm gate refused before spending. Even readable, it would have been a
# different interpreter from the one A1 measured, and silently so.
import shutil
ck("no virtualenv active", os.environ.get("VIRTUAL_ENV") or "none", "none")
ck("python3 resolves to", shutil.which("python3") or "(not found)", "/opt/homebrew/bin/python3")

# The forwarder is the only egress an arm has, and the runbook's step 0 is to start it and
# leave it up. Nothing checked it: on 2026-09-14 it was down, every arm got
# `API Error: Connection refused`, and the sweep wrote 36 excluded arm-runs over two hours and
# called itself completed. The per-arm gate now refuses on this too, from INSIDE the sandbox,
# which is the authoritative check; this one just fails in a second rather than in a minute.
import socket
def _forwarder(port=8899):
    try:
        socket.create_connection(("127.0.0.1", port), 3).close()
        return "listening"
    except OSError as exc:
        return f"not listening ({exc.__class__.__name__})"
ck("model forwarder on :8899", _forwarder(), "listening")

led = RC.consumed(Path("/Users/soko/Cerebros/nexus-a1-fixtures/calib-run"))
ck("ledger charged", str(led["tokens_budgeted"]), str(FROZEN["charged"]))
ck("unresolved arm-runs", str(led["unresolved"]), "0")
print(f"\n{'PREFLIGHT OK' if not bad else 'PREFLIGHT FAILED: ' + '; '.join(bad)}")
print(f"remaining against the cap: {RC.CAP_TOKENS - led['tokens_budgeted']:,} tokens")
if not bad:
    print(f"run from, and do not change, checkout {head}")
raise SystemExit(1 if bad else 0)
