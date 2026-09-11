"""Build one A1 task fixture, and run the two controls that need no model.

A fixture is an isolated checkout at a task's pinned pre-fix commit, with the hidden
acceptance checks staged from the fix commit. Two of ``protocol-a1`` §11's controls are
executable here, before any arm runs and without an API key:

* **§11.1 no-model** -- run the checks with no patch applied. Every task must FAIL. A task
  that passes is vacuous and is removed from the set.
* **§11.4 oracle-reachability** -- assert the fix is unreachable from the checkout: no ref
  or unreferenced object carries it, and no remote is configured to fetch it from.

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
    done = subprocess.run([python, "-m", "pytest", *checks, "-q", "-p", "no:randomly"],
                          cwd=workdir, capture_output=True, text=True,
                          env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"})
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    failed = [l for l in done.stdout.splitlines() if l.startswith("FAILED ")]
    # `passed` is the fact. What it *means* depends on the caller: on an unpatched tree
    # (§11.1) passing means the task is vacuous; on a candidate patch it means the patch
    # works. The field says what happened and the caller names it.
    return {"exit": done.returncode, "summary": tail, "failing_instances": len(failed),
            "failing_functions": len({l.split("[")[0] for l in failed}),
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
        report.append(built)
        ok = (built["oracle"]["isolated"] and not built["no_model"]["passed"]
              and built["checks_absent_from_fixture"])
        print(f"[{'OK ' if ok else 'BAD'}] {task['task']} {task['fix'][:7]}  "
              f"isolated={built['oracle']['isolated']}  "
              f"checks_hidden={built['checks_absent_from_fixture']}  "
              f"no-model: {built['no_model']['summary']}")
    Path(out / "fixtures.json").write_text(json.dumps(report, indent=1))
    return 0 if all(r["oracle"]["isolated"] and not r["no_model"]["passed"]
                    and r["checks_absent_from_fixture"] for r in report) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
