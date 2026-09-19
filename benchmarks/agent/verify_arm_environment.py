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


# The two shell startups a command can meet. `zsh -c` is what this gate used to run and
# nothing else; `zsh -l -c` runs /etc/zprofile, whose `path_helper` rebuilds PATH with the
# system directories first. The arms' own recorded `which -a python3` output -- /usr/bin
# before /opt/homebrew -- matches the login form and not the other, so a check that only ever
# ran the non-login form was testing a command the agent never executed.
#
# This does NOT assert that the Bash tool literally passes `-l`. It asserts something the gate
# can check and that is sufficient either way: the documented commands must work, and resolve
# the SAME interpreter, under both startups. A tool that picks either one is then covered.
STARTUPS = {"non_login": ["/bin/zsh", "-c"], "login": ["/bin/zsh", "-l", "-c"]}


def _sh(profile: Path, repo: Path, env: dict, script: str, timeout: int = 300,
        startup: str = "non_login"):
    return subprocess.run(["sandbox-exec", "-f", str(profile), *STARTUPS[startup], script],
                          cwd=str(repo), capture_output=True, text=True, env=env,
                          timeout=timeout)


def _both(profile: Path, repo: Path, env: dict, script: str, timeout: int = 300) -> dict:
    """Run one script under every startup. -> {startup: CompletedProcess}."""
    return {name: _sh(profile, repo, env, script, timeout, name) for name in STARTUPS}


def _last(d) -> str:
    out = (d.stdout + d.stderr).strip()
    return out.splitlines()[-1] if out else ""


def _agree(results: dict, extract) -> tuple[bool, dict]:
    """-> (every startup produced the same non-empty value, what each produced).

    An empty value is never agreement. Two startups that both printed nothing would otherwise
    compare equal and pass, having compared nothing -- the vacuous shape.
    """
    seen = {name: extract(d) for name, d in results.items()}
    values = set(seen.values())
    return (len(values) == 1 and all(v for v in seen.values())), seen


def _tagged(tag: str):
    """Extract a marker line rather than the last line.

    A login shell may print something of its own, and `path_helper` is not the only thing
    /etc/zprofile can do. Reading the LAST line makes the answer depend on whatever the shell
    said afterwards; reading a tagged line does not.
    """
    def extract(d) -> str:
        for line in (d.stdout + d.stderr).splitlines():
            if line.startswith(tag):
                return line[len(tag):].strip()
        return ""
    return extract


def check_pinned_interpreter(profile: Path, repo: Path, env: dict) -> dict:
    """`$A2_PYTHON` must be set, and must be the SAME interpreter under either startup.

    This is the check whose absence let the v2 sweep run: the gate resolved one interpreter
    and the agent's shell resolved another, and nothing compared them.
    """
    if not env.get("A2_PYTHON"):
        return {"check": "pinned interpreter is named, not resolved", "passed": False,
                "detail": "A2_PYTHON is not set in the arm's environment"}
    script = ('"$A2_PYTHON" -c "import sys, pytest; '
              'print(\'A2RUNTIME\', sys.executable, sys.version.split()[0], '
              'pytest.__version__)"')
    res = _both(profile, repo, env, script, timeout=120)
    same, seen = _agree(res, _tagged("A2RUNTIME"))
    ok = same and all(d.returncode == 0 for d in res.values())
    return {"check": "pinned interpreter is named, not resolved", "passed": ok,
            "detail": f"identical_under_both_startups={same} :: " +
                      "; ".join(f"{k}={v or '(none)'}" for k, v in seen.items()),
            "runtime": seen.get("login") or seen.get("non_login", "")}


def check_interpreter(profile: Path, repo: Path, env: dict) -> dict:
    """The documented import command must import the checkout -- the RIGHT checkout, and the
    same one under either shell startup."""
    res = _both(profile, repo, env,
                'PYTHONPATH=src "$A2_PYTHON" -c "import click; '
                'print(\'A2CLICK\', click.__file__)"')

    def inside(where: str) -> bool:
        try:
            return Path(where).resolve().is_relative_to(repo.resolve())
        except (ValueError, OSError):
            return False

    same, seen = _agree(res, _tagged("A2CLICK"))
    ok = same and all(d.returncode == 0 for d in res.values()) and all(map(inside, seen.values()))
    return {"check": "documented import resolves this checkout", "passed": ok,
            "detail": f"agree={same} inside_repo={ {k: inside(v) for k, v in seen.items()} } "
                      f":: {seen.get('login', '(none)')}"}


def check_tests(profile: Path, repo: Path, env: dict, target: str) -> dict:
    """A representative test must actually execute -- under EVERY startup, not just this
    gate's own. No pipeline: rc is pytest's own."""
    res = _both(profile, repo, env, f'PYTHONPATH=src "$A2_PYTHON" -m pytest {target} -q')
    detail, ok = [], True
    for name, d in res.items():
        out = (d.stdout + d.stderr).strip()
        m = PYTEST_SUMMARY.search(out)
        passed_n = int(m.group(1)) if m else 0
        bad = bool(PYTEST_BAD.search(out))
        good = d.returncode == 0 and passed_n > 0 and not bad
        ok = ok and good
        detail.append(f"{name}: rc={d.returncode} passed={passed_n} bad={bad}")
    return {"check": "documented test command executes a test",
            "passed": ok, "detail": "; ".join(detail)}


