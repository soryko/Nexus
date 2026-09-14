"""Build one A1 task fixture, and run the two controls that need no model.

A fixture is an isolated checkout at a task's pinned pre-fix commit, with the hidden
acceptance checks staged from the fix commit. Two of ``protocol-a1`` §11's controls are
executable here, before any arm runs and without an API key:

* **§11.1 no-model** -- run the checks with no patch applied. Every task must FAIL. A task
  that passes is vacuous and is removed from the set.
* **§11.4 oracle-reachability** -- assert the fix is unreachable from the checkout: no ref
  or unreferenced object carries it, and no remote is configured to fetch it from.
* **fix-oracle** -- run the hidden checks against the tree at the FIX commit. They must
  PASS. This is the control the other two do not cover, and its absence hid a real defect:
  `h2`'s check file cannot be COLLECTED under the pinned pytest at all, because an unrelated
  `parametrize` call in it trips a deprecation this project's own configuration turns into an
  error. The no-model control was satisfied -- the checks did not pass on an unpatched tree --
  for the wrong reason, since they could not pass on any tree. A task whose checks can never
  pass scores every arm zero and reads, in every figure, as a task nobody solved.

Neither establishes benefit, and §11.4 establishes *retrieval* isolation only -- it says
nothing about whether the model memorised a public fix (§13).

Usage:  python build_fixture.py <source-clone> <task-json> <out-dir>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


#: Version of the FUNCTIONAL check runner -- `score` below -- recorded in every artifact it
#: produces. `score_compliance.SCORER_VERSION` versions the other scorer, the one that reads
#: task-requirement compliance off a trace; these are different instruments and a run needs
#: both numbers to be readable later.
#:
#: `a1-functional-1` ran pytest with no `-W`. `a1-functional-2` adds `PYTEST_IGNORE`, below.
FUNCTIONAL_SCORER_VERSION = "a1-functional-2"

#: One warning class, demoted from error to ignored, for every task equally.
#:
#: Click's own configuration turns warnings into errors. Under the pinned pytest 9.1.1 an
#: unrelated `parametrize` call in `tests/test_basic.py` raises `PytestRemovedIn10Warning`
#: during COLLECTION, which made h2's acceptance checks impossible to pass on any tree --
#: including the tree at its own fix commit. Without this the task scores every arm zero and
#: reads as a task nobody solved.
#:
#: Registered rather than reached for. It was measured on all ten tasks before being adopted
#: and before any held-out result existed: identical counts on d1, d2, d3, h1, h3, h4, c1 and
#: c2; h2's fix-oracle goes from `1 error` to 90 passed and its no-model control to 2 failed /
#: 88 passed, so the task remains non-vacuous. It does not suppress the
#: `PytestUnknownMarkWarning` that d4 is built on, which was checked directly rather than
#: inferred from the class names.
#:
#: It is applied to every task, not to h2. A flag applied where it is needed is an instrument
#: that varies with the task it measures.
PYTEST_IGNORE = ("-W", "ignore::pytest.PytestRemovedIn10Warning")


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def test_paths(clone: Path, pre_fix: str, fix: str) -> list[str]:
    """Test files the fix changed, from the parent diff.

    Never ``git show --name-only``: that returns nothing for a merge commit, which silently
    yields a fixture with no acceptance checks and a no-model control that cannot fail.
    """
    out = git(clone, "diff", "--name-only", pre_fix, fix)
    return [p for p in out.splitlines() if p.startswith("tests/") and p.endswith(".py")]


def fix_oracle(clone: Path, task: dict, checks: list[str], python: str, workdir: Path) -> dict:
    """Run the hidden checks against the tree at the fix commit. They must pass.

    The no-model control asks whether the checks fail WITHOUT the fix; this asks whether they
    can succeed WITH it. Only the pair distinguishes a task the agent has to solve from a task
    that cannot be solved, and the second is indistinguishable from the first in every figure
    the run reports.
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    exported = subprocess.run(["git", "-C", str(clone), "archive", task["fix"]],
                              capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(workdir)], input=exported.stdout, check=True)
    done = subprocess.run([python, "-m", "pytest", *checks, "-q", "-p", "no:randomly",
                           *PYTEST_IGNORE],
                          cwd=workdir, capture_output=True, text=True,
                          env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    return {"exit": done.returncode, "summary": tail, "passed": done.returncode == 0,
            "scorer_version": FUNCTIONAL_SCORER_VERSION,
            "detail": done.stdout.strip()[-1500:] if done.returncode else ""}


def build(clone: Path, task: dict, out: Path) -> dict:
    fix, pre_fix = task["fix"], task["pre_fix"]
    checks = test_paths(clone, pre_fix, fix)
    if not checks:
        raise RuntimeError(f"{task['task']}: no acceptance checks in the fix diff")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # Export the tree and re-init, rather than cloning. The §11.4 control caught a clone:
    # `git clone` copies the whole object database, so the fix commit remained readable by
    # `git cat-file -e` and listable by `git fsck --unreachable` even with every ref and
    # remote removed. Isolation by pruning refs is not isolation. This builds a repository
    # whose object database has never contained the fix -- one commit, no remote, no history.
    exported = subprocess.run(["git", "-C", str(clone), "archive", pre_fix],
                              capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(out)], input=exported.stdout, check=True)
    git(out, "init", "--quiet", "--initial-branch", "main")
    git(out, "add", "-A")
    git(out, "-c", "user.email=fixture@localhost", "-c", "user.name=A1 fixture",
        "commit", "--quiet", "-m", f"Fixture at {pre_fix}")

    # The checks are stored BESIDE the fixture, never inside it. An earlier version staged
    # them into the working tree, where `git diff` showed the agent the new tests -- and a
    # test that asserts the corrected behaviour describes the fix. §6 step 4 says hidden;
    # the agent's checkout must therefore not contain them at any point.
    held = out.parent / "checks" / task["task"]
    if held.exists():
        shutil.rmtree(held)
    held.mkdir(parents=True)
    staged = subprocess.run(["git", "-C", str(clone), "archive", fix, *checks],
                            capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(held)], input=staged.stdout, check=True)

    return {"task": task["task"], "fix": fix, "pre_fix": pre_fix,
            "checks": checks, "path": str(out), "held_checks": str(held)}


