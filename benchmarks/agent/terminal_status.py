"""Normalise how a run ended, before any of it reaches scoring.

The runner already knew things the record threw away. `invoke()` sets
`forced_verdict="timeout"` when it kills a run at the wall clock, and the process exit code
is available -- but the terminal record read only the final envelope, which a killed or
crashed run may never emit. Everything downstream then inferred the outcome from
hidden-check success alone, so protocol-a1 section 10's `env_fail` exclusion was registered
and never implemented.

One classification, from all the evidence, and it decides whether the run is scored at all.
"""
from __future__ import annotations

TERMINAL = {
    "completed": "ran to completion",
    "max_turns": "hit the in-run turn ceiling",
    "timeout": "killed at the wall-clock ceiling",
    "env_fail": "harness, auth or transport fault -- EXCLUDED from scored sets",
    "unknown": "could not be classified -- treated as env_fail",
}
EXCLUDED = {"env_fail", "unknown"}


def classify(rec: dict) -> dict:
    env = rec.get("result") or None
    forced = rec.get("forced_verdict")
    reason = (env or {}).get("terminal_reason")
    is_error = (env or {}).get("is_error")

    if forced == "timeout":
        cls = "timeout"
    elif env is None:
        # no envelope at all: the process died before emitting one
        cls = "env_fail"
    elif reason == "api_error" or (is_error and reason in (None, "", "error")):
        cls = "env_fail"
    elif reason == "max_turns":
        cls = "max_turns"
    elif reason == "completed" and not is_error:
        cls = "completed"
    elif reason:
        cls = "max_turns" if reason == "max_turns" else "unknown"
    else:
        cls = "unknown"

    return {
        "terminal": cls,
        "meaning": TERMINAL[cls],
        "scored": cls not in EXCLUDED,
        "truncated": cls in ("max_turns", "timeout"),
        "evidence": {"forced_verdict": forced, "envelope_present": env is not None,
                     "terminal_reason": reason, "is_error": is_error,
                     "subtype": (env or {}).get("subtype")},
    }
