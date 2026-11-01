"""A3's launch ledger: what was STARTED, what was MEASURED, and what is UNRESOLVED.

A launch marker is written **before** the process starts, and an arm-run with a marker and
no usable terminal accounting is `unresolved` -- never zero, and never absent.

The distinction matters because a started process can consume tokens and leave nothing else.
`run_arms_isolated` already says so in its own comment: it buffers the whole trace until the
subprocess returns, so an interruption inside that window leaves the marker and nothing else,
and that window is how A2-R's k4 consumption was lost.

`a3_pipeline.saved_consumption` reintroduced exactly that defect by keying on `trace.jsonl`:

    if not (d / "trace.jsonl").is_file():
        continue                                   # not started: absent, not unresolved

"No trace" and "not started" are different facts, and the comment asserted the second from
the first. A launch interrupted before the runner returned had a marker, had spent tokens,
and reported `[]`. This module is keyed on the MARKER, which is written first and therefore
exists whenever anything could have been spent.

Two differences from `run_calibration.consumed`, which this follows otherwise:

  * **no allowances.** The registration's §7 says an unresolved arm-run is "never covered by
    an allowance written to unblock it". A2-R's ledger accepts `resolution.json`; this one
    refuses it and reports it as a refusal, so writing one cannot clear a stop.
  * **the identity is `(task, attempt, policy)`.** Both policies run the nexus arm, so a
    ledger keyed on the arm would merge the two members of every pair.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import artifact_io
from a3_decision import A3_PLAN, Plan
from run_calibration import usage_tokens

#: A3's own marker, written by `run_a3` before the process starts. It carries the POLICY and
#: the PROMPT DIGEST, which `run_arms_isolated`'s marker does not have and cannot: that
#: runner has no notion of a policy.
A3_MARKER = "a3-launch.json"
#: The runner's own marker, written inside `invoke` immediately before the subprocess. It
#: lands at the same path and OVERWRITES nothing of A3's, because they are different files --
#: an earlier version used one name for both and the runner's subset silently replaced the
#: richer record.
LAUNCH_MARKER = "launched.json"
#: Either proves a launch. The A3 marker is written first and the runner's second, so the
#: window in which neither exists is before anything could have been spent.
MARKERS = (A3_MARKER, LAUNCH_MARKER)
TERMINAL_RECORD = "record.json"

#: Written by the runner when it refuses to launch. A recorded refusal is a FATAL STOP: it
#: is not an arm-run that spent nothing, it is the sweep saying it must not continue.
REFUSAL = "refused.json"


OPERATOR_DIR = ".a3-operator"
FORMAT_FILE = "format.json"
FORMAT = '{"schema":"a3-operator-accounting","version":1}\n'


def _operator_format(root: Path) -> bool:
    area = root / OPERATOR_DIR
    if not os.path.lexists(area):
        return False
    try:
        value = artifact_io.read_text(area / FORMAT_FILE)
    except (OSError, ValueError) as exc:
        raise OSError("unreadable or incomplete A3 operator ledger") from exc
    if value != FORMAT:
        raise OSError("unrecognized A3 operator ledger format")
    return True


def check_execution_root(root: Path) -> None:
    """An epoch may start only on a fresh sweep. Legacy archives remain reportable."""
    operator = _operator_format(root)
    # Setup may pre-provision run-<task>/base. Any other entry in a task tree
    # must belong to a pair claimed by this epoch, even if its first arm never ran.
    with artifact_io.directory_fd(root) as fd, os.scandir(fd) as entries:
        for entry in entries:
            if entry.name.startswith("run-"):
                with artifact_io.directory_fd(root / entry.name) as task_fd:
                    attempts = set(os.listdir(task_fd)) - {"base"}
                for name in attempts:
                    task = entry.name.removeprefix("run-")
                    claim = root / OPERATOR_DIR / "receipts" / task / name / "claim.json"
                    if not operator or not (_present(claim) or any(
                            _present(claim.parent / policy / REFUSAL) for policy in A3_PLAN.policies)):
                        raise OSError("historical or unclaimed A3 attempt; use a fresh sweep root")
            elif not operator:
                if entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                    raise OSError(f"unexpected existing directory in fresh sweep: {entry.name}")
                if entry.name in (*MARKERS, REFUSAL, TERMINAL_RECORD, "trace.jsonl", "records.json"):
                    raise OSError(f"existing run artifact in fresh sweep: {entry.name}")


def start_epoch(root: Path) -> None:
    """Called under the sweep lock, after the non-dry execution guard and before pair claim."""
    check_execution_root(root)
    if not _operator_format(root):
        artifact_io.create_text(root / OPERATOR_DIR / FORMAT_FILE, FORMAT, parents=True)


def _operator_pair(root: Path, task: str, attempt: int) -> Path:
    return root / OPERATOR_DIR / "receipts" / task / f"attempt{attempt}"


def claim_pair(root: Path, task: str, attempt: int) -> None:
    if not _operator_format(root):
        raise OSError("cannot claim a pair outside an operator epoch")
    artifact_io.create_text(_operator_pair(root, task, attempt) / "claim.json",
                            json.dumps({"task": task, "attempt": attempt}), parents=True)


def _operator_cell(root: Path, task: str, attempt: int, policy: str) -> Path:
    return _operator_pair(root, task, attempt) / policy


def record_outcome(root: Path, task: str, attempt: int, policy: str, usage: dict | None) -> None:
    """Publish only the parent process's terminal envelope, after ordinary evidence succeeded."""
    if not _operator_format(root):
        raise OSError("A3 terminal accounting requires an initialized operator ledger")
    cell = _operator_cell(root, task, attempt, policy)
    if not _present(cell / A3_MARKER):
        raise OSError("A3 terminal accounting has no operator launch claim")
    artifact_io.create_text(cell / TERMINAL_RECORD, json.dumps(
        {"task": task, "attempt": attempt, "policy": policy, "usage": usage}) + "\n")


