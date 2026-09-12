"""Kernel-enforced isolation for one arm, and probes that run inside the boundary.

Two surfaces, both enforced by `sandbox-exec` rather than requested by environment:

  network     everything denied except the one loopback port the forwarder listens on, so
              the arm reaches the model endpoint and nothing else -- not another local
              service, and not a proxy of its own. DNS is denied too, so a name lookup for
              an external host fails before a socket is opened.
  filesystem  reads are DENIED BY DEFAULT and re-admitted only for the runtime the arm
              needs and for its own checkout. The previous profile inverted this: it
              allowed everything and then named a handful of paths to deny, which left the
              whole benchmark directory -- saved patches, reports, the corpus and the task
              sheet that states each fix -- readable to the arm. It also dropped any deny
              whose path did not exist yet (`if p.exists()`), so a directory created later
              in the run, such as the scoring area, was never denied at all. Denies are now
              unconditional, and they are written AFTER the allows so they win inside an
              otherwise-allowed subtree.

The probes execute INSIDE the profile the arm will use, because a probe run outside it
measures the harness's own reachability and not the arm's. The previous egress probe also
ran with PIP_NO_INDEX set, so it could fail without attempting a connection at all; these
deliberately leave the index enabled.

A deny-by-default boundary can fail in the opposite direction: a profile that blocks the
runtime blocks the negative controls too, and reads as maximally secure while making the
arm impossible to run. `check_boundary` therefore carries positive controls -- the checkout
is readable, the interpreter runs, `git` runs -- and `all_hold` requires those as well.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

# Read roots the runtime genuinely needs. Deliberately NOT /private/tmp or /var: the run
# tree, the sibling arms, the fixtures and the source clone all live under the scratchpad,
# and a blanket allow there would re-open everything this profile exists to close.
SYSTEM_READ_ROOTS = (
    "/usr", "/bin", "/sbin", "/System", "/Library", "/Applications",
    "/private/etc", "/etc", "/dev",
    "/private/var/folders",          # TMPDIR
    "/private/var/db", "/private/var/run", "/private/var/select",
    "/opt/homebrew", "/opt/local",
)

# The runner is Claude Code: its install, its node runtime and its own configuration.
HOME_READ_ROOTS = (
    ".local/share/claude", ".local/bin", ".local/state",
    ".nvm", ".npm", ".npmrc", ".cache", ".config",
    ".claude", ".claude.json", ".gitconfig", ".gitignore_global",
    "Library/Caches", "Library/Preferences",
)

# ...minus the parts of `~/.claude` that hold transcripts of earlier work on this very
# repository. They are inside an allowed subtree, so they need an explicit deny.
HOME_DENIES = (
    ".claude/projects", ".claude/history.jsonl", ".claude/sessions",
    ".claude/file-history", ".claude/paste-cache", ".claude/shell-snapshots",
    ".claude/backups", ".claude/debug", ".claude/plans", ".claude/tasks",
    ".claude/todos", ".claude/telemetry",
)

HEADER = """(version 1)
(allow default)

; ---- network: deny everything, then re-admit only the forwarder's loopback port ----
(deny network-outbound)
{net}

