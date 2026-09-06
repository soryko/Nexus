"""Print the runtime the suite is about to use, and refuse a runtime it cannot trust.

A result without its interpreter and SQLite version is not a result, and the versions that
matter are the ones the *test process* sees: the runner's system `sqlite3` binary is a
different library from the one CPython is linked against, and reporting it would describe
something that never runs a test. Every requirement is read from the code that enforces it,
so this cannot drift from the floor the storage layer actually applies.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile

from nexus_memory.git.cli import GitCli
from nexus_memory.storage.sqlite import SQLiteRepository


def main() -> int:
    failures: list[str] = []
    floor = SQLiteRepository.MINIMUM_SQLITE

    print(f"interpreter   : {sys.executable}")
    print(f"python        : {sys.version.split()[0]}")
    print(f"sqlite linked : {sqlite3.sqlite_version}")
    print(f"sqlite floor  : {'.'.join(map(str, floor))} (SQLiteRepository.MINIMUM_SQLITE)")
    if sqlite3.sqlite_version_info < floor:
        failures.append(f"linked SQLite {sqlite3.sqlite_version} is below the required floor")

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        print("fts5          : available")
    except sqlite3.Error as error:
        print("fts5          : UNAVAILABLE")
        failures.append(f"FTS5 is not compiled in: {error!r}")
    finally:
        connection.close()

    # Not root: the permission test asserts what permission bits do, and bits do not bind
    # root, so a root runner would skip it and quietly shrink the suite.
    euid = os.geteuid()
    print(f"euid          : {euid}{' — ROOT, permission tests will skip' if euid == 0 else ''}")
    if euid == 0:
        failures.append("running as root; the permission test cannot assert anything")

    git = subprocess.run(["git", "--version"], capture_output=True, text=True)
    print(f"git           : {git.stdout.strip() or 'ABSENT'}")
    if git.returncode != 0:
        failures.append("git is not runnable")
    else:
        # The controls are guarantees, not hygiene: without them a replacement ref can
        # substitute a tree and a pathspec can become a pattern. A git that does not accept
        # one of them cannot enforce what verification claims to enforce.
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True, capture_output=True)
            for control in GitCli.CONTROLS:
                probe = subprocess.run(["git", control, "rev-parse", "--git-dir"],
                                       cwd=directory, capture_output=True, text=True)
                accepted = probe.returncode == 0
                print(f"git control   : {control} {'accepted' if accepted else 'REJECTED'}")
                if not accepted:
                    failures.append(f"git does not accept {control}: {probe.stderr.strip()}")

    if failures:
        print("\nthis runtime cannot run the suite meaningfully:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nruntime verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
