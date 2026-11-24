"""Run untrusted benchmark checkouts with a private filesystem and no host network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

from artifact_io import directory_fd, regular_at


class ScoringUnavailable(OSError):
    """The harness could not safely perform the measurement."""


def _copy_regular(fd: int, name: str, target: Path) -> None:
    with regular_at(fd, name) as src:
        with target.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        mode = os.fstat(src.fileno()).st_mode
        target.chmod(0o700 if mode & 0o111 else 0o600)


def overlay_check(held: Path, name: str, stage: Path) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ScoringUnavailable(f"unsafe hidden-check path: {name}")
    target = stage / path
    # stage is our private, link-free copy. The source is opened component by component.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with directory_fd(held / path.parent) as fd:
        _copy_regular(fd, path.name, target)


def copy_candidate(source: Path, destination: Path) -> None:
    """Copy regular files without following a candidate-controlled link, even during a race.

    The registered Click checkouts need no symlinks or special files. Use directory-relative
    opens so replacing a component during traversal cannot redirect a host read. Always copy
    into fresh harness-private storage, never into a path supplied by the arm.
    """
    def raise_error(exc: OSError):
        raise exc

    with directory_fd(source) as source_fd:
        destination.mkdir()
        for directory, dirs, files, fd in os.fwalk(".", dir_fd=source_fd, onerror=raise_error):
            target = destination / directory
            for name in dirs:
                if not stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                    raise ScoringUnavailable(f"candidate directory link refused: {name}")
                (target / name).mkdir()
            for name in files:
                _copy_regular(fd, name, target / name)


def _runtime_roots(executable: Path) -> set[Path]:
    """Only the interpreter installation and its venv, never the working user's home."""
    actual = executable.resolve(strict=True)
    roots = {executable, actual}
    for prefix in (executable.parent.parent, actual.parent.parent):
        # A system or user prefix can also contain etc/, var/, unrelated applications
        # or credentials. The interpreter's libraries and venv marker suffice.
        for item in ("lib", "pyvenv.cfg"):
            path = prefix / item
            if path.exists():
                roots.add(path)
    return roots


def _mac_command(executable: Path, work: Path, scratch: Path, extra: list[Path]) -> list[str]:
    # The arm policy grants Claude and a model forwarder. A scorer needs neither. No
    # process-fork, signal, network, Mach IPC, host writes or home reads are granted.
    paths = ["/System/Library", "/usr/lib", "/Library/Apple/System/Library",
             "/Library/Developer/CommandLineTools/usr/lib",
             *extra]
    read = " ".join(f"(subpath {json.dumps(str(p))})" for p in paths)
    # Homebrew Python extensions and Git link against other keg libraries. Never expose
    # Homebrew's etc/, var/, or a user's local config as part of the runtime.
    read += ' (regex #"^/(opt/homebrew|usr/local)/Cellar/[^/]+/[^/]+/(lib|Frameworks)/")'
    write = " ".join(f"(subpath {json.dumps(str(p))})" for p in (work, scratch))
    exe = json.dumps(str(executable.resolve()))
    profile = f"""(version 1)
(deny default)
(allow file-read-metadata sysctl-read)
(allow file-read* (literal "/") (literal "/dev/random") (literal "/dev/urandom")
                  (literal "/dev/null") {read} {write})
(allow file-map-executable {read} (literal {exe}))
(allow file-write* {write} (literal "/dev/null"))
(allow process-exec (literal {exe}))
"""
    return ["/usr/bin/sandbox-exec", "-p", profile]


def _linux_command(work: Path, scratch: Path, extra: list[Path]) -> list[str]:
    if not Path("/usr/bin/bwrap").is_file():
        raise ScoringUnavailable("install bubblewrap and enable unprivileged user namespaces")
    cmd = ["/usr/bin/bwrap", "--die-with-parent", "--unshare-all", "--new-session",
           "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev"]
    # Keep user configuration, credentials, the host PID table and Unix sockets off the mount
    # tree. The dynamic loader needs the host's runtime, including merged-/usr aliases.
    for item in ("/usr/bin", "/usr/lib", "/usr/lib64", "/usr/share", "/bin", "/lib", "/lib64"):
        root = Path(item)
        if root.is_symlink():
            cmd += ["--symlink", os.readlink(root), item]
        elif root.is_dir():
            cmd += ["--ro-bind", item, item]
    for root in sorted(set(extra), key=str):
        if not any(root.is_relative_to(prefix) for prefix in ("/usr/bin", "/usr/lib", "/usr/lib64")):
            cmd += ["--ro-bind", str(root), str(root)]
    # Freeze the directories bwrap created to reach each bind target, including /dev/shm
    # on its separate tmpfs. --remount-ro preserves the writable work and scratch submounts.
    return [*cmd, "--bind", str(work), str(work), "--bind", str(scratch), str(scratch),
            "--remount-ro", "/dev", "--remount-ro", "/",
            "--chdir", str(work), "--"]


def run(args: list[str], work: Path, *, input_text: str | None = None,
        scratch: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Start only the confined command. A missing or rejected sandbox is an instrument error."""
    exe = Path(args[0])
    if not exe.is_absolute():
        raise ScoringUnavailable("scoring requires a trusted absolute executable")
    if work.is_symlink():
        raise ScoringUnavailable("refusing a symlinked scoring root")
    work = work.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="nexus-grade-") as temp:
        scratch = (scratch or Path(temp)).resolve(strict=True)
        # For system commands e.g. /usr/bin/patch, do not grant the full /usr prefix on macOS.
        roots = list(_runtime_roots(exe)) if exe.name.startswith("python") else [exe.resolve()]
        framework_env = {}
        if sys.platform == "darwin":
            # Homebrew framework Python's bin/python is a launcher which spawns the app
            # executable. Invoke the latter directly with the documented launcher variable
            # so the venv is retained without permitting an untrusted process to fork.
            app = exe.resolve().parent.parent / "Resources/Python.app/Contents/MacOS/Python"
            if exe.name.startswith("python") and app.is_file():
                framework_env["__PYVENV_LAUNCHER__"] = str(exe)
                exe = app
                args = [str(app), *args[1:]]
                roots.append(app)
            prefix = _mac_command(exe, work, scratch, roots)
        elif sys.platform == "linux":
            prefix = _linux_command(work, scratch, roots)
        else:
            raise ScoringUnavailable(f"no scoring sandbox for {sys.platform}")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(scratch), "TMPDIR": str(scratch),
               "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **framework_env}
        kwargs = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
        done = subprocess.run([*prefix, *args], cwd=work, env=env, capture_output=True,
                              text=True, errors="replace", timeout=timeout, **kwargs)
        if done.returncode and done.stderr.startswith(("sandbox-exec:", "bwrap:")):
            raise ScoringUnavailable(done.stderr.strip())
        return done


def git_binary() -> str:
    git = shutil.which("git")
    if not git:
        raise ScoringUnavailable("git is unavailable")
    return str(Path(git).resolve())


def candidate_patch(tree: Path) -> str:
    """A candidate controls .git/config, attributes, filters and possible hooks."""
    with tempfile.TemporaryDirectory(prefix="nexus-diff-") as td:
        work = Path(td) / "tree"
        copy_candidate(tree, work)
        git = [git_binary(), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
        run([*git, "add", "-N", "-A"], work).check_returncode()
        diff = run([*git, "diff", "--no-ext-diff", "--no-textconv"], work)
        diff.check_returncode()
        return diff.stdout