def oracle_reachability(fixture: Path, fix: str) -> dict:
    """§11.4 -- assert the fix commit is not reachable from this checkout."""
    refs = git(fixture, "for-each-ref", "--format=%(refname)")
    remotes = git(fixture, "remote")
    present = subprocess.run(["git", "-C", str(fixture), "cat-file", "-e", f"{fix}^{{commit}}"],
                             capture_output=True).returncode == 0
    packed = (fixture / ".git" / "objects" / "pack").exists() and any(
        (fixture / ".git" / "objects" / "pack").iterdir())
    # Unreferenced objects survive a plain clone; a fix reachable only from a dangling
    # object is still reachable. Count what `fsck` can still see.
    dangling = [l for l in git(fixture, "fsck", "--unreachable", check=False).splitlines()
                if "unreachable commit" in l]
    return {"fix_object_present": present, "refs": refs.splitlines(),
            "remotes": remotes.splitlines(), "unreachable_commits": len(dangling),
            "inherited_packs": packed,
            "isolated": (not present) and not remotes.splitlines()
            and len(dangling) == 0 and len(refs.splitlines()) == 1}


def score(tree: Path, held: Path, checks: list[str], python: str, workdir: Path) -> dict:
    """Run the hidden checks against a candidate tree, without ever writing them into it.

    The tree is copied, the held checks are laid over the copy, and pytest runs there. The
    agent's own checkout is left exactly as the agent produced it -- so a later run, or a
    diff of the agent's work, is not polluted by the scoring step.
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(tree, workdir, symlinks=True)
    for relative in checks:
        target = workdir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(held / relative, target)
    done = subprocess.run([python, "-m", "pytest", *checks, "-q", "-p", "no:randomly",
                           *PYTEST_IGNORE],
                          cwd=workdir, capture_output=True, text=True,
                          env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    failed = [l for l in done.stdout.splitlines() if l.startswith("FAILED ")]
    # `passed` is the fact. What it *means* depends on the caller: on an unpatched tree
    # (§11.1) passing means the task is vacuous; on a candidate patch it means the patch
    # works. The field says what happened and the caller names it.
    return {"exit": done.returncode, "summary": tail, "failing_instances": len(failed),
            "failing_functions": len({l.split("[")[0] for l in failed}),
            "scorer_version": FUNCTIONAL_SCORER_VERSION,
            "passed": done.returncode == 0}


def main(argv: list[str]) -> int:
    clone, spec, out = Path(argv[1]), json.loads(Path(argv[2]).read_text()), Path(argv[3])
    python = spec.get("python", sys.executable)
    report = []
    for task in spec["tasks"]:
        built = build(clone, task, out / task["task"])
        built["oracle"] = oracle_reachability(out / task["task"], task["fix"])
        built["no_model"] = score(out / task["task"], Path(built["held_checks"]),
                                  built["checks"], task.get("python", python),
                                  out / "scoring" / task["task"])
        built["checks_absent_from_fixture"] = not any(
            (out / task["task"] / c).exists() and
            (out / task["task"] / c).read_bytes() == (Path(built["held_checks"]) / c).read_bytes()
            for c in built["checks"])
        built["fix_oracle"] = fix_oracle(
            clone, task, built["checks"], task.get("python", python),
            out / "fix-oracle" / task["task"]) if task["fix"] else {
                "passed": None, "summary": "authored task: no upstream fix to check against"}
        report.append(built)
        ok = (built["oracle"]["isolated"] and not built["no_model"]["passed"]
              and built["checks_absent_from_fixture"]
              and built["fix_oracle"]["passed"] is not False)
        print(f"[{'OK ' if ok else 'BAD'}] {task['task']} {task['fix'][:7]}  "
              f"isolated={built['oracle']['isolated']}  "
              f"checks_hidden={built['checks_absent_from_fixture']}  "
              f"no-model: {built['no_model']['summary']}  |  "
              f"fix-oracle: {built['fix_oracle']['summary']}")
        if built["fix_oracle"]["passed"] is False:
            print("       ^ THE CHECKS CANNOT PASS EVEN WITH THE FIX APPLIED. This task is "
                  "not scoreable as registered: every arm scores zero on it, and the result "
                  "reads as a task nobody solved.")
            for line in built["fix_oracle"]["detail"].splitlines()[-4:]:
                print(f"         {line}")
    Path(out / "fixtures.json").write_text(json.dumps(report, indent=1))
    return 0 if all(r["oracle"]["isolated"] and not r["no_model"]["passed"]
                    and r["checks_absent_from_fixture"]
                    and r["fix_oracle"]["passed"] is not False for r in report) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
