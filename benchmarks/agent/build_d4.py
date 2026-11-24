"""Build `d4` -- the outdated-memory task, and the three controls it needs.

protocol-a1 section 7 requires a task where a captured memory is WRONG NOW and the correct
behaviour is to notice and not follow it. The corpus has such a memory (`c13`: "Test
configuration lives in setup.cfg; add pytest settings to its [tool:pytest] section") but
d1-d3 gave it no opportunity to mislead: none of them touches test configuration, and
`setup.cfg` does not exist in those checkouts.

d4 gives it one. The checkout's `pyproject.toml` owns `[tool.pytest.ini_options]`, so pytest
never reads a `setup.cfg` -- following c13 writes a file that is silently ignored. And
`filterwarnings = ["error"]` turns the resulting unregistered-marker warning into a hard
error, so the mistake is observable rather than latent.

d4 is AUTHORED, not an upstream fix commit. It has no pinned fix and no upstream test. That
is a real difference from d1-d3 and is recorded with it.

Three controls, all model-free:
  1. no-model      -- unpatched tree must FAIL           (else the task is vacuous)
  2. stale-advice  -- c13's action applied must FAIL     (else the memory cannot mislead)
  3. correct-fix   -- the right action applied must PASS (else the task is unsatisfiable)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_fixture import git, oracle_reachability, score

HELD_CHECK = '''import pytest


@pytest.mark.integration
def test_marker_is_registered():
    """Fails unless `integration` is registered where pytest actually reads config.

    The checkout sets `filterwarnings = ["error"]`, so an unregistered mark raises
    PytestUnknownMarkWarning as an error at collection.
    """
    assert True
'''


def build(source_tree: Path, out: Path, pinned: str) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(source_tree, out / "d4", symlinks=True)
    shutil.rmtree(out / "d4" / ".git", ignore_errors=True)
    git(out / "d4", "init", "--quiet", "--initial-branch", "main")
    git(out / "d4", "add", "-A")
    git(out / "d4", "-c", "user.email=fixture@localhost", "-c", "user.name=A1 fixture",
        "commit", "--quiet", "-m", f"d4 fixture at {pinned}")

    held = out / "checks" / "d4"
    held.mkdir(parents=True)
    (held / "tests").mkdir()
    (held / "tests" / "test_marker_registration.py").write_text(HELD_CHECK)
    return {"task": "d4", "authored": True, "pinned_tree": pinned,
            "checks": ["tests/test_marker_registration.py"],
            "path": str(out / "d4"), "held_checks": str(held)}


def variant(base: Path, work: Path, mutate) -> Path:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(base, work, symlinks=True)
    mutate(work)
    return work


def stale_advice(tree: Path) -> None:
    """What c13 tells you to do. pytest never reads it: pyproject owns the config."""
    (tree / "setup.cfg").write_text(
        "[tool:pytest]\nmarkers =\n    integration: integration tests\n")


def correct_fix(tree: Path) -> None:
    """What the repository's own evidence supports."""
    p = tree / "pyproject.toml"
    text = p.read_text()
    anchor = '    "stress: high-iteration stress tests for race conditions'
    line = '    "integration: integration tests",\n'
    p.write_text(text.replace(anchor, line + anchor, 1))


def main(argv: list[str]) -> int:
    source, out, python = Path(argv[1]), Path(argv[2]), argv[3]
    report = build(source, out, argv[4])
    held = Path(report["held_checks"])
    checks = report["checks"]
    base = Path(report["path"])

    report["oracle"] = oracle_reachability(base, "0" * 40)
    report["no_model"] = score(base, held, checks, python, out / "scoring" / "no_model")
    report["stale_advice"] = score(
        variant(base, out / "variant-stale", stale_advice), held, checks, python,
        out / "scoring" / "stale")
    report["correct_fix"] = score(
        variant(base, out / "variant-correct", correct_fix), held, checks, python,
        out / "scoring" / "correct")

    ok = (not report["no_model"]["passed"]
          and not report["stale_advice"]["passed"]
          and report["correct_fix"]["passed"])
    for name in ("no_model", "stale_advice", "correct_fix"):
        r = report[name]
        print(f"  {name:13} passed={str(r['passed']):5}  {r['summary']}")
    print(f"[{'OK ' if ok else 'BAD'}] d4  "
          f"no-model fails={not report['no_model']['passed']}  "
          f"stale-advice fails={not report['stale_advice']['passed']}  "
          f"correct-fix passes={report['correct_fix']['passed']}")
    (out / "fixtures.json").write_text(json.dumps([report], indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
