"""Re-check every frozen A3 identifier, the runtime and the preconditions, immediately
before an A3 launch.

NOT `preflight-a2r.py`, and not `preflight-a2.py`. Each guards a different authorisation and
each must keep asserting what it froze:

  preflight-a2.py   the CLOSED calibration -- `calib-v2`, three config digests, a historical
                    charge, and zero unresolved arm-runs in the calibration directory. That
                    last cannot be satisfied and must not be: one calibration arm-run is
                    unresolved on purpose and no allowance was written for it.
  preflight-a2r.py  the SPENT A2-R authorisation -- `calib-v3`, 12 arm-runs, three arms.
  preflight-a3.py   this one -- `a3-v1`, 16 arm-runs, TWO POLICIES over one arm.

At the repository ROOT, for the reason `preflight-a2.py` gives: a file under
`benchmarks/agent` would change the `harness_revision` it exists to verify.

  HEAD is the EXECUTION CHECKOUT: reported, never pinned.
  harness_revision is the MEASURED identity: frozen, and what decides whether rows pool.

What this adds over A2-R's preflight, because A3 varies something A2-R did not:

  * a POLICY dimension. The result identity is `(task, attempt, policy)` and both policies
    run the nexus arm, so `(task, arm)` would name eight slots for sixteen arm-runs. The
    assembly diff and the eight prompt digests are asserted here.
  * a corpus whose PROVENANCE has been reviewed rather than scanned, and whose review must
    still match the corpus it reviews.
  * a boundary board whose required controls must be green AND whose narrow reading must
    still be attached to it.

Exits nonzero if anything has moved.  Usage:  python3 preflight-a3.py
"""
import hashlib, json, os, re, socket, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
BENCH = REPO / "benchmarks/agent"
sys.path.insert(0, str(BENCH))
import a1_config, a3_prompts as AP, identity as I, isolation, schedule as sched

CEILING, WALL_CLOCK, ARM_RUNS, PAIRS = 45, 600, 16, 8

FROZEN = {
    "product": "2cd531f9d7c274a065533e58ba3fde8582f8c269",
    "harness": "f8c548b5a9ebac6aedbd8af6834b8d5e6c2a4974",
    "config_version": "a3-v1",
    "config_digest": "40f1f18bcfd25cb6",
    "max_turns": 45,
    "wall_clock_s": 600,
    # The seeded STORE's row digest -- what the arm runner gates on.
    "corpus_store": "ae03ede5f19a3c74",
    # The corpus CONTENT digest for the `distracting` condition -- WHICH MEMORIES. A
    # different claim from the one above, and both are asserted because either could move
    # without the other: a reseed of the same memories changes neither, an edit to the
    # corpus file changes the content digest, and a store seeded from a different file
    # changes the row digest.
    "corpus_content": "004762cab5248613",
    "store_file_sha256":
        "22a91c249cf5cf3494e3e2cc8733e841138d4c1f66b23bb2c722afc1c8cce00e",
    # The FULL digest. `schedule.py` PRINTS `digest[:16]` and STORES the whole sha256, and
    # freezing the printed prefix is freezing the display rather than the value. The first
    # run of this script caught it, which is what it is for.
    "schedule": "de2821a294bddbc55a327b0d3b931ce9baf78239bbf0c8e777d1e0baff099c43",
    "schedule_seed": 20260919,
    "prompt": {
        "A": {"k1": "5f4f1e210aa7f7ea", "k2": "0aa687b3be6b6a26",
              "k3": "b99a1fd5366f507f", "k4": "97ee8c80330b164a"},
        "B": {"k1": "2b00dc58add11488", "k2": "4ca030e6960fb03a",
              "k3": "1edd570c435baa61", "k4": "34da2ea06f44f590"},
    },
    "provenance_json": "8a2eca820be5d8a6711a2b3bc249b54ccb646a652479c65e80c6433890284d59",
}

#: A2-R's and the calibration's frozen identities, asserted here too. A3 does not read them,
#: and that is exactly why they need a guard: nothing else this script touches would notice
#: an `a3-v1` configuration written over one of them, and that would destroy the provenance
#: of a published sweep.
OTHER_SWEEPS = {"a2r-config-45.json": ("calib-v3", "22eb0a3766cdde73"),
                "calib-config-30.json": ("calib-v2", None),
                "calib-config-45.json": ("calib-v2", None),
                "calib-config-60.json": ("calib-v2", None)}

bad: list[str] = []


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a],
                          capture_output=True, text=True).stdout.strip()


def ck(name, got, want):
    ok = got == want
    print(f"  {'ok ' if ok else 'BAD'} {name:38s} {got}")
    if not ok:
        bad.append(f"{name}: {got!r} != {want!r}")


def note(name, value):
    """Reported, never asserted."""
    print(f"      {name:38s} {value}")


head = git("rev-parse", "HEAD")
print("A3 preflight — bounded consultation, two policies, one development comparison\n")
note("execution checkout (reported)", head)
ck("product_revision", git("log", "-1", "--format=%H", "--", "src/nexus_memory"),
   FROZEN["product"])
