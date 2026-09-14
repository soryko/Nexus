#!/bin/bash
# Pre-launch verification for the A2 calibration, against the revision it is run at.
#
# Every section is the OUTPUT of a command, captured verbatim. Nothing here summarises a run
# or restates a result -- the published record and the commands that produced it are the same
# text, so a reader can re-run this file and diff.
#
# No model is invoked and nothing is spent.
#
#   ./make-verification.sh > PRELAUNCH-VERIFICATION.md
set -uo pipefail
cd "$(dirname "$0")"
ARM=${ARM:-/Users/soko/Cerebros/nexus-a1-fixtures/calib-run/c30/run-k1/attempt1/arms/baseline}

echo "# Pre-launch verification — A2 calibration"
echo
echo "Produced by \`make-verification.sh\` against the exact revision below. Every line is the"
echo "output of a command that was run, not a summary of one. No model was invoked."
echo
echo '```'
echo "revision           $(git rev-parse HEAD)"
echo "branch             $(git branch --show-current)"
echo "date (UTC)         $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "config version     $(python3 -c "import json;print(json.load(open('calib-config-30.json'))['config_version'])")"
echo "schedule digest    $(python3 -c "import json;print(json.load(open('schedule-calib-a2.json'))['schedule_digest'][:16])")"
echo '```'
echo
echo "## Accounting and driver counterexamples"
echo "Both driver paths are exercised with a stub runner: normal completion and the budget stop."
echo '```'
python3 test_calibration_counterexamples.py 2>&1 \
  | grep -vE "^(calibration:|ceilings |=== |    exit|known consumption|STOPPED AT|$)"
echo '```'
echo
echo "## Write-detector counterexamples"
echo '```'
python3 test_diagnose_counterexamples.py
echo '```'
echo
echo "## Arm environment gate, against a prepared arm"
echo '```'
python3 verify_arm_environment.py "$ARM"
echo '```'
echo
echo "## Isolation: heredocs work, and are arm-private"
echo "A sentinel written through one arm's profile, read through another's."
echo '```'
python3 - <<'PY'
import os, sys, tempfile, shutil
sys.path.insert(0, '.')
from pathlib import Path
import isolation, a1_config
BASE = Path(os.environ.get(
    "ARM", "/Users/soko/Cerebros/nexus-a1-fixtures/calib-run/c30/run-k1/attempt1/arms/baseline"))
t = Path(tempfile.mkdtemp()); A, B = t / "baseline", t / "nexus"
for a in (A, B):
    (a / "tmp").mkdir(parents=True)
    shutil.copytree(BASE / "repo", a / "repo", symlinks=True)
def prof(me, other):
    return isolation.write_profile(
        me / "sandbox.sb", me / "repo", [other],
        allow_paths=isolation.default_allow_paths(me / "repo") + [me], forwarder_port=8899)
pA, pB = prof(A, B), prof(B, A)
env = a1_config.child_env("http://127.0.0.1:8899", "", {"TMPPREFIX": str(A / "tmp" / "zsh")})
for k, v in isolation.heredoc_probe(pA, A / "repo", env, pB, B / "repo").items():
    print(f"{k}: {v}")
PY
echo '```'
echo
echo "## Product suite"
echo '```'
( cd ../.. && .venv-sqlite/bin/python -m pytest -q 2>&1 | tail -3 )
echo '```'
