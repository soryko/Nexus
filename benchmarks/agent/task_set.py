"""Which harness may run which task, read off the registration rather than off argv.

`freeze-heldout-a1` §1 declares three disjoint sets -- development, capture, held-out -- and
"no task moves between them after this date". Three harnesses can run a task, and each is
wrong for two of the sets:

  run_arms.py            SUPERSEDED. One store shared by every arm and every attempt, the
                         schedule redrawn inside the run, no attempt in any output path.
                         Development only, and only because that is what it already produced.
  run_arms_isolated.py   the evaluation harness: development and held-out.
  run_capture.py         capture only.

The consequence of getting this wrong is not an error, which is why it is checked here rather
than warned about in a runbook. A held-out task run under `run_arms.py` produces a complete,
plausible set of records whose nexus and notes arms shared one mutable store -- the comparison
is void and nothing in the artifact says so.
"""
from __future__ import annotations

import json
from pathlib import Path

SETS = ("development", "capture", "heldout")


def registration(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def assemble(reg: dict, spec: dict) -> str:
    """The prompt one arm is given, from the registration: body + tail + environment + consult.

    Here rather than in the runner because the resume check has to reconstruct it to compare
    against, and a second copy of these four lines is a second thing to keep in step.
    `environment` is OPTIONAL and absent from prompts-a1.json, so every A1 prompt still
    reconstructs byte-for-byte; when present it is appended identically for all three arms.
    """
    return (spec["body"] + reg["tails"][spec["tail"]]
            + reg.get("environment", "") + reg["consult"])


def require(reg: dict, task: str, allowed: tuple[str, ...], harness: str) -> dict:
    """-> the task's registration entry, or raise SystemExit saying which harness to use."""
    spec = reg["tasks"].get(task)
    if spec is None:
        raise SystemExit(f"{harness}: no task {task!r} is registered; "
                         f"the registration has {', '.join(sorted(reg['tasks']))}")
    declared = spec.get("set")
    if declared not in SETS:
        raise SystemExit(f"{harness}: task {task!r} declares set {declared!r}, which is not "
                         f"one of {', '.join(SETS)}")
    if declared not in allowed:
        raise SystemExit(
            f"{harness}: REFUSING to run {task!r}.\n"
            f"  It is registered as a {declared} task; this harness runs "
            f"{' and '.join(allowed)} tasks.\n"
            f"  {_why(harness, declared)}")
    return spec


def _why(harness: str, declared: str) -> str:
    if harness == "run_arms.py":
        return ("run_arms.py is superseded: its arms share one store across every arm and "
                "every attempt, so a write by the nexus arm reaches the notes arm and the "
                "next attempt. Use run_arms_isolated.py, which gives each arm-run a private "
                "copy of the frozen corpus.")
    if declared == "capture":
        return ("A capture task run as an arm would be scored against memories captured FROM "
                "it. Use run_capture.py.")
    return ("An evaluation task run as a capture would put a held-out task in front of the "
            "session that writes the corpus, which is the contamination the held-out set "
            "exists to detect. Use run_arms_isolated.py.")