ck("harness_revision", git("log", "-1", "--format=%H", "--", "benchmarks/agent"),
   FROZEN["harness"])
ck("working tree (tracked)", git("status", "--porcelain") or "clean", "clean")

print("\nthe A3 configuration")
cfg_path = BENCH / f"a3-config-{CEILING}.json"
if not cfg_path.exists():
    bad.append(f"{cfg_path} does not exist")
    print(f"  BAD {'configuration present':38s} {cfg_path} is missing")
else:
    cfg = a1_config.load(cfg_path)
    ck("config_version", cfg.config_version, FROZEN["config_version"])
    ck("config_digest", I.config_digest(cfg), FROZEN["config_digest"])
    ck("max_turns", cfg.max_turns, FROZEN["max_turns"])
    ck("wall_clock_s", cfg.wall_clock_s, FROZEN["wall_clock_s"])
    ck("prompts registration", cfg.prompts, "prompts-a3.json")
    ck("notes file (this corpus)", cfg.notes_file, "notes-dev-m1.md")
    ck("corpus_digest (store rows)", cfg.corpus_digest, FROZEN["corpus_store"])
    store = Path(cfg.store_master)
    ck("store master present", "yes" if store.is_file() else f"MISSING {store}", "yes")
    if store.is_file():
        ck("store file sha256",
           hashlib.sha256(store.read_bytes()).hexdigest(), FROZEN["store_file_sha256"])

print("\nthe two policies — one appended paragraph, verified as bytes")
diff = AP.assembly_diff()
ck("assembly diff", "holds" if diff["holds"] else f"FAILS: {diff['failures'][:1]}", "holds")
ck("bound delta is constant", "yes" if diff["delta_is_constant"] else "no", "yes")
note("bound size", f"{diff['bound_bytes']} bytes")
got = AP.digests()
for policy in ("A", "B"):
    for task in AP.TASKS:
        ck(f"prompt digest {policy}/{task}", got[policy][task],
           FROZEN["prompt"][policy][task])
ck("A and B are different policies",
   "yes" if all(got["A"][t] != got["B"][t] for t in AP.TASKS) else "NO", "yes")

print("\nthe corpus, and the review that stands behind it")
corpus = BENCH / "corpus-dev-m1.json"
import verify_dev_m1 as V
c = json.loads(corpus.read_text())
ck("corpus content digest (distracting)",
   V.condition_digest(c, [m["id"] for m in c["memories"]]), FROZEN["corpus_content"])
ck("memories in corpus", len(c["memories"]), 24)
prov_path = BENCH / "provenance-dev-m1.json"
ck("provenance review present", "yes" if prov_path.is_file() else "MISSING", "yes")
if prov_path.is_file():
    ck("provenance review digest",
       hashlib.sha256(prov_path.read_bytes()).hexdigest(), FROZEN["provenance_json"])
    prov = json.loads(prov_path.read_text())
    ck("review covers every memory", prov["memories_reviewed"], len(c["memories"]))
    ck("review matches this corpus", prov["corpus_sha256"],
       hashlib.sha256(corpus.read_bytes()).hexdigest())
    ck("fix-locality scan actually ran", prov["fix_locality_state"], "measured")
    note("standing of the corpus", prov["conclusion"])
    for r in prov["rows"]:
        if r["flagged"]:
            note("FLAGGED", f"{r['id']} — no k4 result may be read as evidence that "
                            f"retrieval located that mechanism unaided")

print("\nthe predeclared facts, and the production path")
facts_path = BENCH / "facts-a3.json"
ck("facts-a3.json present", "yes" if facts_path.is_file() else "MISSING", "yes")
if facts_path.is_file():
    fd = json.loads(facts_path.read_text())
    lab = json.loads((BENCH / "results-dev-m1-r2.json").read_text())["labels"]
    ck("facts cover every task", sorted(fd["facts"]), ["k1", "k2", "k3", "k4"])
    off = [f"{t}/{fid}/{m}" for t, fs in fd["facts"].items() for fid, f in fs.items()
           for m in f["members"] if m not in lab[t]["useful"]]
    ck("every fact member is `useful` there", off or "none", "none")
    note("facts declared", f"{sum(len(v) for v in fd['facts'].values())} over 4 tasks; "
                           f"{sum(1 for fs in fd['facts'].values() for f in fs.values() if f['redundant'])}"
                           f" genuinely redundant, the rest singletons")
ck("production adapter present", "yes" if (BENCH / "run_a3.py").is_file() else "MISSING",
   "yes")
ck("adapter requires the A3 boundary",
   "yes" if "require=isolation.REQUIRE_A3" in (BENCH / "run_a3.py").read_text() else "NO",
   "yes")
ck("adapter assembles per policy",
   "yes" if "AP.assemble(task, policy)" in (BENCH / "run_a3.py").read_text() else "NO", "yes")

