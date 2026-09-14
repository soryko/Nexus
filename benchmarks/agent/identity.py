"""What two rows must share before they may be pooled -- computed ONCE, here.

Producer and consumer used to compute this separately: `run_identity` in run_arms_isolated
hashed `CFG.as_recorded()` (the resolved configuration, defaults filled in) while
`run_calibration.expected_identity` hashed the raw configuration file. Those are different
inputs, so an UNCHANGED configuration produced a row the resume check then refused -- a
legitimate interrupted sweep could not be resumed. Two implementations of one contract
disagree eventually; there is one here, and both sides call it.

The same applies to the prompt. It was recorded and never compared, so a row produced under a
changed prompt at the same filename resumed silently. The prompt is assembled here, from the
registration the configuration names, so the expectation exists to compare against.

`config_digest` is over the RESOLVED configuration -- a field the operator left to its default
is inside the digest, so changing a default in a1_config.py between rows is not invisible.
`config_source` is excluded: `as_recorded()` fills it from the module constant DEFAULT_PATH
rather than from the file that was actually loaded, so it is both wrong about its own
provenance and an absolute path of the host that ran. The remaining host paths are the
configuration's own fields and belong in its identity.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import task_set

BENCH = Path(__file__).parent
REPO = BENCH.parent.parent

# Every field that decides whether two rows may be pooled. A row missing any of them is
# refused: a field that is absent has not been shown to match.
FIELDS = ("config_version", "product_revision", "harness_revision", "config_digest",
          "max_turns_applied", "corpus_digest_registered", "schedule_digest", "prompt_digest")

# Fields computed from the configuration and the files it names rather than from git. An
# expectation that cannot be computed is not a licence to skip the comparison: these refuse.
# `product_revision` and `harness_revision` are not here because `rev()` returns None when git
# is unavailable, which is a fact about the reader's machine, not about the row.
MUST_BE_COMPUTABLE = ("config_version", "config_digest", "max_turns_applied",
                      "schedule_digest", "prompt_digest")

# The harnesses that may run a calibration or evaluation task, for task_set's refusal message.
EVAL_SETS = ("development", "heldout")


def config_digest(cfg) -> str:
    """The identity of the resolved configuration. See the module docstring for the exclusion."""
    recorded = {k: v for k, v in asdict(cfg).items() if k != "config_source"}
    return hashlib.sha256(json.dumps(recorded, sort_keys=True).encode()).hexdigest()[:16]


def prompt_text(cfg, task: str, harness: str = "run_arms_isolated.py") -> str:
    """The prompt one arm is given: body + tail + environment + consult, assembled once.

    `environment` is optional and absent from prompts-a1.json, so every A1 prompt still
    reconstructs byte-for-byte.
    """
    reg = task_set.registration(cfg.bench_path("prompts"))
    spec = task_set.require(reg, task, EVAL_SETS, harness)
    return task_set.assemble(reg, spec)


def prompt_digest(cfg, task: str, harness: str = "run_arms_isolated.py") -> str:
    return hashlib.sha256(prompt_text(cfg, task, harness).encode()).hexdigest()[:16]


def rev(path: str) -> str | None:
    """The revision that last touched `path`, or None if git cannot say."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%H", "--", path],
                             capture_output=True, text=True, timeout=30)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except Exception:
        return None


def expected(cfg, task: str, schedule_digest: str | None, max_turns_applied: int) -> dict:
    """What a row produced by THIS configuration, for THIS task, must record.

    The ceiling travels as `max_turns_applied` rather than being read back off the config: the
    config's filename does not establish its `max_turns`, and the driver has already refused a
    config whose declared ceiling is not the grid point it was asked for.
    """
    return {
        "config_version": cfg.config_version,
        "product_revision": rev("src/nexus_memory"),
        "harness_revision": rev("benchmarks/agent"),
        "config_digest": config_digest(cfg),
        "max_turns_applied": max_turns_applied,
        "corpus_digest_registered": cfg.corpus_digest or None,
        "schedule_digest": schedule_digest,
        "prompt_digest": prompt_digest(cfg, task),
    }


def incompatible(recorded: dict, want: dict) -> str | None:
    """-> a reason this recorded identity may not be pooled with `want`, or None.

    It used to compare four fields and accept everything else. A row recording a different
    product revision, a different harness revision, a different configuration digest and a
    different prompt digest was accepted for resume, which is the comparison this exists to
    prevent.
    """
    if not recorded:
        return "no identity block: predates configuration recording"
    bad = []
    for field in FIELDS:
        got, exp = recorded.get(field), want.get(field)
        if got is None:
            bad.append(f"{field}: absent from the record")
        elif exp is None:
            if field in MUST_BE_COMPUTABLE:
                bad.append(f"{field}: expectation could not be computed, so {got!r} has not "
                           f"been shown to match")
        elif got != exp:
            bad.append(f"{field}: recorded {got!r} != expected {exp!r}")
    return "; ".join(bad) or None