; ---- filesystem: deny reads, then re-admit the runtime and this arm's own checkout ----
(deny file-read*)
(allow file-read-metadata)   ; path resolution only -- conveys no file contents
(allow file-read* (literal "/"))   ; the root directory entry; dyld aborts without it
"""


def _q(p) -> str:
    return str(p).replace('\\', '\\\\').replace('"', '\\"')


def default_allow_paths(arm_repo: Path, python: str | None = None) -> list[Path]:
    """Runtime the arm needs, plus its own checkout. Nothing from the benchmark tree."""
    allows = [Path(r) for r in SYSTEM_READ_ROOTS]
    allows += [HOME / r for r in HOME_READ_ROOTS]
    allows.append(Path(arm_repo))
    if python:
        # Both spellings of the interpreter: a venv's `bin/python` is a symlink to the base
        # interpreter, so `resolve()` alone points outside the venv and leaves `pyvenv.cfg`
        # unreadable -- which is how the first run of these probes failed.
        py = Path(python)
        allows += [py.parent.parent, py.resolve(), py.resolve().parent.parent]
    return allows


def profile_for(arm_repo: Path, deny_paths: list[Path], allow_paths: list[Path] | None = None,
                forwarder_port: int | None = None) -> str:
    """Deny-by-default reads. `deny_paths` are applied last and are NOT existence-filtered."""
    net = (f'(allow network-outbound (remote ip "localhost:{forwarder_port}"))'
           if forwarder_port else '(allow network-outbound (remote ip "localhost:*"))')
    allows = list(allow_paths if allow_paths is not None else default_allow_paths(arm_repo))
    denies = [*(HOME / d for d in HOME_DENIES), *deny_paths]
    lines = [HEADER.format(net=net)]
    for p in allows:
        lines.append(f'(allow file-read* (subpath "{_q(p)}"))'
                     if Path(p).is_dir() or not Path(p).exists()
                     else f'(allow file-read* (literal "{_q(p)}"))')
    lines.append("\n; ---- denied unconditionally: applied after the allows, and never "
                 "skipped for\n;      a path that does not exist yet ----")
    for p in denies:
        lines.append(f'(deny file-read* (subpath "{_q(p)}"))')
        lines.append(f'(deny file-write* (subpath "{_q(p)}"))')
    return "\n".join(lines) + "\n"


def write_profile(path: Path, arm_repo: Path, deny_paths: list[Path],
                  allow_paths: list[Path] | None = None,
                  forwarder_port: int | None = None) -> Path:
    """Note what is NOT here: the `if p.exists()` filter the previous version applied to
    `deny_paths`. A path that does not exist yet is exactly the case that needs denying --
    the scoring directory is created part-way through the run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_denies = [Path(os.path.realpath(p)) for p in deny_paths]
    path.write_text(profile_for(Path(arm_repo).resolve(), resolved_denies,
                                allow_paths, forwarder_port))
    return path


def probe(profile: Path, argv: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["sandbox-exec", "-f", str(profile), *argv], cwd=str(cwd),
                          capture_output=True, text=True, timeout=timeout)


def paired(profile: Path, argv: list[str], cwd: Path) -> dict:
    """Run an action inside the boundary AND outside it.

    A negative control that fails for its own reasons proves nothing -- the first filesystem
    probe "passed" with `No such file or directory`, which is what an absent path says too.
    A boundary is demonstrated only when the same action succeeds without it and fails with
    it, so both halves are run and both are recorded.
    """
    inside = subprocess.run(["sandbox-exec", "-f", str(profile), *argv], cwd=str(cwd),
                            capture_output=True, text=True, timeout=90)
    outside = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=90)
    return {"blocked_inside": inside.returncode != 0,
            "works_outside": outside.returncode == 0,
            "demonstrates_boundary": inside.returncode != 0 and outside.returncode == 0,
            "inside_tail": (inside.stderr or inside.stdout).strip().splitlines()[-1:],
            "outside_tail": (outside.stderr or outside.stdout).strip().splitlines()[-1:]}


def _first_file(root: Path) -> Path | None:
    if root.is_file():
        return root
    return next((p for p in sorted(root.rglob("*")) if p.is_file()), None) if root.exists() else None


def future_path_probe(profile: Path, deny_paths: list[Path], cwd: Path) -> dict:
    """A denied path that does not exist when the profile is written must still be denied
    once it appears. This is the defect the review reproduced against the scoring directory:
    the old `write_profile` dropped such a path from the profile entirely.

    The probe materialises the first absent deny path, pairs a read against it, and removes
    only what it created.
    """
    target = next((Path(p) for p in deny_paths if not Path(p).exists()), None)
    if target is None:
        return {"applicable": False,
                "reason": "every denied path already exists -- probe would not test the "
                          "absent-path case"}
    created_root = target
    while created_root.parent != created_root and not created_root.parent.exists():
        created_root = created_root.parent
    try:
        target.mkdir(parents=True, exist_ok=True)
        witness = target / "boundary-probe-answers.txt"
        witness.write_text("held-out answer material would live here\n")
        out = paired(profile, ["/bin/cat", str(witness)], cwd)
        out["applicable"] = True
        out["path"] = str(target)
        return out
    finally:
        shutil.rmtree(created_root, ignore_errors=True)


