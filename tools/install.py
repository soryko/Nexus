#!/usr/bin/env python3
"""Build a Nexus environment that satisfies BOTH runtime floors, or refuse and say why.

Two independent requirements, and meeting one does not imply the other:

  * Python >= 3.12          -- `requires-python` in pyproject.toml
  * linked SQLite >= 3.51.3 -- `SQLiteRepository.MINIMUM_SQLITE`, enforced at startup

A Python that satisfies the version floor can still fail the SQLite one, and the failure is
quiet where it matters most: an MCP client that launches a below-floor interpreter sees only
the transport close, because `startup_error: unsupported_runtime` goes to the server's
stderr where no client is looking.

Which SQLite an interpreter links is a property of that individual build, not of its Python
version, its installation source or the `sqlite3` executable on PATH. Builds from the same
distributor and the same version series have been observed on both sides of the floor. So
nothing here assumes a distributor is safe or unsafe: the probe below decides, per
interpreter, and `uv sync` alone can still produce an environment that cannot start this
server.

This probes candidate interpreters, reports BOTH numbers for each, builds the environment
from the first that passes, and then **re-checks the environment it built** rather than
trusting that the venv inherited what the candidate had.

    python3 tools/install.py                      # probe and build .venv
    python3 tools/install.py --python /path/to/python3.12
    python3 tools/install.py --check-only         # report, build nothing
    python3 tools/install.py --venv "$HOME/.local/share/nexus-memory/venvs/0.1.0a2"

What this builds is a NORMAL installation: the package is copied into the environment,
not linked back to this checkout, and development dependencies are left out. So the
sources are needed to build and to upgrade, and not to run. Point `--venv` outside the
checkout and the checkout becomes disposable; the default `.venv` is kept for
compatibility and is not, since deleting the checkout deletes it too.

Developers want the opposite and should keep using `uv sync --frozen`, which installs the
project editable with the development dependencies. Running that against an environment
built here converts it back to an editable one.

Exit status: 0 built and verified, 1 no interpreter meets both floors, 2 the build could
not run or ran and produced something that does not meet them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = ("import sys, sqlite3, json; "
         "print(json.dumps({'python': list(sys.version_info[:3]), "
         "'sqlite': sqlite3.sqlite_version, 'executable': sys.executable}))")


def floors() -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The two floors, READ from the sources that enforce them.

    Hardcoding them here would let this script and the server disagree, and the script that
    disagrees is the one people run before they find out.
    """
    py = re.search(r'requires-python\s*=\s*">=\s*([\d.]+)"',
                   (REPO / "pyproject.toml").read_text())
    sq = re.search(r"MINIMUM_SQLITE\s*=\s*\(([\d,\s]+)\)",
                   (REPO / "src/nexus_memory/storage/sqlite.py").read_text())
    if not py or not sq:
        raise SystemExit("install.py: could not read the runtime floors from the sources "
                         "that enforce them; refusing to guess")
    return (tuple(int(p) for p in py.group(1).split(".")),
            tuple(int(p.strip()) for p in sq.group(1).split(",") if p.strip()))


def _sh(word: str | Path) -> str:
    """One shell word, safe to paste.

    The success message is meant to be copied into a terminal, so a path containing a space
    must arrive as ONE argument. Unquoted, `/some/env with spaces/bin/nexus-memory-check`
    is three words and the shell reports 127 -- a "command not found" for a command that is
    installed and working, which sends the reader looking for the wrong fault entirely.
    """
    if os.name == "nt":                      # cmd/PowerShell, not POSIX quoting
        return f'"{word}"' if " " in str(word) else str(word)
    return shlex.quote(str(word))


def _version() -> str:
    """The version being installed, for the suggested per-version environment path.

    Read from the same file that declares it, so the suggestion cannot name a version
    this checkout does not actually build. A miss here only costs the example its
    precision, so it degrades to a placeholder instead of refusing to install.
    """
    m = re.search(r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(),
                  re.MULTILINE)
    return m.group(1) if m else "VERSION"


