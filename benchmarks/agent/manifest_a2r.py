"""Checksum A2-R's preserved artifacts, in the sweep directory and in the repository.

Two rules, both learned the hard way:

  `SHA256SUMS` is NOT regenerated.  It is A1's freeze snapshot, deliberately stale, and
                                    rewriting it would erase the record it exists to be.
                                    A2-R gets its own file.

  the tree is named, not globbed.   `run_arms_isolated` leaves a working CHECKOUT per arm --
                                    a full click tree with its own `.git`, 12 664 files and
                                    169 MB across the sweep, most of it the fixture repeated.
                                    A manifest over all of it would be unreadable and would
                                    change whenever a `__pycache__` was touched. What is
                                    checksummed is the EVIDENCE: the records, traces, patches,
                                    scoring outputs, sandbox profiles, launch and preflight
                                    logs -- plus, so the checkouts are not simply dropped, one
                                    digest per arm over its tracked working tree.

    python3 manifest_a2r.py <scratch> [--out <path>] [--check <path>]
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent

#: Evidence in the sweep directory, by name. A pattern that matched "everything under the
#: arm" would sweep in the checkout; these are the files a reader reads.
SWEEP_GLOBS = (
    "a2r-summary.json", "compliance-a2r.json", "compliance-a2r-r2.json",
    "preflight-at-launch.txt", "sweep.log",
    "c*/run-*/attempt*/records.json", "c*/run-*/attempt*/launch.json",
    "c*/run-*/attempt*.log",
    "c*/run-*/attempt*/arms/*/record.json", "c*/run-*/attempt*/arms/*/trace.jsonl",
    "c*/run-*/attempt*/arms/*/patch.diff", "c*/run-*/attempt*/arms/*/launched.json",
    "c*/run-*/attempt*/arms/*/envcheck.json", "c*/run-*/attempt*/arms/*/mcp.json",
    "c*/run-*/attempt*/arms/*/sandbox.sb", "c*/run-*/attempt*/arms/*/stderr.txt",
    "c*/run-*/attempt*/ordering-probe/*.sb",
    "c*/run-*/base/fixtures.json",
)

#: The analysis published in the repository. Kept in the same manifest so a reader can tell
#: at one glance whether a figure they are reading came from a file that has since moved.
REPO_FILES = (
    "benchmarks/agent/results-a2r.json", "benchmarks/agent/results-a2r.txt",
    "benchmarks/agent/results-a2r-summary.json",
    "benchmarks/agent/results-a2r-compliance.json",
    "benchmarks/agent/results-a2r-r2.json", "benchmarks/agent/results-a2r-r2.txt",
    "benchmarks/agent/results-a2r-compliance-r2.json",
    "benchmarks/agent/results-a2r-runs.json", "benchmarks/agent/results-a2r-runs.txt",
    "benchmarks/agent/results-turn-accounting.json",
    "benchmarks/agent/a2r-config-45.json",
    "benchmarks/agent/prompts-calib-a2.json", "benchmarks/agent/tasks-calib-a2.json",
    "benchmarks/agent/schedule-calib-a2.json", "benchmarks/agent/notes-heldout-a1.md",
    "benchmarks/agent/CLOSEOUT-a2r.md", "benchmarks/agent/DECISION-a2r-failures.md",
    "benchmarks/agent/DIAGNOSTIC-turn-accounting.md",
    "benchmarks/agent/REGISTRATION-DRAFT-a2r.md",
    "LAUNCH-A2R.md", "preflight-a2r.py",
)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(repo: Path) -> str | None:
    """One digest per arm checkout, over its TRACKED working tree.

    `git stash` was used as a negative control in several arm-runs, so the working tree is
    the state that matters and `HEAD` would not show it. Untracked scratch files are already
    in `patch.diff`, which is checksummed by name above.
    """
    if not (repo / ".git").exists():
        return None
    d = subprocess.run(["git", "-C", str(repo), "stash", "list"], capture_output=True,
                       text=True)
    if d.returncode == 0 and d.stdout.strip():
        return f"REFUSED: {repo} has stash entries; the working tree is not the whole state"
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-s"],
                         capture_output=True, text=True)
    diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD"],
                          capture_output=True, text=True)
    if out.returncode or diff.returncode:
        return None
    return hashlib.sha256((out.stdout + diff.stdout).encode()).hexdigest()


def collect(scratch: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for pattern in SWEEP_GLOBS:
        for p in sorted(scratch.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                rows.append((sha256(p), f"sweep:{p.relative_to(scratch)}"))
    for repo in sorted(scratch.glob("c*/run-*/attempt*/arms/*/repo")):
        if (d := tree_digest(repo)):
            rows.append((d, f"tree:{repo.relative_to(scratch)}"))
    for rel in REPO_FILES:
        p = REPO / rel
        rows.append((sha256(p) if p.is_file() else "MISSING".ljust(64, "-"), f"repo:{rel}"))
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    rows = collect(scratch)
    body = "".join(f"{d}  {n}\n" for d, n in rows)
    if "--check" in argv:
        want = Path(argv[argv.index("--check") + 1]).read_text()
        want_rows = dict(reversed(ln.split("  ", 1)) for ln in want.splitlines() if ln.strip())
        got_rows = dict((n, d) for d, n in rows)
        bad = [n for n in sorted(set(want_rows) | set(got_rows))
               if want_rows.get(n) != got_rows.get(n)]
        for n in bad:
            print(f"CHANGED  {n}\n  was {want_rows.get(n, '(absent)')}\n  now "
                  f"{got_rows.get(n, '(absent)')}")
        print(f"{len(rows) - len(bad)} of {len(rows)} entries match")
        return 1 if bad else 0
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    if out:
        out.write_text(body)
        print(f"{len(rows)} entries -> {out}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
