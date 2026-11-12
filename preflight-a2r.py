"""Re-check every frozen A2-R identifier, the runtime and the output directory, immediately
before an A2-R launch.

NOT `preflight-a2.py`. That script belongs to the CLOSED calibration: it asserts harness
revision `cbbaad64…`, the three `calib-config-*.json` digests, the v2 prompt digests, a
historical charge of 7 825 690 tokens, and **zero unresolved arm-runs in the calibration
directory**. The last of those cannot be satisfied and must not be: one calibration arm-run is
unresolved on purpose, and no allowance was written for it. A2-R must not be gated on
resolving another sweep's accounting, and the calibration's own preflight must keep asserting
what it froze. So there are two.

At the repository ROOT, for the reason `preflight-a2.py` gives: a file under `benchmarks/agent`
would change the `harness_revision` it exists to verify. That is also why the harness revision
below can be a literal at all -- it names the commit that last changed `benchmarks/agent`, and
committing this file and the launch record beside it does not move that.

  HEAD is the EXECUTION CHECKOUT: reported, never pinned, because every commit to a root-level
  note moves it while changing nothing a row records.
  harness_revision is the MEASURED identity: frozen, and what decides whether two rows pool.
  They are different things and a launch needs both -- run from the HEAD this script prints,
  and leave it alone.

Exits nonzero if anything has moved.  Usage:  python3 preflight-a2r.py
"""
import os, re, socket, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "benchmarks/agent"))
import a1_config, identity as I, run_a2r as R2, run_calibration as RC, schedule as sched

CEILING = 45

FROZEN = {
    "product": "2cd531f9d7c274a065533e58ba3fde8582f8c269",
    "harness": "225d8538a5c7fcf4d161448c76ee0ba18b53378e",
    "config_version": "calib-v3",
    "config_digest": "22eb0a3766cdde73",
    "max_turns": 45,
    "wall_clock_s": 600,
    "corpus": "9ae2a9f268dd894d",
    "schedule": "0a352b82ced14f10e85d50d17989ac38e02d4d9582c032ed743a75554a6a5d97",
    "prompt": {"k1": "5f4f1e210aa7f7ea", "k2": "0aa687b3be6b6a26",
               "k3": "b99a1fd5366f507f", "k4": "97ee8c80330b164a"},
}

# The closed calibration's three configurations, asserted here too. A2-R does not read them,
# and that is exactly why they need a guard: nothing else this script touches would notice a
# `calib-v3` configuration written over one of them, and that would destroy the provenance of
# a published sweep.
V2_UNTOUCHED = {30: "69df5d49a39142c4", 45: "396c0d2d48a22eed", 60: "b64cc9c137544b7a"}

SCRATCH = Path("/Users/soko/Cerebros/nexus-a1-fixtures/a2r-run")
CALIB_SCRATCH = Path("/Users/soko/Cerebros/nexus-a1-fixtures/calib-run")

bad: list[str] = []


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a],
                          capture_output=True, text=True).stdout.strip()


def ck(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'BAD'} {name:36s} {got}")
    if not ok:
        bad.append(f"{name}: {got!r} != {want!r}")


def note(name, value):
    """Reported, never asserted."""
    print(f"      {name:36s} {value}")


head = git("rev-parse", "HEAD")
print("A2-R preflight — the development sweep, accounted separately\n")
note("execution checkout (reported)", head)
ck("product_revision", git("log", "-1", "--format=%H", "--", "src/nexus_memory"),
   FROZEN["product"])
ck("harness_revision", git("log", "-1", "--format=%H", "--", "benchmarks/agent"),
   FROZEN["harness"])
ck("working tree (tracked)", git("status", "--porcelain") or "clean", "clean")

print("\nthe A2-R configuration")
cfg_path = R2.config_for(CEILING)
ck("configuration file", cfg_path.name, f"a2r-config-{CEILING}.json")
if not cfg_path.exists():
    bad.append(f"{cfg_path} does not exist")
    print(f"  BAD {'configuration present':36s} {cfg_path} is missing")
else:
    cfg = a1_config.load(cfg_path)
    for problem in cfg.preflight():
        bad.append(f"configuration: {problem}")
        print(f"  BAD {'configuration preflight':36s} {problem}")
    ck("config_version", cfg.config_version, FROZEN["config_version"])
    ck("config_digest", I.config_digest(cfg), FROZEN["config_digest"])
    ck("max_turns", cfg.max_turns, FROZEN["max_turns"])
    ck("wall_clock_s", cfg.wall_clock_s, FROZEN["wall_clock_s"])
    ck("corpus_digest", cfg.corpus_digest, FROZEN["corpus"])
    ck("prompts", Path(cfg.prompts).name, "prompts-calib-a2.json")
    for t, want in FROZEN["prompt"].items():
        ck(f"prompt_digest {t}", I.prompt_digest(cfg, t), want)
    ck("schedule_digest", sched.load(RC.SCHEDULE).schedule_digest, FROZEN["schedule"])