def check_boundary(profile: Path, cwd: Path, held_checks: Path, python: str,
                   deny_paths: list[Path] | None = None,
                   bench_dir: Path | None = None, runner: str = "claude") -> dict:
    """Negative controls, executed inside the boundary. All must FAIL for the arm to run --
    and the positive controls must all SUCCEED, or the profile is merely broken."""
    out: dict = {}

    # network: index deliberately ENABLED, so a failure means the socket was refused
    dl = probe(profile, [python, "-m", "pip", "download", "click", "--no-deps",
                         "-d", str(cwd / ".probe-dl")], cwd)
    fetched = sorted(p.name for p in (cwd / ".probe-dl").glob("*")) if (cwd / ".probe-dl").exists() else []
    out["network_egress_blocked"] = dl.returncode != 0 and not fetched
    out["network_probe_tail"] = (dl.stderr or dl.stdout).strip().splitlines()[-1:]

    # DNS specifically: a name lookup for an external host must not succeed
    dns = probe(profile, ["/usr/bin/curl", "-sS", "-m", "6", "-o", "/dev/null",
                          "https://pypi.org/simple/click/"], cwd)
    out["dns_and_https_blocked"] = dns.returncode != 0
    out["dns_probe_tail"] = (dns.stderr or "").strip().splitlines()[-1:]

    # filesystem: the held-out checks must be unreadable from inside
    held_file = _first_file(held_checks)
    if held_file is not None:
        out["held_checks_paired"] = paired(profile, ["/bin/cat", str(held_file)], cwd)
        out["held_checks_unreadable"] = out["held_checks_paired"]["demonstrates_boundary"]
    else:
        out["held_checks_paired"] = {"demonstrates_boundary": False,
                                     "reason": "no held check file found -- probe would be vacuous"}
        out["held_checks_unreadable"] = False

    # the benchmark directory holds the saved patches, the reports, the corpus and the task
    # sheet. Under the old `(allow default)` profile every one of them was readable.
    if bench_dir is not None:
        bench_file = _first_file(Path(bench_dir))
        out["bench_dir_paired"] = (paired(profile, ["/bin/cat", str(bench_file)], cwd)
                                   if bench_file else
                                   {"demonstrates_boundary": False, "reason": "no file found"})
        out["bench_dir_unreadable"] = out["bench_dir_paired"]["demonstrates_boundary"]
    else:
        out["bench_dir_unreadable"] = None

    # a denied path that did not exist when the profile was written
    out["future_deny_paired"] = future_path_probe(profile, list(deny_paths or []), cwd)
    out["future_path_denied"] = (out["future_deny_paired"].get("demonstrates_boundary")
                                 if out["future_deny_paired"].get("applicable") else None)

    # ---- positive controls: a boundary that blocks the runtime is not a boundary ----
    own = probe(profile, ["/bin/sh", "-c",
                          f'cat "{cwd}/pyproject.toml" >/dev/null 2>&1 || '
                          f'ls "{cwd}" >/dev/null'], cwd)
    out["own_checkout_readable"] = own.returncode == 0
    py = probe(profile, [python, "-c", "import sys, json; print(sys.version)"], cwd)
    out["interpreter_runs"] = py.returncode == 0
    out["interpreter_tail"] = (py.stderr or "").strip().splitlines()[-1:]
    g = probe(profile, ["git", "-C", str(cwd), "status", "--porcelain"], cwd)
    out["git_runs"] = g.returncode == 0
    out["git_tail"] = (g.stderr or "").strip().splitlines()[-1:]
    # the runner itself must start inside the boundary. `--version` makes no model call, so
    # this costs nothing; without it a profile that silently aborts Claude Code reads as a
    # perfect boundary. It also pins the version the arm actually ran under.
    rv = probe(profile, [runner, "--version"], cwd)
    out["runner_starts"] = rv.returncode == 0
    out["runner_version"] = (rv.stdout or "").strip().splitlines()[-1:]
    out["runner_tail"] = (rv.stderr or "").strip().splitlines()[-1:]

    negative = ["network_egress_blocked", "dns_and_https_blocked", "held_checks_unreadable"]
    if out["bench_dir_unreadable"] is not None:
        negative.append("bench_dir_unreadable")
    if out["future_path_denied"] is not None:
        negative.append("future_path_denied")
    positive = ["own_checkout_readable", "interpreter_runs", "git_runs", "runner_starts"]
    out["negative_controls"] = {k: out[k] for k in negative}
    out["positive_controls"] = {k: out[k] for k in positive}
    out["all_hold"] = all(out[k] for k in (*negative, *positive))
    return out


if __name__ == "__main__":
    repo, held, py = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    bench = Path(__file__).parent
    denies = [held, held.parent, bench, *held.parent.parent.glob("arms")]
    prof = write_profile(Path("/tmp/a1-probe.sb"), repo, denies,
                         default_allow_paths(repo, py))
    import json
    print(json.dumps(check_boundary(prof, repo, held, py, denies, bench), indent=1))