@contextmanager
def sweep_lock(root: Path):
    """Serialize pair admission through terminal accounting; a process exit releases the lock."""
    import fcntl  # The production harness requires macOS; offline tests also run on Linux.
    with artifact_io.directory_fd(root) as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def _dir(root: Path, task: str, attempt: int, policy: str) -> Path:
    from a3_pipeline import arm_run_dir
    return arm_run_dir(root, task, attempt, policy)


def pair_directory(root: Path, task: str, attempt: int) -> Path:
    from a3_pipeline import policy_run_dir
    return policy_run_dir(root, task, attempt, "A").parent


def mark_launch(root: Path, task: str, attempt: int, policy: str, *,
                identity: dict, max_turns: int, wall_clock_s: int,
                prompt_digest: str) -> Path:
    """Write the marker BEFORE the process starts. Returns the path written.

    Everything that could be known before spending is recorded here, because after an
    interruption this file may be all there is. `prompt_digest` in particular: it is what
    says WHICH POLICY was launched, and an unresolved row whose policy is unknown cannot
    even be attributed to a cell.
    """
    evidence = json.dumps({
        "task": task, "attempt": attempt, "policy": policy,
        "launched_utc": datetime.now(timezone.utc).isoformat(),
        "max_turns": max_turns, "wall_clock_s_limit": wall_clock_s,
        "prompt_digest": prompt_digest, "identity": identity,
    }, indent=1) + "\n"
    if _operator_format(root):
        artifact_io.create_text(_operator_cell(root, task, attempt, policy) / A3_MARKER,
                                evidence, parents=True)
    p = _dir(root, task, attempt, policy) / A3_MARKER
    artifact_io.create_text(p, evidence, parents=True)
    return p


def mark_refusal(root: Path, task: str, attempt: int, policy: str, reason: str,
                 detail: dict | None = None) -> Path:
    """Record that the runner REFUSED to launch this arm-run. A fatal stop, not a zero."""
    evidence = json.dumps({"task": task, "attempt": attempt, "policy": policy,
                           "refused_utc": datetime.now(timezone.utc).isoformat(),
                           "reason": reason, "detail": detail or {}}, indent=1) + "\n"
    if _operator_format(root):
        artifact_io.create_text(_operator_cell(root, task, attempt, policy) / REFUSAL,
                                evidence, parents=True)
    p = _dir(root, task, attempt, policy) / REFUSAL
    artifact_io.create_text(p, evidence, parents=True)
    return p


