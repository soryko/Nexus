"""Verify an arm's ACTUAL sandbox before spending on it. No model is invoked.

A1 and the v1 calibration rows were measured in an environment where three things silently did
not work. None appeared in `permission_denials`, which counts one envelope field; they appeared
in tool OUTPUT, where nothing was looking.

Every check is EXECUTED inside `sandbox-exec -f <the arm's own profile>` **with the arm's own
restricted child environment**. An earlier version of this file built that environment and then
forgot to pass it, so its probes ran with the operator's environment and could pass where the
arm would fail. The environment is a required argument now; there is no default to forget.

Predicates state what passing MEANS, per check, because a shell pipeline's exit status is its
last stage's and hides everything before it:

  * the interpreter check requires `click.__file__` to resolve INSIDE the intended checkout --
    importing some other click would otherwise pass;
  * the test check runs pytest with no pipeline, requires rc == 0, and requires a non-empty
    passed count with no failures or errors. `1 failed, 23 passed` passed the previous version;
  * egress is checked with `isolation.paired`, which runs the same action inside and outside
    the boundary. A probe that fails for its own reasons proves nothing -- a missing `curl`,
    a down proxy and an enforced sandbox all look alike from the inside.

Usage:  verify_arm_environment.py <arm-dir> [--repo-root R] [--json out.json]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import isolation                                                        # noqa: E402

PYTEST_SUMMARY = re.compile(r"(\d+) passed")
PYTEST_BAD = re.compile(r"\b(\d+) (?:failed|error|errors)\b|Interrupted|INTERNALERROR")


def _sh(profile: Path, repo: Path, env: dict, script: str, timeout: int = 300):
    return subprocess.run(["sandbox-exec", "-f", str(profile), "/bin/zsh", "-c", script],
                          cwd=str(repo), capture_output=True, text=True, env=env,
                          timeout=timeout)


def check_interpreter(profile: Path, repo: Path, env: dict) -> dict:
    """The documented invocation must import the checkout -- and the RIGHT checkout."""
    d = _sh(profile, repo, env,
            'PYTHONPATH=src python3 -c "import click; print(click.__file__)"')
    out = (d.stdout + d.stderr).strip()
    where = out.splitlines()[-1] if out else ""
    inside = False
    try:
        inside = Path(where).resolve().is_relative_to(repo.resolve())
    except (ValueError, OSError):
        inside = False
    return {"check": "interpreter imports the intended checkout",
            "passed": d.returncode == 0 and inside,
            "detail": f"click.__file__={where or '(none)'} inside_repo={inside}"}


def check_tests(profile: Path, repo: Path, env: dict, target: str) -> dict:
    """A representative test must actually execute. No pipeline: rc is pytest's own."""
    d = _sh(profile, repo, env, f'PYTHONPATH=src python3 -m pytest {target} -q')
    out = (d.stdout + d.stderr).strip()
    m = PYTEST_SUMMARY.search(out)
    passed_n = int(m.group(1)) if m else 0
    bad = bool(PYTEST_BAD.search(out))
    return {"check": "documented test command executes a test",
            "passed": d.returncode == 0 and passed_n > 0 and not bad,
            "detail": f"rc={d.returncode} passed={passed_n} failures_or_errors={bad} "
                      f":: {out.splitlines()[-1] if out else ''}"}


def check_heredoc(profile: Path, repo: Path, env: dict) -> dict:
    d = _sh(profile, repo, env, "cat <<EOF\nHEREDOC-OK\nEOF", timeout=60)
    return {"check": "here-document works",
            "passed": d.returncode == 0 and "HEREDOC-OK" in d.stdout,
            "detail": (d.stderr or d.stdout).strip().splitlines()[-1:]}


def check_scratch(profile: Path, repo: Path, env: dict) -> dict:
    d = _sh(profile, repo, env,
            'printf "SCRATCH-OK\\n" > _envprobe.txt && cat _envprobe.txt; rm -f _envprobe.txt',
            timeout=60)
    return {"check": "scratch file round-trips in the checkout",
            "passed": "SCRATCH-OK" in d.stdout,
            "detail": (d.stderr or d.stdout).strip().splitlines()[-1:]}


def check_egress(profile: Path, repo: Path) -> dict:
    """Paired: the same action inside and outside the boundary.

    `isolation.paired` is already the project's control for this and is reused rather than
    reinvented. It reports `demonstrates_boundary` only when the action FAILS inside and
    SUCCEEDS outside, so a missing binary or a down network cannot masquerade as enforcement.
    """
    r = isolation.paired(profile, ["/usr/bin/curl", "-sS", "--max-time", "8",
                                   "https://pypi.org/simple/"], repo)
    return {"check": "egress denied (paired control)",
            "passed": bool(r["demonstrates_boundary"]),
            "detail": f"blocked_inside={r['blocked_inside']} works_outside={r['works_outside']}"}


def check_heredoc_private(profile: Path, repo: Path, env: dict,
                          other_profile: Path | None, other_repo: Path | None) -> dict:
    r = isolation.heredoc_probe(profile, repo, env, other_profile, other_repo)
    return {"check": "heredoc scratch is arm-private",
            # `None` means not tested. Not tested is not passed.
            "passed": r.get("cross_arm_read_blocked") is True,
            "detail": f"tmpprefix_set={r['tmpprefix_set']} "
                      f"cross_arm_read_blocked={r.get('cross_arm_read_blocked')}"}


def run(arm: Path, env: dict, test_target: str = "tests/test_context.py",
        other_arm: Path | None = None) -> dict:
    """Gate one PREPARED arm, using the profile and environment it will actually run under."""
    profile, repo = arm / "sandbox.sb", arm / "repo"
    if not profile.exists() or not repo.exists():
        raise SystemExit(f"{arm}: needs both sandbox.sb and repo/")
    checks = [
        check_interpreter(profile, repo, env),
        check_tests(profile, repo, env, test_target),
        check_heredoc(profile, repo, env),
        check_scratch(profile, repo, env),
        check_egress(profile, repo),
    ]
    if other_arm is not None:
        checks.append(check_heredoc_private(profile, repo, env,
                                            other_arm / "sandbox.sb", other_arm / "repo"))
    return {"arm": str(arm), "checks": checks,
            "all_passed": all(c["passed"] for c in checks)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    arm = Path(argv[1])
    env = a1_config.child_env("http://127.0.0.1:8899", os.environ.get("DEEPSEEK_API_KEY", ""),
                              {"TMPPREFIX": str(arm / "tmp" / "zsh")})
    (arm / "tmp").mkdir(parents=True, exist_ok=True)
    report = run(arm, env)
    for c in report["checks"]:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']:42s} {str(c['detail'])[:80]}")
    print(f"\n{'ALL CHECKS PASS' if report['all_passed'] else 'ENVIRONMENT NOT FIT: do not spend'}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(report, indent=1) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
