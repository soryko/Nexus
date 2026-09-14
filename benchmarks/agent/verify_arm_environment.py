"""Verify an arm's ACTUAL sandbox before spending on it. No model is invoked.

A1 and the v1 calibration rows were measured in an environment where three things silently did
not work. None of them appeared in `permission_denials`, which counts one envelope field;
they appeared in tool OUTPUT, where nothing was looking. Every check below is EXECUTED inside
`sandbox-exec -f <the arm's own profile>` with the arm's own child environment.

The checks, and what each one protects:

  interpreter     the documented command imports the intended checkout. A1's arms ran
                  `python3 -c "import click"` against an uninstalled src/ layout, which cannot
                  work, and then spent turns on venv and pip against a denied network.
  tests           the documented test command actually executes a representative test.
  heredoc         here-documents work. They failed in all 36 A1 and all 9 v1 arm-runs.
  scratch         a reproduction script can be written AND read back.
  no discovery    none of the above needs a download or a hunt for an interpreter.
  boundary intact the repair did not open the runner's scratch tree.

A failure here is a reason not to spend, not a note to file.

Usage:  verify_arm_environment.py <arm-dir-with-sandbox.sb-and-repo> [--json out.json]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402

# (name, script, predicate on (rc, output)) -- the predicate is what "passing" means, stated
# per check rather than inferred from the exit status, because a shell pipeline's rc is the
# last stage's and hides the failure of everything before it.
CHECKS = [
    ("interpreter imports checkout",
     'PYTHONPATH=src python3 -c "import click, sys; print(\'IMPORT-OK\', sys.version_info[:2])" 2>&1',
     lambda rc, o: "IMPORT-OK" in o),
    ("documented test command runs",
     'PYTHONPATH=src python3 -m pytest tests/test_context.py -q 2>&1 | tail -3',
     lambda rc, o: " passed" in o and "error" not in o.lower()),
    ("here-document works",
     'cat <<EOF\nHEREDOC-OK\nEOF',
     lambda rc, o: "HEREDOC-OK" in o),
    ("scratch file round-trips in checkout",
     'printf "SCRATCH-OK\\n" > _envprobe.txt && cat _envprobe.txt; rm -f _envprobe.txt',
     lambda rc, o: "SCRATCH-OK" in o),
    ("no interpreter discovery needed",
     'command -v python3 >/dev/null && echo PY-ON-PATH',
     lambda rc, o: "PY-ON-PATH" in o),
    ("network stays denied",
     'curl -sS --max-time 5 https://pypi.org 2>&1 | head -1; echo "rc=$?"',
     lambda rc, o: "Could not resolve" in o or "curl:" in o or "error" in o.lower()),
    ("runner scratch tree stays shut",
     'ls /private/tmp 2>&1 | head -1',
     lambda rc, o: "Operation not permitted" in o),
]


def run(arm: Path) -> dict:
    profile, repo = arm / "sandbox.sb", arm / "repo"
    if not profile.exists() or not repo.exists():
        raise SystemExit(f"{arm}: needs both sandbox.sb and repo/")
    env = a1_config.child_env("http://127.0.0.1:8899", os.environ.get("DEEPSEEK_API_KEY", ""))
    results = []
    for name, script, ok in CHECKS:
        d = subprocess.run(["sandbox-exec", "-f", str(profile), "/bin/zsh", "-c", script],
                           cwd=str(repo), capture_output=True, text=True, timeout=300)
        out = (d.stdout + d.stderr).strip()
        results.append({"check": name, "passed": bool(ok(d.returncode, out)),
                        "rc": d.returncode, "tail": out.splitlines()[-2:]})
    return {"arm": str(arm), "checks": results,
            "all_passed": all(r["passed"] for r in results)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    report = run(Path(argv[1]))
    for r in report["checks"]:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['check']:36s} {' | '.join(r['tail'])[:90]}")
    print(f"\n{'ALL CHECKS PASS' if report['all_passed'] else 'ENVIRONMENT NOT FIT: do not spend'}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(report, indent=1) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