print("\nthe closed calibration is untouched")
for c, want in V2_UNTOUCHED.items():
    f = REPO / f"benchmarks/agent/calib-config-{c}.json"
    ck(f"calib-config-{c}.json digest",
       I.config_digest(a1_config.load(f)) if f.exists() else "(absent)", want)
    if f.exists():
        ck(f"calib-config-{c}.json version", a1_config.load(f).config_version, "calib-v2")

print("\nthe runtime the arms will actually get")
ck("no virtualenv active", os.environ.get("VIRTUAL_ENV") or "none", "none")
env = a1_config.child_env("http://127.0.0.1:1/anthropic", "not-a-key")
pinned = env.get("A2_PYTHON") or ""
ck("A2_PYTHON is set", "yes" if pinned else "ABSENT", "yes")
if pinned:
    note("A2_PYTHON", pinned)
    ck("A2_PYTHON is executable", "yes" if os.access(pinned, os.X_OK) else "no", "yes")
    # Not `import pytest` in THIS interpreter: the question is what the arms' interpreter has.
    #
    # And this check is an early filter, NOT the authority. It runs OUTSIDE the sandbox, where
    # more is readable: `/usr/bin/python3` imports pytest 8.4.2 here, from
    # ~/Library/Python/3.9/lib/python/site-packages -- a path the arm profile does not grant,
    # which is exactly why the same interpreter answered `No module named pytest` inside every
    # v2 arm-run. A pin that works here can still fail there. The per-arm gate, which runs
    # inside the profile under both shell startups, is what settles it.
    v = subprocess.run([pinned, "-c",
                        "import sys,pytest;print(f'{sys.version.split()[0]} pytest "
                        "{pytest.__version__}')"], capture_output=True, text=True)
    # Matched against a PATTERN, not against itself. Comparing the output to
    # `v.stdout.strip() or "a version"` passes for any non-empty output whatever, which is a
    # check that cannot fail -- the exact shape of vacuity this sweep exists to refuse.
    got = v.stdout.strip()
    why = (v.stderr.strip().splitlines() or ["no output"])[-1][:70]
    ck("A2_PYTHON has pytest",
       got if re.fullmatch(r"\d+\.\d+\.\d+ pytest \d+\.\d+.*", got) else f"NO — {why}",
       got)


def _forwarder(port=8899):
    try:
        socket.create_connection(("127.0.0.1", port), 3).close()
        return "listening"
    except OSError as exc:
        return f"not listening ({exc.__class__.__name__})"


ck("model forwarder on :8899", _forwarder(), "listening")

print("\nthe A2-R output directory")
note("output directory", SCRATCH)
conflict = R2.scope_conflict(SCRATCH)
ck("scope is A2-R's alone", conflict or "no conflict", "no conflict")
a2r = RC.consumed(SCRATCH)
ck("A2-R unresolved arm-runs", str(a2r["unresolved"]), "0")
note("A2-R charged so far", f"{a2r['tokens_budgeted']:,} of "
                            f"{R2.SOFT_LAUNCH_THRESHOLD:,} (soft)")
note("A2-R arm-runs so far", a2r["arm_runs"])

# ---------------------------------------------------------------------------------------
# HISTORICAL, and deliberately NOT a gate. The closed calibration has one arm-run with no
# usable usage record and no written allowance, so its consumption is a LOWER BOUND. That is
# its permanent state: `CLOSEOUT-calib-a2.md` §7 reconciles it without resolving it. Making
# A2-R wait on it would either block this sweep forever or invite an allowance written to
# unblock it -- which is how a budget comes to say whatever one likes.
print("\nthe closed calibration, for visibility only (not a gate)")
if CALIB_SCRATCH.exists():
    old = RC.consumed(CALIB_SCRATCH)
    note("calibration charged", f"{old['tokens_budgeted']:,} tokens over "
                                f"{old['arm_runs']} arm-runs")
    note("calibration unresolved", f"{old['unresolved']} arm-run(s) — "
                                   f"still outstanding, not resolved by A2-R")
    for u in old["unresolved_arm_runs"]:
        note("", u)
    note("consumption", "CERTAIN" if old["consumption_certain"] else "A LOWER BOUND")
else:
    note("calibration scratch", f"{CALIB_SCRATCH} not present on this host")

print()
if bad:
    print("PREFLIGHT FAILED:")
    for b in bad:
        print(f"  - {b}")
else:
    print("PREFLIGHT OK — the frozen A2-R identity matches and the runtime is fit.")
    print(f"run from, and do not change, checkout {head}")
    print(f"  python3 benchmarks/agent/run_a2r.py {SCRATCH} --ceiling {CEILING}")
    print("\nThis does NOT authorise the run. LAUNCH-A2R.md must be approved first.")
raise SystemExit(1 if bad else 0)