def check_repro_script(profile: Path, repo: Path, env: dict) -> dict:
    """A reproduction script written INTO the checkout must run with the pinned interpreter.

    The prompt tells every arm to put scratch and reproduction files in the checkout, and 16
    of the v2 sweep's 27 arm-runs ran one. Nothing gated that path: `check_scratch` proves a
    file round-trips and `check_interpreter` proves an import works, and neither proves the
    two compose.
    """
    # Raw strings, and the file body written with single-quoted printf: this literal has
    # to survive Python escaping AND zsh quoting, and it did not the first time -- the
    # escapes collapsed, zsh got a broken command, and the check failed while the thing
    # it tests worked. A check that fails for its own reasons proves nothing either way.
    script = (r"""printf 'import click\n' > _reproprobe.py; """
              r"""printf 'print("REPRO-OK", click.__file__)\n' >> _reproprobe.py; """
              r"""PYTHONPATH=src "$A2_PYTHON" _reproprobe.py; rc=$?; """
              r"""rm -f _reproprobe.py; exit $rc""")
    res = _both(profile, repo, env, script, timeout=120)
    ok = all(d.returncode == 0 and "REPRO-OK" in d.stdout for d in res.values())
    return {"check": "reproduction script runs from the checkout", "passed": ok,
            "detail": "; ".join(f"{k}=rc{d.returncode}" for k, d in res.items())}


def check_forwarder(profile: Path, repo: Path, env: dict) -> dict:
    """The one egress the boundary PERMITS must work -- the positive half of `egress denied`.

    `check_egress` proves the boundary holds. Nothing proved the single route through it was
    open, and on 2026-09-14 it was not: the forwarder had not been started, every arm got
    `API Error: Connection refused`, and 36 arm-runs across three ceilings recorded honest
    zeros and an excluded terminal after nine and a half minutes each.

    A TCP connect and nothing more. An HTTP request here would be a model call, and a gate
    that spends is not a gate.
    """
    url = env.get("ANTHROPIC_BASE_URL", "")
    m = re.match(r"https?://([^:/]+):(\d+)", url)
    if not m:
        return {"check": "model forwarder reachable", "passed": False,
                "detail": f"ANTHROPIC_BASE_URL={url!r} names no host and port"}
    host, port = m.group(1), m.group(2)
    probe = ("python3 -c \"import socket; "
             f"s=socket.create_connection(('{host}',{port}),5); s.close(); "
             "print('FORWARDER-OK')\"")
    d = _sh(profile, repo, env, probe, timeout=30)
    out = (d.stdout + d.stderr).strip()
    return {"check": "model forwarder reachable", "passed": "FORWARDER-OK" in d.stdout,
            "detail": f"{host}:{port} :: {out.splitlines()[-1] if out else '(no output)'}"}


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
        check_pinned_interpreter(profile, repo, env),
        check_interpreter(profile, repo, env),
        check_tests(profile, repo, env, test_target),
        check_repro_script(profile, repo, env),
        check_heredoc(profile, repo, env),
        check_scratch(profile, repo, env),
        check_egress(profile, repo),
        # Paired with the line above: the boundary must hold, AND the one route through it
        # must be open. Proving only the first is how a sweep spends two hours reaching
        # nothing.
        check_forwarder(profile, repo, env),
    ]
    if other_arm is not None:
        checks.append(check_heredoc_private(profile, repo, env,
                                            other_arm / "sandbox.sb", other_arm / "repo"))
    # The resolved runtime, captured so a later reader can tell WHICH interpreter a row was
    # measured under rather than inferring it from a config path that may since have moved.
    runtime = next((c.get("runtime") for c in checks if c.get("runtime")), "")
    exe, version, pytest_v = (runtime.split() + ["", "", ""])[:3]
    return {"arm": str(arm), "checks": checks,
            "runtime_identity": {"a2_python": env.get("A2_PYTHON", ""), "sys_executable": exe,
                                 "python_version": version, "pytest_version": pytest_v,
                                 "startups_probed": sorted(STARTUPS)},
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
    ri = report["runtime_identity"]
    print(f"\n  runtime: {ri['sys_executable'] or '(unresolved)'} "
          f"python {ri['python_version'] or '?'} pytest {ri['pytest_version'] or '?'} "
          f"(startups probed: {', '.join(ri['startups_probed'])})")
    print(f"\n{'ALL CHECKS PASS' if report['all_passed'] else 'ENVIRONMENT NOT FIT: do not spend'}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(report, indent=1) + "\n")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