print("\nthe frozen A/B schedule")
sched_path = BENCH / "schedule-a3.json"
ck("schedule present", "yes" if sched_path.is_file() else "MISSING", "yes")
if sched_path.is_file():
    s = sched.load(sched_path)                       # raises if edited after freezing
    ck("schedule digest", s.schedule_digest, FROZEN["schedule"])
    ck("schedule seed", s.seed, FROZEN["schedule_seed"])
    ck("schedule arms are the policies", ",".join(s.arms), "A,B")
    ck("pairs", len(s.rows), PAIRS)
    cb = sched.counterbalance(s.rows, tuple(s.arms))
    ck("order is not a constant", cb["distinct_orders"], 2)
    note("orders drawn", ", ".join(cb["orders_used"]))
    note("ran first", str(cb["times_each_arm_ran_first"]))

print("\nthe boundary — required controls, and how narrowly the board may be read")
eb = BENCH / "boundary-evidence-a3.json"
ck("boundary evidence present", "yes" if eb.is_file() else "MISSING", "yes")
if eb.is_file():
    ev = json.loads(eb.read_text())
    ck("required controls named", sorted(ev.get("required_controls", [])),
       sorted(isolation.REQUIRE_A3))
    ck("required unresolved", ev.get("required_unresolved"), [])
    ck("required failed", ev.get("required_failed"), [])
    ck("all_hold", ev.get("all_hold"), True)
    ck("conclusion attached", "yes" if ev.get("conclusion") else "MISSING", "yes")
    for n in (ev.get("conclusion") or {}).get("not_established", []):
        note("NOT established", n[:96])
    note("", "this board was captured on a prepared A2-R arm. It must be RE-RUN on the "
             "prepared A3 arms; an earlier green board is not a property of this sweep.")

print("\nthe other sweeps' identities, guarded so A3 cannot overwrite them")
for name, (want_version, want_digest) in OTHER_SWEEPS.items():
    f = BENCH / name
    ck(f"{name} version",
       a1_config.load(f).config_version if f.exists() else "(absent)", want_version)
    if want_digest and f.exists():
        ck(f"{name} digest", I.config_digest(a1_config.load(f)), want_digest)

print("\nthe runtime the arms will actually get")
ck("no virtualenv active", os.environ.get("VIRTUAL_ENV") or "none", "none")
env = a1_config.child_env("http://127.0.0.1:1/anthropic", "not-a-key")
pinned = env.get("A2_PYTHON") or ""
ck("A2_PYTHON is set", "yes" if pinned else "ABSENT", "yes")
if pinned:
    note("A2_PYTHON", pinned)
    ck("A2_PYTHON is executable", "yes" if os.access(pinned, os.X_OK) else "no", "yes")
    # Not `import pytest` in THIS interpreter: the question is what the arms' interpreter
    # has. An early filter, not the authority -- more is readable outside the sandbox than
    # in, which is why the same interpreter answered "No module named pytest" inside every
    # v2 arm-run. The per-arm gate settles it.
    v = subprocess.run([pinned, "-c",
                        "import sys,pytest;print(f'{sys.version.split()[0]} pytest "
                        "{pytest.__version__}')"], capture_output=True, text=True)
    got_v = v.stdout.strip()
    why = (v.stderr.strip().splitlines() or ["no output"])[-1][:70]
    ck("A2_PYTHON has pytest",
       got_v if re.fullmatch(r"\d+\.\d+\.\d+ pytest \d+\.\d+.*", got_v) else f"NO — {why}",
       got_v)


def _forwarder(port=8899):
    try:
        socket.create_connection(("127.0.0.1", port), 3).close()
        return "listening"
    except OSError as exc:
        return f"not listening ({exc.__class__.__name__})"


ck("model forwarder on :8899", _forwarder(), "listening")

print("\nthe budget, as proposed — NOT approved by this script")
note("launch unit", "the A/B pair. 8 pairs, 16 arm-runs.")
note("per-pair reservation", "4 831 570 tokens — a PLANNING figure set aside before a "
                            "pair is admitted, not a cap on what it may spend")
note("proposed soft threshold", "30 000 000, cap_is_soft: true, checked BEFORE a pair starts")
note("disclosed overshoot", "NO NUMERIC MAXIMUM IS ESTABLISHED. An admitted pair may carry "
                            "consumption above 30 000 000 and is not bounded by the "
                            "reservation estimate.")
note("", "the threshold is an engineering proposal and a green preflight is not its "
         "approval. LAUNCH-A3.md must be approved first.")

print("\nprior unauthorised execution, recorded and NOT netted against this")
inc = REPO / "INCIDENT-a3-unauthorized-spend.md"
ck("incident record present", "yes" if inc.is_file() else "MISSING", "yes")
note("2026-09-20", "2 669 285 tokens spent on k1/attempt1 A and B by a rehearsal that "
                   "reached a stale forwarder on the production port")
note("", "those two arm-runs are NOT A3 results, are not reused, and are not netted "
         "against the 30 000 000 proposal in either direction")

print()
if bad:
    print("PREFLIGHT FAILED:")
    for b in bad:
        print(f"  - {b}")
else:
    print("PREFLIGHT OK — the frozen A3 identity matches and the runtime is fit.")
    print(f"run from, and do not change, checkout {head}")
    print("\nThis does NOT authorise the run. LAUNCH-A3.md must be approved first, and the "
          "boundary board must be re-run on the prepared A3 arms.")
raise SystemExit(1 if bad else 0)
