#!/usr/bin/env python3
"""Read a private P1 pilot log and describe what is in it. Nothing more.

`docs/pilot-p1.md` asks a person to record one JSON file per programming session while
following the routine in `docs/daily-use.md`. This script is the reader for those files.
It exists for two reasons, and it is worth being clear that the second is the important one:

  * Arithmetic a person should not be doing by hand at the end of a fortnight.
  * A refusal. A record that is malformed, duplicated, or internally inconsistent stops the
    report instead of quietly vanishing from it -- because a log's failure mode is not a
    wrong total, it is a row that silently stopped being counted.

What the output establishes is **shape and arithmetic**: these records were well-formed,
and here is what they say. It is not evidence that the records are true, that memory helped,
or that the routine is worth following. This script therefore computes no benefit
percentage, no ROI, no token or money saving, no confidence interval, and no recommendation.

The distinction it works hardest to preserve is **unknown versus zero**. `null` in a record
means the participant did not count or did not review; `0` means they did, and it was zero.
A tool that folded the first into the second would turn an honest gap into a confident
measurement, so every numeric result carries its own coverage and a metric with no known
values reports `observed_sum: null`.

Offline and dependency-free by construction: standard library only, no MCP calls, no
subprocesses, no network, no SQLite, no credential discovery, no telemetry, no upload. It
reads the one directory it is given and writes to stdout.

Diagnostics name a **file and a field, never a value**. The log holds private paths, a
namespace, an actor, memory identifiers and free prose; an error message that echoed the
offending value would leak exactly the material the log is kept outside the repository to
protect. Enumerations are reported as the allowed set, which is public and lives in the
template beside this script.

    python3 tools/summarize_pilot.py --log-dir "$pilot_log_dir"

Exit 0 means the inputs were structurally valid -- including an empty or wholly incomplete
pilot, which is a legitimate thing to have. Exit 2 means they were not, and nothing is
written to stdout in that case.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

# The enumerations `docs/pilot-p1.md` defines. Kept as tuples so the report's key order is
# the documented order rather than whatever a set iterates in.
STATES = ("open", "completed", "abandoned")
CONSULTATIONS = ("attempted", "not_attempted", "unknown")
TASK_OUTCOMES = ("completed", "partial", "failed", "abandoned", "unknown")
IMPACTS = ("helped", "neutral", "harmed", "not_used", "unknown")
FRICTION_CODES = (
    "connection", "scope", "capture_effort", "no_relevant_hit", "irrelevant_hit",
    "stale_memory", "verification_effort", "correction_conflict", "consultation_overhead",
    "logging_burden", "other",
)

COUNT_FIELDS = ("search_calls", "get_calls", "capture_calls")
DURATION_FIELDS = ("consult_seconds", "logging_seconds")
METRIC_FIELDS = COUNT_FIELDS + DURATION_FIELDS
EVIDENCE_FIELDS = ("captured_memories", "reuse_evidence", "friction_codes")

# Every manifest field has to be filled before a report means anything: each one is part of
# the provenance the rows are being read against. The blank template ships with six of them
# null on purpose, and this is what refuses it.
MANIFEST_TEXT_FIELDS = (
    "pilot_id", "routine_revision", "service_version", "client_name", "client_version",
    "project_alias", "server_command", "database_path", "namespace", "actor",
)
MANIFEST_FIELDS = frozenset(
    MANIFEST_TEXT_FIELDS + ("schema_version", "started_at", "session_limit", "day_limit")
)
SESSION_FIELDS = frozenset(
    ("schema_version", "pilot_id", "session_id", "task_alias", "project_alias",
     "routine_revision", "started_at", "state", "consultation", "task_outcome", "impact",
     "captured_memories", "reuse_evidence", "friction_codes", "note") + METRIC_FIELDS
)

CAPTURE_ENTRY_FIELDS = frozenset(("memory_id", "revision_id"))
REUSE_ENTRY_FIELDS = frozenset(
    ("memory_id", "revision_id", "capture_session_id", "basis_checked", "action_note")
)


class PilotError(Exception):
    """An input this tool refuses to summarize. Carries a location, never a value."""


def _fail(location: str, problem: str) -> None:
    raise PilotError(f"{location}: {problem}")


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------

def _reject_constant(name: str):
    # json accepts NaN/Infinity/-Infinity by default. A duration of NaN would compare false
    # against every bound and sum to NaN, poisoning a total that still looks like a number.
    raise ValueError(f"{name} is not permitted")


def _reject_duplicate_keys(pairs):
    # Python's default keeps the last of a repeated key, so `{"impact":"helped",
    # "impact":"harmed"}` would parse cleanly as the second one. Key names are documented
    # field names, so naming the offender here leaks nothing.
    seen: set[str] = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError(f"duplicate object key {key!r}")
        seen.add(key)
    return dict(pairs)


def _read_json(path: Path, location: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(location, f"cannot be read ({exc.strerror or exc.__class__.__name__})")
    try:
        parsed = json.loads(
            text, parse_constant=_reject_constant, object_pairs_hook=_reject_duplicate_keys
        )
    except ValueError as exc:
        _fail(location, f"is not valid JSON ({exc})")
    if not isinstance(parsed, dict):
        _fail(location, "is not a JSON object")
    return parsed


def load_pilot(root: Path) -> tuple[dict, list[dict]]:
    """Read and structurally validate `manifest.json` and `sessions/*.json` under `root`.

    Per-record validation only; checks that need the whole set -- unique identifiers,
    agreement with the manifest, capture links -- belong to `summarize`, so that a caller
    holding records in memory gets them too.
    """
    root = Path(root)
    if not root.is_dir():
        _fail(str(root), "is not a directory")

    manifest = _validate_manifest(_read_json(root / "manifest.json", "manifest.json"))

    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        _fail(
            "sessions/",
            "is missing -- an empty sessions directory is a pilot with no sessions yet, "
            "but a missing one usually means the wrong --log-dir",
        )

    sessions: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted(sessions_dir.glob("*.json")):
        location = f"sessions/{path.name}"
        record = _validate_session(_read_json(path, location), location)
        # Caught here as well as in `summarize` because only here are both filenames known,
        # and "which two files" is the whole of what makes this one actionable.
        first = seen.get(record["session_id"])
        if first is not None:
            _fail(location, f"repeats the session_id already recorded in {first}")
        seen[record["session_id"]] = location
        sessions.append(record)
    return manifest, sessions


# --------------------------------------------------------------------------------------
# Field validation
# --------------------------------------------------------------------------------------

def _exact_keys(record: dict, expected: frozenset[str], location: str) -> None:
    present = set(record)
    missing = sorted(expected - present)
    unknown = sorted(present - expected)
    if missing:
        _fail(location, f"is missing required field(s): {', '.join(missing)}")
    if unknown:
        _fail(location, f"carries field(s) this format does not define: {', '.join(unknown)}")


def _schema_version(record: dict, location: str) -> None:
    value = record["schema_version"]
    if isinstance(value, bool) or value != SCHEMA_VERSION:
        _fail(location, f"field 'schema_version' must be exactly {SCHEMA_VERSION}")


def _text(record: dict, field: str, location: str) -> str:
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        _fail(location, f"field {field!r} must be a non-empty string")
    return value


def _enum(record: dict, field: str, allowed: tuple[str, ...], location: str) -> str:
    value = record[field]
    if not isinstance(value, str) or value not in allowed:
        _fail(location, f"field {field!r} must be one of: {', '.join(allowed)}")
    return value


def _timestamp(raw, field: str, location: str) -> datetime:
    if not isinstance(raw, str):
        _fail(location, f"field {field!r} must be an ISO 8601 timestamp string")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        _fail(location, f"field {field!r} is not a parseable ISO 8601 timestamp")
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A naive timestamp cannot be ordered against the manifest's start without assuming
        # a zone, and assuming one is how a session silently moves across the deadline.
        _fail(location, f"field {field!r} must carry a UTC offset; a naive timestamp is ambiguous")
    return moment


def _count(record: dict, field: str, location: str) -> int | None:
    value = record[field]
    if value is None:
        return None
    # bool is a subclass of int, so `True` would otherwise pass as the count 1.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(location, f"field {field!r} must be a whole number of calls, or null if not counted")
    if value < 0:
        _fail(location, f"field {field!r} cannot be negative")
    return value


def _duration(record: dict, field: str, location: str) -> float | None:
    value = record[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(location, f"field {field!r} must be a number of seconds, or null if not measured")
    # parse_constant catches the NaN/Infinity literals; a float overflowing from something
    # like 1e400 arrives here as inf without ever passing through it.
    if not math.isfinite(value):
        _fail(location, f"field {field!r} must be a finite number of seconds")
    if value < 0:
        _fail(location, f"field {field!r} cannot be negative")
    return value


def _optional_text(record: dict, field: str, location: str) -> str | None:
    value = record[field]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(location, f"field {field!r} must be a non-empty string, or null")
    return value


def _entry_list(value, field: str, entry_fields: frozenset[str], location: str):
    """`null` (unknown), `[]` (reviewed, none), or a list of objects with exactly these keys."""
    if value is None:
        return None
    if not isinstance(value, list):
        _fail(location, f"field {field!r} must be null if unreviewed, or a list of entries")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            _fail(location, f"field {field!r} entry {index} is not an object")
        _exact_keys(entry, entry_fields, f"{location} field {field!r} entry {index}")
    return value


def _validate_manifest(record: dict) -> dict:
    location = "manifest.json"
    _exact_keys(record, MANIFEST_FIELDS, location)
    _schema_version(record, location)

    # Collected rather than reported one at a time: the published blank ships with six nulls
    # and filling them one error at a time would be six runs of this script.
    unfilled = [f for f in MANIFEST_TEXT_FIELDS + ("started_at",) if record[f] is None]
    if unfilled:
        _fail(
            location,
            "is not filled in. Complete these field(s) from the client entry that launches "
            f"the server before reporting: {', '.join(unfilled)}",
        )

    for field in MANIFEST_TEXT_FIELDS:
        _text(record, field, location)
    _timestamp(record["started_at"], "started_at", location)
    for field in ("session_limit", "day_limit"):
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _fail(location, f"field {field!r} must be a positive whole number")
    return record


def _validate_session(record: dict, location: str) -> dict:
    _exact_keys(record, SESSION_FIELDS, location)
    _schema_version(record, location)

    for field in ("pilot_id", "session_id", "project_alias", "routine_revision"):
        _text(record, field, location)
    _optional_text(record, "task_alias", location)
    if record["started_at"] is None:
        _fail(
            location,
            "field 'started_at' is null. The record is created when the work begins, so a "
            "start time is always known, even when nothing else about the session is",
        )
    _timestamp(record["started_at"], "started_at", location)

    _enum(record, "state", STATES, location)
    _enum(record, "consultation", CONSULTATIONS, location)
    _enum(record, "task_outcome", TASK_OUTCOMES, location)
    _enum(record, "impact", IMPACTS, location)

    for field in COUNT_FIELDS:
        _count(record, field, location)
    for field in DURATION_FIELDS:
        _duration(record, field, location)

    captured = _entry_list(
        record["captured_memories"], "captured_memories", CAPTURE_ENTRY_FIELDS, location
    )
    for index, entry in enumerate(captured or []):
        where = f"{location} field 'captured_memories' entry {index}"
        for field in CAPTURE_ENTRY_FIELDS:
            _text(entry, field, where)

    reuse = _entry_list(record["reuse_evidence"], "reuse_evidence", REUSE_ENTRY_FIELDS, location)
    for index, entry in enumerate(reuse or []):
        where = f"{location} field 'reuse_evidence' entry {index}"
        for field in ("memory_id", "revision_id", "action_note"):
            _text(entry, field, where)
        _optional_text(entry, "capture_session_id", where)
        if entry["basis_checked"] not in (True, False, None):
            _fail(where, "field 'basis_checked' must be true, false, or null")

    friction = record["friction_codes"]
    if friction is not None:
        if not isinstance(friction, list):
            _fail(location, "field 'friction_codes' must be null if unreviewed, or a list of codes")
        seen: set[str] = set()
        for code in friction:
            if not isinstance(code, str) or code not in FRICTION_CODES:
                _fail(location, f"field 'friction_codes' accepts only: {', '.join(FRICTION_CODES)}")
            if code in seen:
                _fail(location, f"field 'friction_codes' repeats {code!r}; each code counts once")
            seen.add(code)

    if not isinstance(record["note"], str):
        _fail(location, "field 'note' must be a string, empty if there is nothing to add")
    return record


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------

def observed_metric(rows: list[dict], field: str) -> dict:
    """A sum of what was observed, beside how much was not.

    `observed_sum` is null when nothing was counted, so no reader can mistake an empty
    pilot, or an unmeasured one, for a measured zero.
    """
    values = [row[field] for row in rows if row[field] is not None]
    return {
        "observed_sum": sum(values) if values else None,
        "known_sessions": len(values),
        "unknown_sessions": len(rows) - len(values),
    }


def _tally(rows: list[dict], field: str, allowed: tuple[str, ...]) -> dict:
    counts = dict.fromkeys(allowed, 0)
    for row in rows:
        counts[row[field]] += 1
    return counts


def _check_links(rows: list[dict], starts: dict[str, datetime]) -> None:
    """A claimed capture link has to survive being looked up. It is not proof of anything.

    All this establishes is that the cited source session exists, is a different and earlier
    one, and recorded the memory being cited. Whether the memory was genuinely reused is a
    judgement `docs/pilot-p1.md` reserves for a person reading the records.
    """
    by_id = {row["session_id"]: row for row in rows}
    for row in rows:
        where = f"session {row['session_id']}"
        for index, entry in enumerate(row["reuse_evidence"] or []):
            source_id = entry["capture_session_id"]
            if source_id is None:
                continue                       # unlinked, and reported as such
            at = f"{where} field 'reuse_evidence' entry {index}"
            source = by_id.get(source_id)
            if source is None:
                _fail(at, "names a capture session that is not in this log")
            if source_id == row["session_id"]:
                _fail(at, "names its own session; same-session capture is not cross-session reuse")
            if starts[source_id] >= starts[row["session_id"]]:
                _fail(at, "names a capture session that did not start earlier than this one")
            captured = source["captured_memories"]
            if captured is None:
                _fail(
                    at,
                    "names a capture session that records no captured memories, so the link "
                    "cannot be checked; record capture_session_id as null if the source of "
                    "the memory is not established",
                )
            # Revisions deliberately not compared: a memory may have been revised between
            # being captured and being reused, and that is the normal case, not a defect.
            if not any(item["memory_id"] == entry["memory_id"] for item in captured):
                _fail(at, "cites a memory the named capture session does not record capturing")


def summarize(manifest: dict, sessions: list[dict]) -> dict:
    """Describe the records. Cross-record validation happens here, then counting."""
    if not isinstance(manifest, dict):
        _fail("manifest.json", "is not a JSON object")
    _validate_manifest(manifest)
    pilot_start = _timestamp(manifest["started_at"], "started_at", "manifest.json")
    deadline = pilot_start + timedelta(days=manifest["day_limit"])

    starts: dict[str, datetime] = {}
    for position, row in enumerate(sessions):
        if not isinstance(row, dict):
            _fail(f"session record {position}", "is not an object")
        # Named by id where there is one; a record too broken to identify is named by the
        # position it was supplied in, which is still enough to find it.
        identifier = row.get("session_id")
        where = (
            f"session {identifier}"
            if isinstance(identifier, str) and identifier.strip()
            else f"session record {position}"
        )
        _validate_session(row, where)
        if row["session_id"] in starts:
            _fail(where, "is recorded twice")
        for field in ("pilot_id", "project_alias", "routine_revision"):
            if row[field] != manifest[field]:
                # Not reconciled into the manifest's value: a routine change means the rows
                # were produced under different instructions and aggregating across the
                # break would average two things that are not the same measurement.
                _fail(
                    where,
                    f"field {field!r} disagrees with manifest.json. A change here starts a "
                    f"separately versioned segment; it is not aggregated with this one",
                )
        moment = _timestamp(row["started_at"], "started_at", where)
        if moment < pilot_start:
            _fail(where, "starts before the pilot's own start time in manifest.json")
        starts[row["session_id"]] = moment

    _check_links(sessions, starts)

    # Deterministic, and in the order the sessions actually happened.
    ordered = sorted(sessions, key=lambda row: (starts[row["session_id"]], row["session_id"]))

    deviations: list[dict] = []
    for rank, row in enumerate(ordered):
        if rank >= manifest["session_limit"]:
            deviations.append({"session_id": row["session_id"], "deviation": "over_session_limit"})
        if starts[row["session_id"]] >= deadline:
            deviations.append({"session_id": row["session_id"], "deviation": "after_day_limit"})

    return {
        "pilot_id": manifest["pilot_id"],
        "routine_revision": manifest["routine_revision"],
        "service_version": manifest["service_version"],
        "recorded_sessions": len(ordered),
        "state_counts": _tally(ordered, "state", STATES),
        "consultation_counts": _tally(ordered, "consultation", CONSULTATIONS),
        "task_outcome_counts": _tally(ordered, "task_outcome", TASK_OUTCOMES),
        "reported_impact_counts": _tally(ordered, "impact", IMPACTS),
        "metric_coverage": {f: observed_metric(ordered, f) for f in METRIC_FIELDS},
        "friction_session_counts": _friction_counts(ordered),
        "evidence_coverage": {
            field: {
                "known_sessions": sum(1 for r in ordered if r[field] is not None),
                "unknown_sessions": sum(1 for r in ordered if r[field] is None),
            }
            for field in EVIDENCE_FIELDS
        },
        "reuse_evidence_entries": _reuse_counts(ordered),
        "protocol_deviations": deviations,
    }


def _friction_counts(rows: list[dict]) -> dict:
    """Occurrences per code -- or all null, when no session's list was reviewed at all.

    Every documented code appears either way, so a code with no occurrences reads as the
    observed zero it is rather than being absent and looking like an oversight.
    """
    reviewed = [row["friction_codes"] for row in rows if row["friction_codes"] is not None]
    if not reviewed:
        return dict.fromkeys(FRICTION_CODES, None)
    counts = dict.fromkeys(FRICTION_CODES, 0)
    for codes in reviewed:
        for code in codes:
            counts[code] += 1
    return counts


def _reuse_counts(rows: list[dict]) -> dict:
    """Entries recorded, split by whether their capture session was named and resolved."""
    reviewed = [row["reuse_evidence"] for row in rows if row["reuse_evidence"] is not None]
    if not reviewed:
        return {"observed_entries": None, "linked_entries": None, "unlinked_entries": None}
    entries = [entry for lst in reviewed for entry in lst]
    linked = sum(1 for entry in entries if entry["capture_session_id"] is not None)
    return {
        "observed_entries": len(entries),
        "linked_entries": linked,
        "unlinked_entries": len(entries) - linked,
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="summarize_pilot.py",
        description="Describe a private P1 pilot log. Reads only the directory it is given.",
    )
    parser.add_argument(
        "--log-dir", required=True, type=Path,
        help="the private pilot directory holding manifest.json and sessions/",
    )
    args = parser.parse_args(argv)

    try:
        manifest, sessions = load_pilot(args.log_dir)
        # Built in full before a byte is printed, so a failure late in aggregation cannot
        # leave half a report on stdout looking like a whole one.
        report = summarize(manifest, sessions)
    except PilotError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