def _measured(d: Path) -> int | None:
    """Tokens this arm-run is known to have spent, most durable source first.

    `record.json` is written the moment the arm finishes; the trace envelope is the same
    arm-run seen again, not another one. The first source with a usable usage block is the
    measurement.
    """
    try:
        body = json.loads(artifact_io.read_text(d / TERMINAL_RECORD))
    except (FileNotFoundError, ValueError):
        body = {}
    if isinstance(body, dict):
        record = body.get("record") or body
        tokens = usage_tokens(record.get("usage")) if isinstance(record, dict) else None
        if tokens is not None:
            return tokens
    env = {}
    try:
        with artifact_io.open_regular(d / "trace.jsonl") as trace:
            for line in trace:
                if b'"type":"result"' in line[:40] or b'"type": "result"' in line[:40]:
                    try:
                        env = json.loads(line)
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    return usage_tokens(env.get("usage")) if isinstance(env, dict) else None


def _present(path: Path) -> bool:
    try:
        with artifact_io.open_regular(path):
            return True
    except FileNotFoundError:
        return False


@dataclass(frozen=True)
class Ledger:
    started: list[str]
    measured: dict[str, int]
    unresolved: list[str]
    refusals: dict[str, str]
    rejected_allowances: list[str]
    accounting_source: str

    @property
    def tokens_known(self) -> int:
        return sum(self.measured.values())

    @property
    def consumption_certain(self) -> bool:
        return not self.unresolved

    def as_dict(self) -> dict:
        return {"accounting_source": self.accounting_source, "arm_runs_started": len(self.started), "started": self.started,
                "tokens_known": self.tokens_known, "measured": self.measured,
                "unresolved_arm_runs": self.unresolved, "unresolved": len(self.unresolved),
                "refusals": self.refusals,
                "rejected_allowances": self.rejected_allowances,
                "consumption_certain": self.consumption_certain,
                "note": "tokens_known is a LOWER BOUND whenever unresolved is non-zero"}


def read(root: Path, plan: Plan = A3_PLAN) -> Ledger:
    """The ledger over every cell the plan registers. Keyed on the LAUNCH MARKER."""
    started, measured, refusals, rejected = [], {}, {}, []
    try:
        operator = _operator_format(root)
    except OSError as exc:
        return Ledger([], {}, [], {"sweep": str(exc)}, [], "unusable operator ledger")
    for task, attempt, policy in plan.cells():
        d = (_operator_cell if operator else _dir)(root, task, attempt, policy)
        key = f"{task}/{policy}/{attempt}"
        try:
            if _present(d / REFUSAL):
                try:
                    refusal = json.loads(artifact_io.read_text(d / REFUSAL))
                    refusals[key] = refusal.get("reason", "?") if isinstance(refusal, dict) else "?"
                except ValueError:
                    refusals[key] = "unreadable refusal record"
            if not any(_present(d / marker) for marker in ((A3_MARKER,) if operator else MARKERS)):
                if operator and _present(d / TERMINAL_RECORD):
                    refusals[key] = "operator outcome without a launch claim"
                continue
            started.append(key)
            if operator:
                tokens = None
                try:
                    receipt = json.loads(artifact_io.read_text(d / TERMINAL_RECORD))
                except (FileNotFoundError, ValueError):
                    receipt = None
                if (isinstance(receipt, dict) and receipt.get("task") == task
                        and receipt.get("attempt") == attempt and receipt.get("policy") == policy):
                    tokens = usage_tokens(receipt.get("usage"))
            else:
                tokens = _measured(d)
            if tokens is not None:
                measured[key] = tokens
            if _present(d / "resolution.json"):
                rejected.append(f"{key}: resolution.json present and REFUSED -- A3 registers no "
                                f"allowances, and the row stays unresolved")
        except OSError:
            refusals[key] = "unsafe or unreadable ledger artifact"
    unresolved = sorted(set(started) - set(measured))
    return Ledger(sorted(started), measured, unresolved, refusals, sorted(rejected),
                  "operator receipts" if operator else "historical arm artifacts")


