"""Re-check every frozen identifier and the ledger, immediately before a calibration launch.

At the repository ROOT, not under `benchmarks/agent`: a file there would change the
`harness_revision` this script exists to verify.

It exists because no single guard covers the ways the instrument can move:

  a commit under benchmarks/agent   changes harness_revision   -- caught by the revision check
  an UNCOMMITTED edit there         changes the executing code but NOT the recorded revision,
                                    because `git log` reads commit history, not the worktree
                                    -- caught only by the clean-tree check
  an edit to calib-config-*.json    changes neither: those files are gitignored, so
                                    `git status --porcelain` does not mention them
                                    -- caught only by the configuration digest

Exits nonzero if anything has moved.  Usage:  python3 preflight-a2.py
"""
import subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "benchmarks/agent"))
import a1_config, identity as I, run_calibration as RC

FROZEN = {"checkout": "8ea2f84cf645bab61ee10a2bed627b6347065d83",
          "product": "2cd531f9d7c274a065533e58ba3fde8582f8c269",
          "harness": "19b843e437f2b1bf68080ae4e38f373f1c3d7715",
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

ck("checkout", git("rev-parse", "HEAD"), FROZEN["checkout"])
ck("product_revision", git("log", "-1", "--format=%H", "--", "src/nexus_memory"), FROZEN["product"])
ck("harness_revision", git("log", "-1", "--format=%H", "--", "benchmarks/agent"), FROZEN["harness"])
ck("working tree (tracked)", git("status", "--porcelain") or "clean", "clean")
for c, want in FROZEN["config"].items():
    ck(f"config_digest c{c}", I.config_digest(a1_config.load(REPO / f"benchmarks/agent/calib-config-{c}.json")), want)
cfg = a1_config.load(REPO / "benchmarks/agent/calib-config-30.json")
for t, want in FROZEN["prompt"].items():
    ck(f"prompt_digest {t}", I.prompt_digest(cfg, t), want)
led = RC.consumed(Path("/Users/soko/Cerebros/nexus-a1-fixtures/calib-run"))
ck("ledger charged", str(led["tokens_budgeted"]), str(FROZEN["charged"]))
ck("unresolved arm-runs", str(led["unresolved"]), "0")
print(f"\n{'PREFLIGHT OK' if not bad else 'PREFLIGHT FAILED: ' + '; '.join(bad)}")
print(f"remaining against the cap: {RC.CAP_TOKENS - led['tokens_budgeted']:,} tokens")
raise SystemExit(1 if bad else 0)