def inspect(python: str) -> dict | None:
    """-> {python, sqlite, executable} for an interpreter, or None if it will not run."""
    try:
        r = subprocess.run([python, "-c", PROBE], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def verdict(info: dict, py_floor: tuple, sq_floor: tuple) -> tuple[bool, str]:
    pv = tuple(info["python"])
    sv = tuple(int(x) for x in info["sqlite"].split("."))
    why = []
    if pv < py_floor:
        why.append(f"python {'.'.join(map(str, pv))} < {'.'.join(map(str, py_floor))}")
    if sv < sq_floor:
        why.append(f"sqlite {info['sqlite']} < {'.'.join(map(str, sq_floor))}")
    return (not why), ", ".join(why) or "meets both floors"


def candidates(explicit: str | None) -> list[str]:
    """Interpreters to try, best-guess order. An explicit one is the only one considered.

    The order is a search heuristic and nothing more. No distributor is assumed to link a
    conforming SQLite: every candidate is probed and reported, and the first that passes
    BOTH floors is the one used. If none passes, none is substituted.
    """
    if explicit:
        return [explicit]
    seen, out = set(), []
    for c in ("python3.14", "python3.13", "python3.12",
              "/opt/homebrew/bin/python3.14", "/opt/homebrew/bin/python3.13",
              "/opt/homebrew/bin/python3.12", "/usr/local/bin/python3",
              "/usr/bin/python3", "python3", sys.executable):
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", help="use this interpreter and no other")
    ap.add_argument("--venv", default=".venv")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    py_floor, sq_floor = floors()
    print(f"Nexus runtime floors: python >= {'.'.join(map(str, py_floor))}, "
          f"sqlite >= {'.'.join(map(str, sq_floor))}\n")

    chosen = None
    for c in candidates(a.python):
        info = inspect(c)
        if info is None:
            print(f"  --  {c:<44} would not run")
            continue
        ok, why = verdict(info, py_floor, sq_floor)
        v = ".".join(map(str, info["python"]))
        print(f"  {'OK ' if ok else '-- '} {c:<44} python {v:<9} sqlite "
              f"{info['sqlite']:<9} {'' if ok else '(' + why + ')'}")
        if ok and chosen is None:
            chosen = c
            if a.python:
                break

    if chosen is None:
        print("\nNo interpreter meets BOTH floors.\n"
              "  Install one, for example:  brew install python@3.13\n"
              "  then re-run:               python3 tools/install.py\n"
              "A Python that meets the version floor can still link an older SQLite; both\n"
              "numbers above are reported for exactly that reason.")
        return 1
    print(f"\nchosen: {chosen}")
    if a.check_only:
        return 0

    # uv builds the environment, so its absence is a prerequisite failure, not a crash.
    # Checked after --check-only so a report still works on a host without uv.
    if shutil.which("uv") is None:
        print("\nuv is required to build the environment, and it is not on PATH.\n"
              "  Install it:  https://docs.astral.sh/uv/getting-started/installation/\n"
              f"  then re-run: {sys.executable} tools/install.py"
              + (f" --python {chosen}" if a.python else ""))
        return 2

    venv = REPO / a.venv
    print(f"building {venv} ...")
    # An explicit choice must not be quietly overridden by an inherited request. uv reads
    # UV_PYTHON from the environment, and CI exports it; dropping it here is what keeps
    # "the interpreter I named" and "the interpreter uv used" the same interpreter. The
    # built environment is re-checked below either way.
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)}
    env.pop("UV_PYTHON", None)
    # `--no-editable` is what makes the installation survive its source. A plain
    # `uv sync` installs the project EDITABLE: the environment gets a path entry pointing
    # back at this checkout, so the server keeps running only for as long as the checkout
    # stays where it is. `--no-dev` leaves pytest and hypothesis out of a user runtime.
    # `--python` is belt and braces over UV_PROJECT_ENVIRONMENT: the environment was just
    # created from `chosen`, and naming it again means a stale or inherited preference
    # cannot quietly resolve the sync somewhere else.
    for cmd in (["uv", "venv", "--python", chosen, str(venv)],
                ["uv", "sync", "--frozen", "--no-editable", "--no-dev",
                 "--python", chosen]):
        try:
            r = subprocess.run(cmd, cwd=REPO, env=env)
        except OSError as exc:
            # Losing the executable between the check above and the launch here is rare,
            # but a diagnosis costs nothing and a traceback costs the reader everything.
            print(f"\ncould not run {' '.join(cmd)}: {exc}\n"
                  "Nothing has been verified.")
            return 2
        if r.returncode != 0:
            print(f"\n{' '.join(cmd)} failed. Nothing has been verified.")
            return 2

    # Re-check what was BUILT. The venv is what the client launches, and assuming it
    # inherited the candidate's SQLite is the assumption this script exists to remove.
    exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    info = inspect(str(exe))
    if info is None:
        print(f"\n{exe} will not run. Nothing has been verified.")
        return 2
    ok, why = verdict(info, py_floor, sq_floor)
    v = ".".join(map(str, info["python"]))
    print(f"\nbuilt environment: python {v}, sqlite {info['sqlite']} -> "
          f"{'OK' if ok else 'BELOW FLOOR: ' + why}")
    if not ok:
        print("REFUSING to report success: this environment cannot start the server.")
        return 2

    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    server = venv / scripts / f"nexus-memory{suffix}"
    checker = venv / scripts / f"nexus-memory-check{suffix}"

    # Whether the environment lives inside the checkout decides whether the checkout is
    # disposable, and it is the one thing a user cannot infer from a success message.
    # `--venv` defaults to `.venv` HERE, so the common case is the dependent one.
    try:
        inside = venv.resolve().is_relative_to(REPO.resolve())
    except OSError:
        inside = False
    if inside:
        note = (f"\nThis environment is INSIDE the checkout ({REPO}).\n"
                f"  The package itself no longer needs these sources, but deleting this\n"
                f"  directory would delete the environment along with them. To make the\n"
                f"  checkout disposable, build somewhere else, for example:\n"
                f"    {_sh(sys.executable)} tools/install.py --venv "
                f'"$HOME/.local/share/nexus-memory/venvs/{_version()}"\n')
    else:
        note = (f"\nThis environment is OUTSIDE the checkout ({REPO}),\n"
                f"  which may now be moved or deleted. Keep the environment and your\n"
                f"  database where they are; neither is relocatable.\n")

    # The two paths above are shown bare, to be read. The command below is shown quoted,
    # to be run. They are the same path and the difference is deliberate.
    print(f"\nVerified. The installed commands are:\n  {server}\n  {checker}\n"
          + note
          + f"\nCheck it end to end (starts, stores, restarts, reads back):\n"
          f"  {_sh(checker)}\n\n"
          f"MCP client entry:\n"
          + json.dumps({"mcpServers": {"nexus-memory": {
              "command": str(server),
              "args": ["--namespace", "my-repo", "--actor", "local"]}}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