# ---------------------------------------------------------------------------------------
# TWO GATES, and they are not the same gate.
#
# Admitting a pair is a BUDGET decision, taken once, before the pair starts. A fatal stop is
# a SAFETY decision, taken before EVERY arm-run including the second member of a pair that
# was already admitted. Collapsing them is how "the pair is admitted" comes to mean "both
# arm-runs may run", so an A arm-run that lost its accounting, or a boundary that refused,
# would still be followed by its B.
# ---------------------------------------------------------------------------------------

#: The registered soft launch threshold and per-pair reservation. The reservation is a
#: PLANNING figure -- what is set aside before a pair is admitted. It does not cap what an
#: admitted pair may then spend, and no numeric maximum overshoot is established.
SOFT_LAUNCH_THRESHOLD = 30_000_000
PAIR_RESERVATION = 4_831_570


def fatal_stop(root: Path, plan: Plan = A3_PLAN) -> dict | None:
    """-> a reason NO further arm-run may start, or None.

    Checked before every arm-run. §7's stop conditions that are not about the budget:
    unresolved consumption, and a recorded refusal (the environment gate or the boundary).

    An admitted pair confers no authority here. If A's consumption cannot be accounted for,
    B does not launch -- the sweep does not know what it has spent, and launching more is
    how a budget comes to say whatever one likes.
    """
    led = read(root, plan)
    if led.unresolved:
        return {"stop": True, "stopping_reason": "unresolved_accounting",
                "detail": led.unresolved,
                "reason": f"{len(led.unresolved)} arm-run(s) launched without usable "
                          f"terminal accounting: {', '.join(led.unresolved)}. The sweep does "
                          f"not know what it has spent."}
    if led.refusals:
        return {"stop": True, "stopping_reason": "refused",
                "detail": led.refusals,
                "reason": f"the runner refused {len(led.refusals)} arm-run(s): "
                          f"{'; '.join(f'{k} ({v})' for k, v in led.refusals.items())}"}
    if led.rejected_allowances:
        return {"stop": True, "stopping_reason": "allowance_refused",
                "detail": led.rejected_allowances,
                "reason": "an allowance was written for an unresolved arm-run; A3 registers "
                          "none, and it does not clear the stop"}
    return None


def may_admit_pair(root: Path, task: str, attempt: int, plan: Plan = A3_PLAN, *,
                   threshold: int = SOFT_LAUNCH_THRESHOLD,
                   reservation: int = PAIR_RESERVATION) -> dict:
    """-> whether the pair `(task, attempt)` may be STARTED. Budget, in tokens.

    Monetary-independent: the CLI's dollar field has no provenance on this model
    (`runner-a1` §3), so the threshold is in tokens and nothing here reads a cost.

    A fatal stop is checked first and separately, because a pair that cannot be admitted for
    safety reasons is not a budget outcome and must not be reported as one.
    """
    if (task, attempt) not in plan.pairs():
        return {"may_start": False, "task": task, "attempt": attempt, "gate": "registration",
                "stop": True, "stopping_reason": "unregistered_pair",
                "reason": "the pair is outside the registered plan"}
    stop = fatal_stop(root, plan)
    if stop:
        return {"may_start": False, "task": task, "attempt": attempt,
                "gate": "fatal", **stop}
    if (os.path.lexists(pair_directory(root, task, attempt))
            or os.path.lexists(_operator_pair(root, task, attempt))):
        return {"may_start": False, "task": task, "attempt": attempt, "gate": "existing",
                "stop": True, "stopping_reason": "pair_occupied",
                "reason": "the pair already has an output directory; keeping its evidence"}
    led = read(root, plan)
    known = led.tokens_known
    if known + reservation > threshold:
        return {"may_start": False, "task": task, "attempt": attempt, "gate": "budget",
                "stop": False, "stopping_reason": "soft_threshold",
                "tokens_known": known, "reservation": reservation, "threshold": threshold,
                "reason": f"{known:,} consumed + {reservation:,} reserved exceeds the "
                          f"{threshold:,} soft launch threshold; no new pair is started. "
                          f"Partial results are kept and reported as partial."}
    return {"may_start": True, "task": task, "attempt": attempt, "gate": "budget",
            "stop": False, "stopping_reason": None,
            "tokens_known": known, "reservation": reservation, "threshold": threshold,
            "reason": f"{known:,} consumed + {reservation:,} reserved is within the "
                      f"{threshold:,} soft launch threshold"}
