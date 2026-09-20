"""Drive the A2-R development sweep: one ceiling, the same 4 tasks and 3 arms, repaired
toolchain.

A2-R is NOT the calibration. The calibration is closed with no ceiling selected
(`CLOSEOUT-calib-a2.md`) and nothing here reopens it. This file exists so that A2-R's spending
is charged to A2-R, its rows are counted as A2-R's, and its summary cannot be mistaken for the
calibration's.

What it adds to the driver, and nothing else:

  scope      its own output directory, refused if it overlaps the calibration's, so the
             scratch-wide ledger cannot pool the two sweeps' tokens. `consumed()` globs a
             scratch; two sweeps sharing one scratch share one budget by accident.
  threshold  20 000 000 tokens, a SOFT LAUNCH THRESHOLD, not a maximum. The check is
             identical to the calibration's -- a row is not STARTED without room for one the
             size of the largest yet seen -- and so is its weakness: an arm-run cannot be
             interrupted part-way, so the sweep can finish above the threshold by up to one
             row. A largest-observed-row reservation bounds nothing about the NEXT row.
  one point  exactly one ceiling. A2-R measures a repaired toolchain at one operating point;
             a grid would invite the ceiling comparison §5 already refused.
  pin        `A2_PYTHON` must be resolvable before anything is spent. The gate refuses an
             unpinned arm and `run_arms_isolated` refuses one even with the gate off; this is
             the third refusal, and it is the cheap one, because it happens before the first
             row.

Usage:  run_a2r.py <scratch> [--ceiling 45] [--dry-run]
"""
from __future__ import annotations

import sys
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import run_calibration as RC                                           # noqa: E402

# A SOFT LAUNCH THRESHOLD. See the module docstring: it is where the sweep stops starting
# rows, not a bound on what it spends. `REGISTRATION-DRAFT-a2r.md` §6 states the overshoot.
SOFT_LAUNCH_THRESHOLD = 20_000_000

DEFAULT_CEILING = 45


def config_for(ceiling: int) -> Path:
    """A2-R's own per-ceiling configuration, under its own name.

    NOT `calib-config-<n>.json`. Those three files are the closed calibration's frozen
    identity: `preflight-a2.py` checks their digests against `LAUNCH-A2.md`, and a `calib-v3`
    configuration written over `calib-config-45.json` would silently destroy the provenance of
    a published sweep. Host-local and gitignored, like the calibration's, because they carry
    this host's absolute paths.
    """
    return BENCH / f"a2r-config-{ceiling}.json"


SCOPE = RC.Scope(
    name="A2-R development sweep",
    cap_tokens=SOFT_LAUNCH_THRESHOLD,
    cap_label="soft launch threshold",
    soft=True,
    summary_name="a2r-summary.json",
    # No selection rule is applied to this sweep at all, so a partial one is not described in
    # terms of a rule it never reaches.
    partial_note=("A2-R is PARTIAL: it selects nothing either way, and a partial sweep "
                  "reports only the cells it measured."),
    config_locator=config_for,
)

# Files that identify a scratch as belonging to a different sweep. Sharing a scratch is not a
# tidiness problem: `consumed()` walks the whole tree, so the two ledgers become one silently
# and the closed calibration's tokens start blocking A2-R's rows.
FOREIGN_SUMMARIES = ("calibration-summary.json",)


def scope_conflict(scratch: Path) -> str | None:
    """-> why this directory may not be A2-R's, or None.

    Both directions are checked. A calibration summary INSIDE the scratch means A2-R would
    count the calibration's rows. A2-R sitting inside a calibration scratch means the reverse:
    a later `consumed()` over that parent counts A2-R's rows against the closed sweep's cap
    and its unresolved arm-run against A2-R's.
    """
    for name in FOREIGN_SUMMARIES:
        if (scratch / name).exists():
            return (f"{scratch} already holds {name}: it is another sweep's output directory. "
                    f"A2-R is accounted separately and needs its own.")
    for parent in scratch.resolve().parents:
        for name in FOREIGN_SUMMARIES:
            if (parent / name).exists():
                return (f"{scratch} is inside {parent}, which holds {name}. The ledger globs "
                        f"a whole scratch tree, so this would charge two sweeps to one "
                        f"budget. Put A2-R's output outside it.")
    return None


def pin_absent() -> str | None:
    """-> why the pinned interpreter is not resolvable, or None.

    Absent is not invented. When there is no host configuration `child_env` leaves `A2_PYTHON`
    unset rather than guessing, and an unset pin is exactly the v2 defect this sweep exists to
    assess -- so refusing here costs nothing and spending here measures the wrong thing.
    """
    env = a1_config.child_env("http://127.0.0.1:1/anthropic", "not-a-key")
    if not env.get("A2_PYTHON"):
        return ("A2_PYTHON is unset, so the interpreter the prompt documents as `$A2_PYTHON` "
                "would expand to nothing in every arm. That is the v2 defect, not a repaired "
                "run of it. Configure `pytest_python` in the host-local a1-config.json.")
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    scratch = Path(argv[1])
    ceiling = (int(argv[argv.index("--ceiling") + 1]) if "--ceiling" in argv
               else DEFAULT_CEILING)
    if "--ceilings" in argv:
        raise SystemExit("A2-R runs ONE ceiling. `--ceilings` is the calibration's grid flag; "
                         "use `--ceiling <n>`.")

    if why := scope_conflict(scratch):
        raise SystemExit(f"[a2r] {why}")
    if why := pin_absent():
        raise SystemExit(f"[a2r] {why}")

    print(f"A2-R development sweep: ceiling {ceiling}, {SCOPE.cap_label} "
          f"{SOFT_LAUNCH_THRESHOLD:,} tokens, output {scratch}")
    print("This is not the calibration. It selects no ceiling and reports no arm contrast.")
    return RC.main(["run_a2r.py", str(scratch), "--ceilings", str(ceiling)]
                   + (["--dry-run"] if "--dry-run" in argv else []), scope=SCOPE)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
