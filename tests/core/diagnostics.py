"""Somewhere a rare failure's evidence can outlive the run that produced it.

pytest keeps ``tmp_path`` for three runs, so a flake that fires once in fifty stayed
readable only if nobody ran the suite three more times before anyone looked — which is
exactly what happened to the initialization failure observed on the baseline, whose
diagnosis is gone and whose cause is therefore still unresolved. Diagnoses are written
outside that rotation now, into a directory that is configurable and ignored by git.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
DIRECTORY_VARIABLE = "NEXUS_DIAGNOSTICS_DIR"
DEFAULT_DIRECTORY = PROJECT_ROOT / "artifacts" / "diagnostics"


def diagnostics_directory() -> Path:
    """``$NEXUS_DIAGNOSTICS_DIR`` if set, else an ignored directory inside the checkout.

    Configurable so CI can point it at a path it collects afterwards, and so a run whose
    checkout is read-only has somewhere else to put the evidence.
    """
    configured = os.environ.get(DIRECTORY_VARIABLE)
    return Path(configured) if configured else DEFAULT_DIRECTORY


def preserve(name: str, diagnosis: object) -> str:
    """Write one diagnosis under a unique name; return where it went, or why it did not.

    This never raises. The caller is already failing, and a diagnosis that cannot be
    written must not replace the failure it was meant to explain with an error about
    writing it — the reason is returned as text instead, for a failure message that carries
    the diagnosis inline regardless. Names carry the time, the process and a random suffix,
    so repeated failures in one run and concurrent runs sharing a directory cannot
    overwrite one another.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = diagnostics_directory() / f"{name}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(diagnosis, indent=2, default=str), encoding="utf-8")
    except Exception as error:  # reporting must not depend on the write succeeding
        return f"diagnosis could not be written to {target} ({error!r}); it is reproduced below"
    return f"diagnosis written to {target}"
